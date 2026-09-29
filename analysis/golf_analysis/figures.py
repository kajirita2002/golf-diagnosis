"""図の中身（F1〜F7）。docs/DESIGN_coaching.md §5.6。

サーバーは点の座標・色の区分・注記の文だけを JSON で返し、画面（web/figures.js）が素の SVG で描く。
帯の多角形と窓の中央の1球は D-plane の式が要るので Go（physics.Band）が範囲の band_request を見て
band_shape を足す（図は band_ref で「その範囲の band_shape を使う」と示すだけ）。

座標はすべて右打ちの座標（+ が右）。左打ちの画面は左右を反転して描く（handedness を見る）。
打点のトゥ／ヒールは反転しない（F5 は目標側からフェースを見た形で、右打ちはヒールが右）。
"""

from __future__ import annotations

import numpy as np

from . import band as band_mod
from . import config
from .claims import dir_word
from .shots import dec, is_extreme, m

CAUSE_LABEL = {
    "extreme_only": "ネック・先端寄りだけ",
    "extreme_and_face": "ネック・先端寄り＋フェースも大きくずれた",
    "thin": "薄い当たり（トップ）",
    "face_to_path": "フェース・トゥ・パス",
    "strike": "打点のずれ",
    "mixed": "原因が1つに決まらない",
    "start": "打ち出しのずれ（曲がりは小さい）",
    "unknown": "原因が分からない",
}


def _mark(s: dict, status: dict | None, skip: set) -> str:
    if s["id"] in skip:
        return "mishit"
    if is_extreme(s):
        return "ext"
    if status and status.get("counted"):
        return "in" if status["status"] == "in" else "out"
    return "none"


CLASS = {"in": "g-good", "out": "g-miss", "ext": "g-ext", "mishit": "g-mishit", "none": "g-miss"}


def _point_base(s: dict, status: dict | None, skip: set, good_ids: set) -> dict:
    mk = _mark(s, status, skip)
    return {
        "shot_id": s["id"],
        "seq": s["seq"],
        "club": s.get("club"),
        "mark": mk,
        "class": CLASS[mk],
        "band": (status or {}).get("status") if (status or {}).get("counted") else None,
        "good": s["id"] in good_ids,
    }


def _ellipse(pts: list[tuple[float, float]]) -> dict | None:
    """候補を除いた点の共分散の 1SD 楕円（確率の範囲ではない）。"""
    if len(pts) < 3:
        return None
    a = np.asarray(pts, dtype=float)
    mu = a.mean(0)
    cov = np.cov(a.T, ddof=1)
    w, v = np.linalg.eigh(cov)
    order = np.argsort(w)[::-1]
    w, v = w[order], v[:, order]
    angle = float(np.degrees(np.arctan2(v[1, 0], v[0, 0])))
    return {
        "cx": float(mu[0]), "cy": float(mu[1]),
        "cov": [[float(cov[0, 0]), float(cov[0, 1])], [float(cov[1, 0]), float(cov[1, 1])]],
        "rx": float(np.sqrt(max(w[0], 0))), "ry": float(np.sqrt(max(w[1], 0))), "angle_deg": angle,
        "legend": "ばらつきの形（1SD）。確率の範囲ではありません",
    }


def f1(p: dict, shots: list[dict], skip: set, good_ids: set, carry_medians: dict) -> dict:
    status = {x["shot_id"]: x for x in p["band"]["statuses"]}
    group = p["scope"] == "group"
    pts, missing = [], []
    for s in shots:
        side, carry = m(s, "side"), m(s, "carry")
        if side is None or carry is None:
            missing.append(s["seq"])
            continue
        pt = _point_base(s, status.get(s["id"]), skip, good_ids)
        if group:
            base = carry_medians.get(s.get("club")) or p["carry_median_m"]
            pt.update(x=side / carry, y=carry / base if base else None, carry_base_from="club" if carry_medians.get(s.get("club")) else "scope")
        else:
            pt.update(x=side, y=carry)
        pts.append(pt)
    t = config.GOOD_TARGETS.get(p["category"], config.DEFAULT_TARGET)
    return {
        "id": "F1",
        "title": "着弾の散布と狙いの幅",
        "units": {"x": "side_ratio", "y": "carry_ratio"} if group else {"x": "m", "y": "m"},
        "axis_labels": {"x": "左右 ÷ キャリー", "y": "キャリー ÷ そのクラブの中央値"} if group else {"x": "左右（m）", "y": "キャリー（m）"},
        "target": {"side_pct": t["side_pct"], "side_min_m": None if group else t["side_min_m"]},
        "points": pts,
        "missing_seqs": missing,
        "ellipse": _ellipse([(pt["x"], pt["y"]) for pt in pts if pt["mark"] != "mishit" and pt["y"] is not None]),
        "band_used": p["band"]["used"],
        "legend": {"in": "インパクトが帯に入った球", "out": "入らなかった球", "ext": "ネック・先端寄り", "mishit": "ミスヒットの候補"},
    }


