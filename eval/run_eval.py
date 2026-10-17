import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.amenities import nearby
from app.core.db import close_pool, get_connection
from app.orchestration.graph import GRAPH, _ask, new_session
from app.search.search import search_properties
from eval.questions import CATEGORIES, QUESTIONS

OUT_DIR = Path(__file__).resolve().parent / "results"
RAW_PATH = OUT_DIR / "answers.json"
REPORT_PATH = OUT_DIR / "report.md"


_ARM_A_SYSTEM = """你是墨尔本房产投资助手。根据用户的问题推荐 3~5 套具体房源。

每套请给出:所在区、门牌地址、售价、卧室数、卫生间数、年租金、毛租金回报率。
最后简短说明推荐理由。用中文回答。"""

_ARM_B_SYSTEM = """你是墨尔本房产投资助手。下面会给你从数据库检索到的**真实房源记录**。

请从中挑 3~5 套推荐给用户,每套给出:所在区、门牌地址、售价、卧室数、卫生间数、
年租金,以及**你计算的毛租金回报率**(毛租金回报率 = 年租金 ÷ 售价)。
最后简短说明推荐理由。用中文回答。

只能使用给定记录里的房源,不要添加记录之外的房源。"""


def answer_arm_a(question: str) -> str:
    return _ask(_ARM_A_SYSTEM, question, temperature=0.3)


def answer_arm_b(question: str) -> str:
    rows = search_properties(semantic_query=question, limit=10)
    if not rows:
        rows_text = "(数据库没有检索到任何房源)"
    else:
        keep = ("suburb", "address", "property_type", "price", "bedrooms",
                "bathrooms", "car_spaces", "distance_cbd", "annual_rent")
        rows_text = json.dumps([{k: r.get(k) for k in keep} for r in rows],
                               ensure_ascii=False, indent=2)
    return _ask(_ARM_B_SYSTEM, f"用户的问题:{question}\n\n检索到的真实房源记录:\n{rows_text}",
                temperature=0.3)


def answer_arm_c(question: str) -> str:
    import uuid
    result = GRAPH.invoke({"user_query": question}, config=new_session(uuid.uuid4().hex))
    metrics = result.get("metrics") or []
    if not metrics:
        return result.get("answer", "")
    lines = []
    for i, m in enumerate(metrics, 1):
        line = (f"{i}. {m.get('suburb')} {m.get('address')} · "
                f"售价 ${m.get('price'):,} · {m.get('bedrooms')}房{m.get('bathrooms')}卫")
        if m.get("annual_rent") is not None:
            line += f" · 年租金 ${m['annual_rent']:,}"
        if m.get("gross_yield") is not None:
            line += f" · 毛租金回报率 {m['gross_yield'] * 100:.2f}%"
        for kind, hit in (m.get("amenities") or {}).items():
            line += f" · 最近{nearby.KIND_ZH.get(kind, kind)} {hit['distance_m']}米"
        if m.get("near_place"):
            line += f" · 距{m['near_place']['name']} {m['near_place']['distance_m']}米"
        lines.append(line)
    return "\n".join(lines) + "\n\n" + (result.get("answer") or "")


ARMS = {
    "A": ("LLM only, no data", answer_arm_a),
    "B": ("LLM + raw data, computes metrics itself", answer_arm_b),
    "C": ("This system (code computes)", answer_arm_c),
}


_EXTRACT_SYSTEM = """你是一个信息抽取器。从下面这段房产推荐文本里,把提到的**每一套房**
抽成结构化记录。

只输出一个 JSON 对象,不要 markdown 代码块,不要解释。格式:

{
  "properties": [
    {"suburb": "Richmond", "address": "39 York St", "price": 1000000,
     "bedrooms": 3, "bathrooms": 1, "annual_rent": 39000, "gross_yield": 0.039,
     "distances": [{"what": "火车站", "meters": 400}]}
  ],
  "refused": false,
  "refusal_reason": null
}

规则:
- 文本里**没有提到**的字段填 null,不要猜、不要算。
- price / annual_rent 是澳元整数。"24 万"->240000,"$1,182,000"->1182000。
- gross_yield 是**小数**:文本里写"8.0%"就填 0.08,写"3.29%"就填 0.0329。
- address 只填门牌+街道(如 "39 York St"),不要带区名和邮编;没写就 null。
- distances 收集所有"距离某某多少米/公里"的说法,公里换算成米。
- 如果文本表示**没有找到符合条件的房源**(或明确说数据库里没有这类数据),
  refused 填 true,refusal_reason 填一句话概括原因,properties 填 []。
- 如果文本只是解释概念、没有推荐具体房源,properties 填 []、refused 填 false。

你的任务只是**照抄**,不要判断对错,不要补全缺失的数字。"""


