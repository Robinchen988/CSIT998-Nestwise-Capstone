import ast, builtins, re, sqlite3, sys, types
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
failures = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


class _Stub:
    loading = True
    def __getattr__(self, n): return _Stub()
    def __iter__(self): return iter(())
    def __contains__(self, i): return False
    def __bool__(self): return False
    def __len__(self): return 0
    def __call__(self, *a, **k):
        if _Stub.loading:
            return {}
        raise AssertionError("tested path unexpectedly CALLED a stubbed import")


def load(path, names, pre=None):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    top, imported = {}, set()
    for n in tree.body:
        if isinstance(n, ast.FunctionDef):
            top[n.name] = n
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if isinstance(t, ast.Name):
                    top[t.id] = n
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            imported |= {(a.asname or a.name).split(".")[0] for a in n.names}
    need, todo = [], [x for x in names if x in top]
    while todo:
        nm = todo.pop()
        if nm in need or nm not in top:
            continue
        need.append(nm)
        node = top[nm]
        local = {x.arg for x in ast.walk(node) if isinstance(x, ast.arg)} | {x.id for x in ast.walk(node) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
        todo += [x.id for x in ast.walk(node) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load) and x.id in top and x.id not in local]
    ns = {"__builtins__": builtins, "re": re, "State": dict}
    for imp in imported:
        ns.setdefault(imp, _Stub())
    ns.update(pre or {})
    for nm in sorted(need, key=lambda k: top[k].lineno):
        exec(compile(ast.Module([top[nm]], []), str(path), "exec"), ns)
    return ns


F = load(ROOT / "app/analytics/formulas.py", ["investment_metrics", "roi_unrounded", "roi_certified_prefix", "roi_order_sql", "_VIC_DUTY_BRACKETS", "_duty_unrounded"])
inv = F["investment_metrics"]
S = load(ROOT / "app/search/search.py", ["_ORDER_BY"], pre={"roi_order_sql": F["roi_order_sql"]})
ORDER = S["_ORDER_BY"]


def to_sqlite(sql):
    return re.sub(r"%\((\w+)\)s", r":\1", sql).replace("::float8", "").replace("::float", " * 1.0")


class FakeDB:
    def __init__(self, rows, after_search=None):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, price INTEGER, annual_rent INTEGER, bedrooms INTEGER, property_type TEXT)")
        self.db.executemany("INSERT INTO t VALUES (?,?,?,?,?)", rows)
        self.calls, self.after_search = [], after_search

    def search_properties(self, semantic_query=None, max_price=None, min_price=None, bedrooms=None, bathrooms=None, property_type=None, suburb=None,
                          limit=10, order_by=None, opex_rate=None, other_costs=None):
        where = "(:max_price IS NULL OR price <= :max_price) AND (:min_price IS NULL OR price >= :min_price) AND (:bedrooms IS NULL OR bedrooms = :bedrooms) " \
                "AND (:property_type IS NULL OR property_type = :property_type)"
        cur = self.db.execute(f"SELECT id, price, annual_rent, bedrooms, property_type FROM t WHERE {where} ORDER BY {to_sqlite(ORDER[order_by])}id LIMIT :limit",
                              {"max_price": max_price, "min_price": min_price, "bedrooms": bedrooms, "property_type": property_type, "limit": limit,
                               "opex_rate": opex_rate, "other_costs": float(other_costs) if other_costs is not None else None})
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        self.calls.append({"order_by": order_by, "limit": limit, "returned": len(rows), "opex_rate": opex_rate, "other_costs": other_costs})
        if self.after_search:
            self.after_search()
        return rows


class Box:
    def __init__(self, opex=0.28, fees=2000): self.opex, self.fees = opex, fees
    def opex_rate(self): return self.opex
    def other_acquisition_costs(self): return self.fees
    def snapshot(self): return {"opex_rate": self.opex, "other_acquisition_costs": self.fees}


