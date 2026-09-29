"""範囲ごとの集計（profile）と、クラブをまたぐ傾向（cross_club）。

docs/DESIGN_coaching.md §5.1〜§5.3。解説レポート・理想・プランの数字は全部ここから来る
（同じ数字を節ごとに計算し直さない）。

- **ミスヒットの候補は集計から外し、外した数を必ず書く（R6）。** 例外は球筋の頻度（tendency）と
  ミスの内訳（miss_budget）で、起きた球なので候補も数え、候補の数を別に書く。
- **欠けは偶然ではない（R7）。** 一番悪い球ほど値が欠けるので、欠けた数と、その中の極端な打点の数を
  coverage に出す。
- 説明の割合は単回帰の R² だけ（R4）。区間は bootstrap の平均の 95% 区間で、「偶然ではない」の意味には使わない。
"""

from __future__ import annotations

from collections import Counter
from statistics import median

import numpy as np

from . import band as band_mod
from . import config, stats
from .shots import curve_side, dec, is_extreme, is_thin, m, start_side

PROFILE_METRICS = ["face_angle", "club_path", "face_to_path", "impact_offset", "attack_angle"]
# bias の向きの名前（+ の向き, - の向き）。打点は + がトゥ、- がヒール。
DIRECTION_NAMES = {"impact_offset": ("toe", "heel"), "attack_angle": ("up", "down")}
STARTS = ["push", "straight", "pull"]
CURVES = ["right", "none", "left"]


def _dir_names(metric: str) -> tuple[str, str]:
    return DIRECTION_NAMES.get(metric, ("right", "left"))


# ---------------------------------------------------------------- l1


def metric_summary(values: list[float], metric: str, mishit_excluded: int = 0) -> dict:
    """n / 平均 / 中央値 / SD / 範囲 / 平均の95%区間 / bias / spread。"""
    v = np.asarray(values, dtype=float)
    out: dict = {"metric": metric, "n": int(v.size), "mishit_excluded": mishit_excluded}
    if v.size == 0:
        return out
    out.update(mean=float(v.mean()), median=float(np.median(v)), min=float(v.min()), max=float(v.max()))
    out["q1"], out["q3"] = (float(x) for x in np.percentile(v, [25, 75]))
    mmd = config.MMD.get(metric)
    out["mmd"] = mmd
    if v.size >= 2:
        out["sd"] = float(v.std(ddof=1))
        if mmd:
            ratio = out["sd"] / mmd
            out["spread"] = "stable" if ratio <= config.SPREAD_STABLE else ("wide" if ratio >= config.SPREAD_WIDE else "some")
    ci = stats.bootstrap_mean_ci(v) if v.size >= 3 else None
    out["ci95"] = list(ci) if ci else None
    out["bias"] = None
    if ci and mmd is not None and abs(out["mean"]) >= mmd and (ci[0] > 0 or ci[1] < 0):
        pos, neg = _dir_names(metric)
        out["bias"] = pos if out["mean"] > 0 else neg
    return out


def l1_summary(shots: list[dict], skip: set) -> dict:
    out = {}
    for key in PROFILE_METRICS:
        vals = [m(s, key) for s in shots if s["id"] not in skip and m(s, key) is not None]
        excl = sum(1 for s in shots if s["id"] in skip and m(s, key) is not None)
        out[key] = metric_summary(vals, key, excl)
    return out


# ---------------------------------------------------------------- tendency


def breakdown_cause(s: dict) -> str:
    """9分類の枠の中の原因。極端な打点を先に見る（#18 はフェースが取れていても打点の球）。"""
    if is_extreme(s):
        return "extreme_strike"
    if is_thin(s):
        return "thin"
    c = dec(s).get("curve_cause") or "unknown"
    return c


