"""TrackMan の2日の再確認（docs/DESIGN_v2.md §8.4・段4）。判定を2つ作らない。

- 項目がプランの目標・番手と同じなら、プランの評価（plan_runs.evaluation_json の状態）と STATE_TEXT の
  言葉をそのまま出す。compare._diff は呼ばない。
- それ以外（見張り・飛ぶ距離の行）だけを compare._diff で計算し、verdict で4つの言葉にする:
  良くなった／悪くなった／変わらない／判断できない。**`same` を「変わらない」と読まない。**
- 球数の見積もりは言葉ごとに出す（区間の半幅 h = 1.96×SD×√(2/n)）。
  「良くなった」を見分けるには h ≤ 2×MMD、「変わらない」と言うには h ≤ MMD。
- フェースの向きは1日では判断できない（要る球が数百）を既定にし、件数を並べるだけ。
- 振りの強さが違う日（振りの速さの差が MMD の2倍以上）は、飛ぶ距離を「判断できない」にする。
"""

from __future__ import annotations

import math
from collections import Counter

from . import compare, config
from .shots import is_practice

VERSION = "checkup/0.1"
Z = 1.96
FACE_METRICS = ("face_angle", "face_to_path")

# 最初の面に出す名前（数字・専門用語を使わない。PLAIN_FORBIDDEN を避ける）
PLAIN_NAME = {
    "impact_offset": "当たる場所", "impact_height": "当たる高さ", "face_angle": "クラブの面の向き", "face_to_path": "曲がり方",
    "club_path": "振る向き", "spin_axis": "曲がり方", "side": "左右のずれ", "launch_direction": "出る向き", "carry": "飛ぶ距離",
    "club_speed": "振りの速さ", "ball_speed": "球の速さ", "smash_factor": "当たりの効率", "attack_angle": "入る角度",
    "dynamic_loft": "当たるときの面の傾き", "spin_loft": "当たるときの面の傾き", "launch_angle": "出る高さ", "spin_rate": "球の回転",
}
WORD = {"better": "良くなった", "worse": "悪くなった", "same": "変わらない", "unknown": "判断できない"}
TEXT = {
    "better": "前の日より良い向きに変わりました。",
    "worse": "前の日より悪い向きに変わりました。",
    "same": "変わっていないと言える球数を打てています。",
    "unknown_enough_for_change": "この日の球数では「変わらない」とまでは言えません。変化があれば分かる数は打てています。",
    "unknown_few": "球が足りず、決められません。",
    "unknown_face": "一日の球では決められません。範囲に入った球の数だけ並べます。",
    "unknown_strength": "振りの強さが違う日なので、比べません。",
    "unknown_sd": "ばらつきは、二つの日の球だけでは比べません。",
}
REGRESSION_NOTE = "基準の日は診断した日（いちばん悪く出た日）なので、何もしなくても次は少し良く出やすい点に注意してください。"


def need_n(sd: float | None, mmd: float | None) -> dict | None:
    """言葉ごとに要る球数（1日あたり）。h = Z×SD×√(2/n) ≤ 2×MMD（良くなった）／ ≤ MMD（変わらない）。"""
    if not sd or not mmd or sd <= 0 or mmd <= 0:
        return None
    return {"better": math.ceil(2 * (Z * sd / (2 * mmd)) ** 2), "same": math.ceil(2 * (Z * sd / mmd) ** 2)}


def _good_sign(goal: str, a_mean: float | None) -> int | None:
    """差（b − a）が正なら良い向きのとき +1、負なら良い向きのとき −1。決められなければ None。"""
    if goal == "increase":
        return 1
    if goal == "decrease":
        return -1
    if goal == "reduce_abs" and a_mean:
        return -1 if a_mean > 0 else 1
    return None


def verdict(diff: dict, goal: str, mmd: float | None) -> dict:
    """compare._diff の結果 → {verdict, reason}。_diff の `same` は「変わらない」ではない（区間が広いだけのことがある）。"""
    na, nb = diff.get("a", {}).get("n", 0), diff.get("b", {}).get("n", 0)
    if goal == "reduce_sd":
        return {"verdict": "unknown", "reason": "sd"}
    if diff.get("status") == "insufficient" or min(na, nb) < config.MIN_BLOCK_N:
        return {"verdict": "unknown", "reason": "few"}
    sign = _good_sign(goal, diff["a"].get("mean"))
    if diff.get("status") == "changed" and sign is not None:
        return {"verdict": "better" if diff["diff"] * sign > 0 else "worse", "reason": "changed"}
    lo, hi = diff.get("ci95") or [None, None]
    if mmd and lo is not None and -mmd <= lo and hi <= mmd:
        return {"verdict": "same", "reason": "within_mmd"}
    return {"verdict": "unknown", "reason": "wide"}


def _usable(shots: list[dict], club: str) -> list[dict]:
    return [s for s in shots if not s.get("excluded") and not is_practice(s) and (s.get("club") or "") == club]


def _vals(shots: list[dict], metric: str) -> list[float]:
    return [float(s["metrics"][metric]) for s in shots if (s.get("metrics") or {}).get(metric) is not None]