def make_graph(db, box, L, near=None, zone_found=None, place_err=None):
    def enrich(state):
        ms = []
        for m in state["metrics"]:
            m = dict(m)
            m["amenities"], m["context_scores"] = {}, {}
            if near is not None and not place_err:
                m["near_place"] = {"distance_m": near(m["id"])}
            ms.append(m)
        return {"metrics": ms, "place_lookup": ({"query": "x", "found": 0 if place_err else 1, "error": place_err} if near is not None else None),
                "zone_lookup": ({"query": "z", "found": zone_found, "error": None} if zone_found is not None else None)}
    _Stub.loading = True
    G = load(ROOT / "app/orchestration/graph.py", ["rank", "_rank_once", "search", "analyze", "CANDIDATE_LIMIT", "ROI_EXPAND_CAP", "RESULT_LIMIT", "_SQL_ORDER", "SEARCH_KEYS", "RANK_KEYS"])
    G.update({"answer_guard": __import__("types").SimpleNamespace(**__import__("runpy").run_path(str(__import__("pathlib").Path(__file__).resolve().parents[1] / "app/orchestration/answer_guard.py"))), "_dataset_suburbs": lambda: [], "deepcopy": __import__("copy").deepcopy, "search_properties": db.search_properties, "enrich": enrich, "assumptions": box, "investment_metrics": inv,
              "roi_unrounded": F["roi_unrounded"], "roi_certified_prefix": F["roi_certified_prefix"], "i18n": types.SimpleNamespace(sort_labels_en=lambda d: d),
              "predict_values": lambda rows: [{"predicted_price": r["price"], "is_stub": False, "typical_error_pct": 0.09, "range_low": r["price"], "range_high": r["price"],
                                               "interval_level": None, "interval_low": None, "interval_high": None, "interval_coverage": None, "cross_fitted": True} for r in rows],
              "_rent_sources": lambda ids: {i: "precinct_exact_sheet" for i in ids}, "nearby": types.SimpleNamespace(distance_to_cbd_km=lambda a, b, fb: fb),
              "SEARCH_KEYS": ("semantic_query", "max_price", "min_price", "bedrooms", "bathrooms", "property_type", "suburb"),
              "RANK_KEYS": ("sort_by", "min_gross_yield", "near_place", "school_zone", "unsupported_asks"), "CANDIDATE_LIMIT": L, "ROI_EXPAND_CAP": L * 8})
    _Stub.loading = False
    return G


def pipeline(G, params, lang="zh", drop_snapshot=False):
    st = {"params": params, "user_query": "q", "lang": lang, "intent": "new_search"}
    st.update(G["search"](st))
    if drop_snapshot:
        st.pop("assumption_snap", None)
    st.update(G["analyze"](st))
    e = G["enrich"](st)
    st.update(e)
    return st, G["rank"](st)


N = 5000
rows_a = [(i, 400_000, 30_000, 3, "house") for i in range(1, N + 1)] + [(N + 1, 600_000, 20_000, 3, "house")]
db = FakeDB(rows_a); box = Box()
st, out = pipeline(make_graph(db, box, N, near=lambda i: 5000 if i <= N else 50), {"sort_by": "roi", "near_place": {"name": "Test place", "max_distance_m": 100}})
ids = [m["id"] for m in out["metrics"]]
check(ids == [N + 1], f"[R07B-01a] distance filter empties the first 5000 -> expands and returns id 5001 (got {ids}; note: {out['ranking'][:70]})")
check([c["limit"] for c in db.calls] == [5001, 10001], f"[R07B-01a] fetch limits [5001, 10001] (got {[c['limit'] for c in db.calls]}; cumulative rows returned {sum(c['returned'] for c in db.calls)})")
check("没有找到" not in out["ranking"], "[R07B-01a] no premature 'no compatible properties' message")

