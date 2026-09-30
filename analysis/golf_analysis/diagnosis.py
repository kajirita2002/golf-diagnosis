"""レポートの診断（diagnosis）。いまの課題 → 原因の動き → 理想の動き → 直し方（アクションプラン）→ 確かめ方。

利用者（ゴルファー本人）の声（2026-09-30）:
  「現在はこんな課題があって、それはこの動きが原因で発生してて、理想の動きはこうだから、
   こうゆうふうになおしましょうね、を構造化してわかりやすく」「見るだけで結果は変わらない。
   アクションプランが要る」「体の動きの原因には動画が要る」

だから課題ごとに5段を**開かなくても全部読める**形で返す（docs/DESIGN_v2.md の R1 は守る:
数字・角度・専門用語は最初の面に出さず、数字は evidence（「なぜそう言える？」）だけ）。

- **事実の文はコードの定型文**（R1・R10）。当たる場所・面の向きの量の言葉は profile から
  gist と同じ決まった対応（gist.freq / gist.excess_word）で作る。確率は言わない（R4）。
- **原因の動き・理想・直し方の文は知識ベース**（coaching_kb.json）。ガイドの文章は写さず、
  自分の言葉の要約とページだけ（R7）。ガイドに無い知見は source: general（一般的な見方）。
- **動作と球筋は言い切らない**（R3。ガイド p133〜 の「確定ではなく可能性」）。動画で測れて
  いない原因は basis=likely で「可能性」と書く。動画のチェックで範囲の外なら measured / seen に
  上げて先頭に置き、その動きに効くドリルを先に出す。範囲の中だった動きは原因の候補から外す。
- **TrackMan が言えるのは当たる瞬間のクラブの様子まで**。club_state（面の向き・当たる場所）は
  確かな事実として書き、体の動きは候補として並べる。
- **ドリルを隠さない**。ガイドの練習法は「PGAガイド p.X」、ガイドに無いものは「一般的な練習法」の
  札を付けて出す（安全の注意は必ず添える）。アクションプランを空にしない。
- **左打ち（R9）**。知識ベースの {lead}/{trail}/{dir:right}/{dir:left} を利き手で差し込む。
"""

from __future__ import annotations

import json
import os
import re

from . import config, gist
from .checkpoints import by_id as cp_by_id
from .checkpoints import NOTE_RANGES, fill as cp_fill, p_names, page_ranges
from .drills import render as _render

VERSION = "diagnosis/1"
KB_PATH = os.path.join(os.path.dirname(__file__), "coaching_kb.json")
MAX_ISSUES = 3
MAX_CAUSES = 3
MAX_DRILLS = 3
MAX_SETUP = 4
MENU_WARMUP = 8
MENU_MAIN = 8
MEASURED_BASIS = ("measured", "measured_approx", "measured_tap")
SOURCE_LABEL = {"guide": "PGAガイド", "general": "一般的な見方"}
DRILL_GENERAL_LABEL = "一般的な練習法"
VIEW_WORD = {"dtl": "後ろから", "fo": "正面から", "both": "後ろから（できれば正面からも）", "any": "後ろから"}
CATEGORY_WORD = config.CATEGORY_LABEL
TOKENS = ("{dir:right}", "{dir:left}", "{lead}", "{trail}")
KINDS_SOURCE = ("guide", "general")
BASIS = ("measured", "seen", "likely")
# 画面の言葉にしてよい構造のラベル（club_scope）の形
_CLUB_EN = re.compile(r"^(\d{1,2})\s*(Iron|Wood|Hybrid)$", re.I)
_CLUB_JA = {"iron": "アイアン", "wood": "ウッド", "hybrid": "ユーティリティ"}
_CLUB_NAMED = {"driver": "ドライバー", "pw": "ピッチングウェッジ", "aw": "アプローチウェッジ", "gw": "ギャップウェッジ",
               "sw": "サンドウェッジ", "lw": "ロブウェッジ", "putter": "パター"}
VIDEO_SCOPE_LABEL = "動画のスイング"

_CACHE: dict = {}


# ---------------------------------------------------------------- 知識ベース

def load() -> dict:
    """知識ベースを読む（ファイルが変わったら読み直す）。"""
    st = os.stat(KB_PATH).st_mtime_ns
    if _CACHE.get("mtime") != st:
        with open(KB_PATH, encoding="utf-8") as f:
            kb = json.load(f)
        _CACHE.update(data=kb, mtime=st,
                      symptoms={s["id"]: s for s in kb["symptoms"]},
                      causes={c["id"]: c for c in kb["causes"]},
                      drills={d["id"]: d for d in kb["drills"]})
    return _CACHE["data"]


def symptom(sid: str) -> dict | None:
    load()
    return _CACHE["symptoms"].get(sid)


def cause(cid: str) -> dict | None:
    load()
    return _CACHE["causes"].get(cid)


def drill(did: str) -> dict | None:
    load()
    return _CACHE["drills"].get(did)


def r(text: str | None, hand: str) -> str:
    """向きの差し込みを利き手で埋める。"""
    return _render(text or "", hand)


def pages_label(pages: list[str] | None) -> str:
    """ページの札（構造のラベル）。例: PGAガイド p.119〜127・158〜183"""
    parts = [p.replace("-", "〜") for p in pages or []]
    return "PGAガイド p." + "・".join(parts) if parts else "PGAガイド"


LABEL_RE = re.compile(r"^PGAガイド(?: p\.\d+(?:〜\d+)?(?:・\d+(?:〜\d+)?)*)?$|^一般的な練習法$|^一般的な見方$")


def _texts_of_symptom(s: dict) -> list[str]:
    out = [s.get("title"), s.get("club_state"), s.get("impact"), (s.get("ideal") or {}).get("text"), s.get("check")]
    out += [c.get("explain") for c in s.get("causes") or []]
    fx = s.get("fix") or {}
    out += list(fx.get("setup") or []) + [fx.get("cue"), fx.get("cue_why")]
    return [x for x in out if x is not None]


