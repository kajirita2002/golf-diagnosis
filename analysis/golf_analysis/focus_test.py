"""10球テストの数え（docs/DESIGN_v2.md §8.2・段4）。Claude を呼ばない・保存しない。

入力はスイングごとの判定（swing_checks の state / fault / basis）と、任意の自己評価
（打ち終えてから1回だけ: できたと思った回数・やりすぎ気味／足りない気味／ちょうど）。

- 合格: 判定できた中で範囲の中が 8 以上、かつ判定できたスイングが 8 以上。
- まだ: 範囲の中が 7 以下（判定できたのが 8 以上）。
- 判定できない: 判定できたスイングが 8 に満たない（passed は None）。合格に要る数を黙って下げない。
- 8/10 はガイドの練習の合格ラインで、確率ではない。
"""

from __future__ import annotations

VERSION = "focus_test/0.1"
PASS_IN = 8  # 範囲の中がこの数以上
PASS_JUDGED = 8  # 判定できたスイングがこの数以上
MAX_SWINGS = 10
FEELS = ("too_much", "too_little", "just")

# 次の手（ガイドの調整の仕方）。最初の面に出すので数字・専門用語を使わない
NEXT_TEXT = {
    "passed": "合格です。別の日にもう一回同じ結果が出るかを見ます。",
    "reduce": "加えた感覚が多すぎます。次は少し減らします。",
    "bolder": "感覚が足りません。もっと大胆に。",
    "keep": "同じ感覚のまま、もう一回撮ります。",
    "reshoot": "判定できたスイングが足りません。撮り直します。",
}
FEEL_WORD = {"too_much": "やりすぎ気味", "too_little": "足りない気味", "just": "ちょうど"}
WHY = "合格の線（十回中八回）はガイドの練習の合格ラインで、確率ではありません。判定できなかった回は分母に入れません。"


def _state(sw: dict) -> str:
    st = (sw or {}).get("state")
    return st if st in ("in_range", "out_range") else "unknown"


def judge(swings: list[dict], target_fault: str | None = None, self_rating: dict | None = None) -> dict:
    """スイングごとの判定 → {in_range, judged, passed, next_hint, ...}。

    target_fault はプランを作ったときの外れの向き（例 inside）。範囲の外の回のうち、同じ向きのままなら
    「足りない」、反対の向きに外れたなら「多すぎる」。向きが読めないとき（見た目の項目など）は自己評価で決める。
    """
    if len(swings) > MAX_SWINGS:
        raise ValueError(f"10球テストのスイングは {MAX_SWINGS} 本までです")
    marks = []
    same = opposite = 0
    visual = False
    for sw in swings:
        st = _state(sw)
        fault = (sw or {}).get("fault") or None
        if (sw or {}).get("basis") == "visual":
            visual = True
        side = None
        if st == "out_range" and target_fault and fault:
            side = "same" if fault == target_fault else "opposite"
            same += side == "same"
            opposite += side == "opposite"
        marks.append({"state": st, "side": side})
    n_in = sum(m["state"] == "in_range" for m in marks)
    judged = sum(m["state"] != "unknown" for m in marks)
    passed = None if judged < PASS_JUDGED else n_in >= PASS_IN
    sr = _self_rating(self_rating)

    if passed is None:
        hint = "reshoot"
    elif passed:
        hint = "passed"
    elif same or opposite:
        hint = "reduce" if opposite > same else "bolder"
    elif sr and sr.get("feel") == "too_much":
        hint = "reduce"
    elif sr and sr.get("feel") == "too_little":
        hint = "bolder"
    else:
        hint = "keep"

    skipped = len(marks) - judged
    frac = f"{judged}回中{n_in}回が範囲の中" + (f"（判定できなかった{skipped}回を除く）" if skipped else "")
    out = {
        "version": VERSION,
        "n": len(marks),
        "in_range": n_in,
        "judged": judged,
        "passed": passed,
        "need_more": max(0, PASS_JUDGED - judged) if passed is None else 0,
        "marks": marks,
        "same_side": same,
        "opposite_side": opposite,
        "next_hint": hint,
        "next_text": NEXT_TEXT[hint],
        "label": frac,  # 数字はラベルの層（回数の形）
        "pass_line": PASS_IN,
        "why": WHY,
        "basis_label": "見た目で数えました" if visual else "",
    }
    if sr:
        out["self_rating"] = sr
        if sr.get("count") is not None:
            out["self_compare"] = f"できたと思ったのは{sr['count']}回、範囲の中は{n_in}回"
    return out


def _self_rating(sr: dict | None) -> dict | None:
    if not isinstance(sr, dict):
        return None
    out: dict = {}
    c = sr.get("count")
    if isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= MAX_SWINGS:
        out["count"] = c
    f = sr.get("feel")
    if f in FEELS:
        out["feel"] = f
        out["feel_word"] = FEEL_WORD[f]
    return out or None
