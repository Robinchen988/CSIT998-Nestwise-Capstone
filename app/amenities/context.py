import json
from pathlib import Path

import numpy as np

from app.amenities import nearby, suburb_stats
from app.amenities.registry import (
    ATTRIBUTE_KEYS, ATTRIBUTES, BY_TYPE_KEYS, CONFLICTS, PROPERTY_EVIDENCE,
    SOURCES, SUBURB_EVIDENCE, UNSUPPORTED, evidence_key, evidence_label,
)

_BASELINE_JSON = Path(__file__).resolve().parents[2] / "data" / "context_baseline.json"

_baseline: dict | None = None


def _as_arrays(table: dict) -> dict:
    return {k: (np.asarray(v, dtype=np.float64) if isinstance(v, list) else v)
            for k, v in (table or {}).items()}


def _load_baseline() -> dict | None:
    global _baseline
    if _baseline is None and _BASELINE_JSON.exists():
        raw = json.loads(_BASELINE_JSON.read_text(encoding="utf-8"))
        raw["quantiles"] = _as_arrays(raw.get("quantiles"))
        raw["quantiles_by_type"] = {t: _as_arrays(d)
                                    for t, d in (raw.get("quantiles_by_type") or {}).items()}
        raw["score_quantiles"] = _as_arrays(raw.get("score_quantiles"))
        _baseline = raw
    return _baseline


def score_rank(attr: str, score) -> int | None:
    baseline = _load_baseline()
    if score is None or not baseline:
        return None
    q = (baseline.get("score_quantiles") or {}).get(attr)
    if q is None or len(q) < 2:
        return None
    idx = float(np.searchsorted(q, float(score), side="left"))
    return int(round(min(max(idx / (len(q) - 1), 0.0), 1.0) * 100))


def warm_up() -> dict:
    info = nearby.warm_up()
    baseline = _load_baseline()
    crime = suburb_stats.warm_up()
    return {"points": info["total"],
            "sources": len(SOURCES),
            "crime_suburbs": crime["suburbs"],
            "crime_year": crime["year"],
            "attributes": len(ATTRIBUTES),
            "unsupported": len(UNSUPPORTED),
            "baseline": bool(baseline),
            "missing_quantiles": sorted(
                {k for spec in ATTRIBUTES.values() for k in spec["parts"]}
                - set((baseline or {}).get("quantiles", {}))
                - set(BY_TYPE_KEYS))}


MIN_BUILDING_M2_PER_BEDROOM = 10
MIN_BUILDING_M2 = 20


def _size_m2(prop: dict):
    bedrooms = prop.get("bedrooms")
    floor = max(MIN_BUILDING_M2, MIN_BUILDING_M2_PER_BEDROOM * (bedrooms if isinstance(bedrooms, (int, float)) else 1))
    for key in ("building_area", "land_size"):
        value = prop.get(key)
        if isinstance(value, (int, float)) and value >= floor:
            return float(value)
    return None


def collect_evidence(lat, lon, amenities: dict | None = None, prop: dict | None = None) -> dict:
    if lat is None or lon is None:
        return {}
    out = {}
    reuse = amenities or {}
    for kind, source in SOURCES.items():
        key = evidence_key(kind)
        if source["measure"] == "count":
            value = nearby.count_within(lat, lon, kind, source["radius_m"])
        elif kind in reuse and reuse[kind].get("distance_m") is not None:
            value = int(reuse[kind]["distance_m"])
        else:
            hit = nearby.nearest(lat, lon, kind)
            value = hit["distance_m"] if hit else None
        if value is not None:
            out[key] = value

    prop = {**(prop or {}), "latitude": lat, "longitude": lon}
    out.update(_property_evidence(prop))
    return out


def _property_evidence(prop: dict) -> dict:
    out = {}
    cbd_km = nearby.distance_to_cbd_km(prop.get("latitude"), prop.get("longitude"), prop.get("distance_cbd"))
    if cbd_km is not None:
        out["distance_cbd_km"] = cbd_km
    size = _size_m2(prop)
    if size is not None:
        out["size_m2"] = int(round(size))
    crime = suburb_stats.crime_for(prop.get("suburb"))
    if crime:
        out["crime_rate_per_100k"] = round(crime["rate_per_100k"], 1)
    return out


