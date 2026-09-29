"""1球の値を読む小さな部品（session / profile / band / plan が共通で使う）。"""

from __future__ import annotations

EXTREME_CONTACTS = ("heel_extreme", "toe_extreme")
RIGHT_CURVES = ("fade", "slice")
LEFT_CURVES = ("draw", "hook")


def m(shot: dict, key: str):
    return (shot.get("metrics") or {}).get(key)


def dec(shot: dict) -> dict:
    return shot.get("decomposition") or {}


def flags(shot: dict) -> list:
    return dec(shot).get("flags") or []


def is_thin(shot: dict) -> bool:
    return "thin" in flags(shot)


def is_extreme(shot: dict) -> bool:
    return dec(shot).get("contact") in EXTREME_CONTACTS


def curve_side(shot: dict) -> str:
    """曲がりの向き: right / none / left / unknown。"""
    c = dec(shot).get("curve")
    if c in RIGHT_CURVES:
        return "right"
    if c in LEFT_CURVES:
        return "left"
    if c == "straight":
        return "none"
    return "unknown"


def start_side(shot: dict) -> str:
    """打ち出しの向き: push / straight / pull / unknown。"""
    s = dec(shot).get("start_line")
    return s if s in ("push", "straight", "pull") else "unknown"


# プランの型の練習の球（docs/DESIGN_coaching.md §8.3・§10.2）。Go の payload() が block_kind を付ける
# （そのセッションの plan_runs の実験のブロックからだけ・実験のクラブに合う球だけ）。
# 準備（warmup）とドリル（drill・道具あり）の球は、診断にも A と B の比較にも入れない。
PRACTICE_KINDS = ("warmup", "drill")


def is_practice(shot: dict) -> bool:
    return shot.get("block_kind") in PRACTICE_KINDS


def drop_practice(shots: list[dict]) -> list[dict]:
    """準備とドリルの球を外した並び（外した数は analyze_session が n_practice_excluded で返す）。"""
    return [s for s in shots if not is_practice(s)]
