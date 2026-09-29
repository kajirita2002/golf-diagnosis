"""帯（l1_in_band）: インパクトの数字だけから計算した左右が、目標帯に入る組み合わせだったか。

docs/DESIGN_coaching.md §5.2。

    side_L1 = carry·sin(予測の打ち出し) + k·carry·sin(予測のスピン軸)
    l1_in_band = |side_L1| ≤ max(side_min_m, side_pct·carry)

- **D-plane の式はここに書き写さない。** 予測の打ち出しとスピン軸は Go の分解
  （decomposition.predicted_launch_direction / predicted_axis_from_face_to_path）が
  1球ごとに出した値をそのまま使う。帯の形（図の多角形・窓）も Go の physics.Band が作る。
  ここで決めるのは、係数 k・帯の幅（GOOD_TARGETS）・式が使えるかの判定だけ。
- k は種類ごとに、実測の打ち出し・スピン軸・左右から原点を通る最小二乗で当てる。
- 式が使えるか: 予測だけで出した左右の残差 SD が帯の半幅の1/2を超える種類では使わない
  （実データの5番ウッドは 12.7m で使えない。打点のギア効果が大きい）。
- 10球に満たない種類は「まとめた k」（式が使えると判定された種類の球を全部合わせて当てた k）を使う。
  順番: 種類ごとに k と残差 → 使える種類でまとめた k → 10球未満の種類の判定。
"""

from __future__ import annotations

import math
from statistics import median

import numpy as np

from . import config
from .shots import dec, is_extreme, is_thin, m


def _sin(deg: float) -> float:
    return math.sin(math.radians(deg))


def lim_of(category: str, carry: float) -> float:
    t = config.GOOD_TARGETS.get(category, config.DEFAULT_TARGET)
    return max(t["side_min_m"], t["side_pct"] * carry)


def _fit_rows(shots: list[dict]) -> list[tuple[float, float]]:
    """(x, y) = (carry·sin(実測のスピン軸), side − carry·sin(実測の打ち出し))。"""
    rows = []
    for s in shots:
        ld, ax, side, carry = m(s, "launch_direction"), m(s, "spin_axis"), m(s, "side"), m(s, "carry")
        if None in (ld, ax, side, carry):
            continue
        rows.append((carry * _sin(ax), side - carry * _sin(ld)))
    return rows


def _k_of(rows: list[tuple[float, float]]) -> float | None:
    sxx = sum(x * x for x, _ in rows)
    if not rows or sxx == 0:
        return None
    return sum(x * y for x, y in rows) / sxx


def side_l1(shot: dict, k: float) -> float | None:
    """予測だけから出した左右（m）。予測の打ち出しかスピン軸が無ければ None。"""
    d = dec(shot)
    pl, pa, carry = d.get("predicted_launch_direction"), d.get("predicted_axis_from_face_to_path"), m(shot, "carry")
    if None in (pl, pa, carry):
        return None
    return carry * _sin(pl) + k * carry * _sin(pa)


def _resid(shots: list[dict], k: float) -> list[float]:
    out = []
    for s in shots:
        pred, side = side_l1(s, k), m(s, "side")
        if pred is not None and side is not None:
            out.append(side - pred)
    return out


def _sd(v: list[float]) -> float | None:
    return float(np.std(v, ddof=1)) if len(v) >= 2 else None


