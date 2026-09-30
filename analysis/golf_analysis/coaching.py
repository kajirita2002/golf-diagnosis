"""アクションプラン（Phase 1b）: プランを作る・1回の練習を評価する・状態の移り方。docs/DESIGN_coaching.md §8。

分析サービスは状態を持たない。プラン（plans の行）と練習（plan_runs の行・評価の保存）は Go が持ち、
ここは毎回渡されたものから計算して返すだけ。

- **params は作った時点で固定する**（§2.2・§7.3・§8）。帯の係数 k・帯の幅（GOOD_TARGETS）・中央値・
  段の目標の規則・組み方・ガードレール・動かさないもの。あとから閾値を較正しても、進行中のプランのゴールは動かない。
- **物差しはプランの1回目の A**（診断した日の値ではない。平均への回帰）。基準は保存せず、
  1件目の評価の first_a から毎回読む（§10.3）。
- **準備（warmup）とドリル（drill）の球は A と B の比較に入れない。** ドリルは「ドリル中（参考）」として
  A と同じ比較に通すだけで、判定には使わない（§8.5）。
- 効果が無くても1回で moderate 以上は1割前後出る（scripts/sim_block_order.py）。だから「効いた」は
  **別の日に続けて2回**。割合は利用者に見せない（確率のふりになる）。見せるのは規則だけ。
- 事実の文（数字・向き・量）は claims.py の定型文で作る（R1）。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from statistics import median

import numpy as np

from . import band as band_mod
from . import config, drills, plan, stats
from .claims import Claim, ClaimError, Facts, build_claim
from .compare import _diff
from .experiment import VALID_GOALS, _compare
from .report import CAND_TITLE, METRIC_WORD, _public_candidates, _slim_floats, build_report, cand_title
from .session import analyze_session, mishit_candidates
from .shots import dec, drop_practice, is_extreme, is_thin, m

# ドリル集に確かめ済みのものが無いあいだの「意識する1点を自分で書く」。plans.drill_id は空文字で持つ
# （Go の POST /plans は drill_id が空なら cue を要る）。入力では "custom" も同じ意味に読む。
CUSTOM_DRILL = ""
CUSTOM_ALIASES = ("", "custom")
COUNT_DEFAULT = {"strike_heel": ["heel_extreme"], "strike_toe": ["toe_extreme"], "strike_scatter": ["heel_extreme", "toe_extreme"], "top_extreme": ["thin"]}
COUNT_WORD = {"heel_extreme": "ネック寄り（ヒール 30mm 超）の当たり", "toe_extreme": "先端寄り（トゥ 30mm 超）の当たり", "thin": "薄い当たり（極端なトップ）"}
GUARDRAIL_WORD = {"club_speed": "ヘッドスピード", "carry": "キャリー"}
GOAL_DONE_WORD = {"reduce_abs": "ずれの大きさが減りました", "reduce_sd": "ばらつきが減りました", "decrease": "{dir:left}へ寄りました", "increase": "{dir:right}へ寄りました"}
KIND_WORD = {"baseline": "いつも通り", "intervention": "本番", "drill": "ドリル", "warmup": "準備", "retention": "定着"}


class PlanError(ValueError):
    """入力が合わない（画面にそのまま出してよい理由の文）。HTTP では 422。"""


# ---------------------------------------------------------------- 範囲と候補（report と同じ規則）


def _scopes(shots: list[dict]) -> tuple[dict, dict]:
    """本体の範囲（まとめ・まとめに入らない1本で、候補を除いて5球以上）ごとの profile と候補。

    report.build_report の1回目のループと同じ規則（§6.1）。"""
    an = analyze_session(shots)
    live = drop_practice(shots)
    by_club: dict[str, list[dict]] = defaultdict(list)
    for s in live:
        if not s.get("excluded"):
            by_club[s.get("club") or "(クラブ不明)"].append(s)
    good_ids = {j["id"] for c in an["clubs"] for j in c["good"]["shots"] if j["good"]}
    grouped = {n for g in an["groups"] for n in g["clubs"]}
    out = {}
    for kind, u in [("group", g) for g in an["groups"]] + [("club", c) for c in an["clubs"]]:
        name = u.get("name") or u.get("club")
        if kind == "club" and name in grouped:
            continue
        p = u["profile"]
        if p["n"] - p["mishit_excluded"] < config.MIN_SCOPE_N:
            continue
        clubs = u.get("clubs") or [u["club"]]
        sh = [s for c in clubs for s in by_club[c]]
        skip = {s["id"] for s in sh if s["seq"] in p["mishit_seqs"]}
        out[p["scope_id"]] = {
            "scope_id": p["scope_id"], "label": name, "category": p["category"], "clubs": clubs,
            "profile": p, "shots": sh, "skip": skip, "cands": plan.candidates(p, sh, skip, good_ids),
        }
    return an, out


def _path_mean(sc: dict) -> float | None:
    """範囲のパスの平均（候補を除く・右打ちの座標）。パスの向きで当てるドリルを選ぶのに使う。"""
    return ((sc["profile"].get("l1") or {}).get("club_path") or {}).get("mean")


def _plan_history(c: dict, plans: list[dict] | None) -> dict:
    """この候補（issue）で前に作ったプランの記録。「詰まった」で閉じた issue は blocked（§8.6「issue を blocked にして次へ」）。"""
    names = set(c.get("issue_names") or []) | {c["id"]}
    hs = [p for p in plans or [] if p.get("issue") in names]
    rows = [{k: p.get(k) for k in ("plan_id", "status", "state", "drill_id", "created_at", "closed_at")} for p in hs]
    return {
        "blocked": any(p.get("status") == "blocked" or p.get("state") == "stuck" for p in hs),
        "failed": sum(1 for p in hs if p.get("state") in ("failed", "stuck")),
        "plans": rows,
    }


def candidates_with_drills(shots: list[dict], hand: str = "R", history: list[dict] | None = None, plans: list[dict] | None = None) -> dict:
    """POST /v1/plan/candidates（Go は GET /v1/players/{id}/plan-candidates）: 本体の範囲ごとの候補（いま・次…）と、
    候補に勧めるドリル（確かめ済みだけ。記録のあるものが先・前回の判定つき）。

    history は練習の記録（{drill_id, issue, grade, date}。1回の練習ごと）、plans はこの選手の前のプラン
    （{plan_id, issue, drill_id, status, state}）。前のプランで「詰まった」issue は「いま」「次」から外して後ろへ回し、
    history.blocked を立てる（本人が選ぶなら画面が一度確かめる）。"""
    _, scopes = _scopes(shots)
    out = []
    for sid, sc in scopes.items():
        pub = _public_candidates(sc["cands"])
        for c in pub["candidates"]:
            c["title"] = drills.render(cand_title(c), hand)
            c["drills"] = (drills.for_candidate(c, sc["category"], hand, history, _path_mean(sc)) if c.get("kind") == "drill"
                           else {"drills": [], "n_matching": 0, "n_unchecked": 0})
            c["history"] = _plan_history(c, plans)
        free = [c for c in pub["candidates"] if not c["history"]["blocked"]]
        held = [c for c in pub["candidates"] if c["history"]["blocked"]]
        if held:
            pub["candidates"] = free + held
            pub["now"] = free[0]["id"] if free else None
            pub["next"] = free[1]["id"] if len(free) > 1 else None
            pub["blocked_by_history"] = [c["id"] for c in held]
        out.append({"scope_id": sid, "label": sc["label"], "category": sc["category"], "clubs": sc["clubs"], "candidates": pub})
    return _slim_floats({
        "plan_version": config.PLAN_VERSION, "engine_version": config.ENGINE_VERSION, "catalog_version": drills.version(),
        "handedness": hand, "scopes": out, "measures": drills.listing(hand)["measures"],
    })


# ---------------------------------------------------------------- プランを作る（§8.1・§7.3）


def _template(per_block: int, with_drill: bool, variant: str = "standard") -> list[dict]:
    """ブロックの型。variant:
    - standard  … A-B-B-A（§8.3）
    - alternate … 「移せていない」の次の型（§8.6）: 準備 → A → ドリルと本番を1球ずつ交互 → A。
                  ドリルがあるときだけ（自分で書いた1点にはドリルの段が無い）"""
    d1, d2 = config.PLAN_DRILL_N
    if variant == "alternate":
        pairs = min(per_block, config.PLAN_ALTERNATE_MAX_PAIRS)
        mid = [x for _ in range(pairs) for x in ({"kind": "drill", "n": 1}, {"kind": "intervention", "n": 1})]
        return [{"kind": "warmup", "n": config.PLAN_WARMUP_N}, {"kind": "baseline", "n": per_block}, *mid, {"kind": "baseline", "n": per_block}]
    t = [
        {"kind": "warmup", "n": config.PLAN_WARMUP_N},
        {"kind": "baseline", "n": per_block},
        {"kind": "drill", "n": d1},
        {"kind": "intervention", "n": per_block},
        {"kind": "drill", "n": d2},
        {"kind": "intervention", "n": per_block},
        {"kind": "baseline", "n": per_block},
    ]
    return t if with_drill else [b for b in t if b["kind"] != "drill"]


def _secondary(c: dict, cands: dict) -> list[dict]:
    """副 KPI（記録だけ）。G2（打点とフェースが一緒に動く）がぎりぎりのときは、打点とフェースのどちらを選んでも
    もう一方を一緒に記録する（§5.4）。"""
    g2 = (cands.get("gates") or {}).get("G2") or {}
    if not (g2.get("hit") and g2.get("borderline")):
        return []
    if c["id"] in plan.STRIKE_ISSUES:
        other = next((x for x in cands["candidates"] if x.get("lever") == "face" and x.get("kpi") and x["kpi"]["metric"] == "face_angle"), None)
    elif c.get("lever") == "face" and any(b.get("gate") == "G2" for b in c.get("blocked_by") or []):
        other = next((x for x in cands["candidates"] if x["id"] in plan.STRIKE_ISSUES and x.get("kpi")), None)
    else:
        other = None
    return [dict(other["kpi"])] if other else []


def build_plan(
    shots: list[dict], hand: str, scope_id: str, candidate_id: str, drill_id: str | None = None,
    club: str | None = None, window: dict | None = None, cue: str | None = None, variant: str = "standard",
) -> dict:
    """POST /v1/plan/build: 候補から1つ選んでプランの中身を作る（保存は Go の plans）。

    window は Go が physics.Band で出した、その範囲のパスの中央値での窓（{face_min, face_max, path}。右打ちの座標）。
    「フェースの中央値が窓の端を越えない」の見張りを持つ候補（§8.2 で必ず入れる型）は、窓が無ければ作らない
    （見張りを確かめられないプランは「効いた」に数えられないまま進まなくなる）。
    variant は型（standard / alternate）。alternate は「移せていない」の次の型で、ドリルがあるときだけ。"""
    _, scopes = _scopes(shots)
    sc = scopes.get(scope_id)
    if sc is None:
        raise PlanError(f"範囲 {scope_id} がありません（本体の範囲: {', '.join(scopes) or 'なし'}）")
    cands = sc["cands"]
    c = next((x for x in cands["candidates"] + cands["count_only"] if x["id"] == candidate_id), None)
    if c is None:
        raise PlanError(f"候補 {candidate_id} はこの範囲にありません")
    if c.get("kind") == "count_only" or not c.get("kpi"):
        raise PlanError("この候補は件数を並べるだけで、判定する指標がありません（参考）")
    if c.get("kind") == "measure":
        raise PlanError(f"この候補は、まず{METRIC_WORD.get(c['kpi']['metric'], c['kpi']['metric'])}を測る段です（取れた球が少なく、実験しても判定できません）")
    metric, goal = c["kpi"]["metric"], c["kpi"]["goal"]

    # ドリル: 確かめ済み（checked_by）で、この候補に当てられるものだけ。無ければ「意識する1点を自分で書く」
    d = None
    if drill_id and drill_id not in CUSTOM_ALIASES:
        d = drills.get(drill_id)
        if d is None:
            raise PlanError(f"ドリル {drill_id} はドリル集にありません")
        if not drills.is_checked(d):
            raise PlanError("このドリルは、向きと安全をまだ人が確かめていないので使えません")
        if not drills.matches(d, c, sc["category"], _path_mean(sc)):
            raise PlanError("このドリルは、この候補（issue・レバー・クラブの種類）には当てません")
    drill_ref = d["id"] if d else CUSTOM_DRILL
    if variant not in ("standard", "alternate"):
        raise PlanError(f"型 {variant!r} は使えません（standard / alternate）")
    if variant == "alternate" and d is None:
        raise PlanError("ドリルと本番を1球ずつ交互にする型は、確かめ済みのドリルがあるときだけ作れます")
    if any(g.get("kind") == "face_median_window" for g in c.get("guards") or []) and not (
        window and all(isinstance(window.get(k), (int, float)) for k in ("face_min", "face_max"))
    ):
        raise PlanError("この候補は「フェースの中央値が、まっすぐ落ちる範囲の端を越えない」を見張るので、範囲（解説の帯の窓）が要ります。解説を開き直してから作ってください")

    # 番手1本（既存の評価はクラブ名の完全一致で球を拾う）
    default_club = (c.get("club") or {}).get("club")
    if club:
        match = next((x for x in sc["clubs"] if x.lower() == club.lower()), None)
        if match is None:
            raise PlanError(f"{club} はこの範囲のクラブではありません（{'・'.join(sc['clubs'])}）")
        club = match
    else:
        club = default_club
    if not club:
        raise PlanError("番手を決められません")
    ch = plan.choose_club([s for s in sc["shots"] if s.get("club") == club], sc["skip"], metric) or {}
    group_sd = sc["profile"]["l1"][metric].get("sd")
    design = None
    if ch.get("sd") is not None:
        design, dscope = plan.block_design(ch["sd"], metric, ch["share"]), "club"
    elif group_sd is not None:
        design, dscope = plan.block_design(group_sd, metric, ch.get("share") or 1.0), "group"
    else:
        dscope = "default"
    per_block = design["per_block"] if design else config.PLAN_DEFAULT_PER_BLOCK
    template = _template(per_block, with_drill=d is not None, variant=variant)

    p = sc["profile"]
    b = p["band"]
    cat = sc["category"]
    targets = config.GOOD_TARGETS.get(cat, config.DEFAULT_TARGET)
    club_shots = [s for s in sc["shots"] if s.get("club") == club and s["id"] not in sc["skip"]]
    carries = [m(s, "carry") for s in club_shots if m(s, "carry") is not None]
    sls = [m(s, "spin_loft") for s in club_shots if m(s, "spin_loft") is not None and not is_thin(s)]
    counts = list(d["counts"]) if d and d.get("counts") else sorted({k for i in c["issue_names"] for k in COUNT_DEFAULT.get(i, [])})
    stage = None
    # 段の目標（帯に入る球の数）はフェースのプランだけ（§8.2 のステップ2）
    if metric == "face_angle" and b["used"] and (b.get("stage") or {}).get("shown"):
        stage = {
            "goal_share": config.IDEAL_SHARE_GOAL,
            "step": config.IDEAL_STEP,
            "per_block": per_block,
            "add_per_block": max(1, math.ceil(per_block * config.IDEAL_STEP - 1e-9)),
        }
    params = {
        "metric": metric,
        "goal": goal,
        "mmd": config.MMD.get(metric),
        "category": cat,
        "club": club,
        "band": {
            "used": bool(b["used"]), "reason": b.get("reason"), "k": b.get("k"), "k_source": b.get("k_source"),
            "side_pct": targets["side_pct"], "side_min_m": targets["side_min_m"],
        },
        "carry_median_m": float(median(carries)) if carries else None,
        "spin_loft_median": float(median(sls)) if sls else None,
        "window": ({"face_min": window.get("face_min"), "face_max": window.get("face_max"), "path": window.get("path")} if window else None),
        # 組み方の型は plan.template に1つだけ持つ（design の template は写さない）
        "design": {k: v for k, v in design.items() if k != "template"} if design else None,
        "design_scope": dscope,
        "stage": stage,
        "guardrails": [dict(g) for g in (d.get("guardrails") if d and d.get("guardrails") else config.DEFAULT_GUARDRAILS)],
        "untouched": [{"metric": u["metric"]} for u in cands.get("untouched") or []] if metric in ("face_angle", "face_to_path") else [],
        "guards": [dict(g) for g in c.get("guards") or []],
        "secondary": _secondary(c, cands),
        "variant": variant,
        "counts": counts,
        "rules": {
            "worked_runs": config.PLAN_WORKED_RUNS,
            "settle_runs": config.PLAN_SETTLE_RUNS_SLOW if d and d.get("kind") in drills.SLOW_SETTLE_KINDS else config.PLAN_SETTLE_RUNS,
            "fail_runs": config.PLAN_FAIL_RUNS,
            "worse_runs": config.PLAN_WORSE_RUNS,
            "strike_advance_m": config.CENTER_STRIKE_M if c["id"] in ("strike_heel", "strike_toe") else None,
        },
        "drill_kind": d.get("kind") if d else None,
        "versions": {"engine": config.ENGINE_VERSION, "plan": config.PLAN_VERSION, "catalog": drills.version()},
    }
    trigger = {
        "scope_id": scope_id,
        "label": sc["label"],
        "n": p["n"],
        "mishit_excluded": p["mishit_excluded"],
        "club": ch,
        "issues": c["issues"],
        # 今日の数は「きっかけ」として件数だけ（物差しにしない。§7.3）
        "band_today": {"in": b.get("in"), "counted": b.get("counted")} if b["used"] else None,
    }
    rationale = {
        "rank": c.get("rank"), "slot": "now" if cands["now"] == c["id"] else ("next" if cands["next"] == c["id"] else None),
        "why_first": c.get("why_first"), "blocked_by": c.get("blocked_by"), "gates": cands["gates"],
        "excess_m": c.get("excess_m"), "n_shots": c.get("n_shots"), "borderline": c.get("borderline"),
    }
    # 仮説・組み方・動かさないもの・進む条件の文は、解説の⑦と同じ定型文（番手が既定のときだけ。
    # 別の番手を選んだら、既定の番手の数字の文を出さない。R2）
    claims = []
    if club == default_club and rationale["slot"]:
        rep = build_report(shots, hand)
        scope = next((s for s in rep["scopes"] if s["scope_id"] == scope_id), None)
        slot = rationale["slot"]
        want = (f"{scope_id}/next.{slot}", f"{scope_id}/{slot}.")
        if scope:
            claims = [x for sec in scope["sections"] for x in sec["claims"] if x["id"].startswith(want) or x["id"] == want[0]]
        if variant == "alternate":
            claims = [x for x in claims if not x["id"].endswith(".design")]  # 組み方の文は A-B-B-A のもの
    kind = d.get("kind") if d else None
    out = {
        "plan_version": config.PLAN_VERSION,
        "engine_version": config.ENGINE_VERSION,
        "catalog_version": drills.version(),
        "plan": {
            "issue": candidate_id,
            "issues": c["issue_names"],
            "title": drills.render(cand_title(c), hand),
            "lever": c.get("lever"),
            "drill_id": drill_ref,
            "catalog_version": drills.version(),
            "club": club,
            "target_metric": metric,
            "goal": goal,
            "template": template,
            "short_template": [dict(x) for x in config.PLAN_SHORT_TEMPLATE] if d else [x for x in config.PLAN_SHORT_TEMPLATE if x["kind"] != "drill"],
            "params": params,
            "trigger": trigger,
            "rationale": rationale,
            "engine_version": config.PLAN_VERSION,
        },
        "drill": drills.public(d, hand) if d else None,
        # 介入ブロックで意識する1点（既存の experiments.intervention に入る）
        "intervention": drills.render(d["cue_transfer"], hand) if d else (cue or ""),
        "claims": claims,
        "notes": [],
    }
    out["plan"]["total_shots"] = sum(x["n"] for x in template)
    if variant == "alternate":
        out["notes"].append("ドリルと本番を1球ずつ交互に打ちます（道具ありでできた感覚を、道具なしに移す型です）。")
    if params["secondary"]:
        sw = "・".join(METRIC_WORD.get(x["metric"], x["metric"]) for x in params["secondary"])
        out["notes"].append(f"{sw}も一緒に記録します（記録だけで、判定には使いません）。")
    if kind == "compensation":
        out["notes"].append("このドリルは補正です。根本の直しではありません（定着の判定は1回多く見ます）。")
    if not d:
        out["notes"].append("確かめ済みのドリルがまだ無いので、本番で意識する1点を自分で書いてください。")
    if design and not design["one_session"]:
        out["notes"].append("今日のばらつきでは、1回の練習でわかるのは大きく動いたかどうかまでです。同じ型を何回かの練習で積んで見ます。")
    out["notes"].append("最後の「いつも通り」には効果が残って混ざるので、効いたぶんは小さく見えます（控えめな側への偏りです）。")
    return _slim_floats(out)


# ---------------------------------------------------------------- 1回の練習を評価する（§8.5）


def _say(claims: list, facts: Facts, cid: str, template: str, hand: str) -> None:
    claims.append(build_claim(Claim(id=cid, section="plan", layer="L1_plan", template=template), facts, hand))


def _lim(params: dict, carry: float) -> float:
    b = params["band"]
    return max(b["side_min_m"], b["side_pct"] * carry)


def _l0(shots: list[dict], params: dict) -> dict:
    """実際に狙いの幅（帯）へ落ちた球（L0 の実測。R8 の答え合わせ）。"""
    use = [s for s in shots if m(s, "side") is not None and m(s, "carry") is not None]
    landed = [s for s in use if abs(m(s, "side")) <= _lim(params, m(s, "carry"))]
    right = sum(1 for s in use if m(s, "side") > _lim(params, m(s, "carry")))
    left = sum(1 for s in use if -m(s, "side") > _lim(params, m(s, "carry")))
    return {"landed_in": len(landed), "of": len(use), "out_right": right, "out_left": left, "n": len(shots)}


def _l1_in(shots: list[dict], params: dict) -> dict:
    """インパクトが帯に入った球（作った時点の k と帯で数える）。数えない球は band.shot_status と同じ。"""
    b = params["band"]
    if not b["used"] or b.get("k") is None:
        return {"used": False, "in": 0, "counted": 0}
    k_in = counted = 0
    for s in shots:
        if is_thin(s) or is_extreme(s) or m(s, "face_angle") is None or m(s, "carry") is None:
            continue
        pred = band_mod.side_l1(s, b["k"])
        if pred is None:
            continue
        counted += 1
        k_in += abs(pred) <= _lim(params, m(s, "carry"))
    return {"used": True, "in": k_in, "counted": counted}


def _vals(shots: list[dict], metric: str) -> list[float]:
    return [float(m(s, metric)) for s in shots if m(s, metric) is not None]


def _worst(a_v: list[float], goal: str, b_v: list[float]) -> float:
    """A でいちばん悪かった値（欠けた本番の球を置く値。R7）。reduce_sd は本番の中央から一番遠い A の値。"""
    if goal == "reduce_abs":
        return max(a_v, key=abs)
    if goal == "decrease":
        return max(a_v)
    if goal == "increase":
        return min(a_v)
    c = float(median(b_v)) if b_v else float(median(a_v))
    return max(a_v, key=lambda x: abs(x - c))


def _count(shots: list[dict], kind: str) -> int:
    if kind == "thin":
        return sum(1 for s in shots if is_thin(s))
    return sum(1 for s in shots if dec(s).get("contact") == kind)


def _key(obj) -> str:
    def rnd(x):
        if isinstance(x, float):
            return round(x, 6)
        if isinstance(x, dict):
            return {k: rnd(v) for k, v in sorted(x.items())}
        if isinstance(x, list):
            return [rnd(v) for v in x]
        return x

    blob = json.dumps(rnd(obj), ensure_ascii=False, sort_keys=True) + config.PLAN_VERSION + config.ENGINE_VERSION
    return hashlib.sha256(blob.encode()).hexdigest()


def _fingerprint_blocks(blocks: list[dict]) -> list:
    return [
        [b.get("kind"), b.get("seq_from"), b.get("seq_to"),
         [[s.get("id"), s.get("club"), bool(s.get("excluded")), s.get("metrics") or {}, dec(s).get("contact"), dec(s).get("flags"),
           dec(s).get("predicted_launch_direction"), dec(s).get("predicted_axis_from_face_to_path")] for s in b.get("shots") or []]]
        for b in blocks
    ]


def _params_or_default(plan_in: dict, blocks: list[dict]) -> tuple[dict, bool]:
    """plans.params（作った時点で固定した値）。無い・欠けているときは今の既定値で埋め、埋めたことを返す。

    本来は /v1/plan/build の params をそのまま保存して渡す。埋めた値は作った時点で固定されていないので、
    評価の filled_params に True を立て、画面は「プランの値が無いので今の既定値で見ました」と出す。"""
    given = plan_in.get("params") or {}
    metric = plan_in.get("target_metric")
    cat = given.get("category") or next((s.get("club_category") for b in blocks for s in b.get("shots") or [] if s.get("club_category")), None) or "iron"
    t = config.GOOD_TARGETS.get(cat, config.DEFAULT_TARGET)
    issue = plan_in.get("issue") or ""
    base = {
        "metric": metric,
        "goal": plan_in.get("goal"),
        "mmd": config.MMD.get(metric),
        "category": cat,
        "band": {"used": False, "reason": "no_params", "k": None, "k_source": None, "side_pct": t["side_pct"], "side_min_m": t["side_min_m"]},
        "window": None,
        "stage": None,
        "guardrails": [dict(g) for g in config.DEFAULT_GUARDRAILS],
        "untouched": [],
        "guards": [],
        "counts": COUNT_DEFAULT.get(issue, []),
        "rules": {"worked_runs": config.PLAN_WORKED_RUNS, "settle_runs": config.PLAN_SETTLE_RUNS, "fail_runs": config.PLAN_FAIL_RUNS,
                  "worse_runs": config.PLAN_WORSE_RUNS, "strike_advance_m": config.CENTER_STRIKE_M if issue in ("strike_heel", "strike_toe") else None},
    }
    missing = [k for k in base if k not in given]
    out = {**base, **given}
    if not isinstance(out.get("band"), dict) or "side_pct" not in out["band"]:
        out["band"] = base["band"]
        missing.append("band")
    return out, bool(missing)


# 1回ぶんの判定の言葉（その日の差の大きさ。状態の「効いた」とは別。英語の grade を画面に出さない）
# 大きさ（意味のある差 MMD に届いたか）と確かさ（区間が0をまたがないか）を分けて言う（R4）。
# 前は weak を「小さい」と呼び、MMD を超えた改善でも区間が0をまたぐだけで「小さい」と出ていた
GRADE_JA = {"strong": "はっきりした差", "moderate": "差はあるが、確かさは中くらい", "weak": "向きは良いが、はっきりしない",
            "none": "差なし", "worse": "悪い向き", "insufficient": "球が足りない"}


def size_word(r: dict) -> str:
    """比べた結果の言葉。大きさ（MMD 以上か）と確かさ（区間）を分ける。"""
    g = r.get("grade")
    if g == "insufficient":
        return GRADE_JA[g]
    mmd, imp = r.get("mmd"), r.get("improvement") or 0.0
    big = mmd is not None and imp >= mmd
    lo = (r.get("ci95") or [None, None])[0]
    if g == "strong":
        return "意味のある大きさで、はっきりしている"
    if g == "moderate":
        if lo is not None and lo > 0 and not big and mmd is not None:
            return "はっきりしているが、意味のある大きさより小さい"
        return "意味のある大きさだが、確かさは中くらい"
    if g == "weak":
        return "向きは良く大きさもあるが、はっきりしない" if big else "向きは良いが、小さくはっきりしない"
    return GRADE_JA.get(g, g or "—")
# 「効いた」に数えない理由（counts_as_worked が False になった理由。画面と記録が読む）
NOT_COUNTED = ("guard_broken", "untouched_changed", "measured_dropped", "measured_worst_case", "guard_unknown", "guardrail_unknown",
               "below_mmd", "pending_boundaries")
# 最初に見せる1回ぶんの言葉（数字・専門用語を使わない決まった対応。R1）。「良く見えたが数えない」の理由の言葉
NOT_COUNTED_PLAIN = {
    "guard_broken": "反対の向きへ外れる球が増えました",
    "untouched_changed": "ほかのところが一緒に動きました",
    "measured_dropped": "測れた球が減りました",
    "measured_worst_case": "測れなかった球があり、言い切れません",
    "guard_unknown": "確かめられないところがあります",
    "guardrail_unknown": "飛ぶ距離か振りの速さを確かめられません",
    "below_mmd": "差が小さすぎます",
    "pending_boundaries": "球の区切りをまだ確かめていません",
}
RUN_PLAIN = {
    "strong": ("今日ははっきり差が出ました", "good"),
    "moderate": ("今日は差が出ました", "good"),
    "weak": ("向きは良いですが、はっきりしません", "none"),
    "none": ("今日は変わりませんでした", "none"),
    "worse": ("今日は悪い向きでした", "bad"),
    "insufficient": ("球が足りず、決められませんでした", "warn"),
}


def run_plain(ev: dict) -> dict:
    """1回の練習の最初の言葉（判定の区分と「効いたに数えるか」から決まった対応で作る）。

    良い判定でも「効いた」に数えないときは、良くなったとは言わない（1回の結果を言い切らない。R7・§8.6）。"""
    g = ev.get("grade")
    if g in config.GOOD_GRADES and not ev.get("counts_as_worked"):
        why = next((NOT_COUNTED_PLAIN[k] for k in ev.get("not_counted") or [] if k in NOT_COUNTED_PLAIN), "確かめられないところがあります")
        return {"label": "良く見えましたが、今日は数えません", "why": why, "tone": "warn"}
    label, tone = RUN_PLAIN.get(g, ("—", "none"))
    return {"label": label, "why": None, "tone": tone}
GUARD_WHAT = {"landed_opposite": "反対側へ外れて落ちた球の数", "face_median_window": "フェースの中央値が、まっすぐ落ちる範囲の端を越えないか"}


def _mishit_ids(shots: list[dict], params: dict) -> set:
    """ミスヒットの候補（session.mishit_candidates と同じ規則）。キャリーの中央値は作った時点の値（無ければこの練習の値）。"""
    cm = params.get("carry_median_m")
    if cm is None:
        cs = [m(s, "carry") for s in shots if m(s, "carry") is not None]
        cm = float(median(cs)) if cs else None
    return {x["id"] for x in mishit_candidates(shots, cm)}


def evaluate_run(plan_in: dict, blocks: list[dict], reference: dict | None = None, hand: str = "R") -> dict:
    """POST /v1/plan/evaluate: 1回の練習（1つのセッションの A-B-B-A）の評価。

    plan_in は build_plan の plan（少なくとも club・target_metric・goal・params）。blocks は打った順の範囲と球
    （Go の BlockPayload）。reference はこのプランの物差しの練習の評価（1回目の A が測れた最初の練習。
    無ければ、この練習が物差し）。
    """
    metric, goal = plan_in.get("target_metric"), plan_in.get("goal")
    if goal not in VALID_GOALS:
        raise PlanError(f"goal {goal!r} は使えません")
    if not metric:
        raise PlanError("target_metric がありません")
    params, filled = _params_or_default(plan_in, blocks)
    club = (plan_in.get("club") or "").lower()
    mmd = params.get("mmd", config.MMD.get(metric))
    blocks = sorted(blocks, key=lambda b: (b.get("seq_from") or 0))
    dropped = {"excluded": 0, "other_club": 0, "warmup": 0}
    by: dict[str, list[dict]] = {"baseline": [], "intervention": [], "drill": [], "retention": []}
    rows, first_a = [], None
    for i, b in enumerate(blocks):
        kind = b.get("kind")
        sh = []
        for s in b.get("shots") or []:
            if s.get("excluded"):
                dropped["excluded"] += 1
            elif club and (s.get("club") or "").lower() != club:
                dropped["other_club"] += 1
            else:
                sh.append(s)
        if kind == "warmup":
            dropped["warmup"] += len(sh)
        elif kind in by:
            by[kind].extend(sh)
        if kind == "baseline" and first_a is None:
            first_a = sh
        rows.append({"index": i, "kind": kind, "seq_from": b.get("seq_from"), "seq_to": b.get("seq_to"), "n": len(sh), "measured": len(_vals(sh, metric))})
    A, B, D = by["baseline"], by["intervention"], by["drill"]
    a_v, b_v, d_v = _vals(A, metric), _vals(B, metric), _vals(D, metric)
    primary = {"metric": metric, "goal": goal, **_compare(a_v, b_v, goal, mmd)}

    def share(sh, v):
        return {"n": len(sh), "measured": len(v), "share": (len(v) / len(sh)) if sh else None}

    measured = {"A": share(A, a_v), "B": share(B, b_v), "drill": share(D, d_v)}
    measured["dropped"] = bool(A and B and measured["A"]["share"] is not None and measured["B"]["share"] is not None and measured["A"]["share"] - measured["B"]["share"] >= config.MEASURED_DROP)
    # 欠けは偶然ではない（R7）。一番悪い球ほど値が欠けるので、本番で A の欠け方から見込むより多く欠けた球を、
    # A でいちばん悪かった値に置いても良い判定が残るときだけ「効いた」に数える（割合の差 20% の手前でも黙って比べない）
    worst_case = None
    if A and B and a_v and primary["grade"] in config.GOOD_GRADES:
        miss_a = len(A) - len(a_v)
        extra = (len(B) - len(b_v)) - math.floor(miss_a * len(B) / len(A) + 1e-9)
        if extra >= 1:
            fill_v = _worst(a_v, goal, b_v)
            wc = _compare(a_v, b_v + [fill_v] * extra, goal, mmd)
            worst_case = {"filled": extra, "value": fill_v, "grade": wc["grade"], "improvement": wc.get("improvement"),
                          "holds": wc["grade"] in config.GOOD_GRADES and (mmd is None or (wc.get("improvement") or 0.0) >= mmd)}
    measured["worst_case"] = worst_case
    drill_ref = {"reference": True, **_compare(a_v, d_v, goal, mmd)} if D else None
    guardrails = []
    for g in params.get("guardrails") or []:
        gm = config.MMD.get(g["metric"])
        ga, gb = _vals(A, g["metric"]), _vals(B, g["metric"])
        r = _compare(ga, gb, g["goal"], gm)
        # 「崩れた」は「効いた」と同じ形で数える（向きを逆にした比較が moderate 以上、かつ落ちた量が意味のある差以上）。
        # 前は「区間の上端が0未満 かつ MMD 以上」だけで、良くなった判定より通りにくかった（悪い変化を見落とす側）。
        # 落ちた量が MMD より小さければ数えない（ヘッドスピード 0.3m/s の低下で「止める」にしない）
        worse = False
        if r["grade"] != "insufficient" and (r.get("improvement") or 0.0) < 0:
            rev = _compare(gb, ga, g["goal"], gm)
            worse = rev["grade"] in config.GOOD_GRADES and (gm is None or -(r.get("improvement") or 0.0) >= gm)
        guardrails.append({"metric": g["metric"], "goal": g["goal"], **r, "worse": bool(worse),
                           # 測れなかった（球が足りない）ときは「崩れていない」と黙って扱わない
                           "unknown": r["grade"] == "insufficient"})
    # 副 KPI（記録だけ。判定と状態には使わない）。G2 がぎりぎりの候補で「どちらを選んでももう一方も記録」（§5.4）
    secondary = []
    for sk in params.get("secondary") or []:
        r = _compare(_vals(A, sk["metric"]), _vals(B, sk["metric"]), sk["goal"], config.MMD.get(sk["metric"]))
        secondary.append({"metric": sk["metric"], "goal": sk["goal"], **r})
    # L0（実際に落ちた場所）と帯の数えはミスヒットの候補を除く（R6・§8.2「候補を除いた L0」）
    cand = _mishit_ids(A + B + D, params)
    A0 = [s for s in A if s["id"] not in cand]
    B0 = [s for s in B if s["id"] not in cand]
    mishit = {"A": len(A) - len(A0), "B": len(B) - len(B0), "seqs": sorted(s["seq"] for s in A + B if s["id"] in cand)}
    l0 = {"A": _l0(A0, params), "B": _l0(B0, params)}
    l1_in = {"A": _l1_in(A0, params), "B": _l1_in(B0, params)}
    untouched = []
    for u in params.get("untouched") or []:
        r = _diff(_vals(A, u["metric"]), _vals(B, u["metric"]), u["metric"])
        untouched.append({"metric": u["metric"], **r, "changed": r.get("status") == "changed"})
    guards = []
    window = params.get("window") or {}
    for g in params.get("guards") or []:
        if g["kind"] == "landed_opposite":
            side = g["side"]
            na, nb = l0["A"][f"out_{side}"], l0["B"][f"out_{side}"]
            expect = na * l0["B"]["of"] / l0["A"]["of"] if l0["A"]["of"] else None
            known = expect is not None and l0["B"]["of"] > 0
            broken = known and nb >= expect + config.GUARD_OPPOSITE_EXTRA
            guards.append({"kind": g["kind"], "side": side, "A": na, "A_of": l0["A"]["of"], "B": nb, "B_of": l0["B"]["of"], "expected_B": expect, "broken": bool(broken), "status": "ok" if known else "unknown"})
        elif g["kind"] == "face_median_window":
            fb = _vals(B, "face_angle")
            edge = window.get("face_min") if g["edge"] == "lo" else window.get("face_max")
            med = float(median(fb)) if fb else None
            if edge is None or med is None:
                guards.append({"kind": g["kind"], "side": g["side"], "edge": g["edge"], "median_B": med, "edge_value": edge, "broken": False, "status": "unknown"})
            else:
                broken = med < edge if g["edge"] == "lo" else med > edge
                guards.append({"kind": g["kind"], "side": g["side"], "edge": g["edge"], "median_B": med, "n_B": len(fb), "edge_value": edge, "broken": bool(broken), "status": "ok"})
    counts = [{"kind": k, "A": _count(A, k), "A_of": len(A), "B": _count(B, k), "B_of": len(B), "drill": _count(D, k), "drill_of": len(D)} for k in params.get("counts") or []]

    fa = first_a or []
    fa_v = _vals(fa, metric)
    first = {"n": len(fa), "values": fa_v, **{k: v for k, v in stats.describe(fa_v).items() if k != "n"}, "measured": len(fa_v),
             "median": float(median(fa_v)) if fa_v else None, "usable": len(fa_v) >= config.MIN_BLOCK_N,
             "l1_in": _l1_in([s for s in fa if s["id"] not in cand], params), "l0": _l0([s for s in fa if s["id"] not in cand], params)}
    stage = None
    st = params.get("stage")
    if st and l1_in["B"]["used"]:
        # 段の物差しはプランの1回目の練習の**最初の**「いつも通り」（1ブロック）で帯を判定できた球（§7.3・§8.6）。
        # 最後の「いつも通り」には効果が残って混ざる（§8.3）ので入れない。定着の物差しと同じブロック
        if reference:
            ri = (reference.get("first_a") or {}).get("l1_in") or {}
        else:
            ri = first["l1_in"]
        pb = st["per_block"]
        if ri.get("counted", 0) >= config.STAGE_MIN_COUNTED:
            rate = ri["in"] / ri["counted"]
            reached = rate >= st["goal_share"]
            target = None if reached else min(math.ceil(st["goal_share"] * pb - 1e-9), round(rate * pb) + st["add_per_block"])
            b_rate = l1_in["B"]["in"] / l1_in["B"]["counted"] if l1_in["B"]["counted"] else None
            stage = {
                "shown": True,
                "reference_in": ri["in"], "reference_counted": ri["counted"], "reference_is_this_run": reference is None,
                "per_block": pb, "reference_per_block": rate * pb, "target_per_block": target, "reached_before": reached,
                "B_in": l1_in["B"]["in"], "B_counted": l1_in["B"]["counted"], "B_per_block": b_rate * pb if b_rate is not None else None,
                "met": (b_rate is not None and (b_rate * pb >= target if target is not None else b_rate >= rate)),
            }
        else:
            # 帯を判定できた球が10球に満たない物差しから目標を作らない（§7.3）
            stage = {"shown": False, "reference_counted": ri.get("counted", 0), "min_counted": config.STAGE_MIN_COUNTED,
                     "reference_is_this_run": reference is None, "per_block": pb, "met": False}
    face_plan = metric in ("face_angle", "face_to_path")
    good = primary["grade"] in config.GOOD_GRADES
    not_counted = []
    if any(g["broken"] for g in guards):
        not_counted.append("guard_broken")
    if face_plan and any(u["changed"] for u in untouched):
        not_counted.append("untouched_changed")
    if measured["dropped"]:
        # 欠け方が症状と結びついていると、測れた球だけが良くなって見える（R7）
        not_counted.append("measured_dropped")
    if worst_case and not worst_case["holds"]:
        not_counted.append("measured_worst_case")
    if any(g["status"] == "unknown" for g in guards):
        not_counted.append("guard_unknown")
    if any(g.get("unknown") for g in guardrails):
        # 飛距離や振りの速さを測れず、崩れていないかを確かめられない（黙って「崩れていない」にしない）
        not_counted.append("guardrail_unknown")
    if good and mmd is not None and (primary.get("improvement") or 0.0) < mmd:
        # 判定は「区間が0より上」かつ「改善量が意味のある差（MMD）以上」（DESIGN §3）。moderate には
        # 区間だけ0を越えた小さな差も入るので、それは「効いた」に数えない
        not_counted.append("below_mmd")
    l0_fell = l0["B"]["of"] > 0 and l0["A"]["of"] > 0 and l0["B"]["landed_in"] / l0["B"]["of"] < l0["A"]["landed_in"] / l0["A"]["of"]
    out = {
        "plan_version": config.PLAN_VERSION,
        "engine_version": config.ENGINE_VERSION,
        "evaluation_key": _key({"plan": {"club": club, "metric": metric, "goal": goal, "params": params}, "blocks": _fingerprint_blocks(blocks),
                                "reference": ((reference or {}).get("first_a") or {}).get("l1_in") if reference else None}),
        "grade": primary["grade"],
        "filled_params": filled,
        "primary": primary,
        "counts_as_worked": bool(good and not not_counted),
        "not_counted": not_counted if good else [],
        "guardrail_worse": any(g["worse"] for g in guardrails),
        "drill_moderate_plus": bool(drill_ref and drill_ref["grade"] in config.GOOD_GRADES),
        # A も B も1球も無い（取り込み前・別のセッションに入れた）。練習の回に数えない
        "empty": not A and not B,
        "blocks": rows,
        "stats": {"A": stats.describe(a_v), "B": stats.describe(b_v), "drill": stats.describe(d_v)},
        # 週ごとの数字（進捗）の材料。A（いつも通り）の値だけ（意識して打った B は良く出るので入れない。§8.6）
        "a_values": a_v,
        "excluded": dropped,
        "mishit_excluded": mishit,
        "measured": measured,
        "drill_ref": drill_ref,
        "guardrails": guardrails,
        "secondary": secondary,
        "l0": l0,
        "l1_in": l1_in,
        "l0_fell": bool(l0_fell),
        "untouched": untouched,
        "guards": guards,
        "counts": counts,
        "stage": stage,
        "first_a": first,
    }
    out["claims"] = _run_claims(out, plan_in, hand)
    out["plain"] = run_plain(out)
    return _slim_floats(out)


def _unit(metric: str) -> str:
    return "mm" if metric in ("impact_offset", "impact_height", "low_point") else ("m" if metric in ("carry", "side") else "deg")


def _fine_unit(metric: str) -> str:
    """改善量の単位（mm は小数1桁で書く。0.58mm を「1mm」にしない）。"""
    u = _unit(metric)
    return "mm1" if u == "mm" else u


GOAL_TOWARD_WORD = {"reduce_abs": "ずれの大きさが減る向き", "reduce_sd": "ばらつきが減る向き", "decrease": "{dir:left}へ寄る向き", "increase": "{dir:right}へ寄る向き"}


def _run_claims(ev: dict, plan_in: dict, hand: str) -> list[dict]:
    """評価の定型文（R1）。範囲は番手1本（club:<番手>）で書く（R2）。判定の言葉は日本語（その日の差の大きさ）。"""
    club = plan_in.get("club") or ""
    sid = f"club:{club}"
    fs = Facts()
    out: list[dict] = []

    def f(name, value, unit, n):
        return fs.add(f"run/{name}", value, unit, scope_id=sid, n=n, label=club)

    pr = ev["primary"]
    metric, goal = pr["metric"], pr["goal"]
    word = METRIC_WORD.get(metric, metric)
    na, nb = pr["n"]
    a = f("n_a", na, "count", na)
    b = f("n_b", nb, "count", nb)
    gword = size_word(pr)
    mh = ev.get("mishit_excluded") or {}
    nmis = (mh.get("A") or 0) + (mh.get("B") or 0)
    # ミスヒットの候補は主な指標の比較には含める（候補は薄い当たり・極端に短い・極端な曲がりで、その悪い球こそ
    # 直したいものなので外さない。§8.5 に書いた R6 の例外）。含めたことと数は必ず書く
    mis_note = ""
    if nmis:
        k0 = f("mishit_in_primary", nmis, "count", nmis)
        mis_note = f"ミスヒットの候補 {{n:{k0}}}球も含めて比べています。"
    if pr["grade"] == "insufficient":
        need = f("needed", pr["needed"], "count", pr["needed"])
        _say(out, fs, "run/primary", f"{club}の{word}を測れた球が足りず、判定しません（いつも通り {{n:{a}}}球・本番 {{n:{b}}}球。それぞれ {{n:{need}}}球以上要ります）。", hand)
    else:
        imp = f("improvement", abs(pr["improvement"]), _fine_unit(metric), min(na, nb))
        if pr["improvement"] > 0 and pr["grade"] in config.GOOD_GRADES:
            _say(out, fs, "run/primary", f"本番で{word}の{GOAL_DONE_WORD[goal]}（{{a:{imp}}}・差: {gword}・いつも通り {{n:{a}}}球と本番 {{n:{b}}}球）。{mis_note}", hand)
        elif pr["improvement"] > 0:
            # 1回の結果を「減りました」と言い切らない（R4）。weak / none は偶然と区別できない
            _say(out, fs, "run/primary", f"本番で{word}は{GOAL_TOWARD_WORD[goal]}に動きましたが（{{a:{imp}}}）、はっきりした差ではありません（差: {gword}・いつも通り {{n:{a}}}球と本番 {{n:{b}}}球）。{mis_note}", hand)
        else:
            _say(out, fs, "run/primary", f"本番で{word}は良くなっていません（差: {gword}・いつも通り {{n:{a}}}球と本番 {{n:{b}}}球）。{mis_note}", hand)
        if "below_mmd" in (ev.get("not_counted") or []):
            mm = f("mmd", pr.get("mmd"), _fine_unit(metric), min(na, nb))
            _say(out, fs, "run/below_mmd", f"差は意味のある大きさ（{{a:{mm}}}）に届いていないので、効いたには数えません。", hand)
    ms = ev["measured"]
    if (ms["A"]["n"] and ms["A"]["measured"] < ms["A"]["n"]) or (ms["B"]["n"] and ms["B"]["measured"] < ms["B"]["n"]) or ms["dropped"]:
        # 取れた割合は欠けがあれば必ず並べる（§8.5）。減ったときだけでなく
        am, an_ = f("measured_a", ms["A"]["measured"], "count", ms["A"]["n"]), f("of_a", ms["A"]["n"], "count", ms["A"]["n"])
        bm, bn = f("measured_b", ms["B"]["measured"], "count", ms["B"]["n"]), f("of_b", ms["B"]["n"], "count", ms["B"]["n"])
        if ms["dropped"]:
            tail = "効いたには数えません。" if pr["grade"] in config.GOOD_GRADES else ""
            _say(out, fs, "run/measured", f"{word}が取れた球が、いつも通りは{{n:{an_}}}球中{{n:{am}}}球、本番は{{n:{bn}}}球中{{n:{bm}}}球に減っています。測れた球だけが良くなっている見込みがあります。{tail}", hand)
        else:
            _say(out, fs, "run/measured", f"{word}が取れた球: いつも通り {{n:{an_}}}球中{{n:{am}}}球、本番 {{n:{bn}}}球中{{n:{bm}}}球。", hand)
    wc = ms.get("worst_case")
    if wc:
        fx = f("worst_case.filled", wc["filled"], "count", wc["filled"])
        if wc["holds"]:
            _say(out, fs, "run/worst_case", f"本番で測れなかった{{n:{fx}}}球を、いつも通りでいちばん悪かった値に置いても、良い判定は残りました。", hand)
        else:
            _say(out, fs, "run/worst_case", f"本番で測れなかった{{n:{fx}}}球を、いつも通りでいちばん悪かった値に置くと、良い判定が残りません。測れなかった球が悪かったかもしれないので、効いたには数えません。", hand)
    exc = (ev.get("excluded") or {}).get("excluded") or 0
    if exc:
        ex = f("excluded", exc, "count", exc)
        _say(out, fs, "run/excluded", f"あなたが除外した{{n:{ex}}}球は入れていません。", hand)
    if ev["drill_ref"] and ev["drill_ref"]["grade"] != "insufficient":
        _say(out, fs, "run/drill", f"参考（判定には使いません）: ドリル中の差は「{size_word(ev['drill_ref'])}」でした。", hand)
    for g in ev["guardrails"]:
        if g["worse"]:
            what = GUARDRAIL_WORD.get(g["metric"], g["metric"])
            tail = "当てにいっているかもしれません。" if g["metric"] == "club_speed" else "飛距離が落ちています。"
            _say(out, fs, f"run/guardrail.{g['metric']}", f"本番で{what}が落ちました（意味のある差より大きく下がっています）。{tail}", hand)
        elif g.get("unknown"):
            what = GUARDRAIL_WORD.get(g["metric"], g["metric"])
            tail = "効いたには数えません。" if pr["grade"] in config.GOOD_GRADES else ""
            _say(out, fs, f"run/guardrail_unknown.{g['metric']}", f"{what}を測れた球が足りず、落ちていないかを確かめられません。{tail}", hand)
    for sk in ev.get("secondary") or []:
        sw = METRIC_WORD.get(sk["metric"], sk["metric"])
        if sk["grade"] == "insufficient":
            _say(out, fs, f"run/secondary.{sk['metric']}", f"一緒に記録する{sw}は、測れた球が足りず判定しません（記録だけで、判定には使いません）。", hand)
        else:
            s1, s2 = f(f"secondary.{sk['metric']}.n_a", sk["n"][0], "count", sk["n"][0]), f(f"secondary.{sk['metric']}.n_b", sk["n"][1], "count", sk["n"][1])
            _say(out, fs, f"run/secondary.{sk['metric']}", f"一緒に記録する{sw}の差は「{size_word(sk)}」でした（いつも通り {{n:{s1}}}球と本番 {{n:{s2}}}球。記録だけで、判定には使いません）。", hand)
    l0 = ev["l0"]
    mh = ev.get("mishit_excluded") or {}
    if l0["A"]["of"] and l0["B"]["of"]:
        ai, ao = f("l0.a", l0["A"]["landed_in"], "count", l0["A"]["of"]), f("l0.a_of", l0["A"]["of"], "count", l0["A"]["of"])
        bi, bo = f("l0.b", l0["B"]["landed_in"], "count", l0["B"]["of"]), f("l0.b_of", l0["B"]["of"], "count", l0["B"]["of"])
        excl = ""
        if mh.get("A") or mh.get("B"):
            k = f("mishit", (mh.get("A") or 0) + (mh.get("B") or 0), "count", (mh.get("A") or 0) + (mh.get("B") or 0))
            excl = f"（ミスヒットの候補 {{n:{k}}}球を除く）"
        _say(out, fs, "run/l0", f"実際に狙いの幅へ落ちた球{excl}: いつも通り {{n:{ao}}}球中{{n:{ai}}}球、本番 {{n:{bo}}}球中{{n:{bi}}}球。", hand)
        # 打点のプランでは打点そのものが指標なので「打点以外が効いている」は言わない（フェースのプランだけ）
        if metric in ("face_angle", "face_to_path") and pr["grade"] in config.GOOD_GRADES and l0["B"]["landed_in"] / l0["B"]["of"] <= l0["A"]["landed_in"] / l0["A"]["of"]:
            _say(out, fs, "run/l0_flat", "インパクトの数字は良くなりましたが、実際に狙いの幅へ落ちた球は増えていません（インパクト以外の打点や計測が効いている見込みがあります）。", hand)
    for u in ev["untouched"]:
        if u["changed"]:
            d = f(f"untouched.{u['metric']}", u["diff"], "deg_lat", min(u["a"]["n"], u["b"]["n"]))
            _say(out, fs, f"run/untouched.{u['metric']}", f"代わりに別のものが動きました: {METRIC_WORD.get(u['metric'], u['metric'])}が {{v:{d}}} 変わっています。", hand)
    for g in ev["guards"]:
        if g["broken"]:
            near = "right" if g["side"] == "left" else "left"
            _say(out, fs, f"run/guard.{g['kind']}", f"{{dir:{near}}}の球は減ったかもしれませんが、{{dir:{g['side']}}}へ外れる球が増えました。効いたには数えません。", hand)
        elif g["status"] == "unknown" and pr["grade"] in config.GOOD_GRADES:
            _say(out, fs, f"run/guard_unknown.{g['kind']}", f"動かさないもの（{GUARD_WHAT.get(g['kind'], g['kind'])}）を確かめられないので、効いたには数えません。", hand)
    for c in ev["counts"]:
        k1, o1 = f(f"count.{c['kind']}.a", c["A"], "count", c["A_of"]), f(f"count.{c['kind']}.a_of", c["A_of"], "count", c["A_of"])
        k2, o2 = f(f"count.{c['kind']}.b", c["B"], "count", c["B_of"]), f(f"count.{c['kind']}.b_of", c["B_of"], "count", c["B_of"])
        _say(out, fs, f"run/count.{c['kind']}", f"{COUNT_WORD.get(c['kind'], c['kind'])}: いつも通り {{n:{o1}}}球中{{n:{k1}}}球、本番 {{n:{o2}}}球中{{n:{k2}}}球（判定はしません）。", hand)
    st = ev["stage"]
    if st and st.get("shown"):
        pb = f("stage.per_block", st["per_block"], "count", st["per_block"])
        ri, rc = f("stage.ref_in", st["reference_in"], "count", st["reference_counted"]), f("stage.ref_counted", st["reference_counted"], "count", st["reference_counted"])
        bi, bc = f("stage.b_in", st["B_in"], "count", st["B_counted"]), f("stage.b_counted", st["B_counted"], "count", st["B_counted"])
        base = "この練習" if st["reference_is_this_run"] else "プランの1回目"
        if st["target_per_block"] is None:
            _say(out, fs, "run/stage", f"帯に入る球は、{base}の「いつも通り」で{{n:{rc}}}球中{{n:{ri}}}球と、もうゴールに届いていました。本番では{{n:{bc}}}球中{{n:{bi}}}球でした。", hand)
        else:
            t = f("stage.target", st["target_per_block"], "count", st["per_block"])
            _say(out, fs, "run/stage", f"帯に入る球の目標は{{n:{pb}}}球あたり{{n:{t}}}球です（物差しは{base}の「いつも通り」: {{n:{rc}}}球中{{n:{ri}}}球）。本番では{{n:{bc}}}球中{{n:{bi}}}球でした。", hand)
    elif st:
        rc = f("stage.ref_counted", st["reference_counted"], "count", st["reference_counted"])
        mn = f("stage.min_counted", st["min_counted"], "count", st["min_counted"])
        base = "この練習" if st["reference_is_this_run"] else "プランの1回目"
        _say(out, fs, "run/stage", f"帯に入る球の目標は、まだ作りません（{base}の「いつも通り」で帯を判定できた球が{{n:{rc}}}球で、{{n:{mn}}}球に届いていません）。", hand)
    _say(out, fs, "run/last_a", "最後の「いつも通り」には効果が残って混ざるので、効いたぶんは小さく見えます。", hand)
    return out


def _pending_claim(ev: dict, plan_in: dict, hand: str) -> dict:
    club = plan_in.get("club") or ""
    fs = Facts()
    return build_claim(Claim(id="run/pending", section="plan", layer="L1_plan",
                             template="境目は予定の球数で区切ったままで、打った球の数とも合いません。球の並びで境目を確かめるまで、効いたには数えません。"), fs, hand)


# ---------------------------------------------------------------- 状態の移り方（§8.6）

# 状態の見出しと、進捗の面でだけ言う文（最初の面は見出しと「次の手」の1行だけ。同じことを2回言わない）。
# どれも数字・専門用語・英字を使わない（gist.check_plain をテストで全部に当てる。§0）
STATE_TEXT = {
    "checking": ("確かめ中", "別の日に続けて良くなると「効いた」です。"),
    "maybe": ("効いたかも", "あと一回、別の日に良くなれば「効いた」です。"),
    "worked": ("効いた", "別の日に続けて良くなりました。"),
    "settled": ("定着した", "何もしていない最初の球でも良くなっています。"),
    "not_transferred": ("移せていない", "道具があるとできています。道具なしに移す段です。"),
    "failed": ("効かなかった", "このやり方では届きませんでした。"),
    "stuck": ("詰まった", "計測器の数字で見える範囲ではここまでです。"),
    "stop": ("止める", "飛ぶ距離か振りの速さが、続けて落ちました。"),
    "insufficient": ("足りない", "判定に要る球が足りませんでした。"),
    "pending": ("区切りを確かめる", "予定と打った球の数が合いません。区切りを確かめると判定します。"),
}
# 「止める」の文は、どの見張りが崩れたかで決まった文にする（§8.6 の文案）
STOP_TEXT = {
    "club_speed": "当てにいって、振りが弱くなっています。",
    "carry": "飛ぶ距離が落ちています。",
}
NEXT_ACTION = {
    "checking": "same_template",
    "maybe": "same_template",
    "worked": "check_retention",
    "settled": "recompute_candidates",
    "not_transferred": "alternate_template",
    "failed": "switch_drill",
    "stuck": "video_or_coach",
    "stop": "ask_continue",
    "insufficient": "more_shots",
    "pending": "confirm_blocks",
}
# 次の手の1行（最初の面に出す）
NEXT_TEXT = {
    "same_template": "次の練習も同じ組み方で打ちます。",
    "check_retention": "次は、何もしていない最初の球に残るかを見ます。",
    "recompute_candidates": "最新のセッションで候補を選び直して、次へ進みます。",
    "alternate_template": "ドリルと本番を一球ずつ交互に打つ組み方に替えます。",
    "switch_drill": "別のやり方に替えます。",
    "video_or_coach": "動画で見るか、コーチに見てもらう段階です。",
    "ask_continue": "続けるか、やめるかを選んでください。",
    "more_shots": "次は球を増やして打ちます。",
    "confirm_blocks": "球の並びで区切りを確かめてください。",
}


def _ok(ev: dict) -> bool:
    return bool(ev.get("counts_as_worked"))


def _judged(ev: dict) -> bool:
    """判定できた回（球が足りない・境目が確かめられていない・空の回は、効いた／効かなかったの回数に入れない）。"""
    return ev.get("grade") not in (None, "insufficient") and not ev.get("empty") and not ev.get("pending_boundaries")


def usable_reference(evs: list[dict]) -> int | None:
    """物差し（プランの1回目の A）にする練習の番号。最初の「いつも通り」が MIN_BLOCK_N 球以上測れた最初の回。

    1回目が空（取り込み前）や数球だけだと、そのあと何回効いても比べられず「定着した」に届かないため。"""
    for i, e in enumerate(evs):
        fa = e.get("first_a") or {}
        if not e.get("empty") and (fa.get("measured") if fa.get("measured") is not None else len(fa.get("values") or [])) >= config.MIN_BLOCK_N:
            return i
    return None


def _iso_week(date: str) -> str | None:
    from datetime import date as _date

    try:
        y, w, _ = _date.fromisoformat(date[:10]).isocalendar()
    except (ValueError, TypeError):
        return None
    return f"{y}-W{w:02d}"


def _weekly(runs: list[dict], evs: list[dict], goal: str) -> list[dict]:
    """週ごとの数字: その週の練習の「いつも通り」（A）の値を合わせて1点。5球未満の週は点を打たない（§8.6）。

    プランの外で打った球は、まだ数えていない（練習のセッションの A だけ）。"""
    key = {"reduce_abs": "mean_abs", "reduce_sd": "sd"}.get(goal, "mean")
    by: dict[str, dict] = {}
    for r, e in zip(runs, evs):
        wk = _iso_week(r.get("date") or "")
        if not wk:
            continue
        x = by.setdefault(wk, {"week": wk, "from": r.get("date"), "to": r.get("date"), "values": []})
        x["from"], x["to"] = min(x["from"], r.get("date")), max(x["to"], r.get("date"))
        x["values"] += list(e.get("a_values") or [])
    out = []
    for wk in sorted(by):
        x = by[wk]
        d = stats.describe(x["values"])
        ok = d.get("n", 0) >= config.MIN_BLOCK_N
        out.append({"week": wk, "from": x["from"], "to": x["to"], "n": d.get("n", 0), "value": d.get(key) if ok else None, "shown": ok})
    return out


def _next_counts(plan_in: dict, more: int) -> list[int] | None:
    """「足りない」のとき、次の型の球数（いつも通り・本番の段に足す。1段 BLOCK_N_MAX 球まで）。増やせなければ None。"""
    tpl = plan_in.get("template") or []
    if not tpl or more <= 0:
        return None
    add = math.ceil(more / 2)
    out, grew = [], False
    for b in tpl:
        n = int(b.get("n") or 0)
        if b.get("kind") in ("baseline", "intervention"):
            n2 = min(config.BLOCK_N_MAX, n + add)
            grew = grew or n2 > n
            n = n2
        out.append(n)
    return out if grew else None


def progress(plan_in: dict, runs: list[dict], history: list[dict] | None = None, continue_after_stop: bool = False, hand: str = "R",
             continue_after_run: int | None = None) -> dict:
    """POST /v1/plan/progress: 練習の並び（古い順。{run_id, session_id, date, evaluation}）から、状態・次の手・推移を出す。

    - 空の回（取り込み前・別のセッションに入れた）は練習の回に数えない。
    - 「効いた」「効かなかった」は判定できた回だけで数える（球が足りない回・境目を確かめていない回は入れない）。
      回数はどちらも別の日で数える（同じ日の3回で「効かなかった」にしない）。
    - 「効いた」は直近の判定できた2回（別の日）が続けて効いたとき。一度付いても、そのあと崩れれば外れる。
    - history はこの選手のほかのプラン（{issue, drill_id, state}）。同じ issue で別のやり方（ドリル、または自分で
      書いた1点どうしは別のプラン）が効かなかった記録があれば「詰まった」。
    - continue_after_run は「止める」のあとに本人が続けると選んだとき、その時点で最後だった run の id。
      ガードレールの worse はそれより後の回だけ数える（continue_after_stop は古い形。全部を数えない）。"""
    metric, goal = plan_in.get("target_metric"), plan_in.get("goal")
    params = plan_in.get("params") or {}
    rules = params.get("rules") or {}
    mmd = params.get("mmd", config.MMD.get(metric))
    runs = [r for r in runs if r.get("evaluation") and not r["evaluation"].get("empty")]
    evs = [r["evaluation"] for r in runs]
    dates = [r.get("date") or "" for r in runs]
    n_runs = len(runs)
    judged = [i for i in range(n_runs) if _judged(evs[i])]

    need = rules.get("worked_runs", config.PLAN_WORKED_RUNS)

    def window_ok(w: list[int]) -> bool:
        return all(_ok(evs[j]) and not evs[j].get("guardrail_worse") for j in w) and len({dates[j] for j in w}) == need

    worked_at = None
    for k in range(need - 1, len(judged)):
        if window_ok(judged[k - need + 1:k + 1]):
            worked_at = judged[k]
            break
    worked_now = len(judged) >= need and window_ok(judged[-need:])

    # 定着: 効いたあと、別の日の練習で「最初の A」が物差し（1回目の A が測れた最初の練習）より moderate 以上
    ref_i = usable_reference(evs)
    base = (evs[ref_i].get("first_a") or {}).get("values") if ref_i is not None else None
    settle = []
    for j in range(n_runs):
        fa = (evs[j].get("first_a") or {}).get("values") or []
        cmp = _compare(base or [], fa, goal, mmd) if ref_i is not None and j > ref_i and base else None
        # 区切りを確かめていない回（pending）は定着にも数えない（A の球が準備に入るなどのずれを混ぜない）
        settle.append({"run_id": runs[j].get("run_id"), "date": dates[j], "compare": cmp,
                       "ok": bool(cmp and cmp["grade"] in config.GOOD_GRADES and not evs[j].get("pending_boundaries"))})
    settle_need = rules.get("settle_runs", config.PLAN_SETTLE_RUNS)
    settled = False
    if worked_at is not None:
        streak_dates = []
        for j in range(n_runs - 1, worked_at, -1):
            if not settle[j]["ok"]:
                break
            streak_dates.append(dates[j])
        settled = len(set(streak_dates)) >= settle_need

    start = 0
    if continue_after_run is not None:
        ids = [r.get("run_id") for r in runs]
        start = ids.index(continue_after_run) + 1 if continue_after_run in ids else n_runs
    elif continue_after_stop:
        start = n_runs
    gr_worse = len({dates[j] for j in judged if j >= start and evs[j].get("guardrail_worse")})
    worse = len({dates[j] for j in judged if evs[j].get("grade") == "worse"})
    judged_dates = len({dates[j] for j in judged})
    last = evs[-1] if evs else None
    last2 = judged[-2:]
    drill_ok_last2 = len(last2) == 2 and all(evs[j].get("drill_moderate_plus") and not _ok(evs[j]) for j in last2)
    worse_runs = rules.get("worse_runs", config.PLAN_WORSE_RUNS)
    fail_runs = rules.get("fail_runs", config.PLAN_FAIL_RUNS)

    if gr_worse >= worse_runs:
        state = "stop"
    elif last and last.get("pending_boundaries"):
        # いちばん新しい回の区切りが確かめられていなければ、それを先に頼む（その回は判定に入れていない）
        state = "pending"
    elif settled:
        state = "settled"
    elif worked_now:
        state = "worked"
    elif drill_ok_last2:
        state = "not_transferred"
    elif worse >= worse_runs or (worked_at is None and judged_dates >= fail_runs):
        state = "failed"
        issue = plan_in.get("issue")
        mine = plan_in.get("drill_id") or ""
        # 別のやり方で効かなかった同じ issue のプラン（自分で書いた1点どうしは、プランが違えば別のやり方）
        others = [h for h in history or [] if h.get("issue") == issue and h.get("state") in ("failed", "stuck")
                  and ((h.get("drill_id") or "") != mine or not mine)]
        if others:
            state = "stuck"
    elif last and last.get("grade") == "insufficient":
        state = "insufficient"
    elif last and _ok(last):
        state = "maybe"
    else:
        state = "checking"

    trend = []
    for r, e in zip(runs, evs):
        fa = e.get("first_a") or {}
        bb = ((e.get("primary") or {}).get("n") or [0, 0])
        trend.append({
            "run_id": r.get("run_id"), "session_id": r.get("session_id"), "date": r.get("date"),
            "first_a": {k: fa.get(k) for k in ("n", "measured", "mean", "mean_abs", "sd", "median")},
            "B": {**((e.get("stats") or {}).get("B") or {"n": bb[1] if len(bb) > 1 else 0}), **{k: v for k, v in (e.get("primary") or {}).items() if k in ("improvement", "ci95")}},
            "grade": e.get("grade"),
            "counts_as_worked": _ok(e),
            "judged": _judged(e),
            "guardrail_worse": bool(e.get("guardrail_worse")),
            "l1_in_B": (e.get("l1_in") or {}).get("B"),
            "l0_B": (e.get("l0") or {}).get("B"),
        })
    # 次に進む条件（§8.2）
    adv = {"ok": state == "settled", "by": "settled" if state == "settled" else None}
    sa = rules.get("strike_advance_m")
    if not adv["ok"] and sa:
        # ステップ1: 「効いた」か、最初の A の打点が芯 10mm 以内（MIN_BLOCK_N 球以上測れたとき。1球では決めない）
        fa = next((e.get("first_a") or {} for e in reversed(evs) if (e.get("first_a") or {}).get("usable") or len((e.get("first_a") or {}).get("values") or []) >= config.MIN_BLOCK_N), None)
        if state == "worked":
            adv = {"ok": True, "by": "worked"}
        elif fa and fa.get("median") is not None and len(fa.get("values") or []) >= config.MIN_BLOCK_N and abs(fa["median"]) <= sa:
            adv = {"ok": True, "by": "first_a_center"}
    # ステップ2（フェース）: 段の目標を満たし、実際に帯へ落ちた球が減っておらず、動かさないものが崩れていない。
    # 1回の moderate は効果が無くても1割前後出るので、「効いた」（別の日に2回）になってから見る（設計 §8.2 に書いた）
    if not adv["ok"] and state == "worked" and metric == "face_angle" and params.get("stage") and last:
        st = last.get("stage") or {}
        if st.get("met") and not last.get("l0_fell") and not any(g.get("broken") for g in last.get("guards") or []):
            adv = {"ok": True, "by": "stage_met"}
    title, text = STATE_TEXT[state]
    if state == "stop":
        broke = {g.get("metric") for j in judged if j >= start and evs[j].get("guardrail_worse")
                 for g in evs[j].get("guardrails") or [] if g.get("worse")}
        if len(broke) == 1 and next(iter(broke)) in STOP_TEXT:
            text = STOP_TEXT[next(iter(broke))]
    out = {
        "plan_version": config.PLAN_VERSION,
        "state": state,
        "title": title,
        "text": text,
        "next_action": NEXT_ACTION[state],
        "next_text": NEXT_TEXT[NEXT_ACTION[state]],
        "n_runs": n_runs,
        "n_judged": len(judged),
        "worked_at_run": runs[worked_at].get("run_id") if worked_at is not None else None,
        "reference_run": runs[ref_i].get("run_id") if ref_i is not None else None,
        "settle": {"need": settle_need, "runs": [x for j, x in enumerate(settle) if ref_i is not None and j > ref_i]},
        "advance": adv,
        "trend": trend,
        "weekly": _weekly(runs, evs, goal),
        "record": {"drill_id": plan_in.get("drill_id"), "runs": n_runs, "judged": len(judged), "moderate_plus": sum(1 for j in judged if evs[j].get("grade") in config.GOOD_GRADES),
                   # 「良くなった回」は効いたに数えた回だけ（良い判定でも数えなかった回を入れない）
                   "counted": sum(1 for j in judged if _ok(evs[j]))},
        "rules": {"worked_runs": need, "settle_runs": settle_need, "fail_runs": fail_runs},
    }
    notes = []
    if n_runs and ref_i is None:
        notes.append("物差し（プランの1回目の「いつも通り」）にできる練習がまだありません（最初の「いつも通り」で5球以上測れた回から物差しにします）。")
    elif ref_i:
        notes.append("最初の練習の「いつも通り」は測れた球が少なかったので、測れた最初の練習を物差しにしました。")
    if worked_at is not None and not worked_now and state not in ("settled", "stop", "failed", "stuck"):
        notes.append("前に「効いた」になりましたが、直近の2回は続けて良くなっていません。もう一度同じ型で確かめます。")
    if state == "insufficient" and last:
        pr = last.get("primary") or {}
        n = pr.get("n") or [0, 0]
        out["more_shots"] = max(0, (pr.get("needed") or config.MIN_BLOCK_N) - min(n))
        nc = _next_counts(plan_in, out["more_shots"])
        out["next_counts"] = nc
        if nc is None:
            notes.append("1ブロックの上限（12球）なので、これ以上は球を増やせません。測れなかった球（打点が「-」など）を「1球ずつ」で入れてください。")
    if state == "stuck":
        d = drills.get(plan_in.get("drill_id") or "")
        out["video_checks"] = [drills.render(v, hand) for v in (d or {}).get("video_checks") or []]
    if state == "failed" and last and last.get("drill_ref") and last["drill_ref"].get("grade") not in config.GOOD_GRADES:
        notes.append("このドリルではクラブの動きが変わっていません（ドリル中も動いていません）。")
    if notes:
        out["note"] = notes[0]
        out["notes"] = notes
    return _slim_floats(out)


def evaluate_request(body: dict) -> dict:
    """POST /v1/plan/evaluate の入口。2つの形を受ける。

    - {plan, blocks, reference?}
    - Go の形: {plan, run: {id, index, session_id, date, blocks_source, club_shots, planned},
      experiment: {target_metric, goal, blocks}, past_runs: [{run_id, index, session_id, date, evaluation}],
      continue_after_run?}
      このときは past_runs のうち、最初の「いつも通り」が測れた最初の run の評価を物差し（1回目の A）にし、
      past_runs ＋ この run で状態の移り方（progress）も付けて返す。
    """
    plan_in = dict(body.get("plan") or {})
    exp = body.get("experiment") or {}
    if not plan_in.get("target_metric"):
        plan_in["target_metric"] = exp.get("target_metric")
    if not plan_in.get("goal"):
        plan_in["goal"] = exp.get("goal")
    blocks = body.get("blocks") or exp.get("blocks") or []
    hand = body.get("handedness") or "R"
    run = body.get("run") or {}
    past = sorted([r for r in body.get("past_runs") or [] if r.get("evaluation") and not r["evaluation"].get("empty")],
                  key=lambda r: (r.get("index") if r.get("index") is not None else 0))
    reference = body.get("reference")
    if reference is None and past:
        ri = usable_reference([r["evaluation"] for r in past])
        if ri is not None and (run.get("index") is None or run["index"] > (past[ri].get("index") or 0)):
            reference = past[ri]["evaluation"]
    ev = evaluate_run(plan_in, blocks, reference, hand)
    if run:
        ev["run"] = {k: run.get(k) for k in ("id", "index", "session_id", "date", "blocks_source", "club_shots", "planned")}
        # 取り込んだ球数と予定の合計が違えば、画面は黄色で出す（§8.4）
        ev["planned_mismatch"] = bool(run.get("planned") and run.get("club_shots") is not None and run["club_shots"] != run["planned"])
        ev["blocks_provisional"] = run.get("blocks_source") == "planned"
        # 予定の球数で区切っただけで、打った数とも合わない境目では「効いた」に数えない（A の球が準備に入るなどの
        # ずれを判定に混ぜない）。人が境目を確かめて保存すれば数える
        if ev["blocks_provisional"] and ev["planned_mismatch"]:
            ev["pending_boundaries"] = True
            if ev["counts_as_worked"]:
                ev["not_counted"] = list(ev.get("not_counted") or []) + ["pending_boundaries"]
            ev["counts_as_worked"] = False
            ev["claims"].insert(0, _pending_claim(ev, plan_in, hand))
            ev["plain"] = run_plain(ev)
    if run or past:
        runs = [{"run_id": r.get("run_id"), "session_id": r.get("session_id"), "date": r.get("date"), "evaluation": r["evaluation"]} for r in past]
        runs.append({"run_id": run.get("id"), "session_id": run.get("session_id"), "date": run.get("date"), "evaluation": ev})
        car = body.get("continue_after_run")
        ev["progress"] = progress(plan_in, runs, body.get("history"), bool(body.get("continue_after_stop")), hand,
                                  continue_after_run=int(car) if car not in (None, "", 0) else None)
    return ev


# ---------------------------------------------------------------- 動きのプラン（docs/DESIGN_v2.md §8.3・段4）

# 見出しは STATE_TEXT を使い回し、2つ目の文だけ動きの話に替える（球の文を出すと嘘になる）。
# stuck と pending は動きのプランでは使わない。どれも数字・専門用語・英字を使わない（テストで check_plain を当てる）
STATE_TEXT_MOTION = {
    "checking": "練習の最後の撮影で、範囲に入る回数を数えます。",
    "maybe": "別の日にもう一回合格すれば「効いた」です。",
    "worked": "別の日に続けて合格しました。次は、意識しない最初の撮影に残るかを見ます。",
    "settled": "意識しない最初の撮影でも合格しました。",
    "not_transferred": "意識するとできています。意識しない最初の撮影に移す段です。",
    "failed": "何回撮り直しても合格に届きませんでした。やり方を替える段です。",
    "stop": "飛ぶ距離か振りの速さが、続けて落ちました。",
    "insufficient": "判定できたスイングが八回に届きませんでした。",
}
MOTION_FAIL_DAYS = 4  # 別の日に続けて合格しない回数（ガイドの「2〜4回」の上の端）
MOTION_NOT_TRANSFERRED = 2
MOTION_NEXT_TEXT = {
    "adjust_feel": "感覚の量を変えて、もう一回撮ります。",
    "try_baseline": "次は、練習の最初の撮影（いつも通り）で残っているかを見ます。",
    "next_item": "次の項目へ進む候補を出します。",
}


def motion_progress(tests: list[dict]) -> dict:
    """動きのプランの状態（§8.3 の表）。tests は10球テストの並び（古い順）:
    {date, block: test | baseline, passed: true | false | null, next_hint}。

    - その日の⑥（test）で合格 → 効いたかも。別の日の⑥でもう一回 → 効いた。
    - ①（baseline・意識しない）で合格 → 定着した（⑥で合格したあと）。
    - ⑥は合格・①は不合格が2回 → 移せていない。
    - ⑥が別の日に4回続けて合格しない → 効かなかった（ドリルを替えるかコーチ）。2・3回目は感覚の量を変える。
    - 最後のテストが判定できない → 足りない。
    """
    tests = [t for t in tests if isinstance(t, dict)]
    pass_days: list[str] = []
    fail_streak_days: list[str] = []
    base_fail = 0
    settled = False
    for t in tests:
        d = (t.get("date") or "")[:10]
        if t.get("passed") is None:
            continue
        if t.get("block") == "baseline":
            if t["passed"] and pass_days:
                settled = True
            elif not t["passed"] and pass_days:
                base_fail += 1
            continue
        if t["passed"]:
            if d not in pass_days:
                pass_days.append(d)
            fail_streak_days = []
        elif d not in fail_streak_days:
            fail_streak_days.append(d)
    last = tests[-1] if tests else None
    if settled:
        state = "settled"
    elif last and last.get("passed") is None:
        state = "insufficient"
    elif len(fail_streak_days) >= MOTION_FAIL_DAYS:
        state = "failed"
    elif pass_days and base_fail >= MOTION_NOT_TRANSFERRED:
        state = "not_transferred"
    elif len(pass_days) >= 2:
        state = "worked"
    elif pass_days:
        state = "maybe"
    else:
        state = "checking"
    # 判定できないのは「練習が足りない」ではなく「撮った本数が足りない」。見出しは十球テストの結果と同じ「撮り直し」
    head = "撮り直し" if state == "insufficient" else STATE_TEXT[state][0]
    action = {"checking": "same_template", "maybe": "same_template", "worked": "try_baseline", "settled": "next_item",
              "not_transferred": "alternate_template", "failed": "switch_drill", "insufficient": "more_shots"}.get(state, "same_template")
    if state in ("checking", "maybe") and 2 <= len(fail_streak_days) < MOTION_FAIL_DAYS:
        action = "adjust_feel"
    text = MOTION_NEXT_TEXT.get(action) or NEXT_TEXT.get(action, "")
    if state == "insufficient":
        text = "判定できるように撮り直します。"
    out = {"version": config.PLAN_VERSION, "kind": "motion", "state": state, "head": head, "title": head, "text": STATE_TEXT_MOTION[state],
           "next_action": action, "next_text": text, "pass_days": pass_days, "fail_days": len(fail_streak_days)}
    if state == "failed":
        out["next_options"] = [{"action": "switch_drill", "text": NEXT_TEXT["switch_drill"]}, {"action": "video_or_coach", "text": NEXT_TEXT["video_or_coach"]}]
        out["why"] = "二回から四回撮り直しても変わらないことがあります。ドリルを替えるか、コーチに見てもらう段です。約束はできません。"
    return out