def f2(p: dict) -> dict:
    t = p["tendency"]
    return {
        "id": "F2",
        "title": "打ち出し×曲がりの9分類",
        "rows": ["push", "straight", "pull"],
        "cols": ["left", "none", "right"],
        "cells": [
            {"key": c["key"], "start": c["start"], "curve": c["curve"], "n": c["n"], "seqs": c["seqs"], "shot_ids": c["shot_ids"], "top": c["key"] == t["top_cell"] or (len(t.get("top_tie") or []) == 2 and c["key"] in t["top_tie"]), "breakdown": [{"cause": b["cause"], "n": b["n"]} for b in c["breakdown"]]}
            for c in t["cells"]
        ],
        "unknown": t["cell_unknown"],
        "n": t["n"],
        "mishit_included": t["mishit_included"],
    }


def f3(p: dict, shots: list[dict], skip: set, good_ids: set, band_ref: str | None, r2_line: str | None, missing_line: str, hand: str = "R") -> dict:
    status = {x["shot_id"]: x for x in p["band"]["statuses"]}
    pts = []
    for s in shots:
        face, path = m(s, "face_angle"), m(s, "club_path")
        if face is None or path is None:
            continue
        pt = _point_base(s, status.get(s["id"]), skip, good_ids)
        pt.update(x=path, y=face, warn=is_extreme(s))
        pts.append(pt)
    not_shown = [s for s in shots if m(s, "face_angle") is None or m(s, "club_path") is None]
    b = p["band"]
    reason = None
    if not b["used"]:
        reason = band_mod.reason_text(b["reason"]) + ("、" if band_mod.reason_text(b["reason"]) else "") + "帯を描いていません"
    return {
        "id": "F3",
        "title": "フェース×パスと、まっすぐ落ちる帯",
        "axis_labels": {"x": "クラブパス（°）", "y": "フェースの向き（°）"},
        # 座標は右打ちのまま（左打ちは画面が左右上下を反転して描く）。ラベルの向きの語だけ入れ替える
        "diagonal": {"label_above": f"{dir_word('right', hand)}へ曲がる", "label_below": f"{dir_word('left', hand)}へ曲がる", "note": "対角線（フェース＝パス）は曲がらない球"},
        "points": pts,
        "not_shown": {"n": len(not_shown), "extreme": sum(1 for s in not_shown if is_extreme(s)), "seqs": [s["seq"] for s in not_shown]},
        "band_used": b["used"],
        "band_ref": band_ref if b["used"] else None,
        "band_reason": reason,
        "window_path": p["l1"]["club_path"].get("median") if b["used"] else None,
        "notes": [x for x in (missing_line, r2_line) if x],
        "notes_band": ["番手ごとの帯は少しずつ違います", "TrackMan の数字から計算した範囲です。今日の球の範囲の外では当てにならない目安です"] if b["used"] else [],
    }


def f4(p: dict, band_ref: str | None) -> dict | None:
    rep = p["representative"]
    if not rep:
        return None
    return {
        "id": "F4",
        "title": "上から見た1球（模式図）",
        "exaggerate": 3,
        "note": "模式図・角度は3倍",
        "representative": rep,
        "window_center": {"band_ref": band_ref, "path": p["l1"]["club_path"].get("median")} if band_ref else None,
    }


def f5(p: dict, shots: list[dict], skip: set, good_ids: set, hand: str) -> dict:
    status = {x["shot_id"]: x for x in p["band"]["statuses"]}
    pts = []
    for s in shots:
        off = m(s, "impact_offset")
        if off is None:
            continue
        pt = _point_base(s, status.get(s["id"]), skip, good_ids)
        pt.update(offset_mm=off * 1000, contact=dec(s).get("contact"))
        pts.append(pt)
    st = p["strike"]
    gr = p["good_reference"]
    return {
        "id": "F5",
        "title": "打点（トゥ⇔ヒール）",
        "club_kind": "wood" if p["category"] in ("driver", "wood", "hybrid") else "iron",
        # 目標側からフェースを見た形。右打ちはヒール（ネック）が右、トゥが左
        "heel_on": "left" if hand == "L" else "right",
        "labels": {"heel": "ネック", "toe": "先端"},
        "center_mm": config.CENTER_STRIKE_M * 1000,
        "extreme_mm": config.EXTREME_STRIKE_M * 1000,
        "points": pts,
        "missing": st["missing"],
        "missing_seqs": st["missing_seqs"],
        "vertical_note": "縦の位置に意味はありません（上下は測っていません）",
        "layers": {
            "now": {"median_mm": st["median_m"] * 1000 if st["median_m"] is not None else None, "n": st["measured"], "heel_extreme": st["heel_extreme"], "label": "今日（プランの最初の『いつも通り』で置き換える）"},
            "goal": {"within_mm": config.CENTER_STRIKE_M * 1000, "heel_extreme": 0},
            "ideal": None if not gr["l1_good_enough"] else {"seqs": gr["l1_good_seqs"]},
            "ideal_missing": None if gr["l1_good_enough"] else {"have": len(gr["l1_good_seqs"]), "needed": gr["l1_good_needed"]},
        },
    }


