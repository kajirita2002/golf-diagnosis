"""セッション全体の分析。

1. クラブごとに Good を自動で判定する（人の上書きが優先）。
2. 球筋のグループ（ミスの型 × 曲がりの原因）に分ける。
   球筋だけで分けず、Go が物理で出した「原因」でも分ける ―― 同じプッシュフェードでも
   フェースが原因の球と打点が原因の球は別の話（docs/DESIGN.md ❾）。
3. インパクト（L1）の指標のばらつきと、着地の左右のばらつきを何が説明するか。
4. 言えることだけを findings に出す。球が足りなければ「データ不足」と言う。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median

import numpy as np

from . import config, stats


def _m(shot: dict, key: str):
    return (shot.get("metrics") or {}).get(key)


def judge_good(shot: dict, carry_median: float | None, category: str) -> dict:
    """1球の Good 判定。人の上書きがあればそれを使う。"""
    if shot.get("good_override") is not None:
        return {"good": bool(shot["good_override"]), "by": "override", "failed": []}
    t = config.GOOD_TARGETS.get(category, config.DEFAULT_TARGET)
    failed: list[str] = []
    checked = 0

    side, carry, ld = _m(shot, "side"), _m(shot, "carry"), _m(shot, "launch_direction")
    if side is not None and carry is not None:
        checked += 1
        if abs(side) > max(t["side_min_m"], t["side_pct"] * carry):
            failed.append("side")
    else:
        # 着地の左右が無ければ、Go の分解（打ち出しと曲がり）で代わりに見る
        dec = shot.get("decomposition") or {}
        if dec.get("curve") not in (None, "unknown"):
            checked += 1
            if dec["curve"] in ("slice", "hook"):
                failed.append("curve")
    if ld is not None:
        checked += 1
        if abs(ld) > t["start_deg"]:
            failed.append("start_line")
    if carry is not None and carry_median:
        checked += 1
        if abs(carry - carry_median) > t["carry_pct"] * carry_median:
            failed.append("carry")
    if checked == 0:
        return {"good": None, "by": "auto", "failed": [], "reason": "判定に使える値がありません"}
    return {"good": not failed, "by": "auto", "failed": failed}


def _drivers(shots: list[dict]) -> dict:
    """着地の左右（side）のばらつきを、フェース・パス・打点のどれが説明するか。"""
    predictors = ["face_angle", "club_path"]
    with_offset = [s for s in shots if _m(s, "impact_offset") is not None]
    if shots and len(with_offset) >= 0.8 * len(shots):
        predictors.append("impact_offset")
    rows = [s for s in shots if _m(s, "side") is not None and all(_m(s, p) is not None for p in predictors)]
    if len(rows) < config.MIN_DRIVER_N:
        return {
            "status": "insufficient",
            "n": len(rows),
            "needed": config.MIN_DRIVER_N,
            "outcome": "side",
            "predictors": predictors,
        }
    y = np.array([_m(s, "side") for s in rows], dtype=float)
    X = np.array([[_m(s, p) for p in predictors] for s in rows], dtype=float)
    res = stats.ols_contributions(y, X, predictors)
    return {"status": "ok", "n": len(rows), "outcome": "side", "predictors": predictors, **res}


def _findings(club: str, shots: list[dict], groups: list[dict], drivers: dict) -> list[dict]:
    """言えることだけを並べる。強さは件数と割合で示し、確率のふりをしない（DESIGN ❹）。"""
    out: list[dict] = []
    curved = [s for s in shots if (s.get("decomposition") or {}).get("curve") not in (None, "unknown", "straight")]
    causes = Counter((s.get("decomposition") or {}).get("curve_cause", "unknown") for s in curved)
    if len(curved) >= config.MIN_BLOCK_N:
        for cause in ("strike", "face_to_path"):
            ids = [s["id"] for s in curved if (s.get("decomposition") or {}).get("curve_cause") == cause]
            k = len(ids)
            share = k / len(curved)
            # 多数派の原因だけを出すと、少数でもまとまった群（例: 途中からヒールに
            # 当たり始めた5球）が埋もれる。群として数が揃っていれば別に出す。
            if share >= 0.7:
                strength = "strong"
            elif share >= 0.4 and k >= 3:
                strength = "moderate"
            elif k >= config.MIN_BLOCK_N:
                strength = "subset"
            else:
                continue
            out.append(
                {
                    "club": club,
                    "kind": "curve_cause",
                    "cause": cause,
                    "evidence": {"count": k, "of": len(curved)},
                    "strength": strength,
                    "shot_ids": ids,
                }
            )
        no_strike = sum(1 for s in curved if _m(s, "impact_offset") is None)
        if causes.get("strike", 0) + causes.get("mixed", 0) >= 3 and no_strike:
            out.append(
                {
                    "club": club,
                    "kind": "need_data",
                    "field": "impact_offset",
                    "evidence": {"missing": no_strike, "of": len(curved)},
                    "strength": "info",
                }
            )
    elif curved:
        out.append({"club": club, "kind": "insufficient", "what": "curve_cause", "n": len(curved), "needed": config.MIN_BLOCK_N})
    if drivers.get("status") == "ok" and drivers["contributions"]:
        top = drivers["contributions"][0]
        if top["share"] >= 0.5 and drivers["r2"] >= 0.5:
            out.append(
                {
                    "club": club,
                    "kind": "dispersion_driver",
                    "metric": top["metric"],
                    "evidence": {"share": top["share"], "r2": drivers["r2"], "n": drivers["n"]},
                    "strength": "strong" if top["share"] >= 0.7 else "moderate",
                }
            )
    return out


def analyze_session(shots: list[dict]) -> dict:
    by_club: dict[str, list[dict]] = defaultdict(list)
    excluded = 0
    for s in shots:
        if s.get("excluded"):
            excluded += 1
            continue
        by_club[s.get("club") or "(クラブ不明)"].append(s)

    clubs = []
    findings: list[dict] = []
    for club, cs in sorted(by_club.items(), key=lambda kv: -len(kv[1])):
        category = cs[0].get("club_category") or "unknown"
        carries = [_m(s, "carry") for s in cs if _m(s, "carry") is not None]
        carry_median = median(carries) if len(carries) >= 3 else None

        judged = []
        for s in cs:
            j = judge_good(s, carry_median, category)
            judged.append({"id": s["id"], "seq": s["seq"], **j})
        n_good = sum(1 for j in judged if j["good"])

        groups_map: dict[tuple[str, str], list[int]] = defaultdict(list)
        for s in cs:
            d = s.get("decomposition") or {}
            groups_map[(d.get("miss_type", "unknown"), d.get("curve_cause", "unknown"))].append(s["id"])
        groups = [
            {"miss_type": k[0], "curve_cause": k[1], "n": len(v), "shot_ids": v}
            for k, v in sorted(groups_map.items(), key=lambda kv: -len(kv[1]))
        ]

        variability = {}
        for key in config.L1_METRICS:
            vals = [_m(s, key) for s in cs if _m(s, key) is not None]
            if vals:
                variability[key] = stats.describe(vals)

        drivers = _drivers(cs)
        clubs.append(
            {
                "club": club,
                "club_category": category,
                "n": len(cs),
                "carry_median": carry_median,
                "good": {"n_good": n_good, "targets": config.GOOD_TARGETS.get(category, config.DEFAULT_TARGET), "shots": judged},
                "flight_groups": groups,
                "variability": variability,
                "dispersion_drivers": drivers,
            }
        )
        findings.extend(_findings(club, cs, groups, drivers))

    return {
        "engine_version": config.ENGINE_VERSION,
        "n_shots": len(shots),
        "n_excluded": excluded,
        "clubs": clubs,
        "findings": findings,
    }
