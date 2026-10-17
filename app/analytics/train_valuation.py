import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from app.analytics.valuation import is_unit_address as valuation_is_unit_address
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.core.db import close_pool, get_connection

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODEL_DIR = Path(__file__).resolve().parents[2] / "models"
MODEL_PATH = MODEL_DIR / "valuation_xgb.ubj"
META_PATH = MODEL_DIR / "valuation_meta.json"

CATEGORICAL = ["suburb", "property_type"]
NUMERIC = [
    "bedrooms", "bathrooms", "car_spaces", "land_size", "building_area",
    "year_built", "distance_cbd", "latitude", "longitude", "annual_rent",
    "months_since_start",
]
TARGET = "price"
SEED = 42

FINAL_FEATURES = CATEGORICAL + [
    "bedrooms", "bathrooms", "car_spaces", "land_size", "building_area",
    "distance_cbd", "latitude", "longitude",
    "is_unit_address",
]

_LOAD_SQL = """
    SELECT suburb, property_type, price, bedrooms, bathrooms, car_spaces,
           land_size, building_area, year_built, distance_cbd,
           latitude, longitude, annual_rent, sale_date, address
    FROM properties
    ORDER BY id
"""


def load_frame() -> pd.DataFrame:
    with get_connection() as conn:
        df = pd.read_sql(_LOAD_SQL, conn)

    sale = pd.to_datetime(df["sale_date"])
    df["months_since_start"] = (
        (sale.dt.year - sale.dt.year.min()) * 12 + sale.dt.month
    )
    df["months_since_start"] -= df["months_since_start"].min()
    for col in CATEGORICAL:
        df[col] = df[col].astype("category")
    df["is_unit_address"] = df["address"].map(valuation_is_unit_address)
    return df


def split(df: pd.DataFrame, mode: str):
    if mode == "random":
        rng = np.random.default_rng(SEED)
        idx = rng.permutation(len(df))
        cut = int(len(df) * 0.8)
        return df.iloc[idx[:cut]], df.iloc[idx[cut:]]
    order = df["months_since_start"].sort_values(kind="stable").index
    cut = int(len(df) * 0.8)
    return df.loc[order[:cut]], df.loc[order[cut:]]


def _report(y_true, y_pred) -> dict:
    err = np.abs(y_true - y_pred)
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(np.mean((y_true - y_pred) ** 2))),
        "r2": float(r2_score(y_true, y_pred)),
        "mdape": float(np.median(err / y_true)),
        "within_10pct": float(np.mean(err / y_true <= 0.10)),
        "within_20pct": float(np.mean(err / y_true <= 0.20)),
    }


_MONOTONE = {
    "bedrooms": 1, "bathrooms": 1, "car_spaces": 1,
    "land_size": 1, "building_area": 1,
    "distance_cbd": -1,
}


def fit_xgb(train, test, features, log_target: bool):
    y_full = np.log(train[TARGET]) if log_target else train[TARGET]

    cut = int(len(train) * 0.8)
    X_fit, y_fit = train[features].iloc[:cut], y_full.iloc[:cut]
    X_val, y_val = train[features].iloc[cut:], y_full.iloc[cut:]

    params = dict(
        learning_rate=0.05, max_depth=8,
        subsample=0.8, colsample_bytree=0.8, min_child_weight=3,
        reg_lambda=1.0, random_state=SEED,
        enable_categorical=True, tree_method="hist",
        monotone_constraints=tuple(_MONOTONE.get(f, 0) for f in features),
    )
    probe = xgb.XGBRegressor(n_estimators=2000, early_stopping_rounds=50, **params)
    probe.fit(X_fit, y_fit, eval_set=[(X_val, y_val)], verbose=False)
    best = max(int(probe.best_iteration) + 1, 50)

    model = xgb.XGBRegressor(n_estimators=best, **params)
    model.fit(train[features], y_full, verbose=False)

    pred = model.predict(test[features])
    if log_target:
        pred = np.exp(pred)
    report = _report(test[TARGET].to_numpy(), pred)
    report["n_estimators"] = best
    return model, report


def fit_linear(train, test, features, log_target: bool):
    cats = [c for c in features if c in CATEGORICAL]
    nums = [c for c in features if c not in CATEGORICAL]
    pipe = Pipeline([
        ("prep", ColumnTransformer([
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), cats),
            ("num", Pipeline([("imp", SimpleImputer(strategy="median")),
                              ("sc", StandardScaler())]), nums),
        ])),
        ("lr", LinearRegression()),
    ])
    y_tr = np.log(train[TARGET]) if log_target else train[TARGET]
    pipe.fit(train[features], y_tr)
    pred = pipe.predict(test[features])
    if log_target:
        pred = np.exp(pred)
    return pipe, _report(test[TARGET].to_numpy(), pred)


