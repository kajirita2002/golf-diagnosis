"""何本ものスイングの判定をまとめ、課題を1つ選ぶ（docs/DESIGN_v2.md §4.4・§6.6・R11）。LLM を使わない。

- 項目の状態は、判定できたスイングの**多数**で決める（同数は「スイングごとに食い違う」で判断できない）。
- 1本しか判定できない項目は「1本だけの見立て」として出し、課題の候補にしない。
- `same_as` で束ねた項目は相手の判定をそのまま使い、一覧で1行・範囲の外の件数で1回に数える（エラー名は札として添える）。
  札は**外れの向きが同じときだけ**添える（シャフトが寝すぎのときに「下ろしでクラブが立つ」を添えない）。
- まとめる項目（`measure.spec.kind == "derive"`。`pow.width` など）は元の項目の言い換えなので、課題の候補にも件数にも入れない。
  元の行に札として添える（same_as と同じ扱い。同じ外れを2回数えない＝R2）。
- 件数（見出し）は必須の P の項目だけで数える（任意の P8〜P10 は畳みの中で別に数える）。
- 課題の順は点数を使わない: ドミノの順 → 同じ段ではセットアップ → 手の通り道 → 測れた → 範囲の外の回数。
  TrackMan の症状とつながる印（linked）は順番を変えない（順番はガイド、TrackMan は答え合わせ）。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .. import config
from . import by_id, item_view, version
from .measure import REASON_TEXT

MEASURED = ("measured", "measured_approx", "measured_tap")
BASIS_RANK = {"measured_tap": 0, "measured": 1, "measured_approx": 2, "visual": 3, "conflict": 4, "none": 5}

RATIONALE = "スイングの前半ほど、後ろの動き全部に響くため先にここ（ガイドの考え方）"
RATIONALE_DISTANCE = "飛距離を優先する設定なので、飛ぶ力に関わる項目のうち、スイングの前半のものを先に（ガイドの考え方）"
NEXT_WHY = "見えているずれの原因は、一つ前の形にあることが多いため（ガイドの考え方）"
NEUTRAL_NOTE = "持ち球に合わせて範囲をずらすことはしていません（ガイドがずらす量を書いていないため）。まっすぐな球の範囲で見ています"


def aggregate(swings: list[dict], hand: str = "R", prefs: dict | None = None, symptoms: list[str] | None = None) -> dict:
    """swings: measure_swing の結果の並び（{swing_id, items}）。→ 項目ごとの状態・件数・課題。"""
    prefs = prefs or {}
    symptoms = set(symptoms or [])
    per: dict[str, list[tuple[Any, dict]]] = {}
    order: list[str] = []
    for sw in swings:
        sid = sw.get("swing_id")
        for r in sw.get("items", []):
            if r["id"] not in per:
                per[r["id"]] = []
                order.append(r["id"])
            per[r["id"]].append((sid, r))
    out_items = []
    also: dict[str, list[str]] = {}
    for iid in order:
        it = by_id(iid)
        if not it:
            continue
        rs = per[iid]
        if it.get("same_as"):
            also.setdefault(it["same_as"], []).append(iid)
            continue
        out_items.append(_one(it, rs, hand, symptoms))
    by = {x["id"]: x for x in out_items}
    for target, ids in also.items():
        t = by.get(target)
        if not t:
            continue
        # 外れの向き（when）が札の項目の向きと同じときだけ添える
        tw = _fault_when(by_id(target), t.get("fault"))
        t["also"] = [{"id": i, "title": item_view(by_id(i), hand)["title"]} for i in ids
                     if t["state"] == "out_range" and tw and tw in {f.get("when") for f in by_id(i).get("faults") or []}]
    for x in out_items:
        spec = ((by_id(x["id"]) or {}).get("measure") or {}).get("spec") or {}
        if spec.get("kind") != "derive":
            continue
        x["derived"] = True
        x["candidate"] = False
        if x["state"] == "out_range":
            want = set(spec.get("faults") or [])
            for src in (spec.get("from") or {}).values():
                for sid in src:
                    s_ = by.get(sid)
                    if s_ and s_["state"] == "out_range" and (not want or s_.get("fault") in want):
                        s_.setdefault("also", []).append({"id": x["id"], "title": x["title"]})
    cands = [x for x in out_items if x["candidate"]]
    focus, nxt, rationale = pick_focus(cands, prefs.get("priority") == "distance")
    for x in out_items:
        x["focus"] = bool(focus and x["id"] == focus["id"])
        x["next"] = bool(nxt and x["id"] == nxt["id"])
    main = [x for x in out_items if not x.get("derived") and not x.get("optional")]
    counts = Counter(x["state"] for x in main)
    reasons = Counter(x["reason"] for x in main if x["state"] == "unknown")
    views = {sw.get("view") for sw in swings if sw.get("view")}
    return {
        "catalog_version": version(), "judge_version": config.JUDGE_VERSION,
        "n_swings": len(swings), "views": sorted(views),
        "counts": {"judged": counts["in_range"] + counts["out_range"], "in_range": counts["in_range"], "out_range": counts["out_range"],
                   "unknown": counts["unknown"], "reference": counts["reference"]},
        "unknown_reasons": [{"reason": k, "text": REASON_TEXT.get(k, k), "n": n} for k, n in reasons.most_common()],
        "unchecked": sum(1 for x in out_items if not x.get("checked_by")),
        "focus": focus and focus["id"], "next": nxt and nxt["id"], "rationale": rationale, "next_why": NEXT_WHY if nxt else "",
        "neutral_note": NEUTRAL_NOTE,
        "items": sorted(out_items, key=lambda x: (x["domino_rank"], 0 if x["group"] == "setup" else 1, x["id"])),
    }


def _fault_when(it: dict | None, fault: str | None) -> str | None:
    for f in (it or {}).get("faults") or []:
        if f["id"] == fault:
            return f.get("when")
    return None


def _tight(value: dict | None) -> bool:
    """範囲の幅が誤差の幅（±err）と同じくらいで、「範囲の中」と言えることがほとんど無い項目か（§5.5・R4）。"""
    if not value:
        return False
    parts = value.get("parts") or [value]
    for v in parts:
        lo, hi, err = v.get("lo"), v.get("hi"), v.get("err")
        if v.get("side", "both") == "both" and lo is not None and hi is not None and err is not None and (hi - lo) <= 2 * err + 0.02:
            return True
    return False


def _one(it: dict, rs: list[tuple[Any, dict]], hand: str, symptoms: set[str]) -> dict:
    v = item_view(it, hand)
    judged = [(sid, r) for sid, r in rs if r["state"] in ("in_range", "out_range")]
    n_in = sum(1 for _, r in judged if r["state"] == "in_range")
    n_out = len(judged) - n_in
    state, reason, fault, basis = "unknown", "", None, "none"
    if any(r["state"] == "reference" for _, r in rs) and not judged:
        state, reason = "reference", next(r["reason"] for _, r in rs if r["state"] == "reference")
    elif not judged:
        reason = Counter(r["reason"] for _, r in rs).most_common(1)[0][0] if rs else "no_frame"
    elif n_out > n_in:
        state = "out_range"
        fault = Counter(r["fault"] for _, r in judged if r["state"] == "out_range" and r.get("fault")).most_common(1)
        fault = fault[0][0] if fault else None
    elif n_in > n_out:
        state = "in_range"
    else:
        reason = "split"
    if judged:
        basis = min((r["basis"] for _, r in judged), key=lambda b: BASIS_RANK.get(b, 9))
    single = len(judged) == 1
    measured = basis in MEASURED
    candidate = (
        state == "out_range" and it.get("judge") != "reference" and not single
        and ((measured and len(judged) >= config.CP_FOCUS_MIN_MEASURED) or (basis == "visual" and n_out >= config.CP_FOCUS_MIN_VISUAL))
    )
    # 代表の値（数字を見るの中だけ）: 範囲の外なら外のスイング、そうでなければ最初に判定できたもの
    rep = next((r for _, r in judged if r["state"] == state), None) or (judged[0][1] if judged else next((r for _, r in rs if r.get("value")), None))
    fl = next((f["label"] for f in v["faults"] if f["id"] == fault), "") if fault else ""
    frames = []
    for sid, r in rs:
        for p in r.get("frames") or []:
            frames.append({"swing_id": sid, "p": p})
    v.update({
        "state": state, "reason": reason, "reason_text": REASON_TEXT.get(reason, reason) if reason else "",
        "detail": next((r.get("detail") for _, r in rs if r.get("detail")), ""),
        "fault": fault, "fault_label": fl, "basis": basis,
        "n_swings": len(rs), "n_judged": len(judged), "n_in": n_in, "n_out": n_out, "single": single, "candidate": candidate,
        "value": rep.get("value") if rep else None,
        "values": [{"swing_id": sid, "state": r["state"], "value": r.get("value"), "reason": r.get("reason", "")} for sid, r in rs],
        "frames": frames[:12],
        "linked": bool(symptoms & set(it.get("l1_links") or [])),
        "conflict": any(r.get("conflict") for _, r in rs),
        "tight": _tight(rep.get("value") if rep else None),
    })
    return v


def _key(x: dict, distance_first: bool) -> tuple:
    return (
        0 if (distance_first and "distance" in (x.get("tags") or [])) else 1,
        x["domino_rank"],
        0 if x["group"] == "setup" else 1,
        0 if x["group"] == "path" else 1,
        0 if x["basis"] in MEASURED else 1,
        -x["n_out"],
        x["id"],
    )


def pick_focus(cands: list[dict], distance_first: bool = False) -> tuple[dict | None, dict | None, str]:
    """候補 → (まずここ, 次に見る, 理由)。候補は aggregate が作る（範囲の外・多数・1本だけでない）。"""
    if not cands:
        return None, None, ""
    ranked = sorted(cands, key=lambda x: _key(x, distance_first))
    focus = ranked[0]
    nxt = ranked[1] if len(ranked) > 1 else None
    why = RATIONALE_DISTANCE if (distance_first and "distance" in (focus.get("tags") or [])) else RATIONALE
    return focus, nxt, why
