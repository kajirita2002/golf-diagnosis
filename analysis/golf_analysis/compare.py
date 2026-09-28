"""2つのセッションの比較 ――「今日のスライスは、昨日と何が違ったせいか」。

因果の階層（docs/DESIGN.md §1）どおりに2段で見る。
  L0（球筋）で何が変わったか → それを L1（インパクト）のどれの変化が説明するか。
L1 が変わっていないのに L0 だけ変わった、も立派な答え（打点・計測・風など）。

「変わった」と言うのは、平均の差の 95% 区間が 0 をまたがず、かつ差が MMD 以上のときだけ。
"""

from __future__ import annotations

from collections import Counter, defaultdict

import numpy as np

from . import config, stats

L0_METRICS = ["side", "spin_axis", "launch_direction", "carry", "launch_angle", "spin_rate"]

# L0 の変化を説明しうる L1（物理の関係から決める。当てずっぽうに全部見ない）
EXPLAINERS = {
    "spin_axis": ["face_to_path", "impact_offset"],
    "side": ["face_angle", "face_to_path", "club_path", "impact_offset"],
    "launch_direction": ["face_angle", "club_path"],
    "carry": ["club_speed", "smash_factor", "dynamic_loft", "attack_angle", "impact_height"],
    "launch_angle": ["dynamic_loft", "attack_angle"],
    "spin_rate": ["spin_loft", "club_speed", "impact_height"],
}

COMPARE_L1 = sorted({m for ms in EXPLAINERS.values() for m in ms})


def _vals(shots: list[dict], key: str) -> list[float]:
    return [float(s["metrics"][key]) for s in shots if (s.get("metrics") or {}).get(key) is not None]


def _diff(a: list[float], b: list[float], metric: str) -> dict:
    out = {"a": stats.describe(a), "b": stats.describe(b)}
    if len(a) < config.MIN_BLOCK_N or len(b) < config.MIN_BLOCK_N:
        out["status"] = "insufficient"
        return out
    A, B = np.asarray(a), np.asarray(b)
    stat = lambda x, y: float(np.mean(y) - np.mean(x))  # noqa: E731
    lo, hi = stats.bootstrap_ci(A, B, stat)
    d = stat(A, B)
    mmd = config.MMD.get(metric)
    changed = (lo > 0 or hi < 0) and (mmd is None or abs(d) >= mmd)
    out.update(
        {
            "status": "changed" if changed else "same",
            "diff": d,
            "ci95": [lo, hi],
            "mmd": mmd,
            "size": (abs(d) / mmd) if mmd else None,  # MMD の何倍か
        }
    )
    return out


def _club(a: list[dict], b: list[dict]) -> dict:
    l0 = {m: _diff(_vals(a, m), _vals(b, m), m) for m in L0_METRICS}
    l1 = {m: _diff(_vals(a, m), _vals(b, m), m) for m in COMPARE_L1}
    l0 = {k: v for k, v in l0.items() if v["a"]["n"] or v["b"]["n"]}
    l1 = {k: v for k, v in l1.items() if v["a"]["n"] or v["b"]["n"]}

    explanations = []
    for m, r in l0.items():
        if r["status"] != "changed":
            continue
        cands = []
        for e in EXPLAINERS.get(m, []):
            er = l1.get(e)
            if er and er["status"] == "changed":
                cands.append({"metric": e, "diff": er["diff"], "size": er["size"]})
        cands.sort(key=lambda c: -(c["size"] or 0))
        missing = [e for e in EXPLAINERS.get(m, []) if e not in l1 or l1[e]["status"] == "insufficient"]
        explanations.append(
            {
                "outcome": m,
                "diff": r["diff"],
                "explained_by": cands,
                # 説明できる L1 が1つも変わっていないなら「インパクト以外」（打点が無い・計測・風）
                "unexplained": not cands,
                "unmeasured": missing,
            }
        )

    def causes(shots):
        return dict(Counter((s.get("decomposition") or {}).get("curve_cause", "unknown") for s in shots))

    return {
        "n": [len(a), len(b)],
        "l0": l0,
        "l1": l1,
        "explanations": explanations,
        "curve_causes": {"a": causes(a), "b": causes(b)},
    }


def compare_sessions(a: list[dict], b: list[dict]) -> dict:
    """a = 比べる元（昨日）、b = 今回（今日）。差はすべて b − a。"""
    by_a: dict[str, list[dict]] = defaultdict(list)
    by_b: dict[str, list[dict]] = defaultdict(list)
    for s in a:
        if not s.get("excluded"):
            by_a[s.get("club") or "(クラブ不明)"].append(s)
    for s in b:
        if not s.get("excluded"):
            by_b[s.get("club") or "(クラブ不明)"].append(s)
    common = sorted(set(by_a) & set(by_b), key=lambda c: -(len(by_a[c]) + len(by_b[c])))
    return {
        "engine_version": config.ENGINE_VERSION,
        "clubs": {c: _club(by_a[c], by_b[c]) for c in common},
        "only_in_a": sorted(set(by_a) - set(by_b)),
        "only_in_b": sorted(set(by_b) - set(by_a)),
    }