def _pooled_sd(a: list[float], b: list[float]) -> float | None:
    import statistics

    xs = [statistics.stdev(v) for v in (a, b) if len(v) >= 2]
    return sum(xs) / len(xs) if xs else None


def default_items(a: list[dict], b: list[dict], plan: dict | None = None) -> list[dict]:
    """作る時点で固定する項目（Go が checkups.items_json に保存し、あとから増やさない）。"""
    ca = Counter(s.get("club") for s in a if not s.get("excluded") and not is_practice(s) and s.get("club"))
    cb = Counter(s.get("club") for s in b if not s.get("excluded") and not is_practice(s) and s.get("club"))
    common = [c for c in ca if c in cb]
    if plan and plan.get("club") in common:
        club = plan["club"]
    elif common:
        club = max(common, key=lambda c: min(ca[c], cb[c]))
    else:
        return []
    items: list[dict] = []
    if plan and plan.get("club") == club and not str(plan.get("target_metric") or "").startswith("cp:"):
        items.append({"metric": plan["target_metric"], "goal": plan.get("goal") or "reduce_abs", "club": club})
    for g in config.DEFAULT_GUARDRAILS:
        items.append({"metric": g["metric"], "goal": g["goal"], "club": club})
    for mt in ("impact_offset", "face_angle"):
        items.append({"metric": mt, "goal": "reduce_abs", "club": club})
    seen, out = set(), []
    ua, ub = _usable(a, club), _usable(b, club)
    for it in items:
        if it["metric"] in seen or not (_vals(ua, it["metric"]) or _vals(ub, it["metric"])):
            continue
        seen.add(it["metric"])
        out.append(it)
    return out


def _plan_row(it: dict, plan_eval: dict) -> dict:
    from .coaching import STATE_TEXT

    state = (plan_eval or {}).get("state") or ((plan_eval or {}).get("progress") or {}).get("state") or "checking"
    head, second = STATE_TEXT.get(state, STATE_TEXT["checking"])
    return {**it, "source": "plan", "state": state, "word": head, "text": second, "verdict": None}


def checkup(a_shots: list[dict], b_shots: list[dict], items: list[dict] | None = None, plan: dict | None = None,
            plan_eval: dict | None = None, baseline_is_diagnosis: bool = False) -> dict:
    """a = 基準の日、b = 再確認の日。items を渡さなければ default_items で作る（その items を返す）。"""
    if items is None:
        items = default_items(a_shots, b_shots, plan)
    rows = []
    speed = {}
    for it in items:
        club = it.get("club") or ""
        ua, ub = _usable(a_shots, club), _usable(b_shots, club)
        if club not in speed:
            speed[club] = compare._diff(_vals(ua, "club_speed"), _vals(ub, "club_speed"), "club_speed")
        rows.append((it, ua, ub))
    results = []
    for it, ua, ub in rows:
        metric, goal, club = it["metric"], it.get("goal") or "reduce_abs", it.get("club") or ""
        name = PLAIN_NAME.get(metric, "この項目")
        if plan and plan_eval and metric == plan.get("target_metric") and club == plan.get("club"):
            r = _plan_row(it, plan_eval)
            results.append({**r, "name": name})
            continue
        av, bv = _vals(ua, metric), _vals(ub, metric)
        mmd = config.MMD.get(metric)
        nn = need_n(_pooled_sd(av, bv), mmd)
        base = {**it, "name": name, "source": "compare", "n": [len(av), len(bv)], "need_n": nn, "mmd": mmd}
        if metric in FACE_METRICS:
            band = it.get("band")
            counts = None
            if isinstance(band, list) and len(band) == 2:
                counts = [sum(band[0] <= v <= band[1] for v in xs) for xs in (av, bv)]
            results.append({**base, "verdict": "unknown", "reason": "face", "word": WORD["unknown"], "text": TEXT["unknown_face"], "in_band": counts})
            continue
        sp = speed.get(club) or {}
        if metric == "carry" and sp.get("status") != "insufficient" and sp.get("diff") is not None \
                and abs(sp["diff"]) >= 2 * config.MMD["club_speed"]:
            results.append({**base, "verdict": "unknown", "reason": "strength", "word": WORD["unknown"], "text": TEXT["unknown_strength"]})
            continue
        d = compare._diff(av, bv, metric)
        v = verdict(d, goal, mmd)
        if v["verdict"] == "unknown":
            enough = bool(nn and min(len(av), len(bv)) >= nn["better"])
            key = {"sd": "unknown_sd"}.get(v["reason"]) or ("unknown_enough_for_change" if enough and v["reason"] == "wide" else "unknown_few")
            text = TEXT[key]
        else:
            text = TEXT[v["verdict"]]
        results.append({**base, **v, "word": WORD[v["verdict"]], "text": text, "diff": d.get("diff"), "ci95": d.get("ci95")})
    notes = [REGRESSION_NOTE] if baseline_is_diagnosis else []
    return {"version": VERSION, "items": items, "results": results, "notes": notes}
