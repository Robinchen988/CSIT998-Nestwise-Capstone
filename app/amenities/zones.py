import json
from pathlib import Path

import numpy as np
from shapely import STRtree, points as shapely_points
from shapely.geometry import shape

_GEOJSON = Path(__file__).resolve().parents[2] / "data" / "school_zones.geojson"

LEVEL_ZH = {"primary": "小学", "secondary": "中学"}
LEVELS = tuple(LEVEL_ZH)

_data: dict | None = None


def _load() -> dict:
    global _data
    if _data is not None:
        return _data
    if not _GEOJSON.exists():
        raise RuntimeError(
            f"School zone data missing ({_GEOJSON.name}). "
            "Restore the data folder from the repository.")

    raw = json.loads(_GEOJSON.read_text(encoding="utf-8"))
    by_level: dict[str, dict] = {}
    for feature in raw["features"]:
        level = feature["properties"]["level"]
        bucket = by_level.setdefault(level, {"geoms": [], "names": [], "year": []})
        bucket["geoms"].append(shape(feature["geometry"]))
        bucket["names"].append(feature["properties"]["school"])
        bucket["year"].append(feature["properties"].get("boundary_year"))

    _data = {
        level: {"tree": STRtree(bucket["geoms"]),
                "geoms": bucket["geoms"],
                "names": bucket["names"],
                "year": bucket["year"]}
        for level, bucket in by_level.items()
    }
    return _data


def warm_up() -> dict:
    data = _load()
    return {level: len(v["names"]) for level, v in data.items()}


def zone_for_batch(lats, lons) -> list[dict]:
    n = len(list(lats))
    out: list[dict] = [{} for _ in range(n)]
    usable = [i for i in range(n) if lats[i] is not None and lons[i] is not None]
    if not usable:
        return out

    pts = shapely_points(np.array([lons[i] for i in usable], dtype=float),
                         np.array([lats[i] for i in usable], dtype=float))
    for level, bucket in _load().items():
        pairs = bucket["tree"].query(pts, predicate="within")
        for point_idx, zone_idx in zip(pairs[0], pairs[1]):
            out[usable[int(point_idx)]].setdefault(level, bucket["names"][int(zone_idx)])
    return out


def zone_for(lat, lon) -> dict:
    if lat is None or lon is None:
        return {}
    return zone_for_batch([lat], [lon])[0]


def find_zone(school: str, level: str | None = None) -> list[dict]:
    if not school or not school.strip():
        return []
    needle = school.strip().lower()
    hits = []
    for lvl, bucket in _load().items():
        if level and lvl != level:
            continue
        for i, name in enumerate(bucket["names"]):
            if name and needle in name.lower():
                hits.append({"level": lvl, "school": name, "index": i})
    exact = [h for h in hits if h["school"].lower() == needle]
    return exact or sorted(hits, key=lambda h: len(h["school"]))[:8]


def in_zone_batch(lats, lons, zone_hits: list[dict]) -> list[bool]:
    n = len(list(lats))
    out = [False] * n
    if not zone_hits:
        return out
    usable = [i for i in range(n) if lats[i] is not None and lons[i] is not None]
    if not usable:
        return out
    pts = shapely_points(np.array([lons[i] for i in usable], dtype=float),
                         np.array([lats[i] for i in usable], dtype=float))
    data = _load()
    for hit in zone_hits:
        geom = data[hit["level"]]["geoms"][hit["index"]]
        inside = geom.contains(pts)
        for k, i in enumerate(usable):
            if inside[k]:
                out[i] = True
    return out


def describe(zones: dict) -> str:
    if not zones:
        return "不在任何公立学校学区内(或该坐标无学区数据)"
    return " · ".join(f"{LEVEL_ZH[level]}学区 {name}"
                      for level, name in zones.items() if level in LEVEL_ZH)
