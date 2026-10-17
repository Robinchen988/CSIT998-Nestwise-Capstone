import csv
import math
from pathlib import Path

import numpy as np
from sklearn.neighbors import BallTree

from app.amenities.registry import SOURCES, USER_FACING_KINDS

_CSV = Path(__file__).resolve().parents[2] / "data" / "osm_points.csv"

_EARTH_RADIUS_M = 6_371_000.0

KIND_ZH = {k: SOURCES[k]["zh"] for k in USER_FACING_KINDS}
KINDS = USER_FACING_KINDS

_data: dict | None = None


def _load() -> dict:
    global _data
    if _data is not None:
        return _data
    if not _CSV.exists():
        raise RuntimeError(
            f"Map data missing ({_CSV.name}). Restore the data folder from the repository.")

    buckets: dict[str, list] = {}
    named = []
    edges: dict[str, list] = {}
    with _CSV.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            lat, lon = float(row["latitude"]), float(row["longitude"])
            buckets.setdefault(row["kind"], []).append((lat, lon, row["name"]))
            if row.get("role") == "edge":
                if row.get("osm_id"):
                    edges.setdefault(row["osm_id"], []).append((lat, lon))
                continue
            if row["name"]:
                named.append((row["name"],
                              _tokens(row["name"] + " " + (row.get("alt_names") or "")),
                              row["kind"], lat, lon, row.get("osm_id") or ""))

    _data = {
        "by_kind": {
            kind: {
                "tree": BallTree(np.radians(np.array([[p[0], p[1]] for p in pts])),
                                 metric="haversine"),
                "lat": np.radians(np.array([p[0] for p in pts], dtype=np.float64)),
                "lon": np.radians(np.array([p[1] for p in pts], dtype=np.float64)),
                "name": [p[2] for p in pts],
            }
            for kind, pts in buckets.items() if pts
        },
        "named": named,
        "edges": edges,
        "total": sum(len(v) for v in buckets.values()),
    }
    return _data


def _radians_pairs(lats, lons) -> np.ndarray:
    return np.radians(np.column_stack([np.asarray(lats, dtype=float),
                                       np.asarray(lons, dtype=float)]))


def warm_up() -> dict:
    data = _load()
    return {"total": data["total"],
            "by_kind": {k: len(v["name"]) for k, v in data["by_kind"].items()},
            "user_facing": {k: len(data["by_kind"].get(k, {}).get("name", []))
                            for k in KINDS}}


def _haversine_m(lat_deg, lon_deg, group) -> np.ndarray:
    lat1, lon1 = math.radians(lat_deg), math.radians(lon_deg)
    dlat = group["lat"] - lat1
    dlon = group["lon"] - lon1
    a = np.sin(dlat / 2) ** 2 + math.cos(lat1) * np.cos(group["lat"]) * np.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def nearest_batch(lats, lons, kind: str):
    group = _load()["by_kind"].get(kind)
    if not group:
        return None, None
    distances, indices = group["tree"].query(_radians_pairs(lats, lons), k=1)
    metres = distances[:, 0] * _EARTH_RADIUS_M
    names = [group["name"][int(i)] or "(unnamed)" for i in indices[:, 0]]
    return metres, names


def count_within_batch(lats, lons, kind: str, radius_m: int):
    group = _load()["by_kind"].get(kind)
    if not group:
        return None
    return group["tree"].query_radius(_radians_pairs(lats, lons),
                                      r=radius_m / _EARTH_RADIUS_M, count_only=True)


def nearest(lat, lon, kind: str) -> dict | None:
    if lat is None or lon is None:
        return None
    metres, names = nearest_batch([lat], [lon], kind)
    if metres is None:
        return None
    return {"name": names[0], "distance_m": int(round(float(metres[0])))}


def count_within(lat, lon, kind: str, radius_m: int) -> int | None:
    if lat is None or lon is None:
        return None
    counts = count_within_batch([lat], [lon], kind, radius_m)
    return None if counts is None else int(counts[0])


def nearest_by_kind_batch(lats, lons, kinds=None) -> list[dict]:
    n = len(list(lats))
    out: list[dict] = [{} for _ in range(n)]
    usable = [i for i in range(n) if lats[i] is not None and lons[i] is not None]
    if not usable:
        return out
    sub_lats = [lats[i] for i in usable]
    sub_lons = [lons[i] for i in usable]
    for kind in (kinds or KINDS):
        metres, names = nearest_batch(sub_lats, sub_lons, kind)
        if metres is None:
            continue
        for k, i in enumerate(usable):
            out[i][kind] = {"name": names[k],
                            "distance_m": int(round(float(metres[k])))}
    return out


def nearest_by_kind(lat, lon, kinds=None) -> dict:
    out = {}
    for kind in (kinds or KINDS):
        hit = nearest(lat, lon, kind)
        if hit:
            out[kind] = hit
    return out