def tendency(shots: list[dict], skip: set) -> dict:
    """打ち出しと曲がりの件数・9分類・一番多い枠とその原因の内訳。候補も数える（R6 の例外）。"""
    start = Counter(start_side(s) for s in shots)
    curve = Counter(curve_side(s) for s in shots)
    kinds = Counter(dec(s).get("curve") for s in shots if curve_side(s) in ("right", "left"))
    cells = []
    for st in STARTS:
        for cv in CURVES:
            members = [s for s in shots if start_side(s) == st and curve_side(s) == cv]
            br = Counter(breakdown_cause(s) for s in members)
            cells.append(
                {
                    "key": f"{st}|{cv}",
                    "start": st,
                    "curve": cv,
                    "n": len(members),
                    "shot_ids": [s["id"] for s in members],
                    "seqs": [s["seq"] for s in members],
                    # 極端な打点を先に（一番大きく外れる球なので）、あとは多い順
                    "breakdown": [
                        {"cause": c, "n": k, "seqs": [s["seq"] for s in members if breakdown_cause(s) == c]}
                        for c, k in sorted(br.items(), key=lambda ck: (ck[0] != "extreme_strike", -ck[1]))
                    ],
                }
            )
    ranked = [c for c in cells if c["key"] != "straight|none" and c["n"] > 0]
    ranked.sort(key=lambda c: -c["n"])
    # 同数の枠があれば「一番多い」と言い切らない（並びの順で1つを選ぶと、右打ちと左打ちで結論が入れ替わる。R4・R9）
    tie = [c["key"] for c in ranked if ranked and c["n"] == ranked[0]["n"]]
    top = ranked[0] if ranked and len(tie) == 1 else None
    sides = [m(s, "side") for s in shots if m(s, "side") is not None]
    return {
        "n": len(shots),
        "mishit_included": sum(1 for s in shots if s["id"] in skip),
        "start": {k: start.get(k, 0) for k in STARTS + ["unknown"]},
        "curve": {
            "measured": sum(curve.get(k, 0) for k in CURVES),
            "curved": curve.get("right", 0) + curve.get("left", 0),
            **{k: curve.get(k, 0) for k in CURVES + ["unknown"]},
            **{k: kinds.get(k, 0) for k in ("fade", "slice", "draw", "hook")},
        },
        "cells": cells,
        "cell_unknown": sum(1 for s in shots if start_side(s) == "unknown" or curve_side(s) == "unknown"),
        "top_cell": top["key"] if top else None,
        # 一番多い枠が同数で2つ以上あるときの枠（top_cell は None）。3つ以上なら①で一番多い枠を出さない
        "top_tie": tie if len(tie) >= 2 else [],
        "side_median_m": float(median(sides)) if sides else None,
        "side_n": len(sides),
    }


# ---------------------------------------------------------------- sensitivity


def sensitivity(shots: list[dict], skip: set, outcome: str) -> dict:
    """左右（まとめは side_pct）をフェースとパスで回帰した生の係数と、それぞれの単回帰の R²。
    share は出さない（R² の落ち幅を配ったもので、分散の割合ではない）。"""

    def y_of(s):
        side, carry = m(s, "side"), m(s, "carry")
        if outcome == "side_pct":
            return None if side is None or not carry else side / carry
        return side

    rows = [s for s in shots if s["id"] not in skip and y_of(s) is not None and m(s, "face_angle") is not None and m(s, "club_path") is not None]
    base = {"outcome": outcome, "n": len(rows), "skipped": sum(1 for s in shots if s["id"] in skip)}
    if len(rows) < config.MIN_DRIVER_N:
        return {"status": "insufficient", "needed": config.MIN_DRIVER_N, **base}
    y = np.array([y_of(s) for s in rows])
    face = np.array([m(s, "face_angle") for s in rows])
    path = np.array([m(s, "club_path") for s in rows])
    cf, cp = stats.ols_coef(y, np.column_stack([face, path]))
    carries = [m(s, "carry") for s in rows if m(s, "carry") is not None]
    return {
        "status": "ok",
        **base,
        "coef": {"face_angle": cf, "club_path": cp},
        "r2_single": {"face_angle": stats.simple_r2(y, face), "club_path": stats.simple_r2(y, path)},
        "carry_median_m": float(median(carries)) if carries else None,
    }


# ---------------------------------------------------------------- strike / coverage


def strike(shots: list[dict], skip: set) -> dict:
    use = [s for s in shots if s["id"] not in skip]
    vals = [m(s, "impact_offset") for s in use if m(s, "impact_offset") is not None]
    contact = Counter(dec(s).get("contact") for s in use if m(s, "impact_offset") is not None)
    return {
        "n": len(use),
        "measured": len(vals),
        "missing": len(use) - len(vals),
        "missing_seqs": [s["seq"] for s in use if m(s, "impact_offset") is None],
        "mishit_excluded": len(shots) - len(use),
        "median_m": float(median(vals)) if vals else None,
        "mean_m": float(np.mean(vals)) if vals else None,
        "heel": contact.get("heel", 0) + contact.get("heel_extreme", 0),
        "center": contact.get("center", 0),
        "toe": contact.get("toe", 0) + contact.get("toe_extreme", 0),
        "heel_extreme": contact.get("heel_extreme", 0),
        "toe_extreme": contact.get("toe_extreme", 0),
        "heel_extreme_seqs": [s["seq"] for s in use if dec(s).get("contact") == "heel_extreme"],
        "toe_extreme_seqs": [s["seq"] for s in use if dec(s).get("contact") == "toe_extreme"],
    }


