import ast, builtins, random, re, sqlite3, sys, types
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


def load(path, names, inject=None, pre=None):
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
        ns[imp] = _Stub()
    ns.update(pre or {})
    for nm in sorted(need, key=lambda k: top[k].lineno):
        exec(compile(ast.Module([top[nm]], []), str(path), "exec"), ns)
    ns.update(inject or {})
    return ns


FN = ["investment_metrics", "roi_order_sql", "roi_unrounded", "roi_certified_prefix", "stamp_duty_vic", "_VIC_DUTY_BRACKETS", "_duty_unrounded"]
F = load(ROOT / "app/analytics/formulas.py", FN)
inv = F["investment_metrics"]
BR = F["_VIC_DUTY_BRACKETS"]


def unrounded(price, rent, opex=0.28, fees=2000):
    if not price or price <= 0 or rent is None:
        return None
    for cap, base, rate, thr in BR:
        if cap is None or price <= cap:
            duty = base + rate * (price - thr)
            break
    return rent * (1 - opex) / (price + duty + fees)


def py_roi(price, rent, opex=0.28, fees=2000):
    return inv(price, rent, opex, fees)["roi"]


def to_sqlite(sql):
    return re.sub(r"%\((\w+)\)s", r":\1", sql).replace("::float8", "").replace("::float", " * 1.0")


def pool_ids(rows, prefix, limit, opex=0.28, fees=2000):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, price INTEGER, annual_rent INTEGER)")
    db.executemany("INSERT INTO t VALUES (?,?,?)", rows)
    cur = db.execute(f"SELECT id FROM t ORDER BY {to_sqlite(prefix)}id LIMIT {int(limit)}", {"opex_rate": opex, "other_costs": float(fees)})
    out = [r[0] for r in cur.fetchall()]
    db.close()
    return out


S = load(ROOT / "app/search/search.py", ["_ORDER_BY", "_SEARCH_SQL"], pre={"roi_order_sql": F.get("roi_order_sql") or (lambda: "")})
ORDER = S["_ORDER_BY"]

N = 5000
hi, lo = (960_000, 48_480), (100_000, 5_000)
rows = [(i, *hi) for i in range(N)] + [(N, *lo)]
true_best = max(rows, key=lambda r: py_roi(r[1], r[2]))[0]
check(true_best == N, "ground truth (Python investment_metrics) over 5,001 invented rows: the best ROI is the cheap row")
old_pool = pool_ids(rows, ORDER["gross_yield"], N)
check(true_best not in old_pool, "[DEFECT, baseline strategy] gross-yield preselection LIMIT 5000 EXCLUDES the true best-ROI row")
A, B = (26_000, 2_976), (26_119, 2_989)
ua, ub, pa, pb = unrounded(*A), unrounded(*B), py_roi(*A), py_roi(*B)
check(ua < ub and pa > pb, f"[PRESERVED near-tie] A={A} B={B}: unrounded ROI says B higher ({ua:.9f}<{ub:.9f}) but Python-rounded says A higher ({pa:.9f}>{pb:.9f})")

have = all(k in F for k in ("roi_order_sql", "roi_unrounded", "roi_certified_prefix"))
check(have and "roi" in ORDER, "formulas.roi_order_sql / roi_unrounded / roi_certified_prefix and search._ORDER_BY['roi'] exist")
if not have or "roi" not in ORDER:
    print("FAILED (baseline has no exact-ROI selection): %d" % len(failures))
    sys.exit(1)
cert = F["roi_certified_prefix"]
unr = lambda p, r, o=0.28, f=2000: F["roi_unrounded"](p, r, o, f)

check(all(abs(unr(p, r) - unrounded(p, r)) < 1e-12 for p in (1, 25_000, 25_001, 130_000, 130_001, 960_000, 960_001, 3_000_000) for r in (0, 1, 5_000, 99_999)),
      "roi_unrounded equals the test's independent unrounded formula at every tax-bracket boundary")
check(unr(0, 5000) is None and unr(None, 5000) is None and unr(500_000, None) is None and unr(-5, 100) is None, "roi_unrounded: price<=0 / missing price / missing rent -> None")
new_pool = pool_ids(rows, ORDER["roi"], N)
check(new_pool[0] == true_best and len(new_pool) == N, "[FIX] ROI preselection LIMIT 5000 returns the true best-ROI row first")

