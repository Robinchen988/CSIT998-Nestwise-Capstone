import ast
import builtins
import sys
import types
from pathlib import Path

GRAPH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "app/orchestration/graph.py"
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
        raise AssertionError("rank unexpectedly CALLED a stubbed import")


tree = ast.parse(GRAPH.read_text(encoding="utf-8"))
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
need, todo = [], ["rank", "RESULT_LIMIT", "CANDIDATE_LIMIT", "_SQL_ORDER"]
while todo:
    nm = todo.pop()
    if nm in need or nm not in top:
        continue
    need.append(nm)
    node = top[nm]
    local = {x.arg for x in ast.walk(node) if isinstance(x, ast.arg)} | {x.id for x in ast.walk(node) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
    todo += [x.id for x in ast.walk(node) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load) and x.id in top and x.id not in local]
ns = {"__builtins__": builtins, "State": dict}
for imp in imported:
    ns[imp] = _Stub()
for nm in sorted(need, key=lambda k: top[k].lineno):
    exec(compile(ast.Module([top[nm]], []), str(GRAPH), "exec"), ns)
ns["i18n"] = types.SimpleNamespace(sort_labels_en=lambda d: d)
ns.update({"answer_guard": __import__("types").SimpleNamespace(**__import__("runpy").run_path(str(__import__("pathlib").Path(__file__).resolve().parents[1] / "app/orchestration/answer_guard.py"))), "_dataset_suburbs": lambda: []})
_Stub.loading = False
rank, LIMIT = ns["rank"], ns["RESULT_LIMIT"]


def m(pid, price, gy=None, gap=None, err=0.09):
    return {"id": pid, "price": price, "gross_yield": gy, "cap_rate": None, "roi": None, "predicted_gap": gap,
            "valuation_error_pct": err, "amenities": {}, "context_scores": {}}


def run(metrics, sort_by=None, floor=None, lang="zh"):
    params = {"sort_by": sort_by}
    if floor is not None:
        params["min_gross_yield"] = floor
    out = rank({"metrics": metrics, "params": params, "lang": lang, "user_query": "q", "intent": "new_search"})
    return [x["id"] for x in out["metrics"]], out


ids, out = run([m(1, 0), m(2, 300_000), m(3, 500_000)], "price_asc")
check(ids[:2] == [2, 3] and 1 not in ids[:2], f"[DEFECT] price_asc must not rank a price-0 record as the cheapest (got order {ids})")
ids, _ = run([m(1, 0), m(2, 300_000), m(3, 500_000)], "price_desc")
check(ids[0] == 3, f"[BOUNDARY] price_desc puts the dearest first (got {ids})")
ids, _ = run([m(1, None), m(2, 300_000)], "price_asc")
check(ids == [2], f"[BOUNDARY] missing price is excluded from a price sort, not treated as 0 (got {ids})")

pool = [m(1, 500_000, gy=0.040), m(2, 500_000, gy=0.0399), m(3, 500_000, gy=None), m(4, 500_000, gy=0.05)]
ids, out = run(pool, "gross_yield", floor=0.04)
check(ids == [4, 1], f"[BOUNDARY] floor is inclusive (4.0% kept), below dropped, missing yield dropped, sorted desc (got {ids})")
ids, out = run(pool, "gross_yield", floor=0.06)
check(ids == [] and ("没有候选达到" in out["ranking"]), f"[BOUNDARY] nobody meets the floor -> empty result + explicit note (got {ids}, '{out['ranking'][:40]}')")
ids, out = run([m(1, 500_000, gy=0.0)], "gross_yield", floor=0.0)
check(ids == [1], "[BOUNDARY] floor 0.0 is a real floor (not 'no floor') and keeps a 0.0 yield")

sus = lambda pid, gy: m(pid, 100_000, gy=gy, gap=0.50)
ok_ = lambda pid, gy: m(pid, 600_000, gy=gy, gap=0.0)
ids, out = run([sus(1, 0.30), ok_(2, 0.05), ok_(3, 0.04)], "gross_yield")
check(ids == [2, 3] and ("已剔除 1 套" in out["ranking"]), f"[BOUNDARY] some suspect -> dropped with a count note (got {ids})")
ids, out = run([sus(1, 0.30), sus(2, 0.25)], "gross_yield")
check(ids == [1, 2], f"[BOUNDARY] if EVERY candidate is suspect none are dropped (nothing left to show) (got {ids})")
check("远低于模型估值" in out["ranking"] and ("虚高" in out["ranking"] or "未剔除" in out["ranking"]),
      f"[DEFECT] ...but the user must be told the yields may be inflated (got note: '{out['ranking']}')")
ids, out = run([sus(1, 0.30), sus(2, 0.25)], "gross_yield", lang="en")
check("far below the model estimate" in out["ranking"] and ("inflated" in out["ranking"] or "not dropped" in out["ranking"]),
      f"[DEFECT] same note in English (got: '{out['ranking']}')")

many = [m(i, 100_000 + i) for i in range(60)]
_, out = run(many, "price_asc")
check(len(out["metrics"]) == LIMIT == 5 and len(out["more"]) == 20 and [x["id"] for x in out["more"][:5]] == [x["id"] for x in out["metrics"]],
      "[BOUNDARY] 60 candidates -> 5 shown, 20 kept for 'next batch', shown set is the prefix of it")
_, out = run([m(i, 100_000 + i) for i in range(3)], "price_asc")
check(len(out["metrics"]) == 3 and len(out["more"]) == 3, "[BOUNDARY] fewer than 5 candidates -> all shown, nothing invented")
ids, out = run([m(1, 500_000, gy=None), m(2, 600_000, gy=None)], "gross_yield")
check(ids == [1, 2] and ("没有一套能算出" in out["ranking"]), f"[BOUNDARY] sort key missing everywhere -> relevance order kept + explicit note (got {ids})")

print("FAILED: %d" % len(failures) if failures else "ALL PASSED")
sys.exit(1 if failures else 0)