rows_b = [(i, 1_000_000, 50_000, 3, "house") for i in range(1, N + 1)] + [(N + 1, 10_000, 550, 3, "house")]
db = FakeDB(rows_b); box = Box()
st, out = pipeline(make_graph(db, box, N), {"sort_by": "roi", "min_gross_yield": 0.053})
ids = [m["id"] for m in out["metrics"]]
check(ids == [N + 1], f"[R07B-01b] yield-floor .053 empties the first 5000 -> expands and returns id 5001 (got {ids}; note: {out['ranking'][:70]})")
check([c["limit"] for c in db.calls] == [5001, 10001], f"[R07B-01b] fetch limits [5001, 10001] (got {[c['limit'] for c in db.calls]})")
st, out_en = pipeline(make_graph(FakeDB(rows_b), Box(), N), {"sort_by": "roi", "min_gross_yield": 0.053}, lang="en")
check([m["id"] for m in out_en["metrics"]] == [N + 1] and "No candidate" not in out_en["ranking"] and "No candidates" not in out_en["ranking"], "[R07B-01b] en: same result, no 'no candidate' message")

box = Box(0.28, 199_999)
db = FakeDB(rows_b, after_search=lambda: setattr(box, "fees", 0))
G = make_graph(db, box, N)
st, out = pipeline(G, {"sort_by": "roi"})
top = out["metrics"][0]
want = max(((inv(p, r, 0.28, 199_999)["roi"], -i, i) for (i, p, r, _, _) in rows_b))
check(top["id"] == want[2], f"[R07B-02] ranking uses ONE snapshot (fees 199999): top id {top['id']} equals the true top under that snapshot ({want[2]})")
check(top["roi"] == inv(top["price"], top["annual_rent"], 0.28, 199_999)["roi"], "[R07B-02] the ROI shown was computed with the search-time snapshot, not the drifted global (fees 0)")
check(out["metrics"][0]["assumptions"].get("other_acquisition_costs") == 199_999, f"[R07B-02] the displayed assumptions are the snapshot used ({out['metrics'][0]['assumptions']})")
claimed = "已验证" in out["ranking"]
true_ids_snapshot = [i for _, _, i in sorted(((inv(p, r, 0.28, 199_999)["roi"], i, i) for (i, p, r, _, _) in rows_b), key=lambda x: (-x[0], x[1]))[:20]]
check((not claimed) or [m["id"] for m in out["more"]] == true_ids_snapshot, "[R07B-02] a 'verified' claim, if made, is true for the snapshot")
box2 = Box(0.28, 199_999)
db2 = FakeDB(rows_b, after_search=lambda: setattr(box2, "fees", 0))
st2, out2 = pipeline(make_graph(db2, box2, N), {"sort_by": "roi"}, drop_snapshot=True)
check("已验证" not in out2["ranking"] and ("假设" in out2["ranking"]), f"[R07B-02] if ROI was computed under different assumptions than the pre-sort, NO 'verified' claim and the note says so ({out2['ranking'][:90]})")
st3, out3 = pipeline(make_graph(FakeDB(rows_b, after_search=lambda: None), Box(0.28, 199_999), N), {"sort_by": "roi"}, lang="en", drop_snapshot=True)
check("are verified" not in out3["ranking"], "[R07B-02] en: same (consistent assumptions needed for any 'verified' claim)")

db = FakeDB(rows_a); box = Box()
st, out = pipeline(make_graph(db, box, N, zone_found=0), {"sort_by": "roi", "school_zone": {"school": "Nowhere PS"}})
check(out["metrics"] == [] and len(db.calls) == 1, f"unresolved school zone: no pointless expansion (fetches {[c['limit'] for c in db.calls]}) and an empty, explained result")
db = FakeDB(rows_a); box = Box()
st, out = pipeline(make_graph(db, box, N, near=lambda i: 1, place_err="not found"), {"sort_by": "roi", "near_place": {"name": "Nowhere", "max_distance_m": 100}})
check(out["metrics"] == [] and len(db.calls) == 1, f"unresolved place: no pointless expansion (fetches {[c['limit'] for c in db.calls]})")

