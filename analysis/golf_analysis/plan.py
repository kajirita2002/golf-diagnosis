"""何を先にやるか（plan.candidates）。docs/DESIGN_coaching.md §5.4・§8.1〜§8.3。

- issue（原因の語彙）は profile から表の閾値で機械的に決める。**向きは閾値で決め、割り当ての件数では決めない。**
  打ち出し（フェースの向き）と曲がり（フェース・トゥ・パス）は別々に数える。
- 同じレバー・同じ向きの start_* と curve_*（または *_var）は1つの候補にまとめる。
- ゲート G0〜G3 で「先にやる理由が言えるもの」を先に置く。重みを足した点数は作らない（偽の精度）。
  通ったものは、割り当てた球のはみ出しの合計 → 球の数の順。止めた issue も理由つきで残す。
- 番手はクラブ1本（既存の評価はクラブ名の完全一致で球を拾う）。既定は、主 KPI が取れた球が一番多い番手。
- 仮説は「仮説: 〜すると、〜が減る」の形にし、因果を言い切らない（文は claims / report が作る）。
"""

from __future__ import annotations

import math

from . import config, stats
from .shots import curve_side, dec, is_extreme, is_thin, m, start_side

KPI_OF = {
    ("strike_heel", None): ("impact_offset", "reduce_abs"),
    ("strike_toe", None): ("impact_offset", "reduce_abs"),
    ("strike_scatter", None): ("impact_offset", "reduce_sd"),
    ("start_right", "face"): ("face_angle", "decrease"),
    ("start_left", "face"): ("face_angle", "increase"),
    ("start_right", "path"): ("club_path", "decrease"),
    ("start_left", "path"): ("club_path", "increase"),
    ("curve_right", "face"): ("face_angle", "decrease"),
    ("curve_left", "face"): ("face_angle", "increase"),
    ("curve_right", "path"): ("club_path", "increase"),
    ("curve_left", "path"): ("club_path", "decrease"),
    ("curve_right", "face_to_path"): ("face_to_path", "decrease"),
    ("curve_left", "face_to_path"): ("face_to_path", "increase"),
}
LEVER_METRIC = {"face": "face_angle", "path": "club_path", "face_to_path": "face_to_path"}
STRIKE_ISSUES = ("strike_heel", "strike_toe", "strike_scatter")


def _near(value: float, threshold: float) -> bool:
    return abs(value - threshold) <= config.BORDERLINE * abs(threshold) if threshold else abs(value) <= config.BORDERLINE


def _dir_issue(l1: dict, prefix: str) -> dict | None:
    """start_* / curve_* の向き（右・左・ばらつき）を表の閾値で決める。"""
    mean, sd, n = l1.get("mean"), l1.get("sd"), l1.get("n", 0)
    if mean is None or sd is None or n < 3:
        return None
    th_mean, th_sd = config.ISSUE_DIR_DEG, config.ISSUE_DIR_SD_RATIO * sd
    directional = abs(mean) >= th_mean and abs(mean) >= th_sd
    border = _near(abs(mean), th_mean) or _near(abs(mean), th_sd)
    side = "right" if mean > 0 else "left"
    if directional:
        issue = f"{prefix}_{side}"
        alt = f"{prefix}_var" if border and sd >= config.ISSUE_VAR_SD_DEG else None
    elif sd >= config.ISSUE_VAR_SD_DEG:
        issue = f"{prefix}_var"
        alt = f"{prefix}_{side}" if border else None
    else:
        return None
    return {
        "issue": issue,
        "metric": l1["metric"],
        "values": {"mean": mean, "sd": sd, "n": n, "half_sd": th_sd, "threshold_deg": th_mean},
        "borderline": bool(alt),
        "alt_issue": alt,
    }


def _lever(issue: str, face: dict, path: dict) -> str:
    fm, pm = face.get("mean") or 0.0, path.get("mean") or 0.0
    fs, ps = face.get("sd") or 0.0, path.get("sd") or 0.0
    if issue.startswith("start_") and not issue.endswith("_var"):
        return "face" if abs(fm) >= abs(pm) else "path"
    if fs >= config.LEVER_SD_RATIO * ps:
        return "face"
    if ps >= config.LEVER_SD_RATIO * fs:
        return "path"
    return "face_to_path"