def _texts_of_cause(c: dict) -> list[str]:
    out = [c.get("title"), c.get("issue_title"), c.get("impact"), (c.get("ideal") or {}).get("text"), c.get("check")]
    fx = c.get("fix") or {}
    out += list(fx.get("setup") or []) + [fx.get("cue"), fx.get("cue_why")]
    return [x for x in out if x is not None]


def _texts_of_drill(d: dict) -> list[str]:
    return [d.get("name"), d.get("equipment"), d.get("place"), d.get("why"), d.get("safety")] + list(d.get("steps") or [])


def validate(kb: dict | None = None) -> list[str]:
    """形と中身の検査（pytest が全件通ることを固定する）。誤りの文の並びを返す。"""
    kb = kb or load()
    errs: list[str] = []
    causes = {c["id"]: c for c in kb.get("causes") or []}
    drills = {d["id"]: d for d in kb.get("drills") or []}
    for coll, name in ((kb.get("symptoms") or [], "symptom"), (kb.get("causes") or [], "cause"), (kb.get("drills") or [], "drill")):
        ids = [x.get("id") for x in coll]
        for i in {i for i in ids if ids.count(i) > 1}:
            errs.append(f"{name} {i}: id が重なっています")

    def check_text(owner: str, t: str) -> None:
        if not (t or "").strip():
            errs.append(f"{owner}: 空の文があります")
            return
        if "右" in t.replace("左右", "") or "左" in t.replace("左右", ""):  # 「左右」（両側）は向きの語ではない
            errs.append(f"{owner}: 右・左を直書きしています（{{lead}} / {{dir:right}} などで書く）: {t}")
        for tok in re.findall(r"\{[^}]*\}", t):
            if tok not in TOKENS:
                errs.append(f"{owner}: 知らない差し込み口です: {tok}")
        for hand in ("R", "L"):
            bad = gist.check_plain(r(t, hand))
            if bad:
                errs.append(f"{owner}: 最初の面に出せない語があります {bad}: {t}")

    def check_pages(owner: str, pages) -> None:
        if not pages:
            errs.append(f"{owner}: ガイドのページが要ります")
            return
        for p in pages:
            try:
                for a, b in page_ranges(p):
                    if not any(lo <= a and b <= hi for lo, hi in NOTE_RANGES):
                        errs.append(f"{owner}: ページ {p} がノートの節の範囲にありません")
            except ValueError as e:
                errs.append(f"{owner}: {e}")

    def check_drills(owner: str, ids) -> None:
        if not ids:
            errs.append(f"{owner}: ドリルが一つも無い（アクションプランを空にしない）")
        for d in ids or []:
            if d not in drills:
                errs.append(f"{owner}: 知らないドリル {d}")

    def check_item(owner: str, item_id: str | None, faults=None) -> None:
        if item_id is None:
            return
        it = cp_by_id(item_id)
        if not it:
            errs.append(f"{owner}: チェックポイントのカタログに無い項目 {item_id}")
            return
        fids = {f["id"] for f in it.get("faults") or []}
        for f in faults or []:
            if f not in fids:
                errs.append(f"{owner}: {item_id} に無い外れの向き {f}")

    for s in kb.get("symptoms") or []:
        own = f"symptom {s.get('id')}"
        for t in _texts_of_symptom(s):
            check_text(own, t)
        if s.get("from") != "ball":
            errs.append(f"{own}: from は ball です")
        if not s.get("causes"):
            errs.append(f"{own}: 原因の候補が要ります")
        for link in s.get("causes") or []:
            if link.get("id") not in causes:
                errs.append(f"{own}: 知らない原因 {link.get('id')}")
            if link.get("source") not in KINDS_SOURCE:
                errs.append(f"{own}: 原因 {link.get('id')} の source は guide / general です")
            if link.get("source") == "guide" and not ((causes.get(link.get("id")) or {}).get("guide") or {}).get("pages"):
                errs.append(f"{own}: ガイドを根拠にした原因 {link.get('id')} にページが無い")
        check_drills(own, (s.get("fix") or {}).get("drills"))
        check_item(own, (s.get("ideal") or {}).get("item_id"))
        if s.get("video_view") not in VIEW_WORD:
            errs.append(f"{own}: video_view が違います")
        if len((s.get("fix") or {}).get("setup") or []) == 0:
            errs.append(f"{own}: 構えで直すことが要ります")
    for c in kb.get("causes") or []:
        own = f"cause {c.get('id')}"
        for t in _texts_of_cause(c):
            check_text(own, t)
        own_ids = {it.get("id") for it in c.get("items") or []}
        for it in c.get("items") or []:
            check_item(own, it.get("id"), it.get("faults"))
            tgt = (cp_by_id(it.get("id")) or {}).get("same_as")
            if tgt and tgt not in own_ids:  # 束ねた項目は判定が相手の項目に出る（judge.aggregate）ので、相手も持つ
                errs.append(f"{own}: {it.get('id')} は {tgt} と束ねてあるので、{tgt} も items に要ります")
        cp = c.get("checkpoint")
        if cp is not None:
            check_item(own, cp.get("item_id"))
            if cp.get("view") not in ("dtl", "fo"):
                errs.append(f"{own}: checkpoint.view は dtl / fo です")
            if cp.get("item_id") not in {i["id"] for i in c.get("items") or []}:
                errs.append(f"{own}: checkpoint の項目が items にありません")
        check_item(own, (c.get("ideal") or {}).get("item_id"))
        check_pages(own, (c.get("guide") or {}).get("pages"))
        check_drills(own, (c.get("fix") or {}).get("drills"))
    for d in kb.get("drills") or []:
        own = f"drill {d.get('id')}"
        for t in _texts_of_drill(d):
            check_text(own, t)
        src = d.get("source") or {}
        if src.get("kind") not in KINDS_SOURCE:
            errs.append(f"{own}: source.kind は guide / general です")
        if src.get("kind") == "guide":
            check_pages(own, src.get("pages"))
        steps = d.get("steps") or []
        if not 1 <= len(steps) <= 3:
            errs.append(f"{own}: 手順は一〜三つです")
        if not isinstance(d.get("reps"), int) or d["reps"] <= 0:
            errs.append(f"{own}: 球数（reps）が要ります")
        if d.get("catalog_drill"):
            from .drills import get as catalog_get

            if not catalog_get(d["catalog_drill"]):
                errs.append(f"{own}: ドリル集に無い {d['catalog_drill']}")
    return errs