db = FakeDB(rows_a); box = Box()
st, out = pipeline(make_graph(db, box, N, near=lambda i: 9_999), {"sort_by": "roi", "near_place": {"name": "P", "max_distance_m": 100}})
check(out["metrics"] == [] and [c["limit"] for c in db.calls] == [5001, 10001] and "没有找到" in out["ranking"],
      f"no match anywhere: expanded until the set was exhausted ({[c['limit'] for c in db.calls]}), then the plain no-match message")

rows_c = [(i, 500_000, 25_000, 3, "house") for i in range(1, 301)]
db = FakeDB(rows_c); box = Box()
st, out = pipeline(make_graph(db, box, 10, near=lambda i: 50 if i == 300 else 5000), {"sort_by": "roi", "near_place": {"name": "P", "max_distance_m": 100}}, lang="en")
cum = sum(c["returned"] for c in db.calls)
check(max(c["limit"] for c in db.calls) <= 81 and out["metrics"] == [] and "pre-sorted by ROI" in out["ranking"] and "may still match" in out["ranking"] and "all 300" not in out["ranking"],
      f"cap 80: expansion stops (limits {[c['limit'] for c in db.calls]}, cumulative rows {cum}) and the empty answer carries a pool/cap disclosure: {out['ranking'][:130]}")
st, out = pipeline(make_graph(FakeDB(rows_c), Box(), 10, near=lambda i: 50 if i == 300 else 5000), {"sort_by": "roi", "near_place": {"name": "P", "max_distance_m": 100}})
check("池" in out["ranking"] and "可能" in out["ranking"], f"zh: same disclosure ({out['ranking'][:70]})")

rows_t = [(i, 500_000, 25_000, 3 if i % 2 else 2, "house") for i in range(1, 121)]
db = FakeDB(rows_t); box = Box()
st, out = pipeline(make_graph(db, box, 25), {"sort_by": "roi", "bedrooms": 3})
check([m["id"] for m in out["more"]] == list(range(1, 40, 2)), f"identical ROI + SQL filter: ties by ascending id across expansion rounds (got {[m['id'] for m in out['more']][:6]}…)")

rows_e = [(i, 500_000, 25_000, 3, "house") for i in range(1, 20_801)]
db = FakeDB(rows_e); box = Box()
st, out = pipeline(make_graph(db, box, 5000), {"sort_by": "roi"})
lims = [c["limit"] for c in db.calls]
print(f"   cumulative: 20,800 equal-ROI rows -> fetch limits {lims}, rows returned per round {[c['returned'] for c in db.calls]}, cumulative candidates processed {sum(c['returned'] for c in db.calls)}")
check(len(out["properties"]) == 20_800 and out["roi_pool"] is None and "全部 20800 套" in out["ranking"], "20,800 rows: expansion ended with the whole set and says so")

refm = types.SimpleNamespace(relaxable_need=lambda a, g: None, relative_rank=lambda m, g, p, n: m)
box = Box(); db = FakeDB([(1, 500_000, 25_000, 3, "house")])
G = make_graph(db, box, 5000)
G.update({"refinement": refm, "_score_shift": lambda *a, **k: None, "_tradeoff_hint": lambda *a, **k: None})
mets = [{"id": i, "price": 500_000 + i, "annual_rent": 25_000, "gross_yield": 0.05, "roi": 0.03, "cap_rate": 0.04, "predicted_gap": None, "valuation_error_pct": None,
         "amenities": {}, "context_scores": {}} for i in range(5000)]
goal = [{"field": "price", "direction": "down", "degree": "slight", "baseline": 500_000}]