def _mean_abs_side(xs: list[dict]) -> float | None:
    v = [abs(m(x, "side")) for x in xs if m(x, "side") is not None]
    return float(np.mean(v)) if v else None


def coverage(shots: list[dict], skip: set) -> dict:
    """欠けの数（R7）。一番悪い球ほど欠けるので、欠けた球の中の極端な打点も数える。"""
    use = [s for s in shots if s["id"] not in skip]
    no_face = [s for s in use if m(s, "face_angle") is None]
    ext = [s for s in use if is_extreme(s)]
    rest = [s for s in use if not is_extreme(s)]
    path_vals = [m(s, "club_path") for s in ext if m(s, "club_path") is not None]
    path_others = [m(s, "club_path") for s in rest if m(s, "club_path") is not None]
    curved = [s for s in shots if curve_side(s) in ("right", "left")]  # 起きた球なので候補も数える
    return {
        "n": len(use),
        "mishit_excluded": len(shots) - len(use),
        "face": {"n": len(use) - len(no_face), "missing": len(no_face), "missing_extreme": sum(1 for s in no_face if is_extreme(s)), "missing_seqs": [s["seq"] for s in no_face]},
        "path": {"n": sum(1 for s in use if m(s, "club_path") is not None), "missing": sum(1 for s in use if m(s, "club_path") is None)},
        "strike": {"n": sum(1 for s in use if m(s, "impact_offset") is not None), "missing": sum(1 for s in use if m(s, "impact_offset") is None)},
        "curve": {"n": sum(1 for s in use if curve_side(s) != "unknown"), "missing": sum(1 for s in use if curve_side(s) == "unknown")},
        "curved_no_strike": {"n": sum(1 for s in curved if m(s, "impact_offset") is None), "of": len(curved)},
        "extreme": {
            "n": len(ext),
            "seqs": [s["seq"] for s in ext],
            "with_face": sum(1 for s in ext if m(s, "face_angle") is not None),
            "with_path": len(path_vals),
            "path_min": min(path_vals) if path_vals else None,
            "path_max": max(path_vals) if path_vals else None,
            "path_others_min": min(path_others) if path_others else None,
            "path_others_max": max(path_others) if path_others else None,
            "mean_abs_side_m": _mean_abs_side(ext),
            "mean_abs_side_others_m": _mean_abs_side(rest),
        },
    }


# ---------------------------------------------------------------- miss_budget


def _excess(s: dict) -> float | None:
    side, carry = m(s, "side"), m(s, "carry")
    if side is None or carry is None:
        return None
    return max(0.0, abs(side) - band_mod.lim_of(s.get("club_category") or "unknown", carry))


BUDGET_CAUSES = ["extreme_only", "extreme_and_face", "thin", "face_to_path", "strike", "mixed", "start", "unknown"]


def budget_cause(s: dict) -> str:
    """はみ出しの原因。上から順に割り当てる（§5.3）。"""
    d = dec(s)
    if is_extreme(s):
        ftp = m(s, "face_to_path")
        if m(s, "face_angle") is not None and ftp is not None and abs(ftp) >= 2 * config.MMD["face_to_path"]:
            return "extreme_and_face"
        return "extreme_only"
    if is_thin(s):
        return "thin"
    c = d.get("curve_cause")
    if c in ("face_to_path", "strike", "mixed"):
        return c
    if c == "none" and start_side(s) in ("push", "pull"):
        return "start"
    return "unknown"