def issues(p: dict, shots: list[dict]) -> list[dict]:
    """表で立った issue（§5.4）。"""
    out = []
    st, l1 = p["strike"], p["l1"]
    n_all = st["n"]  # 分母は範囲の全部の球（打点が「-」の球も入れる。候補は除く）
    ext_heel, ext_toe = st["heel_extreme"], st["toe_extreme"]
    off = l1["impact_offset"]
    mean_off, sd_off = off.get("mean"), off.get("sd")

    def extreme_hit(k):
        return k >= config.ISSUE_EXTREME_MIN and n_all and k / n_all >= config.ISSUE_EXTREME_SHARE

    strike_issue = None
    if extreme_hit(ext_heel) or (mean_off is not None and mean_off <= -config.ISSUE_STRIKE_MEAN_M):
        strike_issue = "strike_heel"
    elif extreme_hit(ext_toe) or (mean_off is not None and mean_off >= config.ISSUE_STRIKE_MEAN_M):
        strike_issue = "strike_toe"
    elif sd_off is not None and sd_off >= config.ISSUE_STRIKE_SD_M and abs(mean_off) < config.ISSUE_STRIKE_MEAN_M:
        strike_issue = "strike_scatter"
    if strike_issue:
        out.append(
            {
                "issue": strike_issue,
                "metric": "impact_offset",
                "values": {
                    "extreme": ext_heel if strike_issue != "strike_toe" else ext_toe,
                    "of": n_all,
                    "mean": mean_off,
                    "sd": sd_off,
                    "n": off.get("n", 0),
                    "median": off.get("median"),
                },
                "borderline": False,
                "alt_issue": None,
                "lever": None,
            }
        )
    for prefix, metric in (("start", "face_angle"), ("curve", "face_to_path")):
        it = _dir_issue(l1[metric], prefix)
        if it:
            it["lever"] = _lever(it["issue"], l1["face_angle"], l1["club_path"])
            out.append(it)
    thin = sum(1 for s in shots if is_thin(s))
    if thin >= config.ISSUE_THIN_MIN and thin / len(shots) >= config.ISSUE_THIN_SHARE:
        out.append({"issue": "top_extreme", "metric": None, "values": {"thin": thin, "of": len(shots)}, "borderline": False, "alt_issue": None, "lever": None})
    for it in out:
        it["kpi"] = _kpi(it)
    return out


def _kpi(it: dict) -> dict | None:
    iss, lever = it["issue"], it.get("lever")
    if iss == "top_extreme":
        return None
    if iss.endswith("_var") and not iss.startswith("strike"):
        return {"metric": LEVER_METRIC[lever], "goal": "reduce_sd"}
    key = (iss, None) if iss in STRIKE_ISSUES else (iss, lever)
    metric, goal = KPI_OF[key]
    return {"metric": metric, "goal": goal}


def _direction(iss: str) -> str | None:
    for d in ("right", "left", "var"):
        if iss.endswith("_" + d):
            return d
    return None


def group_candidates(raised: list[dict]) -> list[dict]:
    """同じレバーで向きが合う start_* と curve_* を1つの候補にまとめる。"""
    starts = [i for i in raised if i["issue"].startswith("start_")]
    curves = [i for i in raised if i["issue"].startswith("curve_")]
    used = set()
    out = []
    for s in starts:
        for c in curves:
            if id(c) in used or s["lever"] != c["lever"]:
                continue
            ds, dc = _direction(s["issue"]), _direction(c["issue"])
            if ds == dc or "var" in (ds, dc):
                out.append({"id": s["lever"], "issues": [s, c], "lever": s["lever"], "kpi": s["kpi"]})
                used |= {id(s), id(c)}
                break
    for i in raised:
        if id(i) in used:
            continue
        out.append({"id": i["issue"], "issues": [i], "lever": i.get("lever"), "kpi": i["kpi"]})
    return out


# ---------------------------------------------------------------- 球の割り当て


def assign_shot(s: dict, raised_names: set) -> str | None:
    """Good でない球を、上から順に最初に当たった原因の issue へ（立っていなければ None）。"""

    def strike_target():
        for k in STRIKE_ISSUES:
            if k in raised_names:
                return k
        return None

    if is_thin(s):
        return "top_extreme" if "top_extreme" in raised_names else None
    if is_extreme(s) or dec(s).get("curve_cause") == "strike":
        return strike_target()
    cv = curve_side(s)
    if cv in ("right", "left"):
        for k in (f"curve_{cv}", "curve_var"):
            if k in raised_names:
                return k
        return None
    if cv == "none":
        st = start_side(s)
        side = {"push": "right", "pull": "left"}.get(st)
        if side:
            for k in (f"start_{side}", "start_var"):
                if k in raised_names:
                    return k
    return None


