import json
import math
import re
from pathlib import Path

import pandas as pd
import xgboost as xgb

_MODEL_DIR = Path(__file__).resolve().parents[2] / "models"
_MODEL_PATH = _MODEL_DIR / "valuation_xgb.ubj"
_META_PATH = _MODEL_DIR / "valuation_meta.json"

OOF_PATH = _MODEL_DIR / "valuation_oof.csv"
_OOF_FIELDS = ("address", "suburb", "price", "property_type", "bedrooms", "bathrooms", "car_spaces",
               "land_size", "building_area", "distance_cbd", "latitude", "longitude")

_model: xgb.XGBRegressor | None = None
_meta: dict | None = None
_oof: dict | None = None


def _norm(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return "" if math.isnan(float(value)) else format(float(value), ".10g")
    text = str(value).strip().lower()
    return "" if text in ("nan", "none") else text


def oof_key(row: dict) -> str:
    return "|".join(_norm(row.get(f)) for f in _OOF_FIELDS)


def _load_oof() -> dict:
    global _oof
    if _oof is None:
        table = {}
        if OOF_PATH.exists():
            import csv
            with OOF_PATH.open(encoding="utf-8") as handle:
                for rec in csv.DictReader(handle):
                    table[rec["key"]] = float(rec["log_pred"])
        _oof = table
    return _oof


def _load():
    global _model, _meta
    if _model is None:
        if not _MODEL_PATH.exists() or not _META_PATH.exists():
            raise RuntimeError(
                f"Valuation model files missing ({_MODEL_PATH.name} / {_META_PATH.name}). "
                "Run: python -m app.analytics.train_valuation"
            )
        meta = json.loads(_META_PATH.read_text(encoding="utf-8"))
        model = xgb.XGBRegressor(enable_categorical=True)
        model.load_model(_MODEL_PATH)
        _meta, _model = meta, model
    return _model, _meta


def warm_up() -> dict:
    _, meta = _load()
    return meta


_UNIT_ADDRESS_RE = re.compile(r"^\s*(?:[A-Za-z]?\d+[A-Za-z]?)\s*/")


def is_unit_address(address) -> int:
    return int(bool(_UNIT_ADDRESS_RE.match(str(address or ""))))


def _to_frame(rows: list[dict], meta: dict) -> pd.DataFrame:
    rows = [dict(r) for r in rows]
    if "is_unit_address" in meta["feature_order"]:
        for r in rows:
            if r.get("is_unit_address") is None:
                r["is_unit_address"] = is_unit_address(r.get("address"))

    df = pd.DataFrame(
        {name: [r.get(name) for r in rows] for name in meta["feature_order"]}
    )
    for col in meta["feature_order"]:
        if col in meta["categorical"]:
            df[col] = pd.Categorical(df[col], categories=meta["categories"][col])
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _error_for(property_type, meta: dict) -> float:
    by_type = meta.get("error_by_type") or {}
    info = by_type.get(str(property_type))
    if info and info.get("mdape"):
        return float(info["mdape"])
    return float(meta["metrics"]["mdape"])


def _interval_for(property_type, meta: dict) -> dict | None:
    conf = meta.get("conformal")
    if not conf:
        return None
    level = conf["display_level"]
    table = conf["levels"][str(level)]
    info = table.get(str(property_type)) or table["__all__"]
    return {"level": level, "q": info["q"], "coverage": info["coverage_mean"]}


def _wrap(pred: float, err: float, interval: dict | None, meta: dict) -> dict:
    out = {
        "predicted_price": int(round(pred)),
        "is_stub": False,
        "model": meta["algorithm"],
        "typical_error_pct": err,
        "range_low": int(round(pred * (1 - err))),
        "range_high": int(round(pred * (1 + err))),
        "interval_level": None, "interval_low": None, "interval_high": None, "interval_coverage": None,
    }
    if interval:
        out.update({
            "interval_level": interval["level"],
            "interval_low": int(round(pred * math.exp(-interval["q"]))),
            "interval_high": int(round(pred * math.exp(interval["q"]))),
            "interval_coverage": interval["coverage"],
        })
    return out


def predict_value(features: dict) -> dict:
    return predict_values([features])[0]


def predict_values(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    import numpy as np

    model, meta = _load()
    log_preds = model.predict(_to_frame(rows, meta))
    oof = _load_oof()
    out = []
    for lp, r in zip(log_preds, rows):
        hit = oof.get(oof_key(r)) if oof and r.get("price") else None
        item = _wrap(float(np.exp(hit if hit is not None else lp)), _error_for(r.get("property_type"), meta),
                     _interval_for(r.get("property_type"), meta), meta)
        item["cross_fitted"] = hit is not None
        out.append(item)
    return out
