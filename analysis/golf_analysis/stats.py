"""統計の部品。どれも乱数の種を固定して、同じ入力には同じ答えを返す。"""

from __future__ import annotations

from typing import Callable

import numpy as np

from . import config

Stat = Callable[[np.ndarray, np.ndarray], float]


def improvement_stat(goal: str) -> Stat:
    """「基準 a → 介入 b」で良くなった量。プラスが改善。"""
    if goal == "reduce_abs":
        return lambda a, b: float(np.mean(np.abs(a)) - np.mean(np.abs(b)))
    if goal == "reduce_sd":
        return lambda a, b: float(np.std(a, ddof=1) - np.std(b, ddof=1))
    if goal == "increase":
        return lambda a, b: float(np.mean(b) - np.mean(a))
    if goal == "decrease":
        return lambda a, b: float(np.mean(a) - np.mean(b))
    raise ValueError(f"goal {goal!r} は使えません")


def bootstrap_ci(a: np.ndarray, b: np.ndarray, stat: Stat, n: int = config.BOOTSTRAP_N) -> tuple[float, float]:
    """群ごとに復元抽出して、改善量の 95% 区間を返す。"""
    rng = np.random.default_rng(config.SEED)
    vals = np.empty(n)
    for i in range(n):
        vals[i] = stat(rng.choice(a, a.size, replace=True), rng.choice(b, b.size, replace=True))
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return float(lo), float(hi)


def permutation_p(a: np.ndarray, b: np.ndarray, stat: Stat, n: int = config.PERMUTATION_N) -> float:
    """「介入に効果が無い」としたとき、観測した改善以上が偶然に出る割合（片側）。"""
    rng = np.random.default_rng(config.SEED)
    observed = stat(a, b)
    pooled = np.concatenate([a, b])
    hits = 0
    for _ in range(n):
        rng.shuffle(pooled)
        if stat(pooled[: a.size], pooled[a.size :]) >= observed - 1e-12:
            hits += 1
    return (hits + 1) / (n + 1)


def ols_contributions(y: np.ndarray, X: np.ndarray, names: list[str]) -> dict:
    """y のばらつきを X の各列がどれだけ説明するか。

    標準化した重回帰の決定係数 R² と、各説明変数を1つ抜いたときの R² の落ち幅を
    返す（落ち幅を合計1に正規化したものを share とする）。
    相関の強い説明変数どうしは落ち幅が小さく出る ―― 因果ではなく「説明の割合」。
    """
    Xs = (X - X.mean(axis=0)) / np.where(X.std(axis=0) > 0, X.std(axis=0), 1)
    ys = y - y.mean()

    def r2(cols: list[int]) -> float:
        if not cols:
            return 0.0
        A = np.column_stack([Xs[:, cols], np.ones(len(ys))])
        coef, *_ = np.linalg.lstsq(A, ys, rcond=None)
        resid = ys - A @ coef
        tss = float(ys @ ys)
        return 0.0 if tss == 0 else max(0.0, 1 - float(resid @ resid) / tss)

    full = list(range(X.shape[1]))
    total = r2(full)
    drops = []
    for j in full:
        drops.append(max(0.0, total - r2([k for k in full if k != j])))
    s = sum(drops)
    return {
        "r2": total,
        "contributions": [
            {"metric": names[j], "r2_drop": drops[j], "share": (drops[j] / s) if s > 0 else 0.0}
            for j in sorted(full, key=lambda j: -drops[j])
        ],
    }


def describe(values: list[float]) -> dict:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return {"n": 0}
    out = {"n": int(arr.size), "mean": float(arr.mean()), "mean_abs": float(np.abs(arr).mean())}
    if arr.size >= 2:
        out["sd"] = float(arr.std(ddof=1))
    return out