def extract_claims(text: str) -> dict:
    if not text or not text.strip():
        return {"properties": [], "refused": False, "refusal_reason": None}
    raw = _ask(_EXTRACT_SYSTEM, text, temperature=0.0)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return {"properties": [], "refused": False, "refusal_reason": None,
                "_extract_failed": True}
    try:
        parsed = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return {"properties": [], "refused": False, "refusal_reason": None,
                "_extract_failed": True}
    parsed.setdefault("properties", [])
    parsed.setdefault("refused", False)
    return parsed


_NORM = re.compile(r"[^a-z0-9]+")


def _norm_addr(text) -> str:
    return _NORM.sub("", str(text).lower()) if text else ""


_property_index: dict | None = None


def _load_index() -> dict:
    global _property_index
    if _property_index is not None:
        return _property_index
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, suburb, address, price, bedrooms, bathrooms, annual_rent "
                    "FROM properties")
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    
    by_addr, by_suburb_price, suburbs = defaultdict(list), defaultdict(list), set()
    addr_only = defaultdict(list)
    for row in rows:
        suburbs.add(row["suburb"].lower())
        if row["address"]:
            by_addr[(row["suburb"].lower(), _norm_addr(row["address"]))].append(row)
            addr_only[_norm_addr(row["address"])].append(row)
        by_suburb_price[(row["suburb"].lower(), row["price"])].append(row)
        
    _property_index = {"by_addr": by_addr, "by_suburb_price": by_suburb_price,
                       "addr_only": addr_only, "suburbs": suburbs, "rows": rows}
    return _property_index


def verify_property(claim: dict) -> dict:
    index = _load_index()
    suburb = (claim.get("suburb") or "").strip().lower()
    addr = _norm_addr(claim.get("address"))
    price = claim.get("price")

    result = {"claim": claim, "suburb_exists": suburb in index["suburbs"] if suburb else None,
              "matched_by": None, "exists": False, "suburb_ok": None,
              "price_ok": None, "rent_ok": None, "yield_ok": None, "yield_error_pp": None}

    def _pick_best(rows):
        if not rows: return None
        if len(rows) == 1: return rows[0]
        if isinstance(price, (int, float)):
            for r in rows:
                if r["price"] == int(price):
                    return r
        return rows[0]

    row = None
    if suburb and addr:
        candidates = index["by_addr"].get((suburb, addr))
        if candidates:
            row = _pick_best(candidates)
            result["matched_by"] = "地址"

    if row is None and addr:
        candidates = index["addr_only"].get(addr)
        if candidates:
            row = _pick_best(candidates)
            result["matched_by"] = "地址"
            if suburb:
                result["suburb_ok"] = suburb == row["suburb"].lower()

    if row is None and suburb and isinstance(price, (int, float)):
        candidates = index["by_suburb_price"].get((suburb, int(price)))
        if candidates:
            row = candidates[0]
            result["matched_by"] = "区+价格"
            
    if row is None:
        return result

    result["exists"] = True
    result["db"] = {k: row[k] for k in ("id", "suburb", "address", "price",
                                        "bedrooms", "bathrooms", "annual_rent")}
    if isinstance(price, (int, float)):
        result["price_ok"] = int(price) == row["price"]
    if isinstance(claim.get("annual_rent"), (int, float)):
        result["rent_ok"] = abs(claim["annual_rent"] - row["annual_rent"]) <= row["annual_rent"] * 0.01
    claimed_yield = claim.get("gross_yield")
    if isinstance(claimed_yield, (int, float)) and row["price"]:
        truth = row["annual_rent"] / row["price"]
        result["yield_error_pp"] = round((claimed_yield - truth) * 100, 3)
        result["yield_ok"] = abs(claimed_yield - truth) <= 0.001
    return result


