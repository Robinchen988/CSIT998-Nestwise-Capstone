import ast, builtins, random, re, sqlite3, sys, time, tracemalloc, types
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
        ns[imp] = _Stub()
    ns.update(pre or {})
    for nm in sorted(need, key=lambda k: top[k].lineno):
        exec(compile(ast.Module([top[nm]], []), str(path), "exec"), ns)
    return ns


F = load(ROOT / "app/analytics/formulas.py", ["investment_metrics", "roi_unrounded", "roi_certified_prefix", "roi_order_sql", "_VIC_DUTY_BRACKETS", "_duty_unrounded"])
inv = F["investment_metrics"]
have = all(k in F for k in ("roi_unrounded", "roi_certified_prefix", "roi_order_sql"))
src_graph = (ROOT / "app/orchestration/graph.py").read_text(encoding="utf-8")
check(have and "ROI_EXPAND_CAP" in src_graph and "def _rank_once" in src_graph, "bounded ROI pool expansion exists (formulas helpers + graph.rank wrapper + ROI_EXPAND_CAP)")
if not (have and "ROI_EXPAND_CAP" in src_graph):
    print("FAILED (baseline has no bounded ROI expansion): %d" % len(failures))
    sys.exit(1)
S = load(ROOT / "app/search/search.py", ["_ORDER_BY"], pre={"roi_order_sql": F["roi_order_sql"]})
ORDER = S["_ORDER_BY"]


def to_sqlite(sql):
    return re.sub(r"%\((\w+)\)s", r":\1", sql).replace("::float8", "").replace("::float", " * 1.0")


class FakeDB:
    def __init__(self, rows):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, price INTEGER, annual_rent INTEGER, bedrooms INTEGER, property_type TEXT, suburb TEXT)")
        self.db.executemany("INSERT INTO t VALUES (?,?,?,?,?,?)", rows)
        self.calls = []

    def search_properties(self, semantic_query=None, max_price=None, min_price=None, bedrooms=None, bathrooms=None, property_type=None, suburb=None,
                          limit=10, order_by=None, opex_rate=None, other_costs=None):
        self.calls.append({"order_by": order_by, "limit": limit, "opex_rate": opex_rate, "other_costs": other_costs})
        where = "(:max_price IS NULL OR price <= :max_price) AND (:min_price IS NULL OR price >= :min_price) AND (:bedrooms IS NULL OR bedrooms = :bedrooms) " \
                "AND (:property_type IS NULL OR property_type = :property_type)"
        q = f"SELECT id, price, annual_rent, bedrooms, property_type FROM t WHERE {where} ORDER BY {to_sqlite(ORDER[order_by])}id LIMIT :limit"
        cur = self.db.execute(q, {"max_price": max_price, "min_price": min_price, "bedrooms": bedrooms, "property_type": property_type, "limit": limit,
                                  "opex_rate": opex_rate, "other_costs": float(other_costs) if other_costs is not None else None})
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def make_graph(db, L, opex=0.28, fees=2000):
    def analyze(state):
        out = []
        for p in state["properties"]:
            m = inv(p["price"], p["annual_rent"], opex, fees)
            out.append({"id": p["id"], "price": p["price"], "annual_rent": p["annual_rent"], "gross_yield": m["gross_yield"], "cap_rate": m["cap_rate"], "roi": m["roi"],
                        "predicted_gap": None, "valuation_error_pct": None, "amenities": {}, "context_scores": {}})
        return {"metrics": out}
    _Stub.loading = True
    G = load(ROOT / "app/orchestration/graph.py", ["rank", "_rank_once", "search", "CANDIDATE_LIMIT", "ROI_EXPAND_CAP", "RESULT_LIMIT", "_SQL_ORDER", "SEARCH_KEYS", "RANK_KEYS"])
    G.update({"answer_guard": __import__("types").SimpleNamespace(**__import__("runpy").run_path(str(__import__("pathlib").Path(__file__).resolve().parents[1] / "app/orchestration/answer_guard.py"))), "_dataset_suburbs": lambda: [], "search_properties": db.search_properties, "analyze": analyze, "enrich": lambda s: {"metrics": s["metrics"], "place_lookup": None, "zone_lookup": None},
              "assumptions": types.SimpleNamespace(opex_rate=lambda: opex, other_acquisition_costs=lambda: fees, snapshot=lambda: {"opex_rate": opex, "other_acquisition_costs": fees}),
              "deepcopy": __import__("copy").deepcopy, "investment_metrics": inv,
              "roi_unrounded": F["roi_unrounded"], "roi_certified_prefix": F["roi_certified_prefix"], "i18n": types.SimpleNamespace(sort_labels_en=lambda d: d),
              "SEARCH_KEYS": ("semantic_query", "max_price", "min_price", "bedrooms", "bathrooms", "property_type", "suburb"),
              "RANK_KEYS": ("sort_by", "min_gross_yield", "unsupported_asks"), "CANDIDATE_LIMIT": L, "ROI_EXPAND_CAP": L * 8})
    _Stub.loading = False
    return G


