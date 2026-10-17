import gzip
import json
from collections import Counter
from pathlib import Path

import numpy as np
from shapely import (
    STRtree, box as shapely_box, from_wkb, points as shapely_points, to_wkb,
)
from shapely.geometry import shape

_JSONL = Path(__file__).resolve().parents[2] / "data" / "planning.jsonl.gz"
_CACHE = _JSONL.with_name("planning.cache.npz")

DEFAULT_RADIUS_M = 350

_ZONE_FAMILIES = (
    ("NRZ",  "邻里住宅区(限制加密)",         "low"),
    ("GRZ",  "一般住宅区",                     "medium"),
    ("RGZ",  "住宅增长区(鼓励加密)",         "high"),
    ("HCTZ", "住房选择与交通区(近车站,强制加密)", "high"),
    ("LDRZ", "低密度住宅区",                   "low"),
    ("RLZ",  "农村生活区",                     "low"),
    ("TZ",   "镇区",                           "medium"),
    ("MUZ",  "混合用途区",                     "high"),
    ("ACZ",  "活动中心区(规划的高密度核心)", "high"),
    ("CDZ",  "综合开发区",                     "high"),
    ("PRZ",  "特定片区(重大再开发)",         "high"),
    ("CCZ",  "首府城市区(CBD)",              "high"),
    ("DZ",   "码头区(Docklands)",            "high"),
    ("UGZ",  "城市增长区(规划中的新城)",     "high"),
    ("PDZ",  "优先开发区",                     "high"),
    ("C1Z",  "商业一区(商住混合)",           "high"),
    ("C2Z",  "商业二区",                       "high"),
    ("R1Z",  "一般住宅区(旧代码 R1Z)",       "medium"),
    ("R2Z",  "住宅区(旧代码 R2Z)",           "medium"),
    ("R3Z",  "住宅区(旧代码 R3Z)",           "medium"),
    ("B1Z",  "商业一区(旧代码 B1Z)",         "high"),
    ("B2Z",  "商业二区(旧代码 B2Z)",         "high"),
    ("B3Z",  "商业区(旧代码 B3Z)",           "high"),
    ("B4Z",  "商业区(旧代码 B4Z)",           "high"),
    ("B5Z",  "商业区(旧代码 B5Z)",           "high"),
    ("IN1Z", "工业一区",                       "industrial"),
    ("IN2Z", "工业二区",                       "industrial"),
    ("IN3Z", "工业三区",                       "industrial"),
    ("INZ",  "工业区",                         "industrial"),
    ("PPRZ", "公园与游憩用地",                 "open"),
    ("PCRZ", "公共保育与资源用地",             "open"),
    ("RDZ",  "道路用地",                       "infra"),
    ("TRZ",  "交通用地(主干道/铁路)",       "infra"),
    ("PUZ",  "公共用地(学校/政府/水电)",   "public"),
    ("FZ",   "农业区",                         "rural"),
    ("RAZ",  "农业用地区",                     "rural"),
    ("RCZ",  "乡村保育区",                     "rural"),
    ("GWAZ", "绿楔农业区",                     "rural"),
    ("GWZ",  "绿楔区",                         "rural"),
    ("UFZ",  "城市农业区",                     "rural"),
    ("SUZ",  "特别用途区",                     "other"),
    ("PZ",   "港口区",                         "industrial"),
)
_ZONE_BY_LEN = sorted(_ZONE_FAMILIES, key=lambda item: -len(item[0]))

_OVERLAY_FAMILIES = {
    "HO":   ("历史建筑保护",             "build"),
    "DDO":  ("设计与开发控制(限高等)", "build"),
    "BFO":  ("建筑形态控制",             "build"),
    "NCO":  ("邻里特征保护",             "build"),
    "SCO":  ("特定控制",                 "build"),
    "SLO":  ("重要景观保护",             "build"),
    "VPO":  ("植被保护",                 "build"),
    "ESO":  ("环境重要性保护",           "build"),
    "DPO":  ("须先报开发总体规划",       "build"),
    "IPO":  ("须符合已并入的片区规划",   "build"),
    "RO":   ("重建区",                   "build"),
    "PO":   ("停车控制",                 "build"),
    "RXO":  ("道路封闭",                 "build"),
    "CLPO": ("City Link 工程叠加",       "build"),
    "PSB":  ("受保护聚落边界",           "build"),
    "BAO":  ("缓冲区(限制敏感用途)",   "build"),
    "PAO":  ("政府已划定将来征收",       "risk"),
    "SBO":  ("特殊建筑(排水/内涝)",    "risk"),
    "LSIO": ("洪泛淹没区",               "risk"),
    "FO":   ("洪道区",                   "risk"),
    "BMO":  ("山火管理",                 "risk"),
    "EAO":  ("须做环境审计(土壤污染)", "risk"),
    "EMO":  ("侵蚀管理",                 "risk"),
    "SMO":  ("盐渍化管理",               "risk"),
    "AEO":  ("机场环境(噪声)",         "risk"),
    "MAEO": ("墨尔本机场环境(噪声)",   "risk"),
    "RFO":  ("乡村洪道区",               "risk"),
    "DCPO": ("开发须缴基建费",           "cost"),
    "ICO":  ("基础设施贡献",             "cost"),
}
_OVERLAY_BY_LEN = sorted(_OVERLAY_FAMILIES, key=len, reverse=True)

