import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analytics import assumptions
from app.analytics.valuation import predict_value, predict_values, warm_up
from app.orchestration.graph import (
    PARAM_KEYS, RESULT_LIMIT, _route, _sanitize, enrich, present, rank,
)


p = _sanitize({}, "随便找找")
assert set(p) == set(PARAM_KEYS)
assert p["semantic_query"] == "随便找找"
assert all(p[k] is None for k in PARAM_KEYS if k != "semantic_query")

assert _sanitize({"max_price": 0}, "x")["max_price"] is None

for query, llm_sort, expected in [
    ("Richmond 附近,售价低于估值的两房", "predicted_gap_neg", "predicted_gap"),
    ("找估值低于售价的房子", "predicted_gap", "predicted_gap_neg"),
    ("被低估的两房", None, "predicted_gap"),
    ("卖贵了的房子", None, "predicted_gap_neg"),
    ("undervalued houses in Richmond", "predicted_gap_neg", "predicted_gap"),
    ("回报最高且被低估的", "gross_yield", "gross_yield"),
    ("高估值的房子", None, None),
]:
    got = _sanitize({"sort_by": llm_sort}, query)["sort_by"]
    assert got == expected, f"«{query}» 期望 {expected},得到 {got}"
assert _sanitize({"max_price": -5}, "x")["max_price"] is None
assert _sanitize({"bedrooms": ""}, "x")["bedrooms"] is None
assert _sanitize({"bedrooms": True}, "x")["bedrooms"] is None
assert _sanitize({"max_price": 800000.0}, "x")["max_price"] == 800000

assert _sanitize({"property_type": "villa"}, "x")["property_type"] is None
assert _sanitize({"property_type": " HOUSE "}, "x")["property_type"] == "house"

p = _sanitize({"min_price": 900000, "max_price": 800000}, "x")
assert p["max_price"] == 800000 and p["min_price"] is None

assert _sanitize({"suburb": "Sydney"}, "x")["suburb"] == "Sydney"

assert _sanitize({"sort_by": "gross_yield"}, "x")["sort_by"] == "gross_yield"
assert _sanitize({"sort_by": "whatever"}, "x")["sort_by"] is None

assert _sanitize({"min_gross_yield": 0.05}, "x")["min_gross_yield"] == 0.05
assert _sanitize({"min_gross_yield": 5}, "x")["min_gross_yield"] == 0.05
assert _sanitize({"min_gross_yield": -1}, "x")["min_gross_yield"] is None


def _m(i, yield_, price, cap=None):
    return {"id": i, "gross_yield": yield_, "price": price, "cap_rate": cap}


POOL = [_m(1, 0.03, 900_000), _m(2, 0.08, 300_000), _m(3, 0.05, 500_000),
        _m(4, None, 700_000), _m(5, 0.06, 400_000), _m(6, 0.04, 600_000),
        _m(7, 0.07, 350_000)]


def _run(params):
    return rank({"metrics": list(POOL), "params": params})


out = _run({})
assert [m["id"] for m in out["metrics"]] == [1, 2, 3, 4, 5]
assert "按语义相关度排序" in out["ranking"]

out = _run({"sort_by": "gross_yield"})
assert [m["id"] for m in out["metrics"]] == [2, 7, 5, 3, 6]
assert len(out["metrics"]) == RESULT_LIMIT
assert "毛租金回报率从高到低" in out["ranking"]
assert all(m["gross_yield"] is not None for m in out["metrics"])

assert [m["id"] for m in _run({"sort_by": "price_asc"})["metrics"]] == [2, 7, 5, 3, 6]

out = _run({"min_gross_yield": 0.06})
assert {m["id"] for m in out["metrics"]} == {2, 5, 7}
assert "已筛掉" in out["ranking"]

out = _run({"min_gross_yield": 0.99})
assert out["metrics"] == []
assert "没有候选达到" in out["ranking"]

out = _run({"sort_by": "cap_rate"})
assert len(out["metrics"]) == RESULT_LIMIT
assert "已按相关度排序" in out["ranking"]

ODD = [dict(_m(1, 0.32, 131_000), predicted_gap=1.19, valuation_error_pct=0.094),
       dict(_m(2, 0.06, 500_000), predicted_gap=0.05, valuation_error_pct=0.094),
       dict(_m(3, 0.05, 600_000), predicted_gap=-0.20, valuation_error_pct=0.094)]
