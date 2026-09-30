"""誤差の予算を実測する仕組み（docs/DESIGN_v2.md §5.5・§15 段5）。

姿勢推定の項目は、誤差の予算を実測するまで「測れた（目安）」（`measured_approx`）で出す。
ここでは次の3つの「ばらつき」を集めて、項目ごとに初期の誤差（`config.CP_ANGLE_ERR_DEG` など）と比べる。

1. 同じ動画を2回処理した差（`measure_swing` の結果を2つ並べる。`repeat_samples`）
2. 同じコマを2回タップした差（画素。`tap_samples`）
3. 二つの物差し（ボールの直径と靴の長さ）の食い違い（割合。`scale_samples`）

**「目安」を外すかは人が決める。** `summarize` は候補を出すだけで、外す項目は
`checkpoints/budget.json` の `calibrated` に人が書き足す（書いた日と根拠つき）。
書いてある項目だけ、`measure._measure_item` が `measured_approx` ではなく `measured` を返す。

使い方（PC で）:
    uv run python -m golf_analysis.checkpoints.budget run_a.json run_b.json [--taps taps.json]
  run_*.json は `measure_swing` の結果（{"items": [...]}）か、その配列（同じ順のスイング）。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any

from .. import config

BUDGET_PATH = os.path.join(os.path.dirname(__file__), "budget.json")
# 候補にするのに要る組の数（少ないと偶然そろっただけの差になる。仮）
MIN_PAIRS = 10
_CAL: set[str] | None = None


def calibrated_items() -> set[str]:
    """人が「目安」を外すと決めた項目 id（budget.json の calibrated）。無ければ空。"""
    global _CAL
    if _CAL is None:
        try:
            with open(BUDGET_PATH, encoding="utf-8") as f:
                _CAL = {c["id"] for c in json.load(f).get("calibrated", [])}
        except FileNotFoundError:
            _CAL = set()
    return _CAL


def reset_cache() -> None:
    global _CAL
    _CAL = None


def assumed_err(unit: str | None) -> float | None:
    """いま使っている初期の誤差（§5.5）。単位で決まる。"""
    if unit == "deg":
        return config.CP_ANGLE_ERR_DEG
    if unit == "ball":
        return config.CP_POS_ERR_BALL
    if unit == "ratio":
        return config.CP_POS_ERR_L
    return None


def _items(run: Any) -> list[dict]:
    if isinstance(run, dict):
        return run.get("items") or []
    return run or []


def repeat_samples(runs_a: list, runs_b: list) -> dict[str, list[tuple[float, str]]]:
    """同じ動画を2回処理した結果の組 → {項目 id: [(差の大きさ, 単位)]}。
    どちらかで測れなかった項目・部分に分かれる項目（value.parts）は数えない。"""
    out: dict[str, list[tuple[float, str]]] = {}
    for a, b in zip(runs_a, runs_b):
        vb = {x["id"]: x.get("value") for x in _items(b)}
        for x in _items(a):
            va, v2 = x.get("value"), vb.get(x["id"])
            if not va or not v2 or va.get("v") is None or v2.get("v") is None:
                continue
            out.setdefault(x["id"], []).append((abs(va["v"] - v2["v"]), va.get("unit") or ""))
    return out


def tap_samples(pairs: list[dict]) -> list[float]:
    """同じコマを2回押した点の組 [{"a":[x,y],"b":[x,y],"ball_d":px}] → ボール何個ぶんの差か。"""
    out = []
    for p in pairs:
        d = p.get("ball_d")
        if not d:
            continue
        out.append(math.dist(p["a"], p["b"]) / d)
    return out


def scale_samples(pairs: list[dict]) -> list[float]:
    """二つの物差しの組 [{"ball_px":..,"shoe_px":..}] → 食い違いの割合（§5.4 の検査と同じ式）。"""
    out = []
    for p in pairs:
        b, s = p.get("ball_px"), p.get("shoe_px")
        if not b or not s:
            continue
        est = s / config.CP_SHOE_LEN_BALLS
        out.append(abs(est - b) / b)
    return out


def _p95(xs: list[float]) -> float:
    ys = sorted(xs)
    return ys[min(len(ys) - 1, math.ceil(0.95 * len(ys)) - 1)]


def summarize(samples: dict[str, list[tuple[float, str]]], taps: list[float] | None = None) -> dict:
    """項目ごとの実測のばらつき（95% の端）と、初期の誤差に収まっているか。
    `candidate` は「目安を外す候補」で、外すかどうかは人が budget.json に書いて決める。"""
    items = {}
    for iid, xs in sorted(samples.items()):
        unit = xs[0][1]
        ds = [d for d, _ in xs]
        p95 = _p95(ds)
        err = assumed_err(unit)
        items[iid] = {"n": len(ds), "unit": unit, "p95": round(p95, 3), "assumed": err,
                      "candidate": err is not None and len(ds) >= MIN_PAIRS and p95 <= err}
    out: dict[str, Any] = {"min_pairs": MIN_PAIRS, "items": items}
    if taps:
        out["taps"] = {"n": len(taps), "p95_ball": round(_p95(taps), 3), "assumed_ball": config.CP_POS_ERR_BALL,
                       "within": len(taps) >= MIN_PAIRS and _p95(taps) <= config.CP_POS_ERR_BALL}
    return out


def _load(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return d if isinstance(d, list) else [d]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="誤差の予算を実測した結果をまとめる（候補を出すだけ。決めるのは人）")
    ap.add_argument("run_a")
    ap.add_argument("run_b")
    ap.add_argument("--taps", help="同じコマを2回押した点の組（JSON の配列）")
    a = ap.parse_args(argv)
    taps = tap_samples(_load(a.taps)) if a.taps else None
    rep = summarize(repeat_samples(_load(a.run_a), _load(a.run_b)), taps)
    json.dump(rep, sys.stdout, ensure_ascii=False, indent=1)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
