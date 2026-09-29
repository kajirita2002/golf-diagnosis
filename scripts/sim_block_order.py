"""ブロックの並べ方のシミュレーション（docs/DESIGN_coaching.md §8.3・§8.6）。

「効果が無いのに『効いた』に見える」割合を、ブロックの並べ方ごとに数える。文書に写す数字は必ずここから出す。

条件（ファイルの頭に固定する。変えたら文書の数字も出し直す）:
  - 指標: 打点（impact_offset・m）。平均 −21mm・SD 11.4mm（2026-09-17 のアイアン）。MMD 4mm。goal = reduce_abs
  - 球は正規乱数。球どうしは独立。
  - 「変化あり」: 介入の効果はゼロのまま、練習の中で打点が直線的に 4mm 良くなっていく
    （評価に入る球を打った順に並べ、最初の球 −21mm → 最後の球 −17mm）。準備とドリルの球は並びに入れない。
  - 判定は分析サービスの experiment._compare（本物と同じ bootstrap と並べ替え）。moderate 以上を「良くなった」と数える。
  - 並べ方: A10→B10 / A16→B16 / A-B-B-A（各8球）
  - 回数 REPS・乱数の種 SEED

使い方:  cd analysis && uv run python ../scripts/sim_block_order.py
テスト（analysis/tests/test_plan.py）は make_run と simulate をここから読む（条件を2か所に書かない）。
"""

from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "analysis"))

from golf_analysis.experiment import _compare  # noqa: E402

MEAN_M = -0.021
SD_M = 0.0114
MMD_M = 0.004
GOAL = "reduce_abs"
DRIFT_M = 0.004  # 練習の中で良くなる量（効果ではない）
REPS = 400
SEED = 20260929
CLUB = "9 Iron"

DESIGNS = {
    "A10-B10": [("baseline", 10), ("intervention", 10)],
    "A16-B16": [("baseline", 16), ("intervention", 16)],
    "A-B-B-A（各8球）": [("baseline", 8), ("intervention", 8), ("intervention", 8), ("baseline", 8)],
}
ABBA = DESIGNS["A-B-B-A（各8球）"]


def make_run(rng: np.random.Generator, design, effect_m: float = 0.0, drift_m: float = DRIFT_M, seq0: int = 1) -> list[dict]:
    """1回の練習のブロック（分析サービスの /v1/plan/evaluate の blocks の形）。

    effect_m は介入（B）の球だけ打点が芯へ寄る量。drift_m は並びの最初から最後までに芯へ寄る量（効果ではない）。"""
    total = sum(n for _, n in design)
    blocks, i, seq = [], 0, seq0
    for kind, n in design:
        shots = []
        for _ in range(n):
            mu = MEAN_M + (drift_m * i / (total - 1) if total > 1 else 0.0)
            if kind == "intervention":
                mu += effect_m
            v = float(rng.normal(mu, SD_M))
            shots.append({
                "id": seq, "seq": seq, "club": CLUB, "club_category": "iron",
                "metrics": {"impact_offset": v, "club_speed": float(rng.normal(37.0, 0.8)), "carry": float(rng.normal(118.0, 4.0)), "side": float(rng.normal(8.0, 10.0))},
                "decomposition": {"contact": "heel_extreme" if v < -0.030 else ("heel" if v < -0.010 else "center")},
            })
            i += 1
            seq += 1
        blocks.append({"kind": kind, "seq_from": shots[0]["seq"], "seq_to": shots[-1]["seq"], "shots": shots})
    return blocks


def _pool(blocks, kind):
    return [s["metrics"]["impact_offset"] for b in blocks if b["kind"] == kind for s in b["shots"]]


def simulate(design, drift_m: float, reps: int = REPS, seed: int = SEED) -> float:
    """効果ゼロで moderate 以上が出る割合。"""
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(reps):
        blocks = make_run(rng, design, 0.0, drift_m)
        g = _compare(_pool(blocks, "baseline"), _pool(blocks, "intervention"), GOAL, MMD_M)["grade"]
        hits += g in ("strong", "moderate")
    return hits / reps


def main() -> None:
    print(__doc__.split("使い方")[0].strip())
    print()
    print(f"{'並べ方':<18} {'変化なし':>8} {'変化あり（4mm）':>14}")
    for name, design in DESIGNS.items():
        a = simulate(design, 0.0)
        b = simulate(design, DRIFT_M)
        print(f"{name:<18} {a:>8.3f} {b:>14.3f}")


if __name__ == "__main__":
    main()