_STOPWORDS = frozenset({"the", "of", "and", "at", "in", "a"})
_GENERIC = frozenset({
    "university", "uni", "college", "campus", "station", "hospital",
    "school", "centre", "center", "institute", "airport", "bank", "police",
})


def _tokens(text: str) -> set[str]:
    return {
        word for word in "".join(c if c.isalnum() else " " for c in text.lower()).split()
        if word and word not in _STOPWORDS
    }


def find_place(query: str, kind: str | None = None, limit: int = 40) -> list[dict]:
    if not query or not query.strip():
        return []
    wanted = _tokens(query)
    if not wanted:
        return []
    rows = _load()["named"]

    def search(tokens: set[str], restrict: str | None) -> list[dict]:
        return [{"name": name, "kind": row_kind, "latitude": lat, "longitude": lon, "osm_id": osm_id}
                for name, name_tokens, row_kind, lat, lon, osm_id in rows
                if (not restrict or row_kind == restrict) and tokens <= name_tokens]

    specific = wanted - _GENERIC or wanted
    for tokens, restrict in ((wanted, kind), (specific, kind)):
        hits = search(tokens, restrict)
        if hits:
            head = next(iter(sorted(specific))) if specific else ""
            prefixed = [h for h in hits if h["name"].lower().startswith(head)]
            return (prefixed or hits)[:limit]
    return []


def distance_to_place(lat, lon, places: list[dict]) -> dict | None:
    if lat is None or lon is None or not places:
        return None
    edges = _load()["edges"]
    pts = []
    for p in places:
        pts.append((p["latitude"], p["longitude"], p["name"]))
        pts.extend((a, b, p["name"]) for a, b in edges.get(p.get("osm_id") or "", ()))
    group = {"lat": np.radians(np.array([p[0] for p in pts], dtype=np.float64)),
             "lon": np.radians(np.array([p[1] for p in pts], dtype=np.float64))}
    distances = _haversine_m(lat, lon, group)
    index = int(np.argmin(distances))
    return {"name": pts[index][2],
            "distance_m": int(round(float(distances[index])))}


def walk_minutes(distance_m: int | None) -> int | None:
    if distance_m is None:
        return None
    return max(1, int(round(distance_m / 1000 / 5 * 60)))


CBD_POINT = (-37.8136, 144.9631)


def distance_to_cbd_km(lat, lon, fallback=None) -> float | None:
    if lat is not None and lon is not None:
        return round(distance_between(lat, lon, *CBD_POINT) / 1000, 1)
    return round(float(fallback), 1) if isinstance(fallback, (int, float)) and math.isfinite(fallback) else None


def distance_between(lat1, lon1, lat2, lon2) -> int | None:
    if None in (lat1, lon1, lat2, lon2):
        return None
    group = {"lat": np.radians(np.array([lat2], dtype=np.float64)),
             "lon": np.radians(np.array([lon2], dtype=np.float64))}
    return int(round(float(_haversine_m(lat1, lon1, group)[0])))


_KIND_PRIORITY = {
    "train_station": 1, "airport": 1, "hospital": 1, "university": 1, "mall": 1,
    "library": 2, "park": 2, "beach": 2, "sports_centre": 2, "police": 2,
    "secondary_school": 3, "primary_school": 3, "kindergarten": 3,
    "supermarket": 4, "pharmacy": 4, "bank": 4, "gym": 4,
    "tram_stop": 5, "bus_stop": 5,
}
_MEASURE_KINDS = tuple(_KIND_PRIORITY)


def place_point(query: str, near: tuple | None = None, kind: str | None = None) -> dict | None:
    q = (query or "").strip()
    if not q:
        return None
    ql = q.lower()
    qt = _tokens(q)

    seen, cands = set(), []
    for k in ((kind,) if kind else _MEASURE_KINDS):
        for h in find_place(q, kind=k):
            key = (h["name"], h["kind"], round(h["latitude"], 6), round(h["longitude"], 6))
            if key not in seen:
                seen.add(key)
                cands.append(h)
    if not cands and not kind:
        cands = find_place(q)
    if not cands:
        return None

    def rank(h):
        nl = h["name"].lower()
        return (
            0 if nl == ql else 1,
            0 if (nl.startswith(ql) or ql.startswith(nl)) else 1,
            _KIND_PRIORITY.get(h["kind"], 9),
            len(_tokens(h["name"]) - qt),
        )

    best_key = min(rank(h) for h in cands)
    tied = [h for h in cands if rank(h) == best_key]
    best = tied[0]
    if near and len(tied) > 1:
        lat, lon = near
        group = {"lat": np.radians(np.array([h["latitude"] for h in tied], dtype=np.float64)),
                 "lon": np.radians(np.array([h["longitude"] for h in tied], dtype=np.float64))}
        best = tied[int(np.argmin(_haversine_m(lat, lon, group)))]
    return {"name": best["name"], "kind": best["kind"],
            "latitude": float(best["latitude"]), "longitude": float(best["longitude"]),
            "matches": len(cands)}