for params in ({"sort_by": "gross_yield"}, {"min_gross_yield": 0.04}):
    out = rank({"metrics": list(ODD), "params": params})
    assert 1 not in [m["id"] for m in out["metrics"]], params
    assert "已剔除 1 套售价远低于模型估值" in out["ranking"]
assert rank({"metrics": list(ODD), "params": {"sort_by": "price_asc"}})["metrics"][0]["id"] == 1


meta = warm_up()
assert meta["feature_order"], "模型元信息缺 feature_order"
assert "year_built" not in meta["feature_order"]

f = {"suburb": "Richmond", "property_type": "house", "bedrooms": 3, "bathrooms": 2,
     "car_spaces": 1, "land_size": 300.0, "building_area": 140.0,
     "distance_cbd": 2.4, "latitude": -37.82, "longitude": 144.99}
v = predict_value(f)
assert v["is_stub"] is False, "V2 的估值必须是真模型,不能再是占位值"
assert isinstance(v["predicted_price"], int) and v["predicted_price"] > 0
assert v["range_low"] < v["predicted_price"] < v["range_high"]
assert 0 < v["typical_error_pct"] < 0.5

assert v["interval_level"] == 0.8, "展示用的区间应为 80% 把握"
assert v["interval_low"] < v["range_low"] < v["predicted_price"] < v["range_high"] < v["interval_high"]
assert 0.75 <= v["interval_coverage"] <= 0.85, "实测覆盖率偏离标称太多,区间名不副实"
for ptype, info in meta["conformal"]["levels"]["0.8"].items():
    assert 0.77 <= info["coverage_mean"] <= 0.83, f"{ptype} 的 80% 区间实测覆盖 {info['coverage_mean']:.1%}"
apt = predict_value(dict(f, property_type="apartment"))
assert (apt["interval_high"] / apt["predicted_price"]) > (v["interval_high"] / v["predicted_price"])

from app.analytics.calibrate_valuation import conformal_q
import math as _math
assert conformal_q([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0], 0.8) == 8.0
assert conformal_q([1.0, 2.0, 3.0], 0.9) == _math.inf

assert predict_values([f, f]) == [v, v]
assert predict_values([]) == []

PYNE = {"suburb": "Caulfield", "address": "30 Pyne St", "property_type": "house", "bedrooms": 4,
        "bathrooms": 1, "car_spaces": 2, "land_size": 499.0, "building_area": 155.0,
        "distance_cbd": 8.9, "latitude": -37.8864, "longitude": 145.0242, "price": 131000}
pyne = predict_values([PYNE])[0]
assert pyne["cross_fitted"] and pyne["predicted_price"] > 1_000_000, pyne
assert PYNE["price"] < pyne["interval_low"], "异常低价必须落在区间之外"
assert predict_values([{k: val for k, val in PYNE.items() if k != "price"}])[0]["cross_fitted"] is False

sparse = dict(f, land_size=None, building_area=None)
assert predict_value(sparse)["predicted_price"] > 0

assert predict_value(dict(f, bedrooms=4))["predicted_price"] > v["predicted_price"]


assert 0 < assumptions.opex_rate() < 1
assert assumptions.other_acquisition_costs() >= 0
snap = assumptions.snapshot()
assumptions.set_rate("opex_rate", 0.35)
assert assumptions.opex_rate() == 0.35
assumptions.set_rate("other_acquisition_costs", 3500)
assert assumptions.other_acquisition_costs() == 3500
assumptions.set_rate("other_acquisition_costs", snap["other_acquisition_costs"])
assert assumptions.snapshot() != snap, "快照必须反映改动"
try:
    assumptions.set_rate("opex_rate", 1.5)
except ValueError:
    pass
else:
    raise AssertionError("超出 [0,1) 的假设值必须被拒绝")
assumptions.set_rate("opex_rate", snap["opex_rate"])


p = _sanitize({"amenity_needs": [{"kind": "train_station", "max_distance_m": 800}]}, "x")
assert p["amenity_needs"] == [{"kind": "train_station", "max_distance_m": 800, "strength": "preferred"}]

assert _sanitize({"amenity_needs": [{"kind": "casino", "max_distance_m": 500}]}, "x")["amenity_needs"] is None
got = _sanitize({"amenity_needs": [{"kind": "hospital"}]}, "x")["amenity_needs"]
assert got == [{"kind": "hospital", "max_distance_m": 1500, "strength": "preferred"}]
assert _sanitize({"amenity_needs": [{"kind": "hospital", "max_distance_m": 0}]}, "x")["amenity_needs"]     == [{"kind": "hospital", "max_distance_m": 1500, "strength": "preferred"}]
assert _sanitize({"amenity_needs": "不是数组"}, "x")["amenity_needs"] is None