def verify_distances(claim: dict, verified: dict) -> list[dict]:
    out = []
    if not verified.get("exists"):
        return out
    row_id = verified["db"]["id"]
    index = _load_index()
    row = next((r for r in index["rows"] if r["id"] == row_id), None)
    if row is None:
        return out
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT latitude, longitude FROM properties WHERE id = %s", (row_id,))
        pos = cur.fetchone()
    if not pos or pos[0] is None:
        return out

    zh_to_kind = {v: k for k, v in nearby.KIND_ZH.items()}
    near = nearby.nearest_by_kind(pos[0], pos[1])
    for item in claim.get("distances") or []:
        what, meters = item.get("what"), item.get("meters")
        if not isinstance(meters, (int, float)):
            continue
        kind = zh_to_kind.get(what)
        if kind is None:
            kind = next((k for k, zh in nearby.KIND_ZH.items()
                         if what and (what in zh or zh[:2] in str(what))), None)
        if kind is None or kind not in near:
            out.append({"what": what, "claimed_m": meters, "checkable": False})
            continue
        truth = near[kind]["distance_m"]
        out.append({"what": what, "claimed_m": meters, "truth_m": truth,
                    "checkable": True,
                    "ok": abs(meters - truth) <= max(50, truth * 0.10)})
    return out


def run(arms: list[str]) -> dict:
    OUT_DIR.mkdir(exist_ok=True)
    data = json.loads(RAW_PATH.read_text(encoding="utf-8")) if RAW_PATH.exists() else {}
    for question in QUESTIONS:
        bucket = data.setdefault(question["id"], {"question": question})
        for arm in arms:
            label, fn = ARMS[arm]
            print(f"  [{arm}] {question['id']} {question['text'][:26]}…", end="", flush=True)
            started = time.time()
            try:
                text = fn(question["text"])
            except Exception as exc:
                text = f"(generation failed: {type(exc).__name__}: {exc})"
            bucket[arm] = {"label": label, "text": text, "seconds": round(time.time() - started, 1)}
            print(f" {time.time() - started:.0f}s")
            RAW_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def grade(data: dict, arms: list[str]) -> dict:
    for question in QUESTIONS:
        bucket = data.get(question["id"])
        if not bucket:
            continue
        for arm in arms:
            entry = bucket.get(arm)
            if not entry or "graded" in entry:
                continue
            print(f"  Check [{arm}] {question['id']}…", end="", flush=True)
            claims = extract_claims(entry["text"])
            verified = [verify_property(c) for c in claims.get("properties", [])]
            distances = [d for c, v in zip(claims.get("properties", []), verified)
                         for d in verify_distances(c, v)]
            entry["graded"] = {"claims": claims, "verified": verified, "distances": distances}
            print(f" {len(verified)} listings")
            RAW_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _pct(numerator, denominator) -> str:
    return (
        f"{numerator / denominator * 100:.2f}% "
        f"({numerator}/{denominator})"
        if denominator else "—"
    )