# ---------------------------------------------------------------- 小さな道具

_KANJI = "〇一二三四五六七八九"


def kanji(n: int) -> str:
    """整数を漢数字にする（最初の面に算用数字を出さない。〜九九九）。"""
    n = int(n)
    if n <= 0:
        return "〇"
    out = ""
    for unit, name in ((100, "百"), (10, "十")):
        q, n = divmod(n, unit)
        if q:
            out += ("" if q == 1 else _KANJI[q]) + name
    if n:
        out += _KANJI[n]
    return out


def club_label(name: str | None) -> str:
    """範囲の名前 → 画面のラベル（5 Wood → 5番ウッド。まとめはそのまま）。"""
    t = (name or "").strip()
    mo = _CLUB_EN.match(t)
    if mo:
        return f"{int(mo.group(1))}番{_CLUB_JA[mo.group(2).lower()]}"
    return _CLUB_NAMED.get(t.lower(), t)


def check_club_scope(text: str) -> bool:
    """club_scope のラベルが決めた形か（番手・まとめ・動画のスイング）。"""
    groups = {f"{v}（まとめ）" for v in config.CATEGORY_LABEL.values()} | set(config.CATEGORY_LABEL.values()) | {VIDEO_SCOPE_LABEL}
    return all(p in groups or gist.check_plain_label(p, ("club",)) for p in (text or "").split("・"))


def _p_name(p: str | None) -> str:
    if not p:
        return ""
    names = p_names()
    return "から".join(names.get(x, x) for x in p.split("-"))


def _checkpoint(cp: dict | None) -> dict | None:
    if not cp:
        return None
    return {"p": cp["p"], "item_id": cp["item_id"], "view": cp["view"], "p_name": _p_name(cp["p"])}


def _guide(g: dict | None, hand: str) -> dict | None:
    if not g or not g.get("pages"):
        return None
    return {"pages": list(g["pages"]), "section": r(g.get("section"), hand), "label": pages_label(g["pages"])}


# ---------------------------------------------------------------- 球 → 症状

def symptom_of(cand: dict | None, profile: dict) -> str | None:
    """プランの候補（plan.candidates の1つ）→ 知識ベースの症状 id。測る候補は課題にしない。"""
    if not cand or cand.get("kind") == "measure":
        return None
    cid = cand.get("id") or ""
    names = set(cand.get("issue_names") or [i.get("issue") for i in cand.get("issues") or [] if isinstance(i, dict)])
    if cid in ("strike_heel", "strike_toe", "strike_scatter"):
        return cid
    if cid == "top_extreme":
        return "thin"
    if cid == "low_point":
        return "fat"
    goal = (cand.get("kpi") or {}).get("goal")
    if cid == "path" or cand.get("lever") == "path":
        path = (profile.get("l1") or {}).get("club_path") or {}
        if path.get("bias") in ("right", "left"):
            return f"path_{path['bias']}"
        return "path_right" if (path.get("mean") or 0) > 0 else "path_left"
    if cid in ("face", "start_right", "start_left", "start_var") or cand.get("lever") == "face":
        if goal == "decrease":
            return "face_right"
        if goal == "increase":
            return "face_left"
        return "face_var"
    if cid.startswith("curve_") or cid == "face_to_path":
        if "curve_right" in names or cid == "curve_right":
            return "face_right"
        if "curve_left" in names or cid == "curve_left":
            return "face_left"
        return "face_var"
    return None


def _count_face(shots: list[dict], side: str) -> tuple[int, int]:
    from .shots import m

    vals = [m(s, "face_angle") for s in shots or [] if not s.get("excluded")]
    vals = [v for v in vals if v is not None]
    if side == "right":
        k = sum(1 for v in vals if v > config.ISSUE_DIR_DEG)
    else:
        k = sum(1 for v in vals if v < -config.ISSUE_DIR_DEG)
    return k, len(vals)


def _freq_phrase(word: str | None, noun: str) -> str:
    """量の言葉 + 名詞句 → 文（「ネック寄りに当たる球がほとんどです」「ときどきネック寄りに当たる球があります」）。"""
    if word in ("ほとんど", "半分以上", "多い"):
        return f"{noun}が{word}です"
    if word in ("ときどき", "まれ"):
        return f"{word}{noun}があります"
    if word == config.PLAIN_FREQ_NONE:
        return f"{noun}はありません"
    return f"{noun}がいくつかあります"


