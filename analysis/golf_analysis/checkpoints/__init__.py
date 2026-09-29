"""動画のチェックポイント（docs/DESIGN_v2.md §5・§6）。読むのはここだけ（Go は /v1/checkpoints を中継するだけ）。

- 中身は catalog.json（版 checkpoints/1.0-pgag）。**ガイドの記述で直すのはここだけ**（§18）。
  measure.spec は「どの点から何を測るか」の中身で、measure.py が読む。コードに基準の数を書かない。
- P を選ぶときの見本の線画は svg/p{n}.{dtl,fo}.svg（自前で描いたもの。ガイドの図は写さない）。
- 文の「左」「右」は {lead} / {trail} で持ち、利き手で差し込む（R9）。
- 測る（measure.py）・判定と課題（judge.py）は LLM を使わない。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re

HERE = os.path.dirname(__file__)
CATALOG_PATH = os.path.join(HERE, "catalog.json")
SVG_DIR = os.path.join(HERE, "svg")

GROUPS = ("setup", "position", "error", "power", "tempo", "path")
VIEWS = ("dtl", "fo", "any")
CLUBS = ("iron", "driver", "wood", "hybrid", "wedge")
HOWS = ("pose", "pose+ball", "tap", "tap+ball", "tap+pose", "trajectory", "time", "vision", "none")
JUDGEABLE = ("measurable", "visual", "not_in_2d")
JUDGES = ("binary", "reference")
SIDES = ("both", "lo_only", "hi_only")
UNITS = ("deg", "ball", "head", "ratio", "label")
TAGS = ("distance", "accuracy", "consistency")
P_REQUIRED = ("P1", "P2", "P3", "P4", "P5", "P6", "P7")
P_ALL = ("P1", "P2", "P3", "P4", "P5", "P5_5", "P6", "P6_5", "P7", "P8", "P9", "P10")
TEXT_FIELDS = ("title", "look_at", "ok_text")

_CACHE: dict = {}


def load() -> dict:
    """カタログを読む（ファイルが変わったら読み直す）。"""
    st = os.stat(CATALOG_PATH).st_mtime_ns
    if _CACHE.get("mtime") != st:
        with open(CATALOG_PATH, encoding="utf-8") as f:
            raw = f.read()
        _CACHE["data"] = json.loads(raw)
        _CACHE["etag"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        _CACHE["by_id"] = {it["id"]: it for it in _CACHE["data"]["items"]}
        _CACHE["mtime"] = st
    return _CACHE["data"]


def version() -> str:
    return load()["version"]


def etag() -> str:
    load()
    return _CACHE["etag"]


def items() -> list[dict]:
    return load()["items"]


def by_id(item_id: str) -> dict | None:
    load()
    return _CACHE["by_id"].get(item_id)


def p_names() -> dict:
    return load()["p_names"]


def svgs() -> dict[str, str]:
    """見本の線画（p1.dtl など → SVG の文字列）。無いものは入れない。"""
    out = {}
    if os.path.isdir(SVG_DIR):
        for f in sorted(os.listdir(SVG_DIR)):
            if f.endswith(".svg"):
                with open(os.path.join(SVG_DIR, f), encoding="utf-8") as fh:
                    out[f[:-4]] = fh.read()
    return out


def side_words(hand: str) -> dict[str, str]:
    """{lead} / {trail} に入れる語（右打ちは左が目標側）。"""
    return {"lead": "右", "trail": "左"} if hand == "L" else {"lead": "左", "trail": "右"}


def fill(text: str, hand: str) -> str:
    w = side_words(hand)
    return (text or "").replace("{lead}", w["lead"]).replace("{trail}", w["trail"])


def item_view(it: dict, hand: str) -> dict:
    """画面に出す形（文の差し込みを済ませ、測り方の中身は外す）。"""
    out = {k: it.get(k) for k in ("id", "group", "clubs", "p", "view", "optional", "judgeable", "judge", "domino_rank", "tags", "same_as",
                                   "definition_status", "checked_by", "l1_links")}
    for k in TEXT_FIELDS:
        out[k] = fill(it.get(k, ""), hand)
    out["faults"] = [{"id": f["id"], "label": fill(f["label"], hand)} for f in it.get("faults", [])]
    out["needs_note"] = fill(it.get("needs_note", ""), hand)
    out["note"] = fill(it.get("note", ""), hand)
    out["guide"] = dict(it.get("guide") or {})
    out["proxy"] = fill((it.get("measure") or {}).get("proxy", ""), hand)
    out["how"] = (it.get("measure") or {}).get("how", "none")
    r = it.get("range")
    out["range"] = {k: r.get(k) for k in ("unit", "lo", "hi", "side", "speed_dependent")} if r else None
    return out


def public(hand: str | None = None) -> dict:
    """GET /v1/checkpoints の中身。hand を渡さなければ差し込み前（{lead} / {trail}）のまま。"""
    c = copy.deepcopy(load())
    if hand in ("R", "L"):
        for it in c["items"]:
            for k in TEXT_FIELDS + ("needs_note", "note"):
                if k in it:
                    it[k] = fill(it[k], hand)
            for f in it.get("faults", []):
                f["label"] = fill(f["label"], hand)
        c["p_define"] = {k: fill(v, hand) for k, v in c.get("p_define", {}).items()}
    c["svg"] = svgs()
    c["etag"] = etag()
    return c


# ガイドのページ（ノートの節の範囲）。カタログの guide.pages はこの中に収まる（§16）
NOTE_RANGES = [(4, 19), (20, 31), (34, 52), (54, 67), (70, 70), (71, 86), (87, 101), (102, 118), (119, 127), (133, 145), (146, 155), (158, 183)]


def page_ranges(pages: str) -> list[tuple[int, int]]:
    out = []
    for part in re.split(r"[,、]\s*", pages or ""):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ValueError(f"ページの書き方が違う: {pages!r}")
        a = int(m.group(1))
        b = int(m.group(2) or a)
        out.append((a, b))
    return out