def f6(p: dict) -> dict:
    mb = p["miss_budget"]
    bars = []
    for b in mb["by_cause"]:
        bar = {"cause": b["cause"], "label": CAUSE_LABEL[b["cause"]], "n": b["n"], "excess_m": b["excess_m"], "seqs": b["seqs"], "shot_ids": b["shot_ids"], "hatched": b["cause"] == "extreme_and_face"}
        if "no_strike" in b:
            bar["light"] = {"n": b["no_strike"]["n"], "excess_m": b["no_strike"]["excess_m"], "seqs": b["no_strike"]["seqs"], "label": "打点が測れていない球"}
        bars.append(bar)
    return {"id": "F6", "title": "はみ出した距離の内訳", "total_m": mb["total_m"], "n_over": mb["n_over"], "n_side": mb["n_side"], "bars": bars, "mishit_included": mb["mishit_included"]}


def f7(cross_units: list[dict]) -> dict:
    rows = []
    for u in cross_units:
        p = u["profile"]
        cells = {}
        for metric in ("face_angle", "club_path", "impact_offset"):
            l1 = p["l1"][metric]
            scale = 1000 if metric == "impact_offset" else 1
            cells[metric] = {
                "n": l1["n"],
                **{k: (l1[k] * scale if l1.get(k) is not None else None) for k in ("median", "q1", "q3", "mean")},
                "ci95": [x * scale for x in l1["ci95"]] if l1.get("ci95") else None,
                "bias": l1.get("bias"),
            }
        rows.append({"scope_id": p["scope_id"], "label": u.get("name") or u.get("club"), "n": p["n"] - p["mishit_excluded"], "mishit_excluded": p["mishit_excluded"], "cells": cells})
    return {
        "id": "F7",
        "title": "クラブ別の比較",
        "cols": [{"metric": "face_angle", "unit": "deg", "mmd": config.MMD["face_angle"]}, {"metric": "club_path", "unit": "deg", "mmd": config.MMD["club_path"]}, {"metric": "impact_offset", "unit": "mm", "mmd": config.MMD["impact_offset"] * 1000}],
        "rows": rows,
        "legend": "点が中央値、太い線が真ん中の半分（IQR）、細い線が平均の95%区間。各行の頭は候補を除いた球数",
    }


def band_request(p: dict, request_id: str) -> dict | None:
    """Go の physics.Band に渡す依頼（キーは Go の bandRequest と同じ）。帯の形（パスごとのフェースの
    下限・上限・中央）と窓の断面を頼む。Go は同じオブジェクトの隣に band_shape を足す。"""
    b = p["band"]
    if not b["used"] or p["carry_median_m"] is None or p["spin_loft_median"] is None:
        return None
    path = p["l1"]["club_path"]
    face = p["l1"]["face_angle"]
    lo = np.floor((path.get("min", 0) - 1) / config.BAND_PATH_STEP) * config.BAND_PATH_STEP
    hi = np.ceil((path.get("max", 0) + 1) / config.BAND_PATH_STEP) * config.BAND_PATH_STEP
    paths = [float(x) for x in np.arange(lo, hi + 1e-9, config.BAND_PATH_STEP)]
    t = config.GOOD_TARGETS.get(p["category"], config.DEFAULT_TARGET)
    return {
        "id": request_id,
        "scope_id": p["scope_id"],
        "category": p["category"],
        "carry": p["carry_median_m"],
        "spin_loft": p["spin_loft_median"],
        "lim": band_mod.lim_of(p["category"], p["carry_median_m"]),
        "side_pct": t["side_pct"],
        "side_min_m": t["side_min_m"],
        "k": b["k"],
        "paths": paths,
        "window_path": path.get("median"),
        "face_extent": [face.get("min"), face.get("max")],
    }


# ---------------------------------------------------------------- C1: いま と 理想（要点の「理想との差」）