def miss_budget(shots: list[dict], skip: set) -> dict:
    """はみ出した距離の内訳。候補も数える（起きた球）。球数ははみ出しが0より大きい球だけ。"""
    rows = []
    no_side = []
    for s in shots:
        e = _excess(s)
        if e is None:
            no_side.append(s["seq"])
            continue
        rows.append((s, e))
    total = sum(e for _, e in rows)
    by = []
    for cause in BUDGET_CAUSES:
        over = [(s, e) for s, e in rows if e > 0 and budget_cause(s) == cause]
        if not over:
            continue
        item = {
            "cause": cause,
            "n": len(over),
            "excess_m": sum(e for _, e in over),
            "shot_ids": [s["id"] for s, _ in over],
            "seqs": [s["seq"] for s, _ in over],
            "mishit": sum(1 for s, _ in over if s["id"] in skip),
        }
        if cause == "face_to_path":
            ns = [(s, e) for s, e in over if m(s, "impact_offset") is None]
            item["no_strike"] = {"n": len(ns), "excess_m": sum(e for _, e in ns), "seqs": [s["seq"] for s, _ in ns]}
        by.append(item)
    ext_only = next((b["excess_m"] for b in by if b["cause"] == "extreme_only"), 0.0)
    ext_face = next((b["excess_m"] for b in by if b["cause"] == "extreme_and_face"), 0.0)
    return {
        "total_m": total,
        "n_side": len(rows),
        "n_over": sum(1 for _, e in rows if e > 0),
        "no_side_seqs": no_side,
        "mishit_included": sum(1 for s in shots if s["id"] in skip),
        "by_cause": by,
        # 打点のぶんは数える順で変わるので幅で言う（extreme_only 〜 extreme_only + extreme_and_face）
        "strike_range_m": [ext_only, ext_only + ext_face] if (ext_only or ext_face) else None,
        "per_shot": [{"shot_id": s["id"], "seq": s["seq"], "excess_m": e, "cause": budget_cause(s)} for s, e in rows],
    }


# ---------------------------------------------------------------- representative / good


def representative(shots: list[dict], skip: set, top_key: str | None) -> dict | None:
    """典型の1球: 一番多い枠の球のうち、極端な打点ではなく、フェースとパスがある球の medoid。"""
    if not top_key:
        return None
    st, cv = top_key.split("|")
    pool = [
        s for s in shots
        if s["id"] not in skip and start_side(s) == st and curve_side(s) == cv and not is_extreme(s)
        and m(s, "face_angle") is not None and m(s, "club_path") is not None
    ]
    if not pool:
        return None
    pts = np.array([[m(s, "face_angle"), m(s, "club_path")] for s in pool])
    dist = np.sqrt(((pts[:, None, :] - pts[None, :, :]) ** 2).sum(-1)).sum(1)
    s = pool[int(np.argmin(dist))]
    d = dec(s)
    return {
        "shot_id": s["id"], "seq": s["seq"], "club": s.get("club"), "cell": top_key, "n_pool": len(pool),
        **{k: m(s, k) for k in ("face_angle", "club_path", "face_to_path", "launch_direction", "spin_axis", "side", "carry", "impact_offset")},
        "predicted_launch_direction": d.get("predicted_launch_direction"),
        "predicted_axis_from_face_to_path": d.get("predicted_axis_from_face_to_path"),
    }


def good_reference(shots: list[dict], good_by_id: dict, carry_medians: dict, band_status: list[dict]) -> dict:
    """Good の球（注記だけ）と、キャリーの判定が抜けた Good（lenient）。理想は L1 で選ぶ（§2.2）。"""
    good = [s for s in shots if (good_by_id.get(s["id"]) or {}).get("good")]
    lenient = [
        s for s in good
        if good_by_id[s["id"]].get("by") == "auto" and m(s, "carry") is not None and carry_medians.get(s.get("club")) is None
    ]
    in_ids = {b["shot_id"] for b in band_status if b.get("status") == "in"}
    l1_good = [s for s in shots if s["id"] in in_ids and dec(s).get("contact") == "center"]
    return {
        "n": len(shots),
        "good_seqs": [s["seq"] for s in good],
        "lenient": [s["seq"] for s in lenient],
        "l1_good_seqs": [s["seq"] for s in l1_good],
        "l1_good_enough": len(l1_good) >= config.GOOD_REF_MIN_N,
        "l1_good_needed": config.GOOD_REF_MIN_N,
    }


# ---------------------------------------------------------------- band（範囲ごとの数え上げ）


