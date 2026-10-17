import ast
import builtins
import copy
import json
import operator
import re
import sys
import types
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
GRAPH = ROOT / "app/orchestration/graph.py"
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


def load(path, names, pre=None, post=None):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    top, imported = {}, set()
    for n in tree.body:
        if isinstance(n, ast.FunctionDef):
            top.setdefault(n.name, []).append(n)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if isinstance(t, ast.Name):
                    top.setdefault(t.id, []).append(n)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            imported |= {(a.asname or a.name).split(".")[0] for a in n.names}
    need, todo = [], [x for x in names if x in top]
    while todo:
        nm = todo.pop()
        if nm in need or nm not in top:
            continue
        need.append(nm)
        for node in top[nm]:
            local = {x.arg for x in ast.walk(node) if isinstance(x, ast.arg)} | {x.id for x in ast.walk(node) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
            todo += [x.id for x in ast.walk(node) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load) and x.id in top and x.id not in local or
                     (isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load) and x.id in top and x.id == nm)]
    ns = {"__builtins__": builtins, "State": dict, "re": re, "json": json, "operator": operator, "deepcopy": copy.deepcopy}
    for imp in imported:
        ns.setdefault(imp, _Stub())
    ns.update(pre or {})
    nodes = sorted((n for nm in need for n in top[nm]), key=lambda n: n.lineno)
    for n in nodes:
        exec(compile(ast.Module([n], []), str(path), "exec"), ns)
    ns.update(post or {})
    return ns


PRE = {"context": types.SimpleNamespace(ATTRIBUTE_KEYS=("quiet", "lively")), "nearby": types.SimpleNamespace(KINDS=("train_station",)),
       "zones": types.SimpleNamespace(LEVELS=("primary", "secondary")), "refinement": types.SimpleNamespace(valid_goals=lambda x: [], baseline_for=lambda m, f: None),
       "i18n": types.SimpleNamespace(UNSUPPORTED_KEY_EN={"采光": "natural light"}, sort_labels_en=lambda d: d)}
CAPTURE = {}
ASSUME = {"opex_rate": 0.28, "other_acquisition_costs": 2000}
FAKE_ASSUMPTIONS = types.SimpleNamespace(snapshot=lambda: dict(ASSUME), describe=lambda: ["opex 28%, fees 2000"], opex_rate=lambda: 0.28, other_acquisition_costs=lambda: 2000)


def fake_ask(system, user, temperature=0.0):
    CAPTURE["system"], CAPTURE["user"] = system, user
    if CAPTURE.get("raise"):
        raise RuntimeError("boom")
    return "SIMULATED MODEL ANSWER"


_Stub.loading = True
G = load(GRAPH, ["explain", "_assumption_lines"], pre=PRE,
         post={"_ask_streaming": fake_ask, "assumptions": FAKE_ASSUMPTIONS, "refinement": PRE["refinement"], "i18n": PRE["i18n"],
               "_typo_source": lambda s, q: None, "answer_guard": __import__("types").SimpleNamespace(**__import__("runpy").run_path(str(__import__("pathlib").Path(__file__).resolve().parents[1] / "app/orchestration/answer_guard.py"))), "_dataset_suburbs": lambda: []})
_Stub.loading = False

def run_explain(metrics, query, intent="new_search", lang="zh", raise_llm=False):
    CAPTURE.clear()
    CAPTURE["raise"] = raise_llm
    st = {"metrics": metrics, "intent": intent, "user_query": query, "lang": lang, "params": {}, "ranking": "按相关度排序", "batch_offset": 0}
    out = G["explain"](st)
    return st, out


def mk(i, car):
    return {"id": i, "price": 500_000 + i, "suburb": "S", "car_spaces": car, "assumptions": dict(ASSUME)}


metrics = [mk(1, 0), mk(2, None), mk(3, 3)]
for lang in ("zh", "en"):
    for intent in ("new_search", "refine", "about_results"):
        before = copy.deepcopy(metrics)
        st, result = run_explain(metrics, "compare property values", intent=intent, lang=lang)
        facts = json.loads(CAPTURE["user"].split("已算好的房源数据:", 1)[1].strip())
        label = f"[{lang}/{intent}]"
        check(all("car_spaces" not in f for f in facts), label + " no car_spaces in explanation facts")
        check([f["display_no"] for f in facts] == [1, 2, 3] and [f["price"] for f in facts] == [500001, 500002, 500003], label + " numbers and display IDs intact")
        check(metrics == before and st["metrics"] is metrics, label + " original metrics and model data unchanged")
CAPTURE.clear()
state = {"metrics": metrics, "intent": "about_results", "user_query": "compare property values", "lang": "en", "params": {}, "ranking": "", "batch_offset": 5}
G["explain"](state)
facts = json.loads(CAPTURE["user"].split("已算好的房源数据:", 1)[1].strip())
check([f["display_no"] for f in facts] == [6, 7, 8], "pagination display IDs stay correct")
st, result = run_explain(metrics, "compare values", raise_llm=True)
check("说明生成失败" in result["answer"] and metrics == [mk(1,0), mk(2,None), mk(3,3)], "LLM outage preserves metrics and fallback")
seen_rows = []
def fake_predict(rows):
    seen_rows.extend(rows)
    return [{"predicted_price":1,"is_stub":False,"typical_error_pct":0.1,"range_low":1,"range_high":1,"interval_level":None,"interval_low":None,"interval_high":None,"interval_coverage":None,"cross_fitted":True} for _ in rows]
_Stub.loading = True
GA = load(GRAPH, ["analyze"], pre=PRE, post={"predict_values":fake_predict,"investment_metrics":lambda *a:{k:None for k in ("gross_yield","operating_expenses","noi","cap_rate","roi","stamp_duty","total_cost")},"assumptions":FAKE_ASSUMPTIONS,"_rent_sources":lambda ids:{},"nearby":types.SimpleNamespace(distance_to_cbd_km=lambda a,b,fb:fb)})
_Stub.loading = False
props = [{"id":i,"suburb":"S","address":f"{i} A St","property_type":"house","price":500000,"bedrooms":3,"bathrooms":1,"car_spaces":c,"land_size":None,"building_area":None,"distance_cbd":5.0,"latitude":-37.8,"longitude":145.0,"annual_rent":20000} for i,c in ((1,0),(2,None),(3,3))]
res = GA["analyze"]({"properties":props,"params":{}})
check([r.get("car_spaces","MISSING") for r in seen_rows]==[0,None,3], "valuation model receives original 0 / None / 3 feature")
check(seen_rows == [{k:p.get(k) for k in ("suburb","address","property_type","bedrooms","bathrooms","car_spaces","land_size","building_area","distance_cbd","latitude","longitude","price")} for p in props], "all model features retain original values")
check([m["car_spaces"] for m in res["metrics"]]==[0,None,3], "metrics/API retain original car_spaces")
check('"停车控制": "Parking"' in (ROOT/"app/i18n.py").read_text(encoding="utf-8"), "planning Parking translation retained")
print(f"RESULT {24-len(failures)} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)