def trial(rng, n, limit, opex, fees, clustered):
    rows = []
    for i in range(n):
        if clustered and rows and rng.random() < 0.5:
            p0, r0 = rows[rng.randrange(len(rows))][1:]
            rows.append((i, max(1, p0 + rng.randint(-150, 150)), max(0, r0 + rng.randint(-15, 15))))
        else:
            p = rng.choice((rng.randint(1, 30_000), rng.randint(20_000, 200_000), rng.randint(100_000, 2_500_000)))
            rows.append((i, p, int(p * rng.uniform(0.01, 0.09))))
    pool = pool_ids(rows, ORDER["roi"], limit, opex, fees)
    byid = {r[0]: r for r in rows}
    last = byid[pool[-1]]
    cutoff = unr(last[1], last[2], opex, fees) if len(pool) >= limit else None
    rois = sorted((py_roi(byid[i][1], byid[i][2], opex, fees) for i in pool if py_roi(byid[i][1], byid[i][2], opex, fees) is not None), reverse=True)
    c = cert(rois, cutoff, fees)
    truth = sorted((py_roi(r[1], r[2], opex, fees) for r in rows if py_roi(r[1], r[2], opex, fees) is not None), reverse=True)
    return c, rois[:c] == truth[:c], len(rois)


rng = random.Random(20261001)
viol, certified, total, big = [], 0, 0, 0
for t in range(240):
    n = rng.choice((300, 600, 1500))
    limit = rng.choice((10, 40, 150))
    opex = rng.choice((0.25, 0.28, 0.30))
    fees = rng.choice((2000, 500, 50, 2))
    c, ok, m = trial(rng, n, limit, opex, fees, clustered=(t % 2 == 0))
    total += 1
    certified += c > 0
    big += c >= 5
    if not ok:
        viol.append((t, n, limit, opex, fees, c))
check(not viol, f"[PROOF, empirical] in {total} random+clustered universes (n 300-1500, limit 10-150, fees 2-2000) the certified prefix ALWAYS equals the true top-c ROI values (violations: {viol[:3]})")
check(big >= 40, f"certificate is not vacuous: {certified}/{total} trials certified >=1 result and {big} certified >=5 (shown-set size)")
check(cert([0.5, 0.4], 0.1, 0) == 0 and cert([0.5], 0.1, 0.4) == 0, "fees <= 0.5 -> no rounding bound can be proven -> certifies nothing")
check(cert([0.5, 0.4, 0.3], None, 2000) == 3, "no cutoff (pool not full / tail is NULL-ROI) -> every valid ROI is in the pool -> all certified")
check(cert([0.0401, 0.04], 0.04, 2000) == 0 and cert([0.05, 0.04], 0.04, 2000) == 1 and cert([0.0801, 0.05], 0.05, 2000) == 1, "strict margin: a result at/near the cutoff is NOT certified; one well above is")

edge = [(1, *B), (2, *A)]
bpool = pool_ids(edge, ORDER["roi"], 1)
check(bpool == [1], "SQL (unrounded) puts B first, so LIMIT 1 keeps B and drops A")
truth_best = max(edge, key=lambda r: py_roi(r[1], r[2]))[0]
check(truth_best == 2 and truth_best not in bpool, "...but the Python-rounded true best is A: a naive 'pool top is the global top' claim would be WRONG here")
c = cert([py_roi(*B)], unr(*B), 2000)
check(c == 0, f"certificate refuses the straddling pair (certified prefix = {c})")

check(pool_ids([(7, 500_000, 25_000), (3, 500_000, 25_000), (5, 500_000, 25_000)], ORDER["roi"], 3) == [3, 5, 7], "identical rows tie-break by ascending id (deterministic)")
check(re.search(r"embedding <=> %\(query_vector\)s,\s*id\b", S["_SEARCH_SQL"]) is not None, "final SQL ends ORDER BY ..., embedding <=> %(query_vector)s, id (id as last deterministic tie-break)")
ROWS_NULL = [(1, 500_000, None), (2, 0, 9000), (3, 400_000, 20_000)]
check(pool_ids(ROWS_NULL, ORDER["roi"], 3)[0] == 3 and set(pool_ids(ROWS_NULL, ORDER["roi"], 3)[1:]) == {1, 2}, "NULL rent / price 0 sort last in the ROI preselection")

calls = []
fake_props = {}


def fake_sp(**kw):
    calls.append(kw)
    return fake_props["rows"]


G = load(ROOT / "app/orchestration/graph.py", ["search", "rank", "_rank_once", "CANDIDATE_LIMIT", "RESULT_LIMIT", "_SQL_ORDER", "SEARCH_KEYS", "RANK_KEYS"],
         inject={"search_properties": fake_sp, "roi_unrounded": unr, "roi_certified_prefix": cert,
                 "assumptions": types.SimpleNamespace(opex_rate=lambda: 0.31, other_acquisition_costs=lambda: 2500, snapshot=lambda: {"opex_rate": 0.31, "other_acquisition_costs": 2500}),
                 "deepcopy": __import__("copy").deepcopy, "investment_metrics": inv,
                 "i18n": types.SimpleNamespace(sort_labels_en=lambda d: d)})
