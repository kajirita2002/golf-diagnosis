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