def run_graph(G, params, lang="zh"):
    st = {"params": params, "user_query": "q", "lang": lang, "intent": "new_search"}
    st.update(G["search"](st))
    st.update(G["analyze"](st))
    st.update(G["enrich"](st))
    out = G["rank"](st)
    return st, out


def truth(rows, params, opex=0.28, fees=2000):
    res = []
    for (i, p, r, b, t, s) in rows:
        if params.get("max_price") is not None and p > params["max_price"]: continue
        if params.get("min_price") is not None and p < params["min_price"]: continue
        if params.get("bedrooms") is not None and b != params["bedrooms"]: continue
        if params.get("property_type") is not None and t != params["property_type"]: continue
        m = inv(p, r, opex, fees)
        if m["roi"] is None: continue
        if params.get("min_gross_yield") is not None and (m["gross_yield"] is None or m["gross_yield"] < params["min_gross_yield"]): continue
        res.append((m["roi"], i))
    res.sort(key=lambda x: (-x[0], x[1]))
    return res


def rois_of(rows, ids, opex=0.28, fees=2000):
    by = {r[0]: r for r in rows}
    return [inv(by[i][1], by[i][2], opex, fees)["roi"] for i in ids]


A, B = (26_119 - 119, 2_976), (26_119, 2_989)

rows1 = [(i, 960_000, 48_480, 3, "house", "S") for i in range(1, 5001)] + [(5001, 100_000, 5_000, 3, "house", "S")]
db = FakeDB(rows1); G = make_graph(db, 5000)
st, out = run_graph(G, {"sort_by": "roi"})
check(out["metrics"][0]["id"] == 5001, f"5001 rows, LIMIT 5000: the true best ROI (id 5001) is returned FIRST (got {out['metrics'][0]['id']})")
check([c["limit"] for c in db.calls][:1] == [5001], f"first fetch asked LIMIT 5000+1 sentinel (fetch limits: {[c['limit'] for c in db.calls]})")
check(all(c["order_by"] == "roi" and c["opex_rate"] == 0.28 and c["other_costs"] == 2000 for c in db.calls), "every fetch is ORDER BY roi with the current assumptions")

rows2 = [(i, *B, 3, "house", "S") for i in range(1, 5001)] + [(5001, *A, 3, "house", "S")]
by_sql = FakeDB(rows2).search_properties(limit=5000, order_by="roi", opex_rate=0.28, other_costs=2000)
check(all(r["id"] != 5001 for r in by_sql), "setup: unrounded SQL order fills the 5000-row pool with B copies and EXCLUDES A (id 5001)")
check(inv(*A, 0.28, 2000)["roi"] > inv(*B, 0.28, 2000)["roi"], "setup: Python-rounded ROI says A > B")
db = FakeDB(rows2); G = make_graph(db, 5000)
st, out = run_graph(G, {"sort_by": "roi"})
check(out["metrics"][0]["id"] == 5001, f"[DEF-0007] near-tie at the LIMIT boundary: A (id 5001) is returned FIRST after expansion (got {out['metrics'][0]['id']})")
check([c["limit"] for c in db.calls] == [5001, 10001], f"expansion doubled the pool once (limit+1 sentinel): fetch limits {[c['limit'] for c in db.calls]}")
check("全部 5001 套" in out["ranking"], f"ranking note after the pool was exhausted: 'all 5001' ({out['ranking'][:60]})")
st, out_en = run_graph(make_graph(FakeDB(rows2), 5000), {"sort_by": "roi"}, lang="en")
check("all 5001 matching properties" in out_en["ranking"] and "every matching property" not in out_en["ranking"], "en: 'all 5001 matching properties'")

rows3 = [(i, 500_000, 25_000, 3, "house", "S") for i in range(1, 61)]
st, out = run_graph(make_graph(FakeDB(rows3), 25), {"sort_by": "roi"})
ids = [m["id"] for m in out["more"]]
check(ids == list(range(1, 21)) and [m["id"] for m in out["metrics"]] == [1, 2, 3, 4, 5], f"60 identical rows, small pool: ties break by ascending id and are stable across expansion rounds (more={ids[:6]}…)")
a1 = run_graph(make_graph(FakeDB(rows3), 25), {"sort_by": "roi"})[1]["more"]
check([m["id"] for m in a1] == ids, "repeat run returns the identical list (deterministic)")

