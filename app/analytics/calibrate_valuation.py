import json
import math
import sys

import numpy as np

from app.analytics import valuation
from app.analytics.train_valuation import FINAL_FEATURES, META_PATH, SEED, TARGET, load_frame, split
from app.core.db import close_pool

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LEVELS = (0.8, 0.9)
DISPLAY_LEVEL = 0.8
REPEATS = 500
MIN_GROUP = 100


def conformal_q(residuals: np.ndarray, level: float) -> float:
    n = len(residuals)
    k = math.ceil((n + 1) * level)
    if n == 0 or k > n:
        return math.inf
    return float(np.sort(residuals)[k - 1])


def half_width_pct(q: float) -> tuple[float, float]:
    return 1 - math.exp(-q), math.exp(q) - 1


def predict_log(df) -> np.ndarray:
    model, meta = valuation._load()
    rows = df[FINAL_FEATURES + ["address"]].to_dict("records")
    return model.predict(valuation._to_frame(rows, meta))


def main(write: bool) -> None:
    df = load_frame()
    train, hold = split(df, "time")
    _, meta = valuation._load()

    log_true = np.log(df[TARGET].to_numpy(dtype=float))
    log_pred = predict_log(df)
    df = df.assign(_res=np.abs(log_true - log_pred), _pred=np.exp(log_pred))
    hold = df.loc[hold.index]

    def mdape(frame, col="_pred"):
        return float(np.median(np.abs(frame[TARGET] - frame[col]) / frame[TARGET]))
    print(f"定稿模型留出集(2018 年那批,{len(hold):,} 套)中位误差 {mdape(hold)*100:.2f}%"
          f"(meta 记录 {meta['metrics']['mdape']*100:.2f}%)")
    if abs(mdape(hold) - meta["metrics"]["mdape"]) > 0.002:
        raise SystemExit("留出集误差与训练时记录的不一致:数据库或模型已变,不能沿用这个划分做校准。")

    oof = valuation._load_oof()
    keys = [valuation.oof_key(r) for r in df.to_dict("records")]
    missing = sum(k not in oof for k in keys)
    if missing:
        raise SystemExit(f"{missing} 套房源查不到离折估值:先跑 python -m app.analytics.crossfit_valuation")
    log_oof = np.array([oof[k] for k in keys])
    df = df.assign(_res_oof=np.abs(log_true - log_oof), _pred_oof=np.exp(log_oof))
    hold = df.loc[hold.index]
    print(f"离折估值中位误差:全库 {mdape(df, '_pred_oof')*100:.2f}% · 其中 2018 年那批 {mdape(hold, '_pred_oof')*100:.2f}%\n")

    rng = np.random.default_rng(SEED)
    types = sorted(df["property_type"].astype(str).unique())
    result = {"levels": {}, "repeats": REPEATS, "n_calibration": int(len(df)),
              "calibration": "K-fold out-of-fold residuals on all rows; q = max(OOF, time-split holdout) per type"}

    for level in LEVELS:
        print(f"==== 标称把握 {level:.0%} ====")
        print(f"  {'房型':<10}{'校准n':>7}{'区间(下/上)':>18}{'实测覆盖(均值)':>14}{'5%–95%':>16}{'2018批覆盖':>11}")
        per_type = {}
        for t in types + ["__all__"]:
            g = df if t == "__all__" else df[df["property_type"].astype(str) == t]
            h = hold if t == "__all__" else hold[hold["property_type"].astype(str) == t]
            r = g["_res_oof"].to_numpy()
            q_time = conformal_q(h["_res"].to_numpy(), level) if len(h) >= MIN_GROUP else 0.0
            covs = []
            for _ in range(REPEATS):
                idx = rng.permutation(len(r))
                cal, ev = r[idx[: len(r) // 2]], r[idx[len(r) // 2:]]
                covs.append(float(np.mean(ev <= max(conformal_q(cal, level), q_time))))
            q_oof = conformal_q(r, level)
            widened = q_time > q_oof
            q_full = max(q_oof, q_time)
            cov_hold = float(np.mean(h["_res_oof"].to_numpy() <= q_full)) if len(h) else float("nan")
            lo, hi = half_width_pct(q_full)
            covs = np.array(covs)
            print(f"  {t:<10}{len(r):>7}{f'-{lo:.1%} / +{hi:.1%}':>18}{covs.mean():>14.1%}"
                  f"{f'{np.percentile(covs,5):.1%}–{np.percentile(covs,95):.1%}':>16}{cov_hold:>11.1%}"
                  + ("   (按时间留出集放宽)" if widened else ""))
            per_type[t] = {"q": q_full, "n": int(len(r)), "down_pct": lo, "up_pct": hi,
                           "coverage_mean": float(covs.mean()), "coverage_p5": float(np.percentile(covs, 5)),
                           "coverage_p95": float(np.percentile(covs, 95)), "coverage_2018_rows": cov_hold,
                           "widened_by_time_split": bool(widened)}
        result["levels"][str(level)] = per_type
        print()

    lv = result["levels"][str(DISPLAY_LEVEL)]
    q = df["property_type"].astype(str).map(lambda t: lv.get(t, lv["__all__"])["q"])
    below_oof = float(np.mean(df[TARGET] < df["_pred_oof"] * np.exp(-q)))
    below_fit = float(np.mean(df.loc[train.index, TARGET] < df.loc[train.index, "_pred"] * np.exp(-q[train.index])))
    print(f"售价低于 {DISPLAY_LEVEL:.0%} 区间下限的比例:离折估值 {below_oof:.1%}"
          f"(按定义约 {(1-DISPLAY_LEVEL)/2:.0%})· 旧做法训练集内拟合值 {below_fit:.1%}")

    if write:
        meta_json = json.loads(META_PATH.read_text(encoding="utf-8"))
        meta_json["conformal"] = {**result, "display_level": DISPLAY_LEVEL,
                                  "note": "区间 = 估值·e^(∓q),q 为分房型分割保形分位数;"
                                          "coverage_* 为全库离折残差随机对半 500 次的实测覆盖率(含按时间留出集放宽的规则)"}
        META_PATH.write_text(json.dumps(meta_json, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写入 {META_PATH.name} 的 conformal 段")


if __name__ == "__main__":
    try:
        main(write="--measure" not in sys.argv)
    finally:
        close_pool()