def _now_ball(sid: str, ctx: dict, hand: str) -> str:
    """いま何が起きているか（球のデータから。数字を使わず、量は決まった言葉で）。"""
    p = ctx["profile"]
    s = p.get("strike") or {}
    l1 = p.get("l1") or {}
    t = p.get("tendency") or {}
    R, L = gist.dir_word("right", hand), gist.dir_word("left", hand)
    lines: list[str] = []
    if sid in ("strike_heel", "strike_toe"):
        heel = sid == "strike_heel"
        word = config.PLAIN_TERMS["heel" if heel else "toe"]
        k = s.get("heel" if heel else "toe")
        ext = s.get("heel_extreme" if heel else "toe_extreme") or 0
        lines.append(_freq_phrase(gist.freq(k, s.get("measured")), f"芯から外れて{word}に当たる球") + "。")
        ew = gist.freq(ext, s.get("n")) if ext else None
        if ew and ew != config.PLAIN_FREQ_NONE:
            side = gist._extreme_side(ctx.get("shots"))
            fly = f"大きく{gist.dir_word(side, hand)}へ飛んでいます" if side else "大きく外れています"
            lines.append(f"{ew}大きく{word}に外れて当たり、その球は{fly}。")
        miss = gist.freq(s.get("missing"), s.get("n"))
        if miss and miss not in (config.PLAIN_FREQ_NONE, "まれ"):
            lines.append("当たる場所を測れなかった球もあるので、次はクラブの面にシールを貼って確かめると、もっとはっきりします。")
    elif sid == "strike_scatter":
        lines.append("当たる場所が、芯・ネック寄り・先寄りと毎回変わっていて、決まった場所に当たっていません。")
        miss = gist.freq(s.get("missing"), s.get("n"))
        if miss and miss not in (config.PLAIN_FREQ_NONE, "まれ"):
            lines.append("当たる場所を測れなかった球もあります。")
    elif sid in ("face_right", "face_left"):
        side = "right" if sid == "face_right" else "left"
        k, n = _count_face(ctx.get("shots"), side)
        dw = R if side == "right" else L
        lines.append(_freq_phrase(gist.freq(k, n), f"当たる瞬間にクラブの面が{dw}を向いて当たる球") + "。")
        start = (t.get("start") or {}).get("push" if side == "right" else "pull")
        curve = (t.get("curve") or {}).get(side)
        sw, cw = gist.freq(start, t.get("n")), gist.freq(curve, (t.get("curve") or {}).get("measured"))
        if sw and sw not in (config.PLAIN_FREQ_NONE, "まれ"):
            if cw in ("ほとんど", "半分以上", "多い"):
                more = "同じくらいあります" if cw == sw else f"{cw}です"
                lines.append(f"そのためボールは{dw}へ打ち出されることが{sw}で、さらに{dw}へ曲がる球も{more}。")
            else:
                lines.append(f"そのためボールは{dw}へ打ち出されることが{sw}です。")
        path = l1.get("club_path") or {}
        if (path.get("n") or 0) >= gist.MIN_N and path.get("spread") == "stable" and not path.get("bias"):
            lines.append("振る方向はそろっているので、左右のずれを決めているのは面の向きです。")
    elif sid == "face_var":
        lines.append(f"当たる瞬間の面の向きが毎回変わり、{R}へ出る球も{L}へ出る球もあります。")
    elif sid in ("path_right", "path_left"):
        dw = R if sid == "path_right" else L
        lines.append(f"クラブを振る方向が、狙いより{dw}へ向いていることが多いです。")
    elif sid == "thin":
        from .shots import is_thin

        k = sum(1 for x in ctx.get("shots") or [] if is_thin(x))
        lines.append(_freq_phrase(gist.freq(k, len(ctx.get("shots") or [])), "ボールの上側をたたく薄い当たり") + "。")
    elif sid == "fat":
        lines.append("クラブが一番低くなる場所が毎回そろわず、ボールの手前を打つ当たりが出ています。")
    elif sid == "shallow_attack":
        lines.append(f"{CATEGORY_WORD.get(ctx.get('category'), 'アイアン')}なのに、クラブが上から下りてくる形になりきらず、横から払うような当たりが多いです。")
    else:
        lines.append(r((symptom(sid) or {}).get("club_state"), hand))
    return "".join(lines).replace("。。", "。")


def _impact_ball(sid: str, ctx: dict, cand: dict | None, hand: str) -> str:
    kb = symptom(sid) or {}
    text = r(kb.get("impact"), hand)
    mb = ctx["profile"].get("miss_budget") or {}
    part = None
    if sid in ("strike_heel", "strike_toe") and (mb.get("strike_range_m") or [None])[0]:
        part = mb["strike_range_m"][0]
    elif cand is not None and cand.get("excess_m"):
        part = cand["excess_m"]
    w = gist.excess_word(part, mb.get("total_m")) if part else None
    if w:
        text += f"今日、左右に大きく外れた距離の{w}が、ここから出ています。"
    return text


EVIDENCE_LABEL = {
    "miss.strike": "当たる場所の分かれ方", "miss.extreme": "大きく外れた当たり", "miss.budget": "外れた距離の内訳",
    "summary.top_cell": "いちばん多い球筋", "summary.budget": "外れた距離のもと", "next.now": "先に取り組む理由",
    "unknown.strike_missing": "当たる場所を測れなかった球", "impact.face": "面の向き", "impact.sensitivity": "面の向きと左右のずれ",
    "summary.path_face": "振る方向と面の向き", "next.next": "次に取り組むもの", "next.hypothesis": "次の課題の見立て",
    "impact.representative": "典型の一球", "flight.counts": "打ち出しと曲がりの数", "impact.path": "振る方向",
}


def _evidence_ball(sid: str, ctx: dict) -> list[dict]:
    have = {c["id"]: c["text"] for sec in ctx.get("sections") or [] for c in sec.get("claims") or []}
    out = []
    for suf in (symptom(sid) or {}).get("evidence_claims") or []:
        cid = f"{ctx['scope_id']}/{suf}"
        if cid in have:
            out.append({"label": EVIDENCE_LABEL.get(suf, "根拠"), "value_text": have[cid], "claim_id": cid})
    if sid == "shallow_attack":
        s7 = next((x for x in ctx.get("symptoms") or [] if x.get("id") == "S7"), None)
        if s7 and s7.get("value") is not None:
            out.append({"label": "クラブが下りてくる向き", "value_text": f"入射角の平均 {s7['value']:+.1f}°（{s7.get('n')}球。0° 以上を上から当てきれていない目安にしています・基準の出典は未確認）"})
    return out


# ---------------------------------------------------------------- 動画のチェック → 原因