def compare(p: dict, shots: list[dict], skip: set, carry_medians: dict, hand: str = "R") -> dict | None:
    """要点の「理想との差」に置く比べる図（利用者の方針 2026-09-29。§0・§6.1）。

    左に「いま」、右に「理想」を同じ縮尺で並べる。**細かい軸や目盛りは出さない**（数字は件数だけ）。
    理想は §7 の①「今日すでに打てている球」から作る（予測や手本の絵は使わない）:
      - 帯を使える種類: インパクトが帯に入った球（l1_in_band）
      - 帯を使わない種類（式が合わない・球が足りない）: 実際に狙いの幅に落ちた球（L0 の実測）
    3つの段: 着弾の散らばり（左右 ÷ 飛んだ距離）・当たる瞬間の面の向き（扇）・当たる場所（面の形の上の点）。
    座標は右打ち（+ が右）。左打ちは画面が左右を反転する。当たる場所（ネック／先）は F5 と同じく目標側から見た形。
    """
    status = {x["shot_id"]: x for x in (p.get("band") or {}).get("statuses") or []}
    band_used = bool((p.get("band") or {}).get("used"))
    group = p["scope"] == "group"
    now_pts, lims = [], []
    in_target_ids = set()
    for s in shots:
        side, carry = m(s, "side"), m(s, "carry")
        if side is None or carry is None or carry <= 0:
            continue
        lim = band_mod.lim_of(p["category"], carry)
        base = carry_medians.get(s.get("club")) or p.get("carry_median_m") or carry
        lims.append(lim / carry)
        if abs(side) <= lim:
            in_target_ids.add(s["id"])
        now_pts.append({"id": s["id"], "x": side / carry, "y": carry / base if base else 1.0, "in": abs(side) <= lim,
                        "mishit": s["id"] in skip, "ext": is_extreme(s)})
    if len(now_pts) < config.MIN_SCOPE_N:
        return None
    if band_used:
        ideal_ids = {sid for sid, st in status.items() if st.get("counted") and st.get("status") == "in"}
        ideal_from = "band"
    else:
        ideal_ids = {s["id"] for s in shots if s["id"] in in_target_ids and s["id"] not in skip}
        ideal_from = "landed"
    ideal_pts = [pt for pt in now_pts if pt["id"] in ideal_ids]
    half = float(np.median(lims)) if lims else config.DEFAULT_TARGET["side_pct"]

    def faces(ids=None):
        return [float(m(s, "face_angle")) for s in shots
                if s["id"] not in skip and m(s, "face_angle") is not None and (ids is None or s["id"] in ids)]

    def strikes(ids=None):
        return [float(m(s, "impact_offset")) * 1000 for s in shots
                if m(s, "impact_offset") is not None and (ids is None or s["id"] in ids)]

    now_face, ideal_face = faces(), faces(ideal_ids)
    now_strike = strikes()
    # 当たる場所の理想は、理想の球のうち芯に当たった球（本人の良い球。§7.3 の l1_good と同じ考え）
    ideal_strike = [v for v in strikes(ideal_ids) if abs(v) <= config.CENTER_STRIKE_M * 1000]
    n_now, n_in = len(now_pts), sum(1 for pt in now_pts if pt["in"])
    return {
        "id": "C1",
        "title": "いま と 理想",
        "group": group,
        "band_used": band_used,
        "ideal_from": ideal_from,
        "target_half": half,  # 狙いの幅（左右 ÷ 飛んだ距離）の半分。画面は帯として描くだけで数字は出さない
        "now": {"n": n_now, "in_target": n_in, "points": [{k: v for k, v in pt.items() if k != "id"} for pt in now_pts],
                "caption": f"{n_now}球中{n_in}球が狙いの幅"},
        "ideal": {"n": len(ideal_pts), "points": [{k: v for k, v in pt.items() if k != "id"} for pt in ideal_pts],
                  "caption": (f"今日すでに打てた{len(ideal_pts)}球" if ideal_pts else "狙いの幅にまとまる")},
        "face": {"now": now_face, "ideal": ideal_face if len(ideal_face) >= 2 else [], "show": len(now_face) >= config.PLAIN_MIN_N},
        "strike": {
            "now": now_strike, "ideal": ideal_strike, "show": len(now_strike) >= config.PLAIN_MIN_N,
            "center_mm": config.CENTER_STRIKE_M * 1000, "extreme_mm": config.EXTREME_STRIKE_M * 1000,
            "heel_on": "left" if hand == "L" else "right",
            "club_kind": "wood" if p["category"] in ("driver", "wood", "hybrid") else "iron",
        },
        "labels": {"now": "いま", "ideal": "理想", "landing": "球の散らばり", "face": "クラブの面の向き", "strike": "当たる場所",
                   "heel": "ネック", "toe": "先", "target": "狙いの幅"},
        # 最初に見える図なので、数字（件数を除く）と専門用語を使わない（gist.check_plain で検査する）
        "note": "理想の球は、今日あなたが打った球から選んでいます（手本の絵ではありません）",
    }