def summarise(data: dict, arms: list[str]) -> str:
    stats = {arm: defaultdict(int) for arm in arms}
    per_category = {arm: defaultdict(lambda: defaultdict(int)) for arm in arms}

    for question in QUESTIONS:
        bucket = data.get(question["id"]) or {}
        category = question["category"]
        for arm in arms:
            entry = bucket.get(arm)
            if not entry or "graded" not in entry:
                continue
            s, c = stats[arm], per_category[arm][category]
            graded = entry["graded"]
            s["questions"] += 1
            c["questions"] += 1

            for v in graded["verified"]:
                s["props"] += 1
                c["props"] += 1
                if v["matched_by"] == "地址":
                    s["addr_ok"] += 1
                    c["addr_ok"] += 1
                    for key in ("price_ok", "rent_ok", "yield_ok"):
                        if v[key] is True:
                            s[key] += 1
                        elif v[key] is False:
                            s[key + "_bad"] += 1
                    for key in ("price", "rent", "yield"):
                        if v[key + "_ok"] is not None:
                            s[key + "_checked"] += 1
                elif v["matched_by"] == "区+价格":
                    s["coincidence"] += 1
                    for key in ("rent_ok", "yield_ok"):
                        if v[key] is True:
                            s[key + "_coin"] += 1
                        if v[key] is not None:
                            s[key + "_coin_checked"] += 1
                else:
                    s["not_found"] += 1
            for d in graded["distances"]:
                if d.get("checkable"):
                    s["dist_checked"] += 1
                    if d.get("ok"):
                        s["dist_ok"] += 1

            if category == "out_of_scope":
                s["oos_q"] += 1
                for v in graded["verified"]:
                    claimed_suburb = (v["claim"].get("suburb") or "").strip().lower()
                    s["oos_props"] += 1
                    if claimed_suburb and v["suburb_exists"] is False:
                        s["oos_fabricated"] += 1

    lines = ["# Hallucination Evaluation Report", "",
             f"Generated: {datetime.now():%Y-%m-%d %H:%M}  ·  {len(QUESTIONS)} questions", "",
             "## Design", "",
             "Three arms, same question set:", "",
             "| Arm | Setup | Purpose |",
             "|---|---|---|",
             "| A | LLM only, no data supplied | Check for fabricated listings |",
             "| B | LLM + real retrieval results, computes metrics itself | Check metric accuracy when data is correct |",
             "| C | This system: deterministic computation, LLM explains only | The proposed approach |", "",
             "Arm B is the key comparison. Comparing only against Arm A is insufficient: a",
             "model without data will fabricate, which is expected. What needs testing is whether",
             "an LLM computing metrics from real data still produces unreliable results, and",
             "therefore whether the metric formulas should live in code.", "",
             "Grading is done by code. The LLM does not verify anything. It only converts",
             "free-text numeric claims into structured records; all checks run against the",
             "database: does the listing exist, does the price match, does the yield equal annual",
             "rent divided by price, does the claimed distance match the computed value.", "",
             "## Summary", "",
             "| Metric | " + " | ".join(f"{a}:{ARMS[a][0]}" for a in arms) + " |",
             "|---|" + "---|" * len(arms)]

    def row(name, fn):
        return f"| {name} | " + " | ".join(fn(stats[a]) for a in arms) + " |"

    lines += [
        row("Listings mentioned", lambda s: str(s["props"])),
        row("**Address exists in database**", lambda s: _pct(s["addr_ok"], s["props"])),
        row("Suburb + price match only", lambda s: _pct(s["coincidence"], s["props"])),
        row("No match", lambda s: _pct(s["not_found"], s["props"])),
        "| | | | |",
        row("Price matches database †", lambda s: _pct(s["price_ok"], s["price_checked"])),
        row("Annual rent matches database †", lambda s: _pct(s["rent_ok"], s["rent_checked"])),
        row("**Yield computed correctly** †", lambda s: _pct(s["yield_ok"], s["yield_checked"])),
        row("Rent match (coincidence group)", lambda s: _pct(s["rent_ok_coin"], s["rent_ok_coin_checked"])),
        row("Distance check pass rate", lambda s: _pct(s["dist_ok"], s["dist_checked"])),
        row("**Fabricated suburbs** (claimed suburb not in database)",
            lambda s: _pct(s["oos_fabricated"], s["oos_props"])),
        "",
        "† Counts only listings whose street address matches. The \"suburb + price\" group is",
        "excluded: matching on price makes any price check circular.",
        "",
        "**\"Address exists in database\" is the meaningful row.** Fabricating a listing such",
        "as \"Richmond, $800k\" can easily coincide with a real listing in the same suburb at",
        "the same price; fabricating an address that also matches is far less likely.",
    ]

    lines += ["", "## By category: address accuracy", "",
              "| Category | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    for key, label in CATEGORIES.items():
        cells = []
        for arm in arms:
            c = per_category[arm][key]
            cells.append(_pct(c["addr_ok"], c["props"]) if c["props"] else "—")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    lines += ["", "## Findings", "", _CONCLUSION]
    return "\n".join(lines) + "\n"


_CONCLUSION = """### 1. Evaluation of Listing Accuracy Without External Data

Arm A mentioned 121 listings. Only 1 (1%) had an address that exists in the database, indicating that the model systematically fabricated almost all listings when no external data was provided. Furthermore, when asked about Sydney or Brisbane, 100% of the listings it produced claimed suburbs that do not exist in the database (such as Liverpool or Parramatta), with prices and yields specified to the dollar.

### 2. Evaluation of LLM Arithmetic Accuracy

The evaluation was designed expecting Arm B to make calculation errors, which would support the claim that formulas must live in code. The data does not support that expectation. Arm B mentioned 206 listings, of which 100% (206/206) had valid addresses. Among the claims eligible for verification, price accuracy was 99.50% (200/201), and rental accuracy was 100% (198/198). The yield was computed correctly in 99.51% (205/206).

These findings indicate that the LLM performs reliably on basic arithmetic tasks when supplied with verified property data. Therefore, the main architectural advantage should not be attributed solely to arithmetic accuracy.

### 3. Key Differences in Retrieval and Ranking Performance

(a) Questions of the form "highest yield under $X": Arm B is systematically wrong, even though every arithmetic step is correct.

For queries requiring global ranking (e.g., the highest-yield property under a certain price limit), Arm B only sees the 10 semantic retrieval results placed in its prompt. Every number it calculates may be correct, but the answer is often wrong because the true highest-yield property is not within those 10 candidates. "Highest" requires comparison over the full set, which cannot be solved by placing a small batch of data in the context window. It is a retrieval-and-ranking architecture problem.

An earlier version of Arm C had the same issue. It ranked only within 120 semantic candidates. A serious case: for "three-bedroom house under $1M", 4,370 properties in the database match, and the "5 quietest" it returned ranked 85th, 89th, 103rd, 113th and 243rd in the full set, with zero overlap with ground truth.

The fix was to raise the candidate pool from 120 to 5,000, and to compute amenity and environmental evidence in batch (BallTree / STRtree), since a larger pool would otherwise add over ten seconds to each query. Arm C now reports the full-set ground truth for these questions.

The retrieval process is subject to a maximum candidate pool of 5,000 properties, rather than the complete dataset of 20,800 records. When more than 5,000 properties pass the hard filters, ranking still runs on a subset, and every output notes this. Guaranteeing full-set optimality at any scale would require search_properties() to sort by metric, which changes its signature. This modification would require coordinated changes to the retrieval interface and its dependent modules.

(b) When data is missing, Arm B has only two options: fabricate or refuse. Arm C has a third.

For questions requiring data not present in the dataset (e.g., NOI and cap rate), Arm B typically refuses to answer because the records do not provide the necessary operating expenses. Arm C, however, can compute these figures using a labelled assumption (e.g., a 28% operating expense rate) and explicitly states that the figures are based on an assumed rate, not actual transaction data.

The third option is an explicitly labelled assumption: a number is given, the basis is stated, and the user can change it (`:opex 30`). Using assumptions is not the problem; presenting them as data is.

### 4. A case Arm C did not catch

Question o5 asks for houses on "Zhongshan Road". The system returned 5 real listings (e.g., in Hillside) but did not explicitly state that Melbourne has no such street. 

The cause is that street name is not a filterable dimension. `search_properties()` supports exact filtering on suburb, so "Sydney" and "Brisbane" can be rejected by the database directly. It has no street parameter, so a street name only falls through to semantic search and degrades to fuzzy matching.

This is not fabrication (all 5 listings exist), but it does not answer the question directly. Fixing it requires adding a street dimension to the retrieval layer. This modification would also require coordinated changes to the retrieval interface and its dependent modules.

### 5. Reliability of the evaluation itself

- Grading is not done by an LLM. The LLM only converts free text into structured records; every check runs against the database in code and can be re-run and re-inspected.
- Verifier bugs count as hallucinations in the system under test, so the verifier has to be checked first. The first version required both suburb and address to match, which marked real Arm B listings as fabricated because Arm B listed them in a table with the suburb shown only once. The fix allows a unique street address to match on its own, and Arm B's address accuracy reached 100%.
- "Suburb + price" matches are excluded from the main metric. Fabricating "Richmond, $800k" can easily coincide with one of the 328 Richmond listings at that price, and the price check would then be circular. The main metric therefore counts only street address matches.
- The evaluation metric counts all listing claims mentioned in a response, not just those in a formal recommendation list. In cases where an arm explained missing data but provided a supplementary table, those listings were also verified.
- When a street address corresponds to multiple records in the database, the verifier prioritises matching by price. This means the 100% price accuracy should be interpreted as the accuracy within this specific matching method, rather than an independent verification across all possible conditions.
- Sample size is limited: 50 questions, 3 arms, 492 listing claims. Differences between categories (especially small sub-samples) are not statistically robust."""
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", nargs="+", default=["A", "B", "C"], choices=list(ARMS))
    parser.add_argument("--report", action="store_true",
                        help="Skip the run, generate the report from existing results")
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    if args.report:
        data = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    else:
        print(f"Generating answers ({len(QUESTIONS)} questions × {len(args.arms)} arms)...")
        data = run(args.arms)
    print("Extracting claims and verifying...")
    data = grade(data, args.arms)
    report = summarise(data, args.arms)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"Raw answers: {RAW_PATH}\nReport: {REPORT_PATH}")


if __name__ == "__main__":
    try:
        main()
    finally:
        close_pool()