def _video_state(checks: dict | None) -> dict:
    """動画のチェック結果（judge.aggregate の出力）を、知識ベースの原因ごとの状態にする。

    out[cause_id] = {"state": "out"|"in"|"unknown", "basis": "measured"|"seen", "item": 項目, ...}
    範囲の外は、課題の候補にできるもの（judge の candidate。多数・1本だけでない）だけを数える（R11）。"""
    out: dict[str, dict] = {}
    if not checks:
        return out
    items = {x["id"]: x for x in checks.get("items") or []}
    for c in load()["causes"]:
        judged_in, hit = False, None
        for spec in c.get("items") or []:
            it = items.get(spec["id"])
            if not it:
                continue
            if it.get("state") == "out_range" and it.get("candidate") and (not spec.get("faults") or it.get("fault") in spec["faults"]):
                if hit is None or (it.get("basis") in MEASURED_BASIS and hit.get("basis") not in MEASURED_BASIS):
                    hit = it
            elif it.get("state") == "in_range":
                judged_in = True
        if hit:
            out[c["id"]] = {"state": "out", "basis": "measured" if hit.get("basis") in MEASURED_BASIS else "seen", "item": hit}
        elif judged_in:
            out[c["id"]] = {"state": "in"}
    return out


def _cause_of_item(item_id: str | None, fault: str | None) -> str | None:
    if not item_id:
        return None
    for c in load()["causes"]:
        for spec in c.get("items") or []:
            if spec["id"] == item_id and (not spec.get("faults") or fault in spec["faults"]):
                return c["id"]
    return None


def _seen_phrase(item: dict, hand: str) -> str:
    n_out, n_j = item.get("n_out") or 0, item.get("n_judged") or 0
    return "撮ったスイングのすべてで" if n_j and n_out >= n_j else "撮ったスイングの多くで"


def _basis_text(basis: str, c: dict, link_source: str | None, vs: dict | None, has_video: bool, hand: str, other_club: bool = False) -> str:
    if basis in ("measured", "seen"):
        it = vs["item"]
        fl = it.get("fault_label") or r(c.get("issue_title"), hand)
        how = "測ると" if basis == "measured" else "見た目で"
        return f"動画の『{_p_name(it.get('p'))}』の形を{how}、{_seen_phrase(it, hand)}{fl.rstrip('。')}（ガイドの範囲の外）。"
    src = "ガイドの考え方" if link_source == "guide" else "一般的な見方"
    cp = c.get("checkpoint")
    if not cp:
        return f"この動きは動画のチェックの項目では直接は測れないので、球のデータと{src}から見た可能性です。"
    where = f"{VIEW_WORD[cp['view']]}撮った動画の『{_p_name(cp['p'])}』の形"
    if other_club:
        return f"動画は別の種類のクラブのものなので、このクラブについては球のデータと{src}から見た可能性です（{where}で確かめられます）。"
    if has_video:
        return f"動画はありますが、この動きはまだ判断できていないので、球のデータと{src}から見た可能性です（{where}で確かめられます）。"
    return f"動画がまだ無いので、球のデータと{src}から見た可能性です（{where}で確かめられます）。"


# ---------------------------------------------------------------- ドリルと練習の組み方

def _drill_out(did: str, hand: str, for_: str, tentative: bool = False) -> dict | None:
    d = drill(did)
    if not d:
        return None
    src = d.get("source") or {}
    label = pages_label(src.get("pages")) if src.get("kind") == "guide" else DRILL_GENERAL_LABEL
    steps = [r(x, hand) for x in d.get("steps") or []]
    out = {
        "id": d["id"], "name": r(d["name"], hand), "equipment": r(d.get("equipment"), hand), "place": r(d.get("place"), hand),
        "steps": steps, "how": "".join(x.rstrip("。") + "。" for x in steps),
        "reps": d["reps"], "reps_text": f"{kanji(d['reps'])}球",
        "why": r(d.get("why"), hand), "safety": r(d.get("safety"), hand),
        # ガイドの練習法は本人が共有したガイドで確かめられる。一般的な練習法は札を付けて出す（隠さない）
        "checked": src.get("kind") == "guide", "checked_by": label,
        "source": {"kind": src.get("kind"), "label": label, "pages": list(src.get("pages") or [])},
        "for": for_, "tentative": tentative, "catalog_drill": d.get("catalog_drill"),
        # check は練習そのものより「確かめる道具」（当たった場所の跡など）。練習の組み方の順番には入れない
        "role": d.get("role") or "drill",
    }
    if tentative:
        out["tentative_note"] = "原因の候補の動き（まだ動画で確かめていません）に効く練習です。"
    return out


def _menu(drills: list[dict], cue_move: str) -> tuple[list[dict], str]:
    menu = [{"kind": "baseline", "title": "いつも通り", "balls": MENU_WARMUP, "what": "何も意識せずに打つ（あとで比べるため）"}]
    for d in [x for x in drills if x.get("role") != "check"][:2]:
        menu.append({"kind": "drill", "title": d["name"], "balls": d["reps"], "what": d["steps"][min(1, len(d["steps"]) - 1)], "drill_id": d["id"]})
    menu.append({"kind": "main", "title": "本番", "balls": MENU_MAIN, "what": f"意識する動きだけ: {cue_move}"})
    menu.append({"kind": "baseline", "title": "いつも通り", "balls": MENU_WARMUP, "what": "何も意識せずに打ち、最初と比べる"})
    total = sum(x["balls"] for x in menu)

    def one(x: dict) -> str:
        if x["kind"] == "drill":
            return f"「{x['title']}」{kanji(x['balls'])}球"
        return f"{x['title']}{kanji(x['balls'])}球" + ("（意識する動きだけ）" if x["kind"] == "main" else "")

    seq = "、".join(one(x) for x in menu)
    text = (f"今日の練習は、{seq}の順です（全部で{kanji(total)}球）。"
            "最初と最後のいつも通りは比べるための球なので、何も意識しません。"
            "本番は十球のうち八球でできることを目標にします。")
    return menu, text