p = _sanitize({"near_place": {"name": "Monash University", "kind": "university",
                              "max_distance_m": 3000}}, "x")
assert p["near_place"] == {"name": "Monash University", "kind": "university",
                           "max_distance_m": 3000}
assert _sanitize({"near_place": {"name": "X", "kind": "casino"}}, "x")["near_place"]["kind"] is None
assert _sanitize({"near_place": {"name": "   "}}, "x")["near_place"] is None
assert _sanitize({"near_place": "字符串不行"}, "x")["near_place"] is None

assert _sanitize({"sort_by": "near_place_distance"}, "x")["sort_by"] is None
assert _sanitize({"sort_by": "near_place_distance",
                  "near_place": {"name": "Monash University"}}, "x")["sort_by"] == "near_place_distance"


assert _route({"intent": "new_search"}) == "search"
assert _route({"intent": "refine"}) == "search"
assert _route({"intent": "about_results"}) == "explain"
assert _route({"intent": "concept"}) == "explain"
assert _route({}) == "explain", "意图缺失时不该白跑一趟数据库"


CITY = {"id": 1, "latitude": -37.8197, "longitude": 144.9976, "price": 500_000, "gross_yield": 0.05}
FAR = {"id": 2, "latitude": -38.2000, "longitude": 145.4000, "price": 400_000, "gross_yield": 0.06}

out = enrich({"metrics": [dict(CITY), dict(FAR)],
              "params": {"amenity_needs": [{"kind": "train_station", "max_distance_m": 800}]}})
city, far = out["metrics"]
assert city["amenities"]["train_station"]["distance_m"] < far["amenities"]["train_station"]["distance_m"]
assert set(city["amenities"]) == {"train_station"},     f"enrich 不该算筛选之外的设施,实际算了 {set(city['amenities'])}"
assert city["near_place"] is None

shown = present({"metrics": [dict(city)]})["metrics"][0]
assert {"train_station", "hospital"} <= set(shown["amenities"]),     "present 应当补齐默认展示的设施"
assert shown["amenities"]["train_station"] == city["amenities"]["train_station"]
assert present({"metrics": []}) == {}

out = enrich({"metrics": [dict(CITY)],
              "params": {"near_place": {"name": "完全不存在的大学", "kind": "university"}}})
assert out["place_lookup"]["error"], "找不到的地点必须报错,不能静默"
assert out["metrics"][0]["near_place"] is None

enriched = enrich({"metrics": [dict(CITY), dict(FAR)],
                   "params": {"amenity_needs": [{"kind": "train_station", "max_distance_m": 1000}]}})["metrics"]
out = rank({"metrics": enriched,
            "params": {"amenity_needs": [{"kind": "train_station", "max_distance_m": 1000, "strength": "required"}]}})
assert [m["id"] for m in out["metrics"]] == [1]
assert "要求距火车站 1000 米内" in out["ranking"]

out = rank({"metrics": enriched,
            "params": {"amenity_needs": [{"kind": "train_station", "max_distance_m": 1, "strength": "required"}]}})
assert out["metrics"] == [] and "同时满足" in out["ranking"]

out = rank({"metrics": [enriched[1], enriched[0]],
            "params": {"amenity_needs": [{"kind": "train_station", "max_distance_m": 1000}]}})
assert [m["id"] for m in out["metrics"]] == [1, 2] and "优先距火车站 1000 米内" in out["ranking"]


p = _sanitize({"abstract_needs": [{"attribute": "quiet", "min_score": 75}]}, "x")
assert p["abstract_needs"] == [{"attribute": "quiet", "min_score": 75}]

assert _sanitize({"abstract_needs": [{"attribute": "sunny", "min_score": 60}]}, "x")["abstract_needs"] is None
for bad in ({"attribute": "quiet"}, {"attribute": "quiet", "min_score": -1},
            {"attribute": "quiet", "min_score": 500}, {"attribute": "quiet", "min_score": True}):
    assert _sanitize({"abstract_needs": [bad]}, "x")["abstract_needs"] ==         [{"attribute": "quiet", "min_score": 60}], bad

for boundary in (0, 100):
    need = {"attribute": "quiet", "min_score": boundary, "operator": "eq"}
    assert _sanitize({"abstract_needs": [need]}, "x")["abstract_needs"] == [need]