# ---------------------------------------------------------------- ゲート


def gates(p: dict, shots: list[dict], skip: set) -> dict:
    st = p["strike"]
    n_all = st["n"]
    ext = st["heel_extreme"] + st["toe_extreme"]
    g1 = ext >= config.ISSUE_EXTREME_MIN and n_all > 0 and ext / n_all >= config.ISSUE_EXTREME_SHARE
    rows = [s for s in shots if s["id"] not in skip and m(s, "face_angle") is not None and m(s, "impact_offset") is not None]
    r = stats.pearson([m(s, "impact_offset") for s in rows], [m(s, "face_angle") for s in rows]) if len(rows) >= 3 else None
    ci = stats.fisher_ci(r, len(rows)) if r is not None else None
    sd_off = p["l1"]["impact_offset"].get("sd")
    g2 = bool(len(rows) >= config.G2_MIN_N and ci and (ci[0] > 0 or ci[1] < 0) and sd_off is not None and sd_off >= config.ISSUE_STRIKE_SD_M)
    g2_border = bool(g2 and min(abs(ci[0]), abs(ci[1])) <= config.G2_BORDER_R)
    thin = sum(1 for s in shots if is_thin(s))
    g3 = len(shots) > 0 and thin / len(shots) >= config.ISSUE_THIN_SHARE and thin >= config.ISSUE_THIN_MIN
    return {
        "G1": {"hit": g1, "extreme": ext, "of": n_all, "share": ext / n_all if n_all else None},
        "G2": {"hit": g2, "borderline": g2_border, "r": r, "ci95": list(ci) if ci else None, "n": len(rows), "strike_sd": sd_off},
        "G3": {"hit": g3, "thin": thin, "of": len(shots), "share": thin / len(shots) if shots else None},
    }


# ---------------------------------------------------------------- 番手と球数


def choose_club(shots: list[dict], skip: set, metric: str) -> dict | None:
    """主 KPI が取れた球が一番多い番手。MIN_BLOCK_N 球に満たない番手（7番の2球など）は、ほかにあれば選ばない。"""
    by: dict[str, list[dict]] = {}
    for s in shots:
        if s["id"] in skip:
            continue
        by.setdefault(s.get("club") or "", []).append(s)
    rows = []
    for club, cs in by.items():
        k = sum(1 for s in cs if m(s, metric) is not None)
        rows.append((club, k, len(cs)))
    if not rows:
        return None
    ok = [r for r in rows if r[2] >= config.MIN_BLOCK_N] or rows
    club, k, n = max(ok, key=lambda r: (r[1], r[2]))
    cs = by[club]
    vals = [m(s, metric) for s in cs if m(s, metric) is not None]
    out = {"club": club, "measured": k, "n": n, "share": k / n if n else 0.0}
    if vals:
        vals_sorted = sorted(vals)
        mid = len(vals_sorted) // 2
        med = vals_sorted[mid] if len(vals_sorted) % 2 else (vals_sorted[mid - 1] + vals_sorted[mid]) / 2
        out.update(mean=sum(vals) / len(vals), median=med)
        if len(vals) >= 2:
            mu = out["mean"]
            out["sd"] = math.sqrt(sum((v - mu) ** 2 for v in vals) / (len(vals) - 1))
    out["heel_extreme"] = sum(1 for s in cs if dec(s).get("contact") == "heel_extreme")
    out["toe_extreme"] = sum(1 for s in cs if dec(s).get("contact") == "toe_extreme")
    return out