RISK_GROUPS = {
    "airport_noise": ("机场噪声叠加层", ("AEO", "MAEO")),
    "flood":         ("洪泛/内涝叠加层", ("LSIO", "FO", "RFO", "SBO")),
    "bushfire":      ("山火管理叠加层", ("BMO",)),
    "acquisition":   ("政府征收叠加层", ("PAO",)),
    "contamination": ("土壤污染(须环境审计)叠加层", ("EAO",)),
    "erosion":       ("侵蚀/盐渍化管理叠加层", ("EMO", "SMO")),
}
assert all(_OVERLAY_FAMILIES[f][1] == "risk" for _, fams in RISK_GROUPS.values() for f in fams)


def has_family(info: dict, families) -> bool:
    return any(o.get("family") in families for o in (info or {}).get("overlays", []))

_data: dict | None = None


def _family(code):
    if not code:
        return None, "未知分区", "unknown"
    upper = str(code).upper()
    for prefix, label, density in _ZONE_BY_LEN:
        if upper.startswith(prefix):
            return prefix, label, density
    return upper, upper + "(未分类)", "unknown"


def _overlay_family(code):
    if not code:
        return None, "未知叠加层", "other"
    upper = str(code).upper()
    for prefix in _OVERLAY_BY_LEN:
        if upper.startswith(prefix):
            label, effect = _OVERLAY_FAMILIES[prefix]
            return prefix, label, effect
    return upper, upper + "(未分类)", "other"


def _stamp() -> str:
    stat = _JSONL.stat()
    return f"{stat.st_size}:{int(stat.st_mtime)}"


def _read_jsonl() -> dict:
    buckets: dict[str, dict] = {}
    with gzip.open(_JSONL, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            feature = json.loads(line)
            properties = feature["properties"]
            bucket = buckets.setdefault(properties["kind"],
                                        {"geoms": [], "code": [], "desc": [], "lga": []})
            bucket["geoms"].append(shape(feature["geometry"]))
            bucket["code"].append(properties["code"])
            bucket["desc"].append(properties["description"])
            bucket["lga"].append(properties["lga"])
    return buckets


def _write_cache(buckets: dict) -> None:
    payload = {}
    for kind, bucket in buckets.items():
        chunks = to_wkb(np.asarray(bucket["geoms"], dtype=object))
        offsets = np.zeros(len(chunks) + 1, dtype=np.int64)
        np.cumsum([len(c) for c in chunks], out=offsets[1:])
        payload[f"{kind}__wkb"] = np.frombuffer(b"".join(chunks), dtype=np.uint8)
        payload[f"{kind}__offsets"] = offsets
        attrs = json.dumps({k: bucket[k] for k in ("code", "desc", "lga")},
                           ensure_ascii=False, separators=(",", ":"))
        payload[f"{kind}__attrs"] = np.frombuffer(attrs.encode("utf-8"), dtype=np.uint8)
    payload["__stamp__"] = np.frombuffer(_stamp().encode("utf-8"), dtype=np.uint8)
    tmp = _CACHE.with_name(_CACHE.name + ".tmp")
    with tmp.open("wb") as handle:
        np.savez(handle, **payload)
    tmp.replace(_CACHE)


def _read_cache() -> dict | None:
    if not _CACHE.exists():
        return None
    try:
        with np.load(_CACHE, allow_pickle=False) as store:
            if bytes(store["__stamp__"]).decode("utf-8") != _stamp():
                return None
            buckets = {}
            for key in store.files:
                if not key.endswith("__wkb"):
                    continue
                kind = key[: -len("__wkb")]
                blob = store[key].tobytes()
                offsets = store[f"{kind}__offsets"]
                geoms = from_wkb([blob[a:b] for a, b in zip(offsets[:-1], offsets[1:])])
                attrs = json.loads(bytes(store[f"{kind}__attrs"]).decode("utf-8"))
                buckets[kind] = {"geoms": list(geoms), **attrs}
            return buckets or None
    except Exception:
        return None


def _load() -> dict:
    global _data
    if _data is not None:
        return _data
    if not _JSONL.exists():
        raise RuntimeError(
            "Planning zone data missing (" + _JSONL.name + "). "
            "Restore the data folder from the repository.")

    buckets = _read_cache()
    if buckets is None:
        buckets = _read_jsonl()
        try:
            _write_cache(buckets)
        except Exception:
            pass

    _data = {kind: dict(bucket, tree=STRtree(bucket["geoms"]))
             for kind, bucket in buckets.items()}
    return _data


def warm_up() -> dict:
    return {kind: len(bucket["code"]) for kind, bucket in _load().items()}


def _boxes(lats, lons, radius_m):
    lat_arr = np.asarray(lats, dtype=float)
    lon_arr = np.asarray(lons, dtype=float)
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * np.cos(np.radians(lat_arr)))
    return [shapely_box(lon - dx, lat - dlat, lon + dx, lat + dlat)
            for lat, lon, dx in zip(lat_arr, lon_arr, dlon)]


