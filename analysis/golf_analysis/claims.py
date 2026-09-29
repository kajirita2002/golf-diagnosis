"""事実の文（定型文）を作る部品。docs/DESIGN_coaching.md §5.5・§3 の R1・R2・R9。

- **事実の文はここでだけ作る（R1）。** 数字・向き・量・因果を含む文は、差し込み口を持つ定型文
  （template）に profile から取った値（fact）を差し込んで作る。Claude の文（1c）は事実を持たない。
- **数字には範囲を付ける（R2）。** fact は必ず「どの範囲（scope_id）・何球（n）」を持ち、
  持たない値は差し込めない（ClaimError）。群の値を番手の名前で書かないため、範囲の名前も fact が持つ。
- **左打ち（R9）。** 保存は右打ちの座標。定型文は向きの語を差し込み口（{dir:right} / {dir:left} /
  {lead} / {trail}）で持ち、左打ちなら右と左を入れ替える。左右の角度と左右の距離の符号・向きも入れ替える。
  打点のトゥ／ヒールは入れ替えない。

差し込み口:
  {v:<fact>}      値を単位つきで（deg_lat は +3.3° / m_lat は「右 9.9m」/ strike は「ヒール 21mm」…）
  {a:<fact>}      値の大きさだけ（向きの語を付けない。deg_lat → 3.3° / m_lat → 9.9m）
  {n:<fact>}      件数（整数）
  {seqs:<fact>}   球の番号の並び（#5・#13）
  {dirof:<fact>}  値の符号の向きの語（右 / 左）
  {dir:right} {dir:left}  向きの語
  {lead} {trail}  目標側 / 後ろ側（右打ちは 左 / 右）
  {q:<k>;<n>}     量の言葉（全部 / ほとんど / 約半分）。件数の fact 2つから qual() で選ぶ（§5.5）
  {qs:<k>;<n>}    同じく「◯球とも」「◯球のほとんどが」の形（件数のすぐ後ろに置く）
  量の言葉は件数から機械的に選ぶ。テンプレートに「全部」「◯球とも」を直書きしない。
  選べない件数（例: 7球中4球で「ほとんど」）なら ClaimError。Claim.qual を書いたら、選んだ語と一致しなければ ClaimError。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from . import config


class ClaimError(ValueError):
    pass


# 単位: 左右の向きを持つ量（左打ちで入れ替える）と、持たない量
LATERAL_UNITS = {"deg_lat", "m_lat", "pct_lat"}
UNITS = LATERAL_UNITS | {"deg", "m", "mm", "mm1", "strike", "pct", "count", "ratio", "seqs", "text", "coef"}


@dataclass
class Fact:
    id: str
    value: object
    unit: str
    scope_id: str
    n: int
    label: str = ""  # 範囲の名前（アイアン（まとめ）・4 Hybrid）
    note: str = ""  # 数え方（候補を除く・測れた球だけ）

    def as_dict(self) -> dict:
        return {"id": self.id, "value": self.value, "unit": self.unit, "scope_id": self.scope_id, "n": self.n, "label": self.label, "note": self.note}


class Facts:
    """範囲の無い値を入れられない表。"""

    def __init__(self) -> None:
        self.items: dict[str, Fact] = {}

    def add(self, id: str, value, unit: str, *, scope_id: str, n: int, label: str = "", note: str = "") -> str:
        if unit not in UNITS:
            raise ClaimError(f"知らない単位です: {unit}")
        if not scope_id or n is None:
            raise ClaimError(f"範囲（scope_id と n）の無い値は差し込めません: {id}")
        if value is None:
            raise ClaimError(f"値がありません: {id}")
        if isinstance(value, float) and math.isnan(value):
            raise ClaimError(f"値が NaN です: {id}")
        if unit == "seqs":
            value = sorted(value)
        self.items[id] = Fact(id, value, unit, scope_id, int(n), label, note)
        return id

    def get(self, id: str) -> Fact:
        f = self.items.get(id)
        if f is None:
            raise ClaimError(f"fact がありません: {id}")
        if not f.scope_id or f.n is None:
            raise ClaimError(f"範囲の無い値は差し込めません: {id}")
        return f

    def as_dict(self) -> dict:
        return {k: v.as_dict() for k, v in self.items.items()}


def _flip(hand: str) -> bool:
    return hand == "L"


def dir_word(side: str, hand: str) -> str:
    """right / left → 右 / 左（左打ちなら入れ替える）。"""
    right = side == "right"
    if _flip(hand):
        right = not right
    return "右" if right else "左"


def half_up(x: float, digits: int = 1) -> float:
    """表示の丸め（四捨五入）。round() は偶数丸めと2進の誤差で 9.85 → 9.8 になる。"""
    q = 10 ** digits
    return math.copysign(math.floor(abs(x) * q + 0.5 + 1e-9) / q, x)


def _num(x: float, digits: int = 1) -> str:
    return f"{abs(half_up(x, digits)):.{digits}f}"


def _signed(x: float, digits: int = 1) -> str:
    v = half_up(x, digits)
    if v == 0:
        return f"{0:.{digits}f}"
    return f"{'+' if v > 0 else '−'}{abs(v):.{digits}f}"


def fmt(f: Fact, hand: str, magnitude_only: bool = False) -> str:
    v = f.value
    if f.unit in LATERAL_UNITS and isinstance(v, (int, float)) and _flip(hand):
        v = -v
    u = f.unit
    if u == "deg_lat":
        return f"{_num(v)}°" if magnitude_only else f"{_signed(v)}°"
    if u == "deg":
        return f"{_num(v)}°"
    if u == "m_lat":
        if magnitude_only or half_up(v, 1) == 0:
            return f"{_num(v)}m"
        return f"{'右' if v > 0 else '左'} {_num(v)}m"
    if u == "pct_lat":
        return f"{_num(v * 100)}%" if magnitude_only else f"{_signed(v * 100)}%"
    if u == "m":
        return f"{_num(v)}m"
    if u == "mm":
        return f"{_num(v * 1000, 0)}mm"
    if u == "mm1":
        # 小さな差（練習の判定の改善量）は小数1桁。0.58mm を「1mm」と切り上げて大きく見せない（R4）
        return f"{_num(v * 1000, 1)}mm"
    if u == "strike":
        mm = v * 1000
        if magnitude_only:
            return f"{_num(mm, 0)}mm"
        if half_up(mm, 0) == 0:
            return "芯（0mm）"
        return f"{'トゥ' if mm > 0 else 'ヒール'} {_num(mm, 0)}mm"
    if u == "pct":
        # 10% 未満は小数1桁（キャリーの 2.3% など）、それ以上は整数
        return f"{_num(v * 100, 1 if abs(v) < 0.1 else 0)}%"
    if u == "coef":
        return f"{half_up(v, 2):.2f}"
    if u == "ratio":
        return _num(v, 0) if abs(v) >= 2 else _num(v, 1)
    if u == "count":
        return f"{int(v)}"
    if u == "seqs":
        return "・".join(f"#{s}" for s in v)
    return str(v)


TOKEN = re.compile(r"\{(v|a|n|seqs|dirof|dir|q|qs):([^}]+)\}|\{(lead|trail)\}")


def render(template: str, facts: Facts, hand: str) -> str:
    def rep(mo: re.Match) -> str:
        kind, arg, side = mo.group(1), mo.group(2), mo.group(3)
        if side:
            return dir_word("left" if side == "lead" else "right", hand)
        if kind == "dir":
            if arg not in ("right", "left"):
                raise ClaimError(f"向きは right / left だけです: {arg}")
            return dir_word(arg, hand)
        if kind in ("q", "qs"):
            word = _qual_of(arg, facts)
            return (QUAL_WORDS if kind == "q" else QUAL_SUFFIX)[word]
        f = facts.get(arg)
        if kind == "v":
            return fmt(f, hand)
        if kind == "a":
            return fmt(f, hand, magnitude_only=True)
        if kind == "n":
            if f.unit != "count":
                raise ClaimError(f"{{n:}} は件数の fact にだけ使えます: {arg}")
            return str(int(f.value))
        if kind == "seqs":
            if f.unit != "seqs":
                raise ClaimError(f"{{seqs:}} は球の番号の fact にだけ使えます: {arg}")
            return fmt(f, hand)
        if kind == "dirof":
            x = f.value
            return dir_word("right" if x > 0 else "left", hand)
        raise ClaimError(mo.group(0))

    return TOKEN.sub(rep, template)


def fact_ids(template: str) -> list[str]:
    out = []
    for mo in TOKEN.finditer(template):
        kind = mo.group(1)
        if not kind or kind == "dir":
            continue
        out += mo.group(2).split(";") if kind in ("q", "qs") else [mo.group(2)]
    return out


def _qual_of(arg: str, facts: "Facts") -> str:
    try:
        kid, nid = arg.split(";")
    except ValueError:
        raise ClaimError(f"量の言葉の差し込み口は {{q:<件数>;<全体>}} の形です: {arg}") from None
    k, n = facts.get(kid), facts.get(nid)
    if k.unit != "count" or n.unit != "count":
        raise ClaimError(f"量の言葉は件数の fact からだけ選べます: {arg}")
    word = qual(int(k.value), int(n.value))
    if word is None:
        raise ClaimError(f"この件数（{int(k.value)}/{int(n.value)}）に合う量の言葉がありません: {arg}")
    return word


def quals_in(template: str, facts: "Facts") -> list[str]:
    return [_qual_of(mo.group(2), facts) for mo in TOKEN.finditer(template) if mo.group(1) in ("q", "qs")]


# 量の言葉は件数から機械的に選ぶ（R1）。
def qual(k: int, n: int) -> str | None:
    if n <= 0:
        return None
    if k == n:
        return "all"
    r = k / n
    if r >= 0.8:
        return "most"
    if 0.4 <= r <= 0.6:
        return "half"
    return None


QUAL_WORDS = {"all": "全部", "most": "ほとんど", "half": "約半分"}
QUAL_SUFFIX = {"all": "とも", "most": "のほとんどが", "half": "の約半分が"}


@dataclass
class Claim:
    id: str
    section: str
    layer: str  # L0 / L1 / L1_plan / hint / meta
    template: str
    must: bool = True
    qual: str | None = None
    finding_ids: list[str] = field(default_factory=list)
    needs_band: bool = False  # 範囲の band_shape（Go）で {band:lo} / {band:hi} を埋めるまで出せない文
    basis: list[str] = field(default_factory=list)  # 文に数字は出さないが、根拠にした fact


def build_claim(c: Claim, facts: Facts, hand: str) -> dict:
    ids = fact_ids(c.template)
    ids += [b for b in c.basis if b not in ids]
    for i in ids:
        facts.get(i)  # 範囲の無い値・無い値はここで落ちる
    text = render(c.template, facts, hand)
    quals = quals_in(c.template, facts)
    if c.qual is not None and any(q != c.qual for q in quals):
        raise ClaimError(f"量の言葉（{c.qual}）が件数から選んだ語（{quals}）と合いません: {c.id}")
    if c.layer in ("L0", "L1"):
        bad = config.layer_terms_in(text)
        if bad:
            raise ClaimError(f"L1 までの文に体・クラブの動きの言葉があります（{bad}）: {c.id}")
    out = {
        "id": c.id,
        "section": c.section,
        "layer": c.layer,
        "must": c.must,
        "qual": c.qual if c.qual is not None else (quals[0] if quals else None),
        "facts": ids,
        "finding_ids": c.finding_ids,
        "template": c.template,
        "text": text,
    }
    if c.needs_band:
        out["needs_band"] = True
    return out
