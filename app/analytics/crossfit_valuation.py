import csv
import math
import re
import sys
from pathlib import Path

import numpy as np

from app.analytics.train_valuation import FINAL_FEATURES, SEED, TARGET, fit_xgb, load_frame
from app.analytics.valuation import OOF_PATH, oof_key
from app.core.db import close_pool

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

K = 5


def folds_by_group(df, k: int = K) -> np.ndarray:
    groups = (df["address"].astype(str).str.strip().str.lower() + "|"
              + df["suburb"].astype(str).str.strip().str.lower())
    uniq = groups.unique()
    rng = np.random.default_rng(SEED)
    fold_of = dict(zip(uniq[rng.permutation(len(uniq))], np.arange(len(uniq)) % k))
    return groups.map(fold_of).to_numpy()


def main() -> None:
    df = load_frame().reset_index(drop=True)
    fold = folds_by_group(df)
    log_pred = np.full(len(df), np.nan)
    for f in range(K):
        train, test = df[fold != f], df[fold == f]
        model, report = fit_xgb(train, test, FINAL_FEATURES, log_target=True)
        log_pred[fold == f] = model.predict(test[FINAL_FEATURES])
        print(f"  第 {f + 1}/{K} 折:训练 {len(train):,} · 预测 {len(test):,} · "
              f"中位误差 {report['mdape'] * 100:.2f}% · {report['n_estimators']} 棵树")

    rows = df.to_dict("records")
    keys = [oof_key(r) for r in rows]
    dup = len(keys) - len(set(keys))
    with OOF_PATH.open("w", newline="", encoding="utf-8") as handle:
        w = csv.writer(handle)
        w.writerow(["key", "log_pred", "fold"])
        for key, lp, fo in zip(keys, log_pred, fold):
            w.writerow([key, f"{lp:.6f}", int(fo)])

    reread = {}
    with OOF_PATH.open(encoding="utf-8") as handle:
        for rec in csv.DictReader(handle):
            reread[rec["key"]] = float(rec["log_pred"])
    fresh = [oof_key(r) for r in load_frame().to_dict("records")]
    lost = [k for k in fresh if k not in reread]
    if lost or any(re.search(r"(?:^|\|)-?nan(?:\||$)", k) for k in reread):
        OOF_PATH.unlink()
        raise SystemExit(f"离折估值文件自检失败({len(lost)} 个键对不上,例:{lost[:2]}),已删除,请重跑。")

    err = np.abs(np.exp(log_pred) - df[TARGET]) / df[TARGET]
    print(f"\n已写入 {OOF_PATH.name}:{len(df):,} 行(键重复 {dup} 个 —— 重复行特征与售价完全相同,估值也相同)")
    print(f"离折估值中位误差(全库,每套都没被自己的模型见过):{np.median(err) * 100:.2f}%")
    for t, g in df.assign(_e=err).groupby("property_type", observed=True):
        print(f"  {str(t):<10}{np.median(g['_e']) * 100:>6.2f}%  n={len(g):,}")
    assert not math.isnan(float(log_pred.sum())), "有行没被任何一折预测到"


if __name__ == "__main__":
    try:
        main()
    finally:
        close_pool()