def _fix(sid: str | None, top_causes: list[dict], hand: str) -> dict:
    """直し方（アクションプラン）。動画で確かめた原因があれば、その動きの直し方を先に。"""
    kb = symptom(sid) if sid else None
    firm = [x for x in top_causes if x["basis"] in ("measured", "seen")]
    setup: list[str] = []
    cue_src = None
    drill_ids: list[tuple[str, str, bool]] = []
    for x in firm:
        c = cause(x["id"]) or {}
        setup += [r(t, hand) for t in (c.get("fix") or {}).get("setup") or []]
        cue_src = cue_src or c.get("fix")
        drill_ids += [(d, f"cause:{x['id']}", False) for d in (c.get("fix") or {}).get("drills") or []]
    if kb:
        setup += [r(t, hand) for t in (kb.get("fix") or {}).get("setup") or []]
        cue_src = cue_src or kb.get("fix")
        drill_ids += [(d, f"symptom:{sid}", False) for d in (kb.get("fix") or {}).get("drills") or []]
    if not firm and top_causes:
        c = cause(top_causes[0]["id"]) or {}
        ds = (c.get("fix") or {}).get("drills") or []
        if ds:
            drill_ids.append((ds[0], f"cause:{top_causes[0]['id']}", True))
    seen, drills = set(), []
    for did, for_, tent in drill_ids:
        if did in seen:
            continue
        seen.add(did)
        d = _drill_out(did, hand, for_, tent)
        if d:
            drills.append(d)
    # 一つ目と二つ目は確かな原因（または球の症状）に効くもの。候補の動きの練習は最後に置く
    drills = [d for d in drills if not d["tentative"]][:MAX_DRILLS - (1 if any(d["tentative"] for d in drills) else 0)] + [d for d in drills if d["tentative"]][:1]
    setup = list(dict.fromkeys(setup))[:MAX_SETUP]
    cue_move = r((cue_src or {}).get("cue"), hand)
    cue_why = r((cue_src or {}).get("cue_why"), hand)
    menu, practice = _menu(drills, cue_move)
    return {"setup": setup, "cue": f"{cue_move}{cue_why}", "cue_move": cue_move, "cue_why": cue_why,
            "drills": drills, "practice": practice, "menu": menu}


# ---------------------------------------------------------------- 課題を組む

def _causes_for(sid: str, vstate: dict, has_video: bool, hand: str, other_club: bool = False) -> tuple[list[dict], list[str]]:
    kb = symptom(sid) or {}
    rows, ruled_out = [], []
    for i, link in enumerate(kb.get("causes") or []):
        c = cause(link["id"]) or {}
        vs = vstate.get(link["id"])
        if vs and vs["state"] == "in":
            ruled_out.append(r(c.get("title"), hand))
            continue
        basis = vs["basis"] if vs and vs["state"] == "out" else "likely"
        rows.append({
            "id": link["id"], "title": r(c.get("title"), hand), "explain": r(link.get("explain"), hand),
            "basis": basis, "basis_text": _basis_text(basis, c, link.get("source"), vs, has_video, hand, other_club),
            "source": link.get("source"), "source_label": SOURCE_LABEL[link.get("source") or "general"],
            "checkpoint": _checkpoint(c.get("checkpoint")), "guide": _guide(c.get("guide"), hand),
            "_order": (0 if basis == "measured" else 1 if basis == "seen" else 2, i),
            "_vs": vs,
        })
    rows.sort(key=lambda x: x["_order"])
    return rows[:MAX_CAUSES], ruled_out


def _clean(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in x.items() if not k.startswith("_")} for x in rows]


def _ideal(sid: str | None, top: list[dict], hand: str) -> dict:
    firm = next((x for x in top if x["basis"] in ("measured", "seen")), None)
    if firm:
        c = cause(firm["id"]) or {}
        idl = c.get("ideal") or {}
        cp = c.get("checkpoint") or {}
        return {"text": r(idl.get("text"), hand), "p": idl.get("p"), "item_id": idl.get("item_id"), "p_name": _p_name(idl.get("p")),
                "figure": {"kind": "checkpoint", "p": idl.get("p"), "view": cp.get("view"), "item_id": idl.get("item_id"), "overlay": True}}
    kb = symptom(sid) or {}
    idl = kb.get("ideal") or {}
    return {"text": r(idl.get("text"), hand), "p": idl.get("p"), "item_id": idl.get("item_id"), "p_name": _p_name(idl.get("p")),
            "figure": dict(idl.get("figure") or {}) or None}


def _video_cta(sid: str, causes: list[dict], has_video: bool) -> dict | None:
    if any(x["basis"] in ("measured", "seen") for x in causes):
        return None
    kb = symptom(sid) or {}
    ps = sorted({x["checkpoint"]["p"] for x in causes if x.get("checkpoint")}, key=lambda p: int(re.sub(r"\D.*", "", p[1:]) or 0))
    if not ps:
        return None
    names = "・".join(f"『{_p_name(p)}』" for p in ps)
    view = kb.get("video_view") or "dtl"
    return {"button": "動画を撮って原因を確かめる", "view": view,
            "text": f"{VIEW_WORD[view]}スイングを撮ると、{names}の形を見て、どの動きが原因かを確かめられます。"
                    + ("いまある動画では、まだこの動きを判断できていません。" if has_video else ""),
            "p": ps}


