import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.orchestration import graph as g

checks = 0


def run(needs, dists):
    metrics = [{"id": i, "price": 500_000 + i, "gross_yield": 0.04, "planning": {},
                "amenities": {"train_station": {"distance_m": d}}} for i, d in enumerate(dists)]
    state = {"params": {**{k: None for k in g.PARAM_KEYS}, "amenity_needs": needs},
             "metrics": metrics, "lang": "zh", "user_query": "q"}
    return g.rank(state)


dists = [3000, 400, 2500, 900, 5000, 700, 1200]
out = run([{"kind": "train_station", "max_distance_m": 1500, "strength": "preferred"}], dists)
order = [m["amenities"]["train_station"]["distance_m"] for m in out["more"]]
assert order == [400, 900, 700, 1200, 3000, 2500, 5000], order
assert "优先距火车站 1500 米内" in out["ranking"] and "展示的 5 套中 4 套满足" in out["ranking"], out["ranking"]
checks += 2
out = run([{"kind": "train_station", "max_distance_m": 1500}], dists)
assert len(out["more"]) == len(dists)
checks += 1
out = run([{"kind": "train_station", "max_distance_m": 1000, "strength": "required"}], dists)
assert sorted(m["amenities"]["train_station"]["distance_m"] for m in out["more"]) == [400, 700, 900]
assert "要求距火车站 1000 米内" in out["ranking"]
checks += 2
san = g._sanitize({"intent": "new_search", "amenity_needs": [
    {"kind": "train_station", "max_distance_m": 800, "strength": "required"},
    {"kind": "primary_school", "max_distance_m": 1500, "strength": "必须"},
    {"kind": "park"}]}, "q")
assert [n["strength"] for n in san["amenity_needs"]] == ["required", "preferred", "preferred"]
checks += 1

def run_attr(strength, goals=None):
    ms = [{"id": i, "price": 1, "gross_yield": 0.04, "planning": {}, "context_scores": {"quiet": q}}
          for i, q in enumerate([30, 80, 55, 90, 20, 65, 70])]
    need = {"attribute": "quiet", "min_score": 60, "operator": "gte", "strength": strength, "value_source": "inferred"}
    params = {**{k: None for k in g.PARAM_KEYS}, "abstract_needs": [need], "relative_preferences": goals}
    return g.rank({"params": params, "metrics": ms, "lang": "zh", "user_query": "q"})
out = run_attr("preferred")
assert [m["context_scores"]["quiet"] for m in out["more"]] == [80, 90, 65, 70, 30, 55, 20], out["more"]
assert "优先「安静」评分 ≥ 60" in out["ranking"] and "展示的 5 套中 4 套满足" in out["ranking"]
out = run_attr("required")
assert [m["context_scores"]["quiet"] for m in out["more"]] == [80, 90, 65, 70]
assert "已筛出「安静」评分 ≥ 60 的" in out["ranking"]
out = run_attr(None)
assert len(out["more"]) == 7
checks += 5

cases = [
    ({"property_type": "house", "land_size": 433_014, "distance_cbd": 2.5}, (True, False)),
    ({"property_type": "house", "land_size": 146_699, "distance_cbd": 54.6}, (False, False)),
    ({"property_type": "house", "land_size": 732, "building_area": 6791, "distance_cbd": 12}, (False, True)),
    ({"property_type": "house", "land_size": 44_500, "building_area": 44_515, "distance_cbd": 51.8}, (False, True)),
    ({"property_type": "apartment", "land_size": 37_000, "building_area": 80, "distance_cbd": 5}, (True, False)),
    ({"property_type": "apartment", "land_size": 300, "building_area": 1200, "distance_cbd": 3}, (False, False)),
    ({"property_type": "townhouse", "land_size": 250, "building_area": 15, "distance_cbd": 8}, (False, True)),
    ({"property_type": "house", "land_size": 0, "building_area": None, "distance_cbd": 8}, (False, False)),
    ({"property_type": "house", "land_size": 600, "building_area": 250, "distance_cbd": 8}, (False, False)),
]
for m, (land, bld) in cases:
    f = g.area_suspect(m)
    assert (f["land"], f["building"]) == (land, bld), (m, f)
    checks += 1
assert g._explain_fact({"property_type": "house", "land_size": 433_014, "distance_cbd": 2.5})["area_suspect"]["land"] is True
checks += 1

print(f"距离优先/必须 + 面积存疑 全部通过:{checks} 项检查(离线,不调用 LLM)")
