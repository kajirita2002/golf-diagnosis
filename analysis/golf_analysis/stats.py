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


def improvement_rows(goal: str) -> Callable[[np.ndarray, np.ndarray], np.ndarray]:
    """improvement_stat の行ごと版（2次元の配列の各行が1回の再標本）。"diff" は b − a の平均の差。"""
    if goal == "reduce_abs":
        return lambda a, b: np.abs(a).mean(axis=1) - np.abs(b).mean(axis=1)
    if goal == "reduce_sd":
        return lambda a, b: a.std(axis=1, ddof=1) - b.std(axis=1, ddof=1)
    if goal == "increase" or goal == "diff":
        return lambda a, b: b.mean(axis=1) - a.mean(axis=1)
    if goal == "decrease":
        return lambda a, b: a.mean(axis=1) - b.mean(axis=1)
    raise ValueError(f"goal {goal!r} は使えません")


def bootstrap_ci_goal(a, b, goal: str, n: int = config.BOOTSTRAP_N) -> tuple[float, float]:
    """bootstrap_ci のベクトル化版（群ごとの復元抽出を2次元の添字で1回に引く）。

    ループ版は1回 0.2秒ほどかかり、CPU 0.1 の環境で評価を4つ回すと数秒になる（docs/DESIGN_coaching.md §8.5）。
    乱数の引き方はループ版と違うので、これを使う評価は analysis/0.4 から。
    """
    A, B = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    rng = np.random.default_rng(config.SEED)
    ia = rng.integers(0, A.size, size=(n, A.size))
    ib = rng.integers(0, B.size, size=(n, B.size))
    vals = improvement_rows(goal)(A[ia], B[ib])
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return float(lo), float(hi)


def permutation_p_goal(a, b, goal: str, n: int = config.PERMUTATION_N) -> float:
    """permutation_p のベクトル化版（並べ替えを n 行まとめて作る）。"""
    A, B = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    f = improvement_rows(goal)
    observed = float(f(A[None, :], B[None, :])[0])
    rng = np.random.default_rng(config.SEED)
    pooled = np.broadcast_to(np.concatenate([A, B]), (n, A.size + B.size))
    perm = rng.permuted(pooled, axis=1)
    hits = int(np.count_nonzero(f(perm[:, : A.size], perm[:, A.size :]) >= observed - 1e-12))
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


def bootstrap_mean_ci(values, n: int = config.BOOTSTRAP_N) -> tuple[float, float] | None:
    """平均の 95% 区間（復元抽出を1回の2次元の添字でまとめて引く）。

    5000回のループを指標×範囲の数だけ回すと、CPU 0.1 の環境では十数秒になりうるので
    ベクトル化した（docs/DESIGN_coaching.md §5.1）。乱数の引き方は analysis/0.2 と違う。
    区間は「今日の球では〜に偏っていた」とだけ言う（球どうしが独立でないと狭く出る）。
    """
    v = np.asarray(values, dtype=float)
    if v.size < 2:
        return None
    rng = np.random.default_rng(config.SEED)
    means = v[rng.integers(0, v.size, size=(n, v.size))].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def pearson(x, y) -> float | None:
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if x.size < 3 or x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def fisher_ci(r: float, n: int) -> tuple[float, float] | None:
    """相関係数の 95% 区間（Fisher の z 変換）。"""
    if n < 4 or r is None or abs(r) >= 1:
        return None
    z = np.arctanh(r)
    se = 1 / np.sqrt(n - 3)
    return float(np.tanh(z - 1.96 * se)), float(np.tanh(z + 1.96 * se))


def simple_r2(y, x) -> float | None:
    """単回帰の決定係数（＝相関の2乗）。説明の割合はこれだけを使う（R4）。"""
    r = pearson(x, y)
    return None if r is None else r * r


def ols_coef(y, X) -> list[float]:
    """切片つきの重回帰の生の係数（切片は返さない）。"""
    X = np.asarray(X, dtype=float)
    A = np.column_stack([X, np.ones(len(y))])
    coef, *_ = np.linalg.lstsq(A, np.asarray(y, dtype=float), rcond=None)
    return [float(c) for c in coef[:-1]]