for invalid in ("eval", {}, None):
    need = {"attribute": "quiet", "min_score": 40, "operator": invalid}
    assert _sanitize({"abstract_needs": [need]}, "x")["abstract_needs"][0]["operator"] == "gte"
explicit = [{"attribute": "quiet", "min_score": 60, "operator": "lt"},
            {"attribute": "lively", "min_score": 60, "operator": "gte"}]
assert _sanitize({"abstract_needs": explicit}, "x")["abstract_needs"] == explicit

p = _sanitize({"abstract_needs": [{"attribute": "quiet", "min_score": 60},
                                  {"attribute": "lively", "min_score": 60}]}, "x")
assert [w["attribute"] for w in p["abstract_needs"]] == ["quiet", "lively"]
assert "_conflict" not in p
p = _sanitize({"abstract_needs": [{"attribute": "lively", "min_score": 60},
                                  {"attribute": "quiet", "min_score": 60}]}, "x")
assert [w["attribute"] for w in p["abstract_needs"]] == ["lively", "quiet"]
assert "_conflict" not in p
p = _sanitize({"abstract_needs": [{"attribute": "lively", "min_score": 60},
                                  {"attribute": "convenient", "min_score": 60}]}, "x")
assert len(p["abstract_needs"]) == 2 and "_conflict" not in p

for attr in ("quiet", "lively", "convenient", "green"):
    assert _sanitize({"sort_by": attr}, "x")["sort_by"] == attr


Q = [{"id": 1, "context_scores": {"quiet": 90}, "price": 1},
     {"id": 2, "context_scores": {"quiet": 40}, "price": 2},
     {"id": 3, "context_scores": {"quiet": 70}, "price": 3},
     {"id": 4, "price": 4},
     {"id": 5, "context_scores": {"quiet": 65}, "price": 5}]

out = rank({"metrics": list(Q), "params": {"abstract_needs": [{"attribute": "quiet", "min_score": 60, "strength": "required"}]}})
assert {m["id"] for m in out["metrics"]} == {1, 3, 5}
pref = rank({"metrics": list(Q), "params": {"abstract_needs": [{"attribute": "quiet", "min_score": 60}]}})
assert [m["id"] for m in pref["metrics"]][:3] == [1, 3, 5] and len(pref["metrics"]) == 5, pref["metrics"]
assert "优先「安静」评分 ≥ 60" in pref["ranking"]
assert "已筛出「安静」评分 ≥ 60 的" in out["ranking"]

out = rank({"metrics": list(Q), "params": {"sort_by": "quiet"}})
assert [m["id"] for m in out["metrics"]] == [1, 3, 5, 2], "应按安静分降序,没分的排除"

out = rank({"metrics": list(Q), "params": {"abstract_needs": [{"attribute": "quiet", "min_score": 99, "strength": "required"}]}})
assert out["metrics"] == [] and "同时满足" in out["ranking"]

for op, ids, symbol in (("gte", {1, 3}, "≥"), ("gt", {1}, ">"),
                        ("lte", {2, 3, 5}, "≤"), ("lt", {2, 5}, "<"), ("eq", {3}, "=")):
    need = {"attribute": "quiet", "min_score": 70, "operator": op, "strength": "required"}
    for lang in ("zh", "en"):
        out = rank({"metrics": list(Q), "params": {"abstract_needs": [need]}, "lang": lang})
        assert {m["id"] for m in out["metrics"]} == ids, (op, out["metrics"])
        assert symbol + " 70" in out["ranking"]
for op in ("lt", "eq"):
    out = rank({"metrics": list(Q), "params": {"abstract_needs": [
        {"attribute": "quiet", "min_score": 0, "operator": op, "strength": "required"}]}})
    assert out["metrics"] == [] and "已忽略" not in out["ranking"]
edges = [{"id": 10, "context_scores": {"quiet": 0}}, {"id": 11, "context_scores": {"quiet": 100}}]
for value, expected in ((0, 10), (100, 11)):
    out = rank({"metrics": edges, "params": {"abstract_needs": [
        {"attribute": "quiet", "min_score": value, "operator": "eq", "strength": "required"}]}})
    assert [m["id"] for m in out["metrics"]] == [expected]

out = rank({"metrics": list(Q), "params": {"_conflict": "lively"}})
assert "互斥" not in out["ranking"] and "已忽略" not in out["ranking"]

print("编排层 + 估值 + 假设 + 设施 + 抽象需求 全部通过。")