def error_by_type(df: pd.DataFrame, features: list[str]) -> dict:
    out = {}
    for mode in ("time", "random"):
        train, test = split(df, mode)
        model, _ = fit_xgb(train, test, features, log_target=True)
        pred = np.exp(model.predict(test[features]))
        frame = test.assign(_pred=pred)
        for ptype, group in frame.groupby("property_type", observed=True):
            r = _report(group[TARGET].to_numpy(), group["_pred"].to_numpy())
            key = str(ptype)
            prev = out.get(key, {})
            if r["mdape"] > prev.get("mdape", 0):
                out[key] = {"mdape": r["mdape"], "n": int(len(group)), "split": mode,
                            "within_20pct": r["within_20pct"]}
    return out


def _row(name, m):
    return (f"  {name:<34}{m['mae']:>11,.0f}{m['mdape']*100:>9.1f}%"
            f"{m['r2']:>8.3f}{m['within_10pct']*100:>9.1f}%{m['within_20pct']*100:>9.1f}%")


def main(save: bool = True) -> None:
    df = load_frame()
    print(f"训练数据:{len(df):,} 行 · {df['suburb'].nunique()} 个区 · "
          f"{df['sale_date'].min()} ~ {df['sale_date'].max()}\n")

    features_full = CATEGORICAL + NUMERIC
    features_notime = [f for f in features_full if f != "months_since_start"]

    results = {}
    for split_mode in ("random", "time"):
        train, test = split(df, split_mode)
        label = "随机划分 8:2" if split_mode == "random" else "按时间划分(用过去预测未来)"
        print(f"【{label}】训练 {len(train):,} / 测试 {len(test):,}")
        print(f"  {'配置':<34}{'MAE':>11}{'中位误差':>9}{'R²':>8}{'±10%内':>9}{'±20%内':>9}")
        _, m = fit_linear(train, test, features_full, log_target=False)
        print(_row("线性回归 baseline", m)); results[f"{split_mode}/linear"] = m
        _, m = fit_linear(train, test, features_full, log_target=True)
        print(_row("线性回归 baseline(对数价)", m)); results[f"{split_mode}/linear_log"] = m
        _, m = fit_xgb(train, test, features_full, log_target=False)
        print(_row("XGBoost", m)); results[f"{split_mode}/xgb"] = m
        _, m = fit_xgb(train, test, features_full, log_target=True)
        print(_row("XGBoost(对数价)", m)); results[f"{split_mode}/xgb_log"] = m
        _, m = fit_xgb(train, test, features_notime, log_target=True)
        print(_row("XGBoost(对数价,去掉时间特征)", m)); results[f"{split_mode}/xgb_log_notime"] = m
        print()

    if not save:
        return

    train, test = split(df, "time")
    model, metrics = fit_xgb(train, test, FINAL_FEATURES, log_target=True)
    _, baseline = fit_linear(train, test, FINAL_FEATURES, log_target=True)
    per_type = error_by_type(df, FINAL_FEATURES)

    MODEL_DIR.mkdir(exist_ok=True)
    model.save_model(MODEL_PATH)
    META_PATH.write_text(json.dumps({
        "trained_on": str(date.today()),
        "rows": int(len(df)),
        "algorithm": "XGBRegressor(log price target)",
        "categorical": CATEGORICAL,
        "feature_order": FINAL_FEATURES,
        "categories": {c: sorted(map(str, df[c].cat.categories)) for c in CATEGORICAL},
        "log_target": True,
        "monotone_constraints": {k: v for k, v in _MONOTONE.items() if k in FINAL_FEATURES},
        "split": "time(前 80% 训练,后 20% 测试)",
        "metrics": metrics,
        "baseline_metrics": baseline,
        "error_by_type": per_type,
        "excluded_features": {
            "months_since_start": "去掉后 MAE 更低;数据仅跨 26 个月,无趋势可学",
            "annual_rent": "去掉代价 0.4%,换取估值与租金回报率两个信号互相独立",
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已保存:{MODEL_PATH.name} + {META_PATH.name}")
    print(f"定稿模型(按时间划分):MAE ${metrics['mae']:,.0f} · "
          f"中位误差 {metrics['mdape']*100:.1f}% · R² {metrics['r2']:.3f} · "
          f"±20% 内 {metrics['within_20pct']*100:.1f}%")
    print(f"线性回归 baseline 同口径:MAE ${baseline['mae']:,.0f} · "
          f"中位误差 {baseline['mdape']*100:.1f}% · R² {baseline['r2']:.3f}")
    print("分房型中位误差(取两种划分里更保守的):")
    for ptype, info in sorted(per_type.items()):
        print(f"  {ptype:<12}{info['mdape']*100:>6.1f}%   (n={info['n']}, {info['split']} 划分)")

    print()
    print("重算库内房源的离折估值(K 折交叉拟合):")
    from app.analytics.crossfit_valuation import main as crossfit
    crossfit()
    print()
    print("重新校准估值区间(分割保形预测):")
    from app.analytics.calibrate_valuation import main as calibrate
    calibrate(write=True)


if __name__ == "__main__":
    try:
        main(save="--compare" not in sys.argv)
    finally:
        close_pool()