def _ball_issue(sid: str, ctx: dict, cand: dict | None, merged: list[dict], vstate: dict, has_video: bool, hand: str,
                video_categories: set | None = None) -> dict:
    kb = symptom(sid) or {}
    # 動画のスイングの番手の種類と、この課題の範囲の種類が違えば、動画の判定を当てない（アイアンの動画でウッドを語らない）
    other_club = bool(has_video and video_categories and ctx.get("category") not in video_categories)
    if other_club:
        vstate, has_video = {}, False
    causes, ruled_out = _causes_for(sid, vstate, has_video, hand, other_club)
    now = _now_ball(sid, ctx, hand)
    if merged:
        words = "・".join(dict.fromkeys(CATEGORY_WORD.get(m["category"], "ほかのクラブ") for m in merged))
        now += f"{words}でも同じ傾向が出ています。"
    fix = _fix(sid, causes, hand)
    check = r(kb.get("check"), hand)
    firm = next((x for x in causes if x["basis"] in ("measured", "seen")), None)
    if firm:
        check += r((cause(firm["id"]) or {}).get("check"), hand)
    evidence = _evidence_ball(sid, ctx)
    for x in causes:
        vs = x.get("_vs")
        if vs and vs.get("state") == "out":
            it = vs["item"]
            evidence.append({"label": f"動画: {it.get('title')}",
                             "value_text": f"{it.get('n_judged')}本中{it.get('n_out')}本が範囲の外（{'測った値' if x['basis'] == 'measured' else '見た目'}）",
                             "item_id": it.get("id")})
    return {
        "id": sid, "from": "ball",
        "club_scope": "・".join(dict.fromkeys([club_label(ctx["label"])] + [club_label(m["label"]) for m in merged])),
        "scope_ids": [ctx["scope_id"]] + [m["scope_id"] for m in merged],
        "title": r(kb.get("title"), hand),
        "now": now,
        "impact": _impact_ball(sid, ctx, cand, hand),
        "club_state": r(kb.get("club_state"), hand),
        "causes": _clean(causes),
        "causes_ruled_out": ruled_out,
        "video_cta": _video_cta(sid, causes, has_video),
        "ideal": _ideal(sid, causes, hand),
        "fix": fix,
        "check": check,
        "evidence": evidence,
        "plan_candidate": cand.get("id") if cand else None,
    }


def _motion_issue(cid: str, vs: dict, hand: str) -> dict:
    """動画のチェックで範囲の外だった動きが、どの球の課題にも結びつかないとき（動きが主役・§4.4）。"""
    c = cause(cid) or {}
    it = vs["item"]
    basis = vs["basis"]
    row = {"id": cid, "title": r(c.get("title"), hand), "explain": r(c.get("impact"), hand), "basis": basis,
           "basis_text": _basis_text(basis, c, None, vs, True, hand), "source": "guide" if c.get("guide") else "general",
           "source_label": SOURCE_LABEL["guide" if c.get("guide") else "general"],
           "checkpoint": _checkpoint(c.get("checkpoint")), "guide": _guide(c.get("guide"), hand), "_vs": vs, "_order": (0, 0)}
    fl = it.get("fault_label") or r(c.get("issue_title"), hand)
    fix = _fix(None, [row], hand)
    return {
        "id": f"motion:{cid}", "from": "video", "club_scope": VIDEO_SCOPE_LABEL, "scope_ids": [],
        "title": r(c.get("issue_title"), hand),
        "now": f"動画の『{_p_name(it.get('p'))}』の形で、{_seen_phrase(it, hand)}{fl.rstrip('。')}。",
        "impact": r(c.get("impact"), hand),
        "club_state": None,
        "causes": _clean([row]),
        "causes_ruled_out": [],
        "video_cta": None,
        "ideal": _ideal(None, [row], hand),
        "fix": fix,
        "check": r(c.get("check"), hand),
        "evidence": [{"label": f"動画: {it.get('title')}",
                      "value_text": f"{it.get('n_judged')}本中{it.get('n_out')}本が範囲の外（{'測った値' if basis == 'measured' else '見た目'}）",
                      "item_id": it.get("id")}],
        "plan_candidate": None,
    }


def _scope_order(scopes: list[dict]) -> list[dict]:
    return sorted(scopes, key=lambda c: (0 if c["kind"] == "group" else 1, -(c["profile"].get("n") or 0)))


def _ball_list(scopes: list[dict]) -> list[tuple[str, dict, dict | None, str]]:
    """(症状, 範囲, 候補, which) を優先順に。主の範囲（まとめ・球の多い順の先頭）の now → next →
    ほかの範囲の now → next → 追加の症状（主の範囲の二つは同じ日の同じクラブの課題なので、先に並べる）。"""
    out = []
    head, rest = scopes[:1], scopes[1:]
    passes = [(head, "now"), (head, "next"), (rest, "now"), (rest, "next"), (scopes, "extra")]
    for group, rank in passes:
        for ctx in group:
            cands = ctx.get("cands") or {}
            cl = {c["id"]: c for c in cands.get("candidates") or []}
            if rank in ("now", "next"):
                c = cl.get(cands.get(rank))
                sid = symptom_of(c, ctx["profile"])
                if sid:
                    out.append((sid, ctx, c, rank))
            elif ctx.get("category") in ("iron", "wedge") and any(x.get("id") == "S7" for x in ctx.get("symptoms") or []):
                out.append(("shallow_attack", ctx, None, rank))
    return out


def _strengths(scopes: list[dict], issue_ids: set[str], hand: str) -> list[str]:
    out: list[str] = []
    for ctx in scopes:
        path = (ctx["profile"].get("l1") or {}).get("club_path") or {}
        if (path.get("n") or 0) >= gist.MIN_N and path.get("spread") == "stable" and not path.get("bias") and not issue_ids & {"path_right", "path_left"}:
            out.append(f"{CATEGORY_WORD.get(ctx['category'], '')}はクラブを振る方向がもう安定しています。ここは今のまま触りません。")
            break
    for ctx in scopes:
        s = ctx["profile"].get("strike") or {}
        io = (ctx["profile"].get("l1") or {}).get("impact_offset") or {}
        own = {sid for sid, c2, _, _ in _ball_list([ctx])}
        if (s.get("measured") or 0) >= gist.MIN_N and io.get("spread") != "wide" and not (own & {"strike_heel", "strike_toe", "strike_scatter"}):
            med = s.get("median_m")
            if med is not None and abs(med) <= config.CENTER_STRIKE_M:
                out.append(f"{CATEGORY_WORD.get(ctx['category'], '')}は芯の近くに当たっています。")
                break
    for ctx in scopes:
        it = ((ctx["profile"].get("band") or {}).get("ideal_type") or {}) if (ctx["profile"].get("band") or {}).get("used") else {}
        if it.get("shown") and it.get("kind") == "trim":
            out.append(f"真ん中に飛んだ球は今のままで大丈夫です。直すのは{gist.dir_word(it['side'], hand)}へ外れる球だけです。")
            break
    return out[:2]