def for_batch(lats, lons, radius_m: int = DEFAULT_RADIUS_M) -> list[dict]:
    lats = list(lats)
    lons = list(lons)
    out: list[dict] = [{} for _ in lats]
    usable = [i for i in range(len(lats)) if lats[i] is not None and lons[i] is not None]
    if not usable:
        return out

    data = _load()
    use_lats = [lats[i] for i in usable]
    use_lons = [lons[i] for i in usable]
    pts = shapely_points(np.array(use_lons, dtype=float), np.array(use_lats, dtype=float))

    zone_bucket = data.get("zone")
    if zone_bucket is not None:
        pairs = zone_bucket["tree"].query(pts, predicate="within")
        for point_idx, poly_idx in zip(pairs[0], pairs[1]):
            slot = out[usable[int(point_idx)]]
            if "zone" in slot:
                continue
            code = zone_bucket["code"][int(poly_idx)]
            _, label, density = _family(code)
            slot["zone"] = code
            slot["zone_desc"] = zone_bucket["desc"][int(poly_idx)]
            slot["zone_label"] = label
            slot["density"] = density
            slot["lga"] = zone_bucket["lga"][int(poly_idx)]

    overlay_bucket = data.get("overlay")
    if overlay_bucket is not None:
        pairs = overlay_bucket["tree"].query(pts, predicate="within")
        for point_idx, poly_idx in zip(pairs[0], pairs[1]):
            slot = out[usable[int(point_idx)]]
            code = overlay_bucket["code"][int(poly_idx)]
            prefix, label, effect = _overlay_family(code)
            slot.setdefault("overlays", [])
            if not any(o["code"] == code for o in slot["overlays"]):
                slot["overlays"].append({"code": code, "family": prefix,
                                         "label": label, "effect": effect})

    if zone_bucket is not None:
        boxes = _boxes(use_lats, use_lons, radius_m)
        pairs = zone_bucket["tree"].query(boxes, predicate="intersects")
        near: dict[int, Counter] = {}
        for box_idx, poly_idx in zip(pairs[0], pairs[1]):
            _, _, density = _family(zone_bucket["code"][int(poly_idx)])
            near.setdefault(int(box_idx), Counter())[density] += 1
        for box_idx, counter in near.items():
            slot = out[usable[box_idx]]
            slot["nearby"] = dict(counter)
            slot["nearby_total"] = sum(counter.values())
            slot["nearby_high"] = counter.get("high", 0)
            slot["nearby_low"] = counter.get("low", 0)
            slot["nearby_open"] = counter.get("open", 0)
            slot["nearby_industrial"] = counter.get("industrial", 0)
            slot["radius_m"] = radius_m

    return out


def for_one(lat, lon, radius_m: int = DEFAULT_RADIUS_M) -> dict:
    if lat is None or lon is None:
        return {}
    return for_batch([lat], [lon], radius_m)[0]


def can_redevelop(info: dict):
    if not info or "zone" not in info:
        return None
    return not any(o["effect"] == "build" for o in info.get("overlays", []))


def risks(info: dict) -> list[dict]:
    if not info:
        return []
    return [o for o in info.get("overlays", []) if o["effect"] == "risk"]


def describe(info: dict) -> str:
    if not info or "zone" not in info:
        return "该坐标无规划分区数据"

    parts = ["分区 " + str(info["zone"]) + "(" + info["zone_label"] + ")"]

    overlays = info.get("overlays") or []
    if overlays:
        shown = "、".join(o["code"] + " " + o["label"] for o in overlays[:4])
        if len(overlays) > 4:
            shown += " 等 " + str(len(overlays)) + " 项"
        parts.append("叠加层:" + shown)
    else:
        parts.append("无叠加层限制")

    total = info.get("nearby_total") or 0
    if total:
        radius = info.get("radius_m", DEFAULT_RADIUS_M)
        bits = ["周边 ±" + str(radius) + " 米方形范围内 " + str(total) + " 块地"]
        if info.get("nearby_low"):
            bits.append(str(info["nearby_low"]) + " 块法律上限制加密")
        if info.get("nearby_high"):
            bits.append(str(info["nearby_high"]) + " 块允许高密度开发")
        if info.get("nearby_open"):
            bits.append(str(info["nearby_open"]) + " 块是公园绿地(不会变成建筑)")
        if info.get("nearby_industrial"):
            bits.append(str(info["nearby_industrial"]) + " 块是工业用地")
        parts.append(",".join(bits))
    return " · ".join(parts)