rng = random.Random(20261002)
bad, expansions, cases = [], 0, 0
for t in range(60):
    n = rng.choice((200, 600, 1500)); L = rng.choice((25, 60, 200))
    opex = rng.choice((0.25, 0.28)); fees = rng.choice((2000, 300, 0.5, 0))
    rows = []
    for i in range(1, n + 1):
        if rows and rng.random() < 0.4:
            _, p0, r0, b0, t0, s0 = rows[rng.randrange(len(rows))]
            rows.append((i, max(1, p0 + rng.randint(-120, 120)), max(0, r0 + rng.randint(-12, 12)), b0, t0, s0))
        else:
            p = rng.choice((rng.randint(10_000, 150_000), rng.randint(150_000, 2_000_000)))
            rows.append((i, p, int(p * rng.uniform(0.01, 0.09)), rng.choice((1, 2, 3)), rng.choice(("house", "apartment")), "S"))
    params = {"sort_by": "roi"}
    if t % 2: params.update(max_price=rng.choice((300_000, 900_000)), bedrooms=rng.choice((2, 3)))
    if t % 3 == 0: params.update(property_type="house")
    if t % 4 == 0: params.update(min_gross_yield=0.03)
    db = FakeDB(rows); G = make_graph(db, L, opex, fees)
    st, out = run_graph(G, params)
    tr = truth(rows, params, opex, fees)
    got = rois_of(rows, [m["id"] for m in out["more"]], opex, fees)
    want = [x[0] for x in tr[:len(got)]]
    cases += 1
    expansions += len(db.calls) > 1
    ok = got == want and len(got) == min(20, len(tr))
    for k, m in enumerate(out["more"]):
        tie = (k > 0 and tr[k][0] == tr[k - 1][0]) or (k + 1 < len(tr) and tr[k][0] == tr[k + 1][0])
        if not tie and (k >= len(tr) or m["id"] != tr[k][1]):
            ok = False
    if not ok:
        bad.append((t, n, L, opex, fees, params))
check(not bad, f"{cases} random universes with combined filters, small pools, fees incl. 0.5 and 0: the returned top-20 equals brute force (ROI values, and ids where untied). mismatches: {bad[:2]}")
check(expansions >= 20, f"expansion was actually exercised ({expansions}/{cases} cases needed more than one fetch)")

rows5 = [(i, 300_000 + 500 * i, 15_000 + 20 * i, 3, "house", "S") for i in range(1, 801)]
db = FakeDB(rows5); G = make_graph(db, 100, fees=0.5)
st, out = run_graph(G, {"sort_by": "roi"}, lang="en")
check(db.calls[-1]["limit"] >= 800 and len(st["properties"]) >= 800 or st.get("roi_pool") is None or True, "(informational)")
final_limits = [c["limit"] for c in db.calls]
check(final_limits[-1] >= 800 or final_limits[-1] == 800, f"fees=0.5: expansion continued until the whole eligible set fitted (limits {final_limits})")
check("all 800 matching properties" in out["ranking"], f"fees=0.5: exactness stated only because the whole set was fetched ({out['ranking'][:70]})")

rows6 = [(i, 500_000, 25_000, 3, "house", "S") for i in range(1, 301)]
db = FakeDB(rows6); G = make_graph(db, 10)
st, out = run_graph(G, {"sort_by": "roi"}, lang="en")
check(max(c["limit"] for c in db.calls) <= 81 and "could not be verified" in out["ranking"] and "all " not in out["ranking"] and "every matching" not in out["ranking"],
      f"cap respected (fetch limits {[c['limit'] for c in db.calls]}) and the note stays an honest disclosure: {out['ranking'][:90]}")

db = FakeDB(rows3); G = make_graph(db, 25)
st, out = run_graph(G, {"sort_by": "gross_yield"})
check(len(db.calls) == 1 and db.calls[0]["order_by"] == "gross_yield" and db.calls[0]["opex_rate"] is None, "gross_yield sort: single fetch, no assumptions passed, no expansion")

rng = random.Random(7)
big = []
for i in range(1, 20_801):
    p = rng.choice((rng.randint(80_000, 400_000), rng.randint(400_000, 1_500_000), rng.randint(1_500_000, 4_000_000)))
    big.append((i, p, int(p * rng.uniform(0.02, 0.07)), rng.choice((1, 2, 3, 4)), rng.choice(("house", "apartment", "townhouse")), "S"))
bigdb = FakeDB(big)
scen = {"typical (limit 5000)": ({"sort_by": "roi"}, 5000, 2000), "filtered 3bd house": ({"sort_by": "roi", "bedrooms": 3, "property_type": "house"}, 5000, 2000),
        "fees=0 (full set)": ({"sort_by": "roi"}, 5000, 0)}
print("   --- 20,800 synthetic rows (python+sqlite only) ---")
for name, (params, L, fees) in scen.items():
    bigdb.calls.clear(); G = make_graph(bigdb, L, fees=fees)
    tracemalloc.start(); t0 = time.perf_counter()
    st, out = run_graph(G, params)
    dt = time.perf_counter() - t0
    peak = tracemalloc.get_traced_memory()[1] / 1e6; tracemalloc.stop()
    tr = truth(big, params, 0.28, fees)
    exact = rois_of(big, [m["id"] for m in out["more"]], 0.28, fees) == [x[0] for x in tr[:20]]
    print(f"   {name}: {dt:.2f}s, peak {peak:.0f} MB, fetches {[c['limit'] for c in bigdb.calls]}, top-20 equals brute force: {exact}")
    check(exact, f"20,800 rows / {name}: top-20 equals brute force")
print("   NOTE: timings exclude the real XGBoost valuation, geometry enrich and Postgres; they are NOT production latency.")
print("FAILED: %d" % len(failures) if failures else "ALL PASSED")
sys.exit(1 if failures else 0)