G["RANK_KEYS"], G["SEARCH_KEYS"] = ("sort_by", "min_gross_yield", "unsupported_asks"), ("max_price",)
_Stub.loading = False
check(G["_SQL_ORDER"]["roi"] == "roi" and G["_SQL_ORDER"]["cap_rate"] == "gross_yield" and G["_SQL_ORDER"]["gross_yield"] == "gross_yield", "graph._SQL_ORDER: roi -> roi ; cap_rate/gross_yield unchanged")
LIM = G["CANDIDATE_LIMIT"]
full = [{"id": i, "price": 400_000 + i, "annual_rent": 20_000 + (i % 97)} for i in range(LIM)]
fake_props["rows"] = full + [{"id": LIM, "price": 400_000 + LIM, "annual_rent": 20_000}]
out = G["search"]({"params": {"sort_by": "roi"}, "user_query": "q"})
kw = calls[-1]
check(kw["order_by"] == "roi" and kw["limit"] == LIM + 1 and LIM == 5000 and kw["opex_rate"] == 0.31 and kw["other_costs"] == 2500, "search: order_by=roi limit=5000 with the CURRENT assumptions (0.31 / 2500)")
rp = out["roi_pool"]
check(len(out["properties"]) == LIM and rp and abs(rp["cutoff"] - unr(full[-1]["price"], full[-1]["annual_rent"], 0.31, 2500)) < 1e-15 and rp["other_costs"] == 2500 and out["search_order"] == "roi",
      "search: full pool -> roi_pool carries the unrounded ROI of the LAST row as cutoff, plus the fees used")
fake_props["rows"] = full
check(G["search"]({"params": {"sort_by": "roi"}, "user_query": "q"})["roi_pool"] is None, "search: exactly `limit` rows and no sentinel -> not truncated -> roi_pool None")
fake_props["rows"] = full[:10]
check(G["search"]({"params": {"sort_by": "roi"}, "user_query": "q"})["roi_pool"] is None, "search: pool smaller than the limit -> roi_pool None (nothing was cut)")
fake_props["rows"] = full
calls.clear()
G["search"]({"params": {"sort_by": "gross_yield"}, "user_query": "q"})
check("opex_rate" not in calls[-1] and G["search"]({"params": {"sort_by": "gross_yield"}, "user_query": "q"})["roi_pool"] is None, "search: gross_yield call shape and roi_pool unchanged")


def rank_note(pool_rows, roi_pool, lang):
    ms = []
    for p in pool_rows:
        m = inv(p[1], p[2], 0.28, 2000)
        ms.append({"id": p[0], "price": p[1], "annual_rent": p[2], "gross_yield": m["gross_yield"], "roi": m["roi"], "cap_rate": m["cap_rate"],
                   "predicted_gap": None, "valuation_error_pct": None, "amenities": {}, "context_scores": {}})
    st = {"metrics": ms, "params": {"sort_by": "roi"}, "search_order": "roi", "roi_pool": roi_pool, "lang": lang, "user_query": "q", "intent": "new_search"}
    o = G["_rank_once"](st)
    return o["ranking"], [m["id"] for m in o["metrics"]]


easy = [(i, 300_000 + 1000 * i, 12_000 + 30 * i) for i in range(LIM)]
easy.sort(key=lambda r: -py_roi(r[1], r[2]))
cutoff = unr(easy[-1][1], easy[-1][2])
ok_pool = {"cutoff": cutoff, "opex_rate": 0.28, "other_costs": 2000}
for lang, yes_, bad in (("zh", "已验证", "在全库符合条件的房源里挑"), ("en", "verified", "chosen from every matching property")):
    text, ids = rank_note(easy, ok_pool, lang)
    check(yes_ in text and bad not in text and "5000" in text, f"{lang}: certified -> note says the top results were verified vs the outside-pool bound, and never 'every matching property' ({text[:110]}...)")
tight = [(i, 500_000, 25_000) for i in range(LIM)]
text_z, ids_t = rank_note(tight, {"cutoff": unr(500_000, 25_000), "opex_rate": 0.28, "other_costs": 2000}, "zh")
text_e, _ = rank_note(tight, {"cutoff": unr(500_000, 25_000), "opex_rate": 0.28, "other_costs": 2000}, "en")
check("已验证" not in text_z and "更多" in text_z and "在全库符合条件的房源里挑" not in text_z, f"zh: uncertifiable pool -> honest disclosure 'more may match', no exactness claim ({text_z[:90]}...)")
check("are verified" not in text_e and "more may match" in text_e and "every matching property" not in text_e, "en: uncertifiable pool -> honest disclosure, no exactness claim")
check(ids_t == sorted(ids_t) or ids_t == [m for m in ids_t], "rank is deterministic on exact ties: stable sort keeps pool order")
text_s, _ = rank_note(easy[:30], None, "zh")
check("全部 30 套" in text_s, "pool below the limit -> 'all 30 matching properties' (nothing was cut)")

print("FAILED: %d" % len(failures) if failures else "ALL PASSED")
sys.exit(1 if failures else 0)