def build(scopes: list[dict], hand: str = "R", checks: dict | None = None, n_videos: int = 0,
          video_categories: set | None = None) -> dict | None:
    """診断を組む。scopes は本体の範囲ごとの材料（report.build_report が渡す）:
      {scope_id, label, kind(group|club), category, profile, cands, sections, symptoms, shots}
    checks はそのセッションのスイングのチェック結果（judge.aggregate の出力）か None。
    video_categories はそのスイングの番手の種類（iron など）。分からなければ None（全部の範囲に当てる）。"""
    hand = "L" if hand == "L" else "R"
    scopes = _scope_order([s for s in scopes if s.get("profile")])
    has_video = bool(checks and any(x.get("state") in ("in_range", "out_range") for x in checks.get("items") or []))
    vstate = _video_state(checks) if has_video else {}

    # 球の課題（症状ごとに1つ。同じ症状がほかの範囲にも出ていれば club_scope に足す）
    picked: dict[str, dict] = {}
    order: list[str] = []
    for sid, ctx, cand, _rank in _ball_list(scopes):
        if sid in picked:
            if ctx is not picked[sid]["ctx"] and ctx not in picked[sid]["merged"]:
                picked[sid]["merged"].append(ctx)
            continue
        picked[sid] = {"ctx": ctx, "cand": cand, "merged": []}
        order.append(sid)
    issues = [_ball_issue(sid, picked[sid]["ctx"], picked[sid]["cand"], picked[sid]["merged"], vstate, has_video, hand, video_categories)
              for sid in order]

    # 動画: 課題の順（ドミノの順・judge.pick_focus）で「まずここ」になった動きを先頭へ（§4.4 動きが主役）
    if has_video and checks:
        front: list[dict] = []
        for key in ("focus", "next"):
            iid = checks.get(key)
            it = next((x for x in checks.get("items") or [] if x["id"] == iid), None)
            cid = _cause_of_item(iid, (it or {}).get("fault"))
            if not cid or cid not in vstate or vstate[cid]["state"] != "out":
                continue
            host = next((x for x in issues if any(c["id"] == cid and c["basis"] != "likely" for c in x["causes"])), None)
            if host is None:
                host = next((x for x in front if any(c["id"] == cid for c in x["causes"])), None) or _motion_issue(cid, vstate[cid], hand)
            if host in issues:
                issues.remove(host)
            if host not in front:
                front.append(host)
        issues = front + issues

    issues = issues[:MAX_ISSUES]
    for i, x in enumerate(issues, 1):
        x["rank"] = i
        x["now_or_next"] = "now" if i == 1 else "next"
    if not issues and not scopes:
        return None

    ids = {x["id"] for x in issues}
    strengths = _strengths(scopes, ids, hand)
    if issues:
        summary = []
        if strengths:
            summary.append(strengths[0])
        summary.append(f"今日いちばん大きな課題は「{issues[0]['title']}」です。")
        if len(issues) >= 2:
            rest = "」「".join(x["title"] for x in issues[1:])
            summary.append(f"ほかに「{rest}」がありますが、まずは一つ目だけに絞って練習します。")
        else:
            summary.append("今日の練習はこの一つに絞ります。")
    else:
        summary = ["今日の球では、先に直すものを一つに決められませんでした。"] + strengths[:1]

    if has_video:
        video_hint = "動画のチェックで確かめた動きは、原因の先頭に置いています。同じ向きで撮り続けると、直ったかも動画で確かめられます。"
    elif n_videos:
        video_hint = "動画はありますが、まだ形を測れていません。コマを選んで測ると、原因の動きを確かめられます。"
    else:
        video_hint = "球のデータで分かるのは、当たる瞬間のクラブの様子までです。後ろから（できれば正面からも）スイングを撮ると、どの体の動きが原因かを確かめられます。"
    return {
        "version": VERSION,
        "kb_version": load()["version"],
        "summary": "".join(summary),
        "strengths": strengths,
        "issues": issues,
        "video_needed": not has_video,
        "video_hint": video_hint,
    }


# ---------------------------------------------------------------- 検査用

LABEL_KEYS = {"club_scope", "checked_by", "label", "source_label"}
SKIP_KEYS = {"id", "item_id", "p", "view", "kind", "version", "kb_version", "from", "scope_ids", "pages", "for", "plan_candidate",
             "basis", "source", "now_or_next", "drill_id", "catalog_drill", "figure", "rank", "reps", "balls", "section", "role"}


def texts(d: dict | None) -> list[str]:
    """最初の面に出す文の全部（evidence と構造のラベル・鍵を除く）。check_plain を通すことをテストで固定する。"""
    out: list[str] = []

    def walk(x, key=None):
        if key == "evidence" or key in SKIP_KEYS or key in LABEL_KEYS:
            return
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, k)
        elif isinstance(x, list):
            for v in x:
                walk(v, key)
        elif isinstance(x, str) and x:
            out.append(x)

    walk(d or {})
    return out


def labels(d: dict | None) -> list[tuple[str, str]]:
    """構造のラベル（(鍵, 値)）。決めた形だけを許すことをテストで固定する。"""
    out: list[tuple[str, str]] = []

    def walk(x, key=None):
        if key == "evidence":
            return
        if isinstance(x, dict):
            for k, v in x.items():
                if k in LABEL_KEYS and isinstance(v, str):
                    out.append((k, v))
                else:
                    walk(v, k)
        elif isinstance(x, list):
            for v in x:
                walk(v, key)

    walk(d or {})
    return out


__all__ = ["VERSION", "build", "load", "validate", "texts", "labels", "symptom_of", "club_label", "kanji", "cp_fill"]