def collect_evidence_batch(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    lats = [r.get("latitude") for r in rows]
    lons = [r.get("longitude") for r in rows]
    usable = [i for i, (a, b) in enumerate(zip(lats, lons)) if a is not None and b is not None]
    out: list[dict] = [{} for _ in rows]
    if not usable:
        return out

    sub_lats = [lats[i] for i in usable]
    sub_lons = [lons[i] for i in usable]
    for kind, source in SOURCES.items():
        key = evidence_key(kind)
        if source["measure"] == "count":
            counts = nearby.count_within_batch(sub_lats, sub_lons, kind, source["radius_m"])
            if counts is None:
                continue
            for n, i in enumerate(usable):
                out[i][key] = int(counts[n])
        else:
            metres, _ = nearby.nearest_batch(sub_lats, sub_lons, kind)
            if metres is None:
                continue
            for n, i in enumerate(usable):
                out[i][key] = int(round(float(metres[n])))

    for i in usable:
        out[i].update(_property_evidence(rows[i]))
    return out


def _percentile(value, quantiles) -> float | None:
    if value is None or quantiles is None or len(quantiles) < 2:
        return None
    n = len(quantiles) - 1
    idx = float(np.searchsorted(quantiles, value, side="left"))
    return min(max(idx / n, 0.0), 1.0)


MIN_WEIGHT_COVERAGE = 0.6


def scores(ev: dict, property_type: str | None = None, bedrooms=None) -> dict:
    baseline = _load_baseline()
    if not ev or not baseline:
        return {}
    q = baseline.get("quantiles", {})
    by_type = (baseline.get("quantiles_by_type") or {}).get(str(property_type), {})

    def part_value(key: str, direction: str) -> float | None:
        if key == "bedrooms_many":
            return (None if not isinstance(bedrooms, (int, float))
                    else min(max((bedrooms - 1) / 4, 0.0), 1.0))
        table = by_type.get(key) if key in BY_TYPE_KEYS else None
        pct = _percentile(ev.get(key), q.get(key) if table is None else table)
        if pct is None:
            return None
        return 1 - pct if direction in ("near", "few") else pct

    out = {}
    for name, spec in ATTRIBUTES.items():
        total = available = 0.0
        for key, (direction, weight) in spec["parts"].items():
            value = part_value(key, direction)
            if value is not None:
                total += value * weight
                available += weight
        if available >= MIN_WEIGHT_COVERAGE * sum(w for _, w in spec["parts"].values()):
            out[name] = round(total / available * 100)
    return out


def part_strength(ev: dict, property_type: str | None = None, bedrooms=None) -> dict:
    baseline = _load_baseline()
    if not ev or not baseline:
        return {}
    q = baseline.get("quantiles", {})
    by_type = (baseline.get("quantiles_by_type") or {}).get(str(property_type), {})

    def one(key: str, direction: str):
        if key == "bedrooms_many":
            if not isinstance(bedrooms, (int, float)):
                return None
            return round(min(max((bedrooms - 1) / 4, 0.0), 1.0) * 100)
        table = by_type.get(key) if key in BY_TYPE_KEYS else None
        pct = _percentile(ev.get(key), q.get(key) if table is None else table)
        if pct is None:
            return None
        return round((1 - pct if direction in ("near", "few") else pct) * 100)

    out = {}
    for name, spec in ATTRIBUTES.items():
        parts = {k: v for k, v in ((key, one(key, d)) for key, (d, _) in spec["parts"].items())
                 if v is not None}
        if parts:
            out[name] = parts
    return out


def describe_evidence(ev: dict, keys=None) -> list[str]:
    out = []
    for key in (keys or ev):
        if key not in ev:
            continue
        label, unit = evidence_label(key)
        out.append(f"{label} {ev[key]} {unit}")
    return out


def evidence_for(attribute: str) -> tuple[str, ...]:
    spec = ATTRIBUTES.get(attribute)
    return tuple(spec["parts"]) if spec else ()


def explain_attribute(name: str) -> str:
    spec = ATTRIBUTES.get(name)
    return f"「{spec['zh']}」= {spec['note']}" if spec else ""


ATTRIBUTE_ZH = {k: v["zh"] for k, v in ATTRIBUTES.items()}