def band_summary(shots: list[dict], skip: set, bands: dict, category: str) -> dict:
    b = bands.get(category) or {}
    statuses = [band_mod.shot_status(s, bands, skip) for s in shots]
    counted = [x for x in statuses if x["counted"]]
    ins = [x for x in counted if x["status"] == "in"]
    outs = [x for x in counted if x["status"] != "in"]
    right = sum(1 for x in outs if x["status"] == "out_right")
    left = len(outs) - right

    def sd(xs):
        v = [x["side"] for x in xs if x.get("side") is not None]
        return float(np.std(v, ddof=1)) if len(v) >= 2 else None

    out = {
        "used": bool(b.get("usable")),
        "reason": b.get("reason") if b else "no_category",
        "category": category,
        "k": b.get("k"),
        "k_source": b.get("k_source"),
        "k_own": b.get("k_own"),
        "n_fit": b.get("n_fit"),
        "resid_sd_m": b.get("resid_sd") if b.get("usable") else b.get("resid_sd_own"),
        "n_resid": b.get("n_resid"),
        "half_width_m": b.get("half_width_m"),
        "pooled": b.get("pooled"),
        "counted": len(counted),
        "in": len(ins),
        "out_right": right,
        "out_left": left,
        "not_counted": dict(Counter(x["reason"] for x in statuses if not x["counted"])),
        "in_landed": sum(1 for x in ins if x.get("landed_in")),
        "out_landed": sum(1 for x in outs if x.get("landed_in")),
        "in_side_sd_m": sd(ins),
        "out_side_sd_m": sd(outs),
        "statuses": statuses,
    }
    out["ideal_type"] = ideal_type(out)
    out["stage"] = stage_goal(out)
    return out


def ideal_type(b: dict) -> dict | None:
    """直し方の型（§7.3）。帯の判定ができた球が10球以上のときだけ文にする。"""
    if not b["used"] or b["counted"] == 0:
        return None
    outs = b["out_right"] + b["out_left"]
    shown = b["counted"] >= config.IDEAL_TYPE_MIN_N
    if outs == 0:
        return {"kind": "all_in", "side": None, "shown": shown}
    side = "right" if b["out_right"] >= b["out_left"] else "left"
    same = max(b["out_right"], b["out_left"]) / outs
    in_share = b["in"] / b["counted"]
    if same >= config.IDEAL_SAME_SIDE and in_share >= config.IDEAL_IN_SHARE:
        kind = "trim"
    elif same >= config.IDEAL_SAME_SIDE:
        kind = "shift"
    else:
        kind = "scatter"
    return {"kind": kind, "side": side, "same_side_share": same, "in_share": in_share, "shown": shown}


def stage_goal(b: dict) -> dict | None:
    """段の目標の規則。閾値（帯）は動かさず「何球入れるか」だけ動かす（§7.3）。

    **物差しはプランの1回目の A**（診断した日の値ではない。平均への回帰。§2.2）。だからここでは今日の値から
    次の目標の数を作らない。今日の件数は「きっかけ」として返すだけで、目標は規則（1段の幅・ゴール）だけを返す。
    帯の判定ができた球が IDEAL_TYPE_MIN_N 球に満たないときは出さない（型の文と同じ）。
    今日すでにゴールに届いているなら、次の段は作らない（目標が今日より下がらないように）。
    """
    if not b["used"] or b["counted"] == 0:
        return None
    now = b["in"] / b["counted"]
    return {
        "now_in": b["in"],
        "now_counted": b["counted"],
        "now_share": now,
        "shown": b["counted"] >= config.IDEAL_TYPE_MIN_N,
        "reached": now >= config.IDEAL_SHARE_GOAL,
        "goal_share": config.IDEAL_SHARE_GOAL,
        "step": config.IDEAL_STEP,
    }


# ---------------------------------------------------------------- まとめ


def launch_residual_sd(shots: list[dict], skip: set) -> dict | None:
    v = [dec(s).get("launch_direction_residual") for s in shots if s["id"] not in skip]
    v = [x for x in v if x is not None]
    if len(v) < 2:
        return None
    return {"value": float(np.std(v, ddof=1)), "n": len(v), "below_info": float(np.std(v, ddof=1)) < config.LAUNCH_RESID_INFO_DEG}