def rel(search_order, roi_pool, lang):
    st = {"metrics": mets, "params": {"relative_preferences": goal}, "search_order": search_order, "roi_pool": roi_pool, "lang": lang, "user_query": "q", "intent": "refine"}
    return G["_rank_once"](st)["ranking"]


t_trunc = rel("roi", {"cutoff": 0.03, "opex_rate": 0.28, "other_costs": 2000, "limit": 5000}, "zh")
check("语义" not in t_trunc and "按投资回报率预排序" in t_trunc and "并非全库" in t_trunc, f"relative goals + ROI pool (truncated): wording describes the ROI pre-sort, not 'semantically relevant' ({t_trunc[-70:]})")
e_trunc = rel("roi", {"cutoff": 0.03, "opex_rate": 0.28, "other_costs": 2000, "limit": 5000}, "en")
check("semantically" not in e_trunc and "pre-sorted by ROI" in e_trunc and "not the whole dataset" in e_trunc, "en: same")
t_full = rel("roi", None, "zh")
check("语义" not in t_full and "并非全库" not in t_full and "全部" in t_full, f"relative goals + complete ROI pool: no 'not the whole dataset' claim ({t_full[-60:]})")
t_gy = rel("gross_yield", None, "zh")
check("语义相关" in t_gy, "non-ROI order keeps the original wording (no regression)")

import json
_Stub.loading = True
GE = load(ROOT / "app/orchestration/graph.py", ["explain", "_assumption_lines", "_EXPLAIN_SYSTEM", "_EXPLAIN_LANG_EN"])
GE.update({"answer_guard": __import__("types").SimpleNamespace(**__import__("runpy").run_path(str(__import__("pathlib").Path(__file__).resolve().parents[1] / "app/orchestration/answer_guard.py"))), "_dataset_suburbs": lambda: []})
_Stub.loading = False
seen = {}


class DescBox(Box):
    def describe(self): return [f"GLOBAL opex {self.opex:.0%}, fees {self.fees}"]


def ask(system, user, temperature=0.0):
    seen["user"] = user
    return "SIMULATED"


def explain_prompt(box, metrics, snap=None, lang="zh", intent="new_search"):
    GE.update({"json": json, "assumptions": box, "_ask_streaming": ask, "refinement": types.SimpleNamespace()})
    st = {"metrics": metrics, "intent": intent, "user_query": "q", "lang": lang, "params": {}, "ranking": "x", "batch_offset": 0}
    if snap:
        st["assumption_snap"] = snap
    GE["explain"](st)
    return seen["user"]


used = {"opex_rate": 0.28, "other_acquisition_costs": 199_999}
mm = [{"id": 1, "price": 500_000, "assumptions": used}]
p = explain_prompt(DescBox(0.28, 0), mm)
check("$199,999" in p and "GLOBAL" not in p and "已被改动" in p, "[explain drift] global fees changed to 0 after analyze: the prompt carries the metrics' own fees ($199,999), not the global describe()")
p = explain_prompt(DescBox(0.28, 199_999), mm)
check("GLOBAL opex 28%, fees 199999" in p and "已被改动" not in p, "[explain] assumptions unchanged: the normal describe() text is used unchanged")
p = explain_prompt(DescBox(0.28, 0), mm, lang="en")
check("$199,999" in p and "GLOBAL" not in p and "has since changed" in p, "[explain drift] en: same")
check("Other acquisition costs excluding stamp duty" in p and "Settlement" not in p, "[explain label] en: the fees line says 'other acquisition costs excluding stamp duty', not 'settlement costs'")
pz = explain_prompt(DescBox(0.28, 0), mm)
check("印花税之外的购置开销" in pz and "过户杂费" not in pz and "印花税另按法定税率算" in pz, "[explain label] zh: the fees line says purchase costs other than stamp duty (conveyancing/legal/inspection), duty calculated separately")
p = explain_prompt(DescBox(0.40, 2000), [{"id": 1, "price": 1, "assumptions": used}], intent="about_results")
check("28.0%" in p and "$199,999" in p and "GLOBAL" not in p, "[explain about_results] follow-up on EARLIER results after the global moved: uses the earlier results' assumptions (28% / $199,999)")
p = explain_prompt(DescBox(0.28, 0), [{"id": 1, "price": 1, "assumptions": {"something": 1}}])
check("不要引用具体" in p and "GLOBAL" not in p and "$" not in p.split("本次测算所依据的假设")[1].split("已算好的房源数据")[0], "[explain] assumptions of unknown shape: no invented number, the model is told not to quote one")
p = explain_prompt(DescBox(0.28, 2000), [{"id": 1, "price": 1}])
check("GLOBAL opex 28%, fees 2000" in p, "[explain] metrics without any assumptions and no snapshot (legacy/detail path): describe() as before")
p = explain_prompt(DescBox(0.28, 199_999), mm, snap={"display": used, "opex_rate": 0.28, "other_costs": 199_999})
check("GLOBAL" in p, "[explain] snapshot present and equal to the global: describe()")