def block_design(sd: float | None, metric: str, share: float) -> dict | None:
    """条件ごとの球数: 1.96 × SD × √(2/n) ≤ 2 × MMD を満たす最小の n。取れた割合 p で割り増す（§8.3）。"""
    mmd = config.MMD.get(metric)
    if sd is None or not mmd:
        return None
    n = math.ceil(2 * (1.96 * sd / (config.DETECT_MMD_MULT * mmd)) ** 2 - 1e-9)
    n = max(n, 2 * config.MIN_BLOCK_N)
    # 欠けた球を手で埋めない（打点ならシールで入れない）ときは、取れた割合で割り増す
    n_if_missing = math.ceil(n / share) if 0 < share < 1 else n
    # 1ブロックは 8〜12球（§8.3）。判定に要る球数がそれを超えるなら、1回の型は上限で組み、
    # 練習をまたいで同じ型を積む（1回で121球のような型を出さない）
    per_block = min(math.ceil(n / 2), config.BLOCK_N_MAX)
    per_block_if_missing = min(math.ceil(n_if_missing / 2), config.BLOCK_N_MAX)
    template = [
        {"kind": "warmup", "n": 5},
        {"kind": "baseline", "n": per_block},
        {"kind": "drill", "n": 5},
        {"kind": "intervention", "n": per_block},
        {"kind": "drill", "n": 3},
        {"kind": "intervention", "n": per_block},
        {"kind": "baseline", "n": per_block},
    ]
    return {
        "sd": sd,
        "mmd": mmd,
        "measured_share": share,
        "n_per_condition": n,
        "per_block": per_block,
        "n_per_condition_if_missing": n_if_missing,
        "per_block_if_missing": per_block_if_missing,
        "template": template,
        "total": sum(b["n"] for b in template),
        "one_session": n <= 2 * per_block,
        # 判定に要る球数（条件ごと）に届くまでの練習の回数（1回で A・B を各2ブロック）
        "sessions": math.ceil(n / (2 * per_block)),
    }


# ---------------------------------------------------------------- 動かさないもの（§8.2）


def _guards(p: dict, c: dict, shots: list[dict], skip: set) -> list[dict]:
    """decrease / increase と理想の型のずれを塞ぐ。

    理想の型が trim（真ん中の球は今のままでよく、片側へ外れた球を減らす）なのに、主 KPI の decrease は
    全体を反対側へずらしても良く出る。そこでこの型のときは「動かさないもの」に
      - 帯の反対側へ外れた球の数（L0 の実測。候補を除く）
      - フェースの中央値が窓の反対側の端（Go の band_shape.window）を越えないこと
    を必ず入れる（どちらかが崩れたら「効いた」に数えない）。
    """
    kpi, it = c.get("kpi"), (p["band"].get("ideal_type") or {})
    if not kpi or kpi["metric"] != "face_angle" or kpi["goal"] not in ("decrease", "increase"):
        return []
    if not p["band"]["used"] or it.get("kind") != "trim" or not it.get("shown"):
        return []
    opp = "left" if it["side"] == "right" else "right"
    from .band import lim_of  # 循環を避ける

    use = [s for s in shots if s["id"] not in skip and m(s, "side") is not None and m(s, "carry") is not None]
    sign = -1 if opp == "left" else 1
    n_opp = sum(1 for s in use if sign * m(s, "side") > lim_of(s.get("club_category") or "unknown", m(s, "carry")))
    face = p["l1"]["face_angle"]
    return [
        {"kind": "landed_opposite", "side": opp, "n": n_opp, "of": len(use)},
        {"kind": "face_median_window", "side": opp, "edge": "lo" if opp == "left" else "hi", "median": face.get("median"), "n": face.get("n", 0)},
    ]


# ---------------------------------------------------------------- まとめ