def build_profile(
    shots: list[dict], *, scope: str, scope_id: str, category: str, skip: set, good_by_id: dict,
    bands: dict, carry_medians: dict, drivers: dict | None = None,
) -> dict:
    shots = sorted(shots, key=lambda s: s["seq"])
    skip = {s["id"] for s in shots if s["id"] in skip}
    tend = tendency(shots, skip)
    bs = band_summary(shots, skip, bands, category)
    carries = [m(s, "carry") for s in shots if s["id"] not in skip and m(s, "carry") is not None]
    sls = [m(s, "spin_loft") for s in shots if s["id"] not in skip and m(s, "spin_loft") is not None and not is_thin(s)]
    return {
        "scope": scope,
        "scope_id": scope_id,
        "category": category,
        "n": len(shots),
        "mishit_excluded": len(skip),
        "mishit_seqs": sorted(s["seq"] for s in shots if s["id"] in skip),
        "carry_median_m": float(median(carries)) if carries else None,
        "spin_loft_median": float(median(sls)) if sls else None,
        "tendency": tend,
        "l1": l1_summary(shots, skip),
        "sensitivity": sensitivity(shots, skip, "side_pct" if scope == "group" else "side"),
        "band": bs,
        "strike": strike(shots, skip),
        "coverage": coverage(shots, skip),
        "miss_budget": miss_budget(shots, skip),
        "representative": representative(shots, skip, tend["top_cell"]),
        "good_reference": good_reference(shots, good_by_id, carry_medians, bs["statuses"]),
        "launch_residual_sd": launch_residual_sd(shots, skip),
    }


# ---------------------------------------------------------------- cross_club

CROSS_METRICS = ["face_angle", "club_path", "impact_offset"]


def cross_units(clubs: list[dict], groups: list[dict]) -> list[dict]:
    """比べる単位: 種類ごとに1つ（まとめがあればまとめ）。"""
    grouped = {n for g in groups for n in g["clubs"]}
    return list(groups) + [c for c in clubs if c["club"] not in grouped and c["club_category"] not in ("unknown", "putter")]


def cross_club(clubs: list[dict], groups: list[dict]) -> dict:
    """クラブをまたぐ傾向（§5.1）。

    common: 同じ向きの bias が2つ以上の単位にある / same_direction_unclear: 平均の向きは同じだが区間が0をまたぐ /
    different_type: 逆向きの bias、または別の指標にだけ bias。
    「〜だけ」とは言わない（ほかで区間が0をまたぐのは、無いことの証拠ではない）。
    """
    units = []
    for u in cross_units(clubs, groups):
        p = u["profile"]
        if p["n"] - p["mishit_excluded"] < config.MIN_CROSS_N:
            continue
        units.append(u)

    def unit_view(u, metric):
        p = u["profile"]
        l1 = p["l1"][metric]
        v = {
            "scope_id": p["scope_id"], "label": u.get("name") or u.get("club"), "category": p["category"],
            # mishit_excluded はその指標を測れた候補の数、mishit_total は範囲の候補の総数（文は総数で書く）
            "n": l1["n"], "mishit_excluded": l1["mishit_excluded"], "mishit_total": p["mishit_excluded"],
            "mean": l1.get("mean"), "median": l1.get("median"),
            "q1": l1.get("q1"), "q3": l1.get("q3"), "ci95": l1.get("ci95"), "bias": l1.get("bias"),
        }
        if metric == "impact_offset" and l1.get("mean") is not None:
            v["center_inside"] = abs(l1["mean"]) < config.CENTER_STRIKE_M
        return v

    entries = []
    for metric in CROSS_METRICS:
        views = [unit_view(u, metric) for u in units if u["profile"]["l1"][metric]["n"] >= config.MIN_CROSS_N]
        pos, neg = _dir_names(metric)
        for direction, sign in ((pos, 1), (neg, -1)):
            biased = [v for v in views if v["bias"] == direction]
            opposite = [v for v in views if v["bias"] == (neg if sign == 1 else pos)]
            if len(biased) >= 2:
                entries.append({"metric": metric, "direction": direction, "kind": "common", "units": biased})
                unclear = [v for v in views if v["bias"] is None and v["mean"] is not None and v["mean"] * sign > 0]
                if unclear:
                    entries.append({"metric": metric, "direction": direction, "kind": "same_direction_unclear", "units": unclear})
            elif len(biased) == 1 and len(views) >= 2:
                # 比べる相手（その指標を測れたほかの単位）が無ければ「型が違う」とは言えない
                reason = "opposite_bias" if opposite else "no_bias_elsewhere"
                entries.append({"metric": metric, "direction": direction, "kind": "different_type", "reason": reason, "units": biased})
    return {
        "units": [{"scope_id": u["profile"]["scope_id"], "label": u.get("name") or u.get("club"), "n": u["profile"]["n"], "mishit_excluded": u["profile"]["mishit_excluded"]} for u in units],
        "entries": entries,
    }
