import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from app.amenities.context import (
    ATTRIBUTES, UNSUPPORTED, collect_evidence, describe_evidence, scores, warm_up,
)

info = warm_up()
assert info["baseline"], "Context baseline missing. Restore the data folder from the repository."
assert info["points"] > 100_000, f"地理点太少({info['points']})"
assert info["attributes"] >= 10 and info["unsupported"] >= 8
assert not info["missing_quantiles"], f"这些证据缺分位表:{info['missing_quantiles']}"


CBD = (-37.8183, 144.9671)
CHAPEL = (-37.8400, 144.9930)
OUTER = (-37.9800, 144.7000)

ev_cbd, ev_chapel, ev_outer = (collect_evidence(*CBD), collect_evidence(*CHAPEL),
                               collect_evidence(*OUTER))
for ev in (ev_cbd, ev_chapel, ev_outer):
    assert len(ev) >= 25, f"证据项太少:{len(ev)}"
    assert all(isinstance(v, (int, float)) and v >= 0 for v in ev.values())

assert collect_evidence(None, None) == {}
assert collect_evidence(-37.8, None) == {}

assert ev_outer["major_road_m"] > ev_cbd["major_road_m"] * 5
assert ev_outer["railway_m"] > ev_cbd["railway_m"] * 5
assert ev_cbd["nightlife_300m"] > 10 and ev_outer["nightlife_300m"] == 0
assert ev_cbd["shop_800m"] > 100 and ev_outer["shop_800m"] < 5


s_cbd, s_chapel, s_outer = scores(ev_cbd), scores(ev_chapel), scores(ev_outer)
GEO_BASED = {"quiet", "lively", "green", "transport", "school_access", "medical",
             "fitness", "shopping", "beach_access", "away_industry", "away_cemetery"}
for sc in (s_cbd, s_chapel, s_outer):
    assert GEO_BASED <= set(sc), f"只靠坐标也该算得出 {GEO_BASED - set(sc)}"
    assert set(sc) <= set(ATTRIBUTES)
    assert all(0 <= v <= 100 for v in sc.values())
    assert "spacious" not in sc, "没有面积数据却给出了「宽敞」分 —— 不该拿中位数兜底"
    assert "low_crime" not in sc, "没有区名却给出了「治安较好」分 —— 不该拿平均值兜底"

assert s_outer["quiet"] > 85, f"远郊安静分只有 {s_outer['quiet']},评分不可信"
assert s_chapel["quiet"] < 25, f"Chapel St 商圈安静分居然有 {s_chapel['quiet']}"
assert s_cbd["lively"] > 90, f"CBD 热闹分只有 {s_cbd['lively']}"
assert s_outer["lively"] < 15, f"远郊热闹分居然有 {s_outer['lively']}"
full = collect_evidence(*CBD, None, {"distance_cbd": 2.0, "building_area": 120.0,
                                     "suburb": "Melbourne"})
full_scores = scores(full, "apartment", bedrooms=2)
missing_attrs = set(ATTRIBUTES) - set(full_scores)
assert not missing_attrs, f"证据齐全时应算全部属性,缺了 {missing_attrs}"

rng = np.random.default_rng(7)
lats = -37.6 - rng.random(120) * 0.5
lons = 144.8 + rng.random(120) * 0.6
pairs = [(scores(e)["quiet"], scores(e)["lively"])
         for e in (collect_evidence(a, b) for a, b in zip(lats, lons)) if e]
q = np.array([p[0] for p in pairs], dtype=float)
l = np.array([p[1] for p in pairs], dtype=float)
corr = float(np.corrcoef(q, l)[0, 1])
assert corr < -0.6, f"安静与热闹的相关系数是 {corr:.3f},两者应当明显反向"
assert corr > -0.995, f"相关系数 {corr:.3f},安静和热闹几乎就是同一个指标"
assert q.std() > 10, "安静分几乎没有区分度"


from app.amenities import nearby
from app.amenities.context import _size_m2

assert _size_m2({"building_area": 1.0, "land_size": 319.0, "bedrooms": 4}) == 319.0
assert _size_m2({"building_area": 4.0, "land_size": None, "bedrooms": 4}) is None
assert _size_m2({"building_area": 55.0, "land_size": 0.0, "bedrooms": 1}) == 55.0
assert _size_m2({"building_area": 120.0, "land_size": 400.0, "bedrooms": 3}) == 120.0
assert _size_m2({"building_area": 15.0, "land_size": 15.0, "bedrooms": 3}) is None

werribee = (-37.8990, 144.6610)
assert 25 < nearby.distance_to_cbd_km(*werribee, fallback=14.7) < 32
assert nearby.distance_to_cbd_km(None, None, fallback=14.7) == 14.7
assert nearby.distance_to_cbd_km(None, None) is None
ev_w = collect_evidence(*werribee, None, {"distance_cbd": 14.7})
assert ev_w["distance_cbd_km"] > 25, "证据里的距 CBD 仍在用数据集的错值"

punt = nearby.nearest(-37.8201, 144.9898, "park")
assert punt["distance_m"] < 60 and punt["name"] == "Yarra Park", punt
albert = nearby.find_place("Albert Park", "park")
edge_lat, edge_lon = nearby._load()["edges"][albert[0]["osm_id"]][0]
assert nearby.distance_to_place(edge_lat, edge_lon, albert)["distance_m"] == 0
assert nearby.distance_between(edge_lat, edge_lon, albert[0]["latitude"], albert[0]["longitude"]) > 300

assert {"tertiary_road_m", "tram_line_m"} <= set(ATTRIBUTES["quiet"]["parts"])
assert "tertiary_road_m" in ev_cbd and "tram_line_m" in ev_cbd


lines = describe_evidence(ev_cbd)
assert len(lines) == len(ev_cbd)
assert len(describe_evidence(ev_cbd, ["major_road_m"])) == 1
assert all(any(ch.isdigit() for ch in line) for line in lines), "每条证据都要带数字"
assert describe_evidence({}) == []

assert scores({}) == {}


assert len(UNSUPPORTED) >= 8, "明确列出'没有这项数据'的类别太少"
assert any("治安" in k for k in UNSUPPORTED)
assert all(v.strip() for v in UNSUPPORTED.values()), "每一项都要写清为什么没有"
assert not (set(UNSUPPORTED) & {v["zh"] for v in ATTRIBUTES.values()})

print("抽象需求匹配(环境证据 + 评分) 全部通过。")