def candidates(p: dict, shots: list[dict], skip: set, good_ids: set) -> dict:
    """1つの範囲の候補（いま・次）と、止めたもの・参考。"""
    raised = issues(p, shots)
    names = {i["issue"] for i in raised}
    per_shot = {x["shot_id"]: x for x in p["miss_budget"]["per_shot"]}
    assigned: dict[str, list[dict]] = {}
    unassigned = []
    for s in shots:
        if s["id"] in good_ids:
            continue
        target = assign_shot(s, names)
        if target is None:
            unassigned.append(s["seq"])
        else:
            assigned.setdefault(target, []).append(s)
    g = gates(p, shots, skip)
    cands = group_candidates(raised)
    thin_ref = None
    out = []
    for c in cands:
        members = [s for i in c["issues"] for s in assigned.get(i["issue"], [])]
        c["shots"] = [s["seq"] for s in members]
        c["n_members"] = len(members)
        # 件数ははみ出しが0より大きい球だけ（§5.3。④の内訳・F6 と同じ数え方）
        over = [s for s in members if (per_shot.get(s["id"]) or {}).get("excess_m", 0.0) > 0]
        c["n_shots"] = len(over)
        c["over_seqs"] = [s["seq"] for s in over]
        c["excess_m"] = sum((per_shot.get(s["id"]) or {}).get("excess_m", 0.0) for s in members)
        c["borderline"] = any(i["borderline"] for i in c["issues"])
        kinds = {i["issue"] for i in c["issues"]}
        blocked = []
        face_or_path = any(k.startswith(("start_", "curve_")) for k in kinds)
        if face_or_path and g["G1"]["hit"]:
            blocked.append({"gate": "G1", **g["G1"]})
        if face_or_path and c["lever"] == "face" and g["G2"]["hit"]:
            blocked.append({"gate": "G2", **g["G2"]})
        if face_or_path and g["G3"]["hit"]:
            blocked.append({"gate": "G3", **g["G3"]})
        c["blocked_by"] = blocked
        if c["id"] == "top_extreme":
            c["kind"] = "count_only"
        c["issue_names"] = sorted(kinds)
        c["club"] = None
        c["design"] = None
        c["guards"] = []
        if c["kpi"]:
            ch = choose_club(shots, skip, c["kpi"]["metric"])
            c["club"] = ch
            sd = p["l1"][c["kpi"]["metric"]].get("sd")
            if ch and ch["share"] < config.G0_MEASURED_SHARE:
                c["kind"] = "measure"
                c["measure"] = f"measure.{c['kpi']['metric']}"
                c["gate0"] = {"gate": "G0", "measured": ch["measured"], "of": ch["n"], "share": ch["share"]}
            else:
                c.setdefault("kind", "drill")
                # 組み方は番手1本で打つので、番手のばらつきで出す（群の値を番手の名前で書かない。R2）。
                # 番手の SD が出せない（測れた球が2球未満）ときだけ群の値で出し、範囲を群と書く
                c["design_group"] = block_design(sd, c["kpi"]["metric"], ch["share"] if ch else 1.0)
                c["design_club"] = block_design(ch["sd"], c["kpi"]["metric"], ch["share"]) if ch and ch.get("sd") is not None else None
                c["design"] = c["design_club"] or c["design_group"]
                c["design_scope"] = "club" if c["design_club"] else "group"
            c["guards"] = _guards(p, c, shots, skip)
        out.append(c)
    thin = g["G3"]["thin"]
    if "top_extreme" not in names and thin >= config.ISSUE_THIN_MIN:
        thin_ref = {"issue": "top_extreme", "thin": thin, "of": len(shots), "share": thin / len(shots)}

    # ゲートで止めた理由の相手（G1・G2 は打点の候補、G3 はトップの候補）
    for c in out:
        for b in c["blocked_by"]:
            if b["gate"] in ("G1", "G2"):
                b["first"] = next((x["id"] for x in out if x["id"] in STRIKE_ISSUES), None)
            else:
                b["first"] = "top_extreme"
    active = [c for c in out if c.get("kind") != "count_only"]
    free = sorted([c for c in active if not c["blocked_by"]], key=lambda c: (-c["excess_m"], -c["n_shots"]))
    held = sorted([c for c in active if c["blocked_by"]], key=lambda c: (-c["excess_m"], -c["n_shots"]))
    ordered = free + held
    for rank, c in enumerate(ordered):
        c["rank"] = rank + 1
        # 「いま」に来た理由: ほかを止めたゲートを外すため、またははみ出しが一番大きいから
        opened = sorted({b["gate"] for x in ordered for b in x["blocked_by"] if b.get("first") == c["id"]})
        c["why_first"] = {"gates": opened, "by_budget": not opened}
    untouched = []
    path = p["l1"]["club_path"]
    if path.get("spread") == "stable" and not path.get("bias") and not any(c["lever"] == "path" for c in ordered):
        untouched.append({"metric": "club_path", "sd": path.get("sd"), "n": path.get("n")})
    return {
        "plan_version": config.PLAN_VERSION,
        "scope_id": p["scope_id"],
        "issues": raised,
        "gates": g,
        "candidates": ordered,
        "now": ordered[0]["id"] if ordered else None,
        "next": ordered[1]["id"] if len(ordered) > 1 else None,
        "count_only": [c for c in out if c.get("kind") == "count_only"],
        "reference": [thin_ref] if thin_ref else [],
        "unassigned_seqs": unassigned,
        "untouched": untouched,
    }
