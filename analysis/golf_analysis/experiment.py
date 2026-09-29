"""ブロック実験の評価（このアプリの中心 ―― docs/DESIGN.md ❺）。

「基準 → 介入 → 定着」で、目標の指標が意味のある大きさで動いたかを判定する。
介入したら L1 が動いた、という記録だけが信頼できる因果の証拠になる。

判定（grade）:
  insufficient … どちらかの群が MIN_BLOCK_N 球に満たない。何も言わない
  strong       … 改善の 95% 区間が 0 より上、かつ改善量が MMD 以上
  moderate     … 区間は 0 より上だが MMD 未満 / または MMD 以上で p < 0.10
  weak         … 改善の向きには動いたが、偶然と区別できない
  none         … 改善していない
  worse        … 悪くなった（区間が 0 より下）
"""

from __future__ import annotations

import numpy as np

from . import config, stats

VALID_GOALS = {"reduce_abs", "reduce_sd", "increase", "decrease"}


def _values(block_shots: list[dict], metric: str) -> tuple[list[float], int]:
    vals, estimated = [], 0
    for s in block_shots:
        if s.get("excluded"):
            continue
        v = (s.get("metrics") or {}).get(metric)
        if v is None:
            continue
        vals.append(float(v))
        if metric in (s.get("estimated") or []):
            estimated += 1
    return vals, estimated


def _compare(a: list[float], b: list[float], goal: str, mmd: float | None) -> dict:
    if len(a) < config.MIN_BLOCK_N or len(b) < config.MIN_BLOCK_N:
        return {
            "grade": "insufficient",
            "n": [len(a), len(b)],
            "needed": config.MIN_BLOCK_N,
        }
    A, B = np.asarray(a), np.asarray(b)
    observed = stats.improvement_stat(goal)(A, B)
    # ベクトル化した bootstrap と並べ替え（analysis/0.4。ループ版は CPU 0.1 で評価1回に数秒かかる）
    lo, hi = stats.bootstrap_ci_goal(A, B, goal)
    p = stats.permutation_p_goal(A, B, goal)
    big = mmd is not None and observed >= mmd
    if lo > 0 and (big or mmd is None):
        grade = "strong"
    elif lo > 0 or (big and p < 0.10):
        grade = "moderate"
    elif hi < 0:
        grade = "worse"
    elif observed > 0:
        grade = "weak"
    else:
        grade = "none"
    return {
        "grade": grade,
        "improvement": observed,
        "ci95": [lo, hi],
        "p_one_sided": p,
        "mmd": mmd,
        "n": [len(a), len(b)],
    }


def evaluate(target_metric: str, goal: str, blocks: list[dict]) -> dict:
    if goal not in VALID_GOALS:
        raise ValueError(f"goal {goal!r} は使えません")
    by_kind: dict[str, list[dict]] = {"baseline": [], "intervention": [], "retention": []}
    for b in blocks:
        if b.get("kind") in by_kind:
            by_kind[b["kind"]].extend(b.get("shots") or [])

    values, estimated = {}, 0
    for kind, shots in by_kind.items():
        v, e = _values(shots, target_metric)
        values[kind] = v
        estimated += e

    mmd = config.MMD.get(target_metric)
    result = {
        "engine_version": config.ENGINE_VERSION,
        "target_metric": target_metric,
        "goal": goal,
        "blocks": {k: stats.describe(v) for k, v in values.items() if by_kind[k]},
        "intervention_vs_baseline": _compare(values["baseline"], values["intervention"], goal, mmd),
        "notes": [],
    }
    if by_kind["retention"]:
        result["retention_vs_baseline"] = _compare(values["baseline"], values["retention"], goal, mmd)
    if not by_kind["baseline"] or not by_kind["intervention"]:
        result["notes"].append("基準（baseline）と介入（intervention）の両方のブロックが要ります")
    if estimated:
        result["notes"].append(f"{estimated} 球で {target_metric} が計測器の推定値です。判定は弱めに読んでください")
    if mmd is None:
        result["notes"].append(f"{target_metric} の意味のある差（MMD）が未設定のため、区間だけで判定しています")
    return result