def fit_bands(by_club: dict[str, list[dict]], skip: set, carry_medians: dict) -> dict:
    """種類ごとの k・残差・式が使えるか。候補（skip）は除く。"""
    by_cat: dict[str, list[dict]] = {}
    for club, cs in by_club.items():
        if not cs:
            continue
        cat = cs[0].get("club_category") or "unknown"
        if cat in ("unknown", "putter"):
            continue
        by_cat.setdefault(cat, []).extend(s for s in cs if s["id"] not in skip)

    out: dict[str, dict] = {}
    for cat, shots in by_cat.items():
        rows = _fit_rows(shots)
        k = _k_of(rows)
        carries = [m(s, "carry") for s in shots if m(s, "carry") is not None]
        sls = [m(s, "spin_loft") for s in shots if m(s, "spin_loft") is not None and not is_thin(s)]
        carry_med = median(carries) if carries else None
        half = lim_of(cat, carry_med) if carry_med else None
        res = _resid(shots, k) if k is not None else []
        rsd = _sd(res)
        info = {
            "category": cat,
            "k_own": k,
            "n_fit": len(rows),
            "resid_sd_own": rsd,
            "n_resid": len(res),
            "carry_median": carry_med,
            "spin_loft_median": median(sls) if sls else None,
            "half_width_m": half,
            "targets": config.GOOD_TARGETS.get(cat, config.DEFAULT_TARGET),
        }
        if k is None or rsd is None or len(res) < 3 or half is None:
            # 「球が足りない」だけにまとめない。何が欠けて計算できないのかで入れ方が違う（R5・R7）
            n_fp = sum(1 for s in shots if m(s, "face_angle") is not None and m(s, "club_path") is not None)
            if len(shots) < 3:
                reason = "few_shots"
            elif carry_med is None:
                reason = "no_carry"
            elif k is None:
                reason = "no_flight"
            elif n_fp < 3:
                reason = "no_face_path"
            else:
                reason = "few_shots"
            info.update(usable=False, reason=reason)
        elif rsd > config.BAND_RESID_RATIO * half:
            info.update(usable=False, reason="residual_too_large")
        else:
            info.update(usable=True, reason=None)
        out[cat] = info

    usable = [c for c, v in out.items() if v["usable"]]
    pooled_rows = [r for c in usable for r in _fit_rows(by_cat[c])]
    pooled_k = _k_of(pooled_rows)
    pooled = {"k": pooled_k, "n": len(pooled_rows), "categories": sorted(usable)}
    for cat, v in out.items():
        v["pooled"] = pooled
        if not v["usable"]:
            v.update(k=None, k_source=None, resid_sd=None)
            continue
        if v["n_fit"] < config.BAND_MIN_OWN_K_N and pooled_k is not None:
            v["k"], v["k_source"] = pooled_k, "pooled"
        else:
            v["k"], v["k_source"] = v["k_own"], "own"
        res = _resid(by_cat[cat], v["k"])
        v["resid_sd"] = _sd(res)
        if v["resid_sd"] is not None and v["resid_sd"] > config.BAND_RESID_RATIO * v["half_width_m"]:
            v.update(usable=False, reason="residual_too_large")
    return out


# 帯を計算しない理由（文の前半）。⑥の定型文と F3 の注記が同じ表を読む（片方だけ直さない）
REASON_TEXT = {
    "residual_too_large": "この種類は打点の影響が大きく、フェースとパスの数字だけでは落ちる場所が決まらないので",
    "few_shots": "この範囲は球が足りない（帯の計算には3球以上要ります）ので",
    "no_carry": "キャリーが測れていない（取り込みか手入力でキャリーを入れると計算できます）ので",
    "no_flight": "打ち出し・スピン軸・左右がそろった球が無い（TrackMan のタイルにこの3つを入れると計算できます）ので",
    "no_face_path": "フェースとパスの両方が測れた球が3球に満たない（TrackMan のタイルに Face Ang. と Club Path を入れると計算できます）ので",
    "no_category": "クラブの種類が分からない（またはパター）ので",
}


def reason_text(reason: str | None) -> str:
    return REASON_TEXT.get(reason or "", "")


def shot_status(shot: dict, bands: dict, skip: set) -> dict:
    """1球の帯の判定。数えない球（候補・thin・極端な打点・フェースが無い）は counted=False と理由。"""
    cat = shot.get("club_category") or "unknown"
    b = bands.get(cat)
    carry, side = m(shot, "carry"), m(shot, "side")
    base = {"shot_id": shot["id"], "seq": shot["seq"]}
    if b is None or not b.get("usable"):
        return {**base, "counted": False, "reason": "band_not_used"}
    if shot["id"] in skip:
        return {**base, "counted": False, "reason": "mishit_candidate"}
    if is_thin(shot):
        return {**base, "counted": False, "reason": "thin"}
    if is_extreme(shot):
        return {**base, "counted": False, "reason": "extreme_strike"}
    if m(shot, "face_angle") is None:
        return {**base, "counted": False, "reason": "no_face"}
    pred = side_l1(shot, b["k"])
    if pred is None:
        return {**base, "counted": False, "reason": "no_prediction"}
    lim = lim_of(cat, carry)
    status = "in" if abs(pred) <= lim else ("out_right" if pred > 0 else "out_left")
    landed = None if side is None else abs(side) <= lim
    return {**base, "counted": True, "status": status, "side_l1": pred, "lim_m": lim, "landed_in": landed, "side": side}
