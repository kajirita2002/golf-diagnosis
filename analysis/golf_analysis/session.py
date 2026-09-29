"""セッション全体の分析。

1. クラブごとに Good を自動で判定する（人の上書きが優先）。
2. 球筋のグループ（ミスの型 × 曲がりの原因）に分ける。
   球筋だけで分けず、Go が物理で出した「原因」でも分ける ―― 同じプッシュフェードでも
   フェースが原因の球と打点が原因の球は別の話（docs/DESIGN.md ❾）。
3. インパクト（L1）の指標のばらつきと、着地の左右のばらつきを何が説明するか。
4. 言えることだけを findings に出す。球が足りなければ「データ不足」と言う。
5. ミスヒット（トップ・薄い当たり・極端に短い球）を除外の候補として出す。
   自動では外さない ―― 外すかどうかは人が決める。ばらつきの要因分析だけは候補を除いて計算する
   （4番ユーティリティのキャリー 12m・スピン軸 -100° の1球で回帰が壊れた。2026-09-17 の実データ）。
   極端なヒール・トゥの当たりは候補にしない。それ自体が直すべきミスだから（トップは除く）。
   平均と SD（variability）と profile の l1 も候補を除く（R6。外した数を必ず出す）。
6. 同じ種類のクラブが2本以上あればまとめても見る（groups）。1本ずつだと球が足りないことが多い。
   番手で飛距離が違うので、まとめたときの左右は「キャリーに対する割合」で見る。
7. 範囲ごとに profile（傾向・L1 の偏りとばらつき・打点・欠け・帯…）を、セッションに
   cross_club と帯の係数 k を足す（docs/DESIGN_coaching.md §5.1・§5.2）。/analysis の形は足すだけ。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median

import numpy as np

from . import band as band_mod
from . import config, stats
from . import profile as profile_mod


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


def _drivers(shots: list[dict], outcome: str = "side", skip: set | None = None) -> dict:
    """左右のばらつきを、フェース・パス・打点のどれが説明するか。

    outcome="side_pct" は着地の左右をキャリーで割ったもの（番手をまとめて見るとき）。
    skip にある id（ミスヒットの候補）は使わない。
    """
    skip = skip or set()

    def y_of(s):
        if outcome == "side_pct":
            side, carry = _m(s, "side"), _m(s, "carry")
            return None if side is None or not carry else side / carry
        return _m(s, "side")

    use = [s for s in shots if s["id"] not in skip]
    predictors = ["face_angle", "club_path"]
    with_offset = [s for s in use if _m(s, "impact_offset") is not None]
    if use and len(with_offset) >= 0.8 * len(use):
        predictors.append("impact_offset")
    rows = [s for s in use if y_of(s) is not None and all(_m(s, p) is not None for p in predictors)]
    base = {"outcome": outcome, "predictors": predictors, "skipped": len(shots) - len(use)}
    if len(rows) < config.MIN_DRIVER_N:
        return {"status": "insufficient", "n": len(rows), "needed": config.MIN_DRIVER_N, **base}
    y = np.array([y_of(s) for s in rows], dtype=float)
    X = np.array([[_m(s, p) for p in predictors] for s in rows], dtype=float)
    res = stats.ols_contributions(y, X, predictors)
    r2_single = {p: stats.simple_r2(y, X[:, j]) for j, p in enumerate(predictors)}
    return {"status": "ok", "n": len(rows), **base, **res, "r2_single": {k: v for k, v in r2_single.items() if v is not None}}


def _dec(s: dict) -> dict:
    return s.get("decomposition") or {}


def mishit_candidates(shots: list[dict], carry_median: float | None) -> list[dict]:
    """除外の候補（人が決める）。

    極端な打点の球は候補にしない（それ自体が直すべきミス）。ただしトップ・薄い当たりは
    打点に関係なく候補にする（4番ユーティリティの4球目はヒール 34mm かつダイナミックロフト 0.6°
    のトップで、極端な打点として残すと回帰を壊した）。
    """
    out = []
    for s in shots:
        d = _dec(s)
        thin = "thin" in (d.get("flags") or [])
        if d.get("contact") in ("heel_extreme", "toe_extreme") and not thin:
            continue
        reasons = []
        if thin:
            reasons.append("thin")
        carry = _m(s, "carry")
        if carry is not None and carry_median and carry < config.MISHIT_CARRY_RATIO * carry_median:
            reasons.append("short_carry")
        axis = _m(s, "spin_axis")
        if axis is not None and abs(axis) > config.MISHIT_AXIS_DEG:
            reasons.append("extreme_axis")
        if reasons:
            out.append({"id": s["id"], "seq": s["seq"], "reasons": reasons})
    return out


def _findings(club: str, shots: list[dict], drivers: dict, scope: str = "club") -> list[dict]:
    """言えることだけを並べる。強さは件数と割合で示し、確率のふりをしない（DESIGN ❹）。"""
    out: list[dict] = []

    def add(f: dict) -> None:
        out.append({"club": club, "scope": scope, **f})

    curved = [s for s in shots if _dec(s).get("curve") not in (None, "unknown", "straight")]
    causes = Counter(_dec(s).get("curve_cause", "unknown") for s in curved)
    if len(curved) >= config.MIN_BLOCK_N:
        for cause in ("strike", "face_to_path"):
            ids = [s["id"] for s in curved if _dec(s).get("curve_cause") == cause]
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
            add({"kind": "curve_cause", "cause": cause, "evidence": {"count": k, "of": len(curved)}, "strength": strength, "shot_ids": ids})
        no_strike = sum(1 for s in curved if _m(s, "impact_offset") is None)
        if causes.get("strike", 0) + causes.get("mixed", 0) >= 3 and no_strike:
            add({"kind": "need_data", "field": "impact_offset", "evidence": {"missing": no_strike, "of": len(curved)}, "strength": "info"})
    elif curved and scope == "club":
        add({"kind": "insufficient", "what": "curve_cause", "n": len(curved), "needed": config.MIN_BLOCK_N})

    # 極端な打点（ネック・先端寄り）。一番ひどいミスがここから来ていることが多い
    for contact in ("heel_extreme", "toe_extreme"):
        ext = [s for s in shots if _dec(s).get("contact") == contact]
        if len(ext) < config.MIN_EXTREME_STRIKE_N:
            continue
        rest = [s for s in shots if _dec(s).get("contact") != contact and _m(s, "side") is not None]

        def mean_abs_side(xs):
            v = [abs(_m(x, "side")) for x in xs if _m(x, "side") is not None]
            return sum(v) / len(v) if v else None

        add(
            {
                "kind": "extreme_strike",
                "contact": contact,
                "evidence": {
                    "count": len(ext),
                    "of": len(shots),
                    "mean_abs_side": mean_abs_side(ext),
                    "mean_abs_side_others": mean_abs_side(rest),
                    # フェースが無い球と、パスも無い球を分けて数える（フラグの no_club_data は
                    # 4項目すべてが欠けたときだけ立つので使わない。実データの極端なヒールは
                    # フェースが無い4球のすべてでパスが取れていた）。
                    "no_face": sum(1 for s in ext if _m(s, "face_angle") is None),
                    "no_face_and_path": sum(1 for s in ext if _m(s, "face_angle") is None and _m(s, "club_path") is None),
                    # 旧名（analysis/0.2 まで）。中身は no_face と同じ。画面を直したら消す
                    "no_club_data": sum(1 for s in ext if _m(s, "face_angle") is None),
                },
                "strength": "strong" if len(ext) >= 3 else "moderate",
                "shot_ids": [s["id"] for s in ext],
            }
        )

    if drivers.get("status") == "ok" and drivers["contributions"]:
        # 説明の割合は単回帰の R² だけで言う（R4）。share は R² の落ち幅を配ったもので、
        # 分散の割合ではない（「ばらつきの98%はフェース」は読み違いだった）。
        singles = drivers.get("r2_single") or {}
        best = max(singles, key=lambda k: singles[k], default=None)
        if best is not None and singles[best] >= 0.5:
            share = next((c["share"] for c in drivers["contributions"] if c["metric"] == best), None)
            add(
                {
                    "kind": "dispersion_driver",
                    "metric": best,
                    "outcome": drivers["outcome"],
                    "evidence": {"r2_single": singles[best], "r2": drivers["r2"], "n": drivers["n"], "share": share},
                    "strength": "strong" if singles[best] >= 0.7 else "moderate",
                }
            )
    for f in out:
        f["id"] = _finding_id(f)
    return out


def _finding_id(f: dict) -> str:
    """"{scope}:{club}:{kind}[:{detail}]"。定型文が根拠を指すための名前。"""
    detail = f.get("cause") or f.get("contact") or f.get("metric") or f.get("field") or f.get("what")
    return ":".join(str(x) for x in (f["scope"], f["club"], f["kind"], detail) if x)


def _flight_groups(shots: list[dict]) -> list[dict]:
    groups_map: dict[tuple[str, str], list[int]] = defaultdict(list)
    for s in shots:
        d = _dec(s)
        groups_map[(d.get("miss_type", "unknown"), d.get("curve_cause", "unknown"))].append(s["id"])
    return [
        {"miss_type": k[0], "curve_cause": k[1], "n": len(v), "shot_ids": v}
        for k, v in sorted(groups_map.items(), key=lambda kv: -len(kv[1]))
    ]


def _variability(shots: list[dict], skip: set | None = None) -> dict:
    """L1 の平均と SD。ミスヒットの候補を除き、除いた数を添える（R6）。"""
    skip = skip or set()
    use = [s for s in shots if s["id"] not in skip]
    out = {}
    for key in config.L1_METRICS:
        vals = [_m(s, key) for s in use if _m(s, key) is not None]
        if vals:
            d = stats.describe(vals)
            d["mishit_excluded"] = sum(1 for s in shots if s["id"] in skip and _m(s, key) is not None)
            out[key] = d
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
    all_candidates: set = set()
    by_category: dict[str, list[str]] = defaultdict(list)
    for club, cs in sorted(by_club.items(), key=lambda kv: -len(kv[1])):
        category = cs[0].get("club_category") or "unknown"
        by_category[category].append(club)
        carries = [_m(s, "carry") for s in cs if _m(s, "carry") is not None]
        carry_median = median(carries) if len(carries) >= 3 else None

        judged = []
        for s in cs:
            j = judge_good(s, carry_median, category)
            judged.append({"id": s["id"], "seq": s["seq"], **j})
        n_good = sum(1 for j in judged if j["good"])

        candidates = mishit_candidates(cs, carry_median)
        skip = {c["id"] for c in candidates}
        all_candidates |= skip
        drivers = _drivers(cs, skip=skip)
        clubs.append(
            {
                "club": club,
                "club_category": category,
                "n": len(cs),
                "carry_median": carry_median,
                "good": {"n_good": n_good, "targets": config.GOOD_TARGETS.get(category, config.DEFAULT_TARGET), "shots": judged},
                "mishit_candidates": candidates,
                "flight_groups": _flight_groups(cs),
                "variability": _variability(cs, skip),
                "dispersion_drivers": drivers,
            }
        )
        findings.extend(_findings(club, cs, drivers))
        if candidates:
            findings.append(
                {
                    "id": f"club:{club}:mishit_candidates",
                    "club": club,
                    "scope": "club",
                    "kind": "mishit_candidates",
                    "evidence": {"count": len(candidates), "of": len(cs)},
                    "strength": "info",
                    "shot_ids": [c["id"] for c in candidates],
                }
            )

    groups = []
    for category, names in by_category.items():
        if len(names) < 2 or category in ("unknown", "putter"):
            continue
        gs = [s for n in names for s in by_club[n]]
        label = f"{config.CATEGORY_LABEL.get(category, category)}（まとめ）"
        drivers = _drivers(gs, outcome="side_pct", skip=all_candidates)
        groups.append(
            {
                "name": label,
                "club_category": category,
                "clubs": names,
                "n": len(gs),
                "flight_groups": _flight_groups(gs),
                "variability": _variability(gs, all_candidates),
                "dispersion_drivers": drivers,
            }
        )
        findings.extend(_findings(label, gs, drivers, scope="group"))

    # ---- 帯の係数（種類ごと → 使える種類でまとめる）と、範囲ごとの profile ----
    carry_medians = {c["club"]: c["carry_median"] for c in clubs}
    bands = band_mod.fit_bands(by_club, all_candidates, carry_medians)
    good_by_id = {j["id"]: j for c in clubs for j in c["good"]["shots"]}
    for g in groups:
        gs = [s for n in g["clubs"] for s in by_club[n]]
        g["scope_id"] = f"group:{g['club_category']}"
        g["profile"] = profile_mod.build_profile(
            gs, scope="group", scope_id=g["scope_id"], category=g["club_category"], skip=all_candidates,
            good_by_id=good_by_id, bands=bands, carry_medians=carry_medians, drivers=g["dispersion_drivers"],
        )
    for c in clubs:
        cs = by_club[c["club"]]
        c["scope_id"] = f"club:{c['club']}"
        c["profile"] = profile_mod.build_profile(
            cs, scope="club", scope_id=c["scope_id"], category=c["club_category"],
            skip={x["id"] for x in c["mishit_candidates"]}, good_by_id=good_by_id, bands=bands,
            carry_medians=carry_medians, drivers=c["dispersion_drivers"],
        )
    # 「フェースの値は計測器の計算かも」（R8）は、まとめ（または1本だけの種類）で1回だけ出す
    for unit in profile_mod.cross_units(clubs, groups):
        lr = unit["profile"].get("launch_residual_sd")
        if lr and lr["n"] >= config.MIN_SCOPE_N and lr["value"] < config.LAUNCH_RESID_INFO_DEG:
            name = unit.get("name") or unit.get("club")
            scope = "group" if "name" in unit else "club"
            findings.append(
                {
                    "id": f"{scope}:{name}:face_maybe_computed",
                    "club": name,
                    "scope": scope,
                    "kind": "face_maybe_computed",
                    "evidence": {"launch_residual_sd": lr["value"], "n": lr["n"]},
                    "strength": "info",
                }
            )

    return {
        "engine_version": config.ENGINE_VERSION,
        "n_shots": len(shots),
        "n_excluded": excluded,
        "clubs": clubs,
        "groups": groups,
        "findings": findings,
        "bands": bands,
        "cross_club": profile_mod.cross_club(clubs, groups),
    }