class FlipBox(DescBox):
    def describe(self):
        out = super().describe()
        self.fees = 0
        return out


p = explain_prompt(FlipBox(0.28, 199_999), mm)
check("GLOBAL" not in p and "$199,999" in p, "[explain race] global flips while describe() runs: detected by the second snapshot read, the metrics' assumptions are used")


for review_lang in ("zh", "en"):
    decimal_prompt = explain_prompt(DescBox(0.2, 0), [{**mm[0], "assumptions": {"opex_rate": 0.284, "other_acquisition_costs": 199999}}], lang=review_lang)
    check("28.4%" in decimal_prompt, f"[{review_lang}] the real .284 assumption is described as 28.4%, not rounded to 28%")
RS = load(ROOT / "app/orchestration/refinement.py", ["snapshot"], pre={"deepcopy": __import__("copy").deepcopy})
review_rows = [(i, 1000000, 50000, 3, "house") for i in range(1, 301)]
for review_lang in ("zh", "en"):
    old_state, old_out = pipeline(make_graph(FakeDB(review_rows), Box(.28, 2000), 10), {"sort_by": "roi"}, lang=review_lang)
    old_state.update(old_out)
    old_base = RS["snapshot"](old_state)
    review_db = FakeDB(review_rows)
    review_G = make_graph(review_db, Box(.28, 3000), 10, near=lambda i: 5000)
    review_state = {**old_state, "params": {"sort_by": "roi", "near_place": {"name": "P", "max_distance_m": 100}},
                    "lang": review_lang, "intent": "refine", "user_query": "q", "refinement_base": old_base}
    for node in ("search", "analyze", "enrich"):
        review_state.update(review_G[node](review_state))
    restored = {**review_state, **review_G["rank"](review_state)}
    check(restored["metrics"][0]["assumptions"]["other_acquisition_costs"] == restored["assumption_snap"]["other_costs"] == restored["roi_pool"]["other_costs"] == 2000,
          f"[{review_lang}] row, assumption_snap and roi_pool all restore fees 2000")
    check([m["id"] for m in restored["metrics"]] == [m["id"] for m in old_state["metrics"]] and restored["params"] == old_state["params"],
          f"[{review_lang}] prior IDs and filters unchanged")
    notice = restored.get("turn_notice") or ""
    check(("扩容上限" in notice and "池外可能还有" in notice) if review_lang == "zh" else
          ("expansion cap reached" in notice and "properties outside it may still match" in notice),
          f"[{review_lang}] capped failed refinement discloses outside-pool possibility")
legacy_snap = RS["snapshot"]({"params": {}, "metrics": []})
check(legacy_snap["roi_pool"] is None and legacy_snap["assumption_snap"] is None, "missing metadata remains None; no invented fee default")

print("FAILED: %d" % len(failures) if failures else "ALL PASSED")
sys.exit(1 if failures else 0)
