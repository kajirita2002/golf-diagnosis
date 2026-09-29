"""ドリル集（docs/DESIGN_coaching.md §5.7）。読むのはここだけ（Go は /v1/drills を中継するだけ）。

- 中身は catalog.json、置き方の図は svg/*.svg（右打ちで描く。左打ちは左右を反転して出す）。
- **checked_by（向き・安全・図が正しいかを人が確かめた）が無いドリルは出さない。** 置く側が逆だと
  逆の動きを罰するため（ヒールの定番は、ボールの向こう側＝トゥ側に障害物。手前に置くと逆）。
  coach_reviewed_by は札だけ（無ければ「コーチ未確認のドリル」と出す）。
- **効果は集に書かない。** 効いたかは本人の実験の記録（plan_runs）で持つ。
- 文の向きの語は差し込み（{dir:right} / {dir:left} / {lead} 目標側 / {trail} 後ろ側）で持ち、
  ここで利き手を見て入れる（R9）。トゥ／ヒールは入れ替えない。
"""

from __future__ import annotations

import copy
import json
import os
import re

from .. import config
from ..claims import dir_word

HERE = os.path.dirname(__file__)
CATALOG_PATH = os.path.join(HERE, "catalog.json")

KINDS = ("constraint", "feel", "setup", "compensation")
# 定着の判定を1回延ばす種類（§5.7）
SLOW_SETTLE_KINDS = ("setup", "compensation")
ISSUES = (
    "strike_heel", "strike_toe", "strike_scatter", "start_right", "start_left", "start_var",
    "curve_right", "curve_left", "curve_var", "top_extreme", "low_point",
)
LEVERS = ("face", "path", "face_to_path")
CATEGORIES = ("driver", "wood", "hybrid", "iron", "wedge")
GOALS = (None, "reduce_abs", "reduce_sd", "increase", "decrease")
COUNT_KINDS = ("heel_extreme", "toe_extreme", "thin")
TEXT_FIELDS = ("title", "what_changes", "cue_drill", "cue_transfer", "safety", "note")
# 体の言葉を許すのは手順（steps）だけ（R3 の例外）。ここに挙げた欄には LAYER_TERMS を置かない
NO_BODY_FIELDS = ("what_changes", "cue_drill", "cue_transfer")
MAX_STEPS = 3

_CACHE: dict = {}


def load() -> dict:
    """カタログを読む（ファイルが変わったら読み直す）。"""
    st = os.stat(CATALOG_PATH).st_mtime_ns
    if _CACHE.get("mtime") != st:
        with open(CATALOG_PATH, encoding="utf-8") as f:
            _CACHE["data"] = json.load(f)
        _CACHE["mtime"] = st
    return _CACHE["data"]


def version() -> str:
    return load()["version"]


def is_checked(d: dict) -> bool:
    return bool((d.get("checked_by") or "").strip())


def _all_text(d: dict) -> list[str]:
    out = [d.get(k) or "" for k in TEXT_FIELDS]
    out += list(d.get("steps") or []) + list(d.get("video_checks") or [])
    return out


def validate(cat: dict) -> list[str]:
    """形の検査（pytest が全件通ることを固定する）。誤りの文の並びを返す。"""
    errs: list[str] = []
    seen = set()
    for d in cat.get("drills") or []:
        did = d.get("id") or "?"
        if did in seen:
            errs.append(f"{did}: id が重なっています")
        seen.add(did)
        for k in ("title", "what_changes", "cue_drill", "cue_transfer", "diagram", "safety"):
            if not (d.get(k) or "").strip():
                errs.append(f"{did}: {k} が空です")
        if not d.get("issues") or any(i not in ISSUES for i in d["issues"]):
            errs.append(f"{did}: issues が §5.4 の語彙にありません: {d.get('issues')}")
        if any(lv not in LEVERS for lv in d.get("levers") or []):
            errs.append(f"{did}: levers が語彙にありません: {d.get('levers')}")
        if not d.get("categories") or any(c not in CATEGORIES for c in d["categories"]):
            errs.append(f"{did}: categories が語彙にありません")
        if d.get("kind") not in KINDS:
            errs.append(f"{did}: kind が {KINDS} にありません")
        kpi = d.get("kpi") or {}
        if kpi.get("metric") not in config.MMD:
            errs.append(f"{did}: kpi.metric が config.MMD にありません: {kpi.get('metric')}")
        if kpi.get("goal") not in GOALS:
            errs.append(f"{did}: kpi.goal が使えません: {kpi.get('goal')}")
        for g in d.get("guardrails") or []:
            if g.get("metric") not in config.MMD or g.get("goal") not in GOALS[1:]:
                errs.append(f"{did}: guardrails の形が違います: {g}")
        if d.get("path_sign") not in PATH_SIGNS:
            errs.append(f"{did}: path_sign は negative / positive です: {d.get('path_sign')}")
        if any(c not in COUNT_KINDS for c in d.get("counts") or []):
            errs.append(f"{did}: counts が語彙にありません")
        steps = d.get("steps") or []
        if not steps or len(steps) > MAX_STEPS:
            errs.append(f"{did}: steps は1〜{MAX_STEPS}個です（{len(steps)}個）")
        for k in NO_BODY_FIELDS:
            bad = config.layer_terms_in(d.get(k) or "")
            if bad:
                errs.append(f"{did}: {k} に体・クラブの動きの言葉があります: {bad}")
        for t in _all_text(d):
            if "右" in t or "左" in t:
                errs.append(f"{did}: 右・左を直書きしています（{{dir:right}} / {{lead}} などの差し込みで書く）: {t}")
            for tok in re.findall(r"\{[^}]*\}", t):
                if tok not in ("{dir:right}", "{dir:left}", "{lead}", "{trail}"):
                    errs.append(f"{did}: 知らない差し込み口です: {tok}")
        path = os.path.join(HERE, d.get("diagram") or "")
        if not d.get("diagram") or not os.path.isfile(path):
            errs.append(f"{did}: 図がありません: {d.get('diagram')}")
        else:
            svg = open(path, encoding="utf-8").read()
            if re.search(r"<script|\son\w+\s*=|href\s*=|fill=\"#|stroke=\"#", svg, re.I):
                errs.append(f"{did}: 図に script・イベント・リンク・直書きの色があります")
            if "<title>" not in svg or "<desc>" not in svg:
                errs.append(f"{did}: 図に title と desc が要ります")
            if "右" in svg or "左" in svg:
                errs.append(f"{did}: 図の文字に右・左を直書きしています（左打ちで反転するため）")
        if "checked_by" not in d or "coach_reviewed_by" not in d or not (d.get("author") or "").strip():
            errs.append(f"{did}: author / checked_by / coach_reviewed_by の欄が要ります")
    for mz in cat.get("measures") or []:
        if mz.get("metric") not in config.MMD or not mz.get("steps"):
            errs.append(f"{mz.get('id')}: 計測プランの形が違います")
    return errs


# ---------------------------------------------------------------- 利き手

_TOKEN = re.compile(r"\{dir:(right|left)\}|\{(lead|trail)\}")


def render(text: str, hand: str) -> str:
    """向きの差し込みを利き手で埋める（右打ち: {lead}=左・{trail}=右）。"""

    def rep(mo: re.Match) -> str:
        if mo.group(1):
            return dir_word(mo.group(1), hand)
        return dir_word("left" if mo.group(2) == "lead" else "right", hand)

    return _TOKEN.sub(rep, text or "")


def diagram_svg(d: dict, hand: str) -> str | None:
    """置き方の図。右打ちで描いてあるので、左打ちは形を左右反転し、文字の位置だけ鏡に写す（文字は裏返さない）。"""
    path = os.path.join(HERE, d.get("diagram") or "")
    if not os.path.isfile(path):
        return None
    svg = open(path, encoding="utf-8").read()
    if hand != "L":
        return svg
    w = float(re.search(r'viewBox="0 0 ([0-9.]+) ', svg).group(1))
    svg = svg.replace('<g class="d-shapes">', f'<g class="d-shapes" transform="translate({w:g} 0) scale(-1 1)">', 1)

    def mirror_text(mo: re.Match) -> str:
        return f'{mo.group(1)}x="{w - float(mo.group(2)):g}"'

    head, sep, tail = svg.partition('<g class="d-labels">')
    tail = re.sub(r'(<text [^>]*?)x="([0-9.]+)"', mirror_text, tail)
    return head + sep + tail


def public(d: dict, hand: str) -> dict:
    """画面へ出す形（向きの語を埋め、図の SVG を添える）。"""
    out = copy.deepcopy(d)
    for k in TEXT_FIELDS:
        if out.get(k):
            out[k] = render(out[k], hand)
    out["steps"] = [render(s, hand) for s in out.get("steps") or []]
    out["video_checks"] = [render(s, hand) for s in out.get("video_checks") or []]
    out["diagram_svg"] = diagram_svg(d, hand)
    out["checked"] = is_checked(d)
    out["coach_reviewed"] = bool((d.get("coach_reviewed_by") or "").strip())
    out["slow_settle"] = d.get("kind") in SLOW_SETTLE_KINDS
    return out


def listing(hand: str = "R") -> dict:
    """GET /v1/drills の中身。確かめ済みのものだけ。"""
    cat = load()
    drills = cat.get("drills") or []
    shown = [public(d, hand) for d in drills if is_checked(d)]
    return {
        "catalog_version": cat["version"],
        "drills": shown,
        "n_total": len(drills),
        "n_unchecked": len(drills) - len(shown),
        "measures": [{**mz, "steps": [render(s, hand) for s in mz.get("steps") or []]} for mz in cat.get("measures") or []],
    }


def get(drill_id: str) -> dict | None:
    return next((d for d in load().get("drills") or [] if d["id"] == drill_id), None)


PATH_SIGNS = (None, "negative", "positive")


def matches(d: dict, cand: dict, category: str | None, path_mean: float | None = None) -> bool:
    """候補（plan.candidates の1つ）にこのドリルを当ててよいか。issue・レバー・クラブの種類・パスの向きで見る。

    path_sign のあるドリル（パスが − のときのヘッドカバーなど。§5.7）は、範囲のパスの平均（右打ちの座標）が
    その向きのときだけ当てる。パスが分からなければ当てない（曲がりが同じでもパスが逆なら逆の動きを練習させる）。"""
    names = set(cand.get("issue_names") or [i["issue"] for i in cand.get("issues") or []])
    if not names & set(d.get("issues") or []):
        return False
    if d.get("levers") and cand.get("lever") not in d["levers"]:
        return False
    if category and d.get("categories") and category not in d["categories"]:
        return False
    sign = d.get("path_sign")
    if sign:
        if path_mean is None:
            return False
        if (sign == "negative" and not path_mean < 0) or (sign == "positive" and not path_mean > 0):
            return False
    return True


def records_by_drill(history: list[dict] | None) -> dict:
    """あなたの記録（§8.6）: plan_runs をドリルごとに集め、件数だけで持つ（割合は出さない）。

    history の1行は {drill_id, issue, grade, counts_as_worked}（1回の練習の主 KPI の判定と、効いたに数えたか）。
    「良くなった回」は効いたに数えた回だけ（良い判定でも、測れた球が減った・見張りが崩れた回は数えない）。
    counts_as_worked の無い古い行は判定の区分で数える。"""
    out: dict[str, dict] = {}
    for h in history or []:
        did = h.get("drill_id")
        if not did:
            continue
        r = out.setdefault(did, {"drill_id": did, "runs": 0, "moderate_plus": 0, "none_or_worse": 0, "last_grade": None, "last_date": None})
        g = h.get("grade")
        r["runs"] += 1
        ok = h.get("counts_as_worked") if "counts_as_worked" in h else g in ("strong", "moderate")
        if ok:
            r["moderate_plus"] += 1
        if g in ("none", "worse"):
            r["none_or_worse"] += 1
        r["last_grade"], r["last_date"] = g, h.get("date") or r["last_date"]
    return out


def for_candidate(cand: dict, category: str | None, hand: str, history: list[dict] | None = None, path_mean: float | None = None) -> dict:
    """候補に勧めるドリル（確かめ済みだけ）。記録のあるドリルを先に置き、効かなかった記録は後ろへ。"""
    recs = records_by_drill(history)
    pool = [d for d in load().get("drills") or [] if matches(d, cand, category, path_mean)]
    shown = [d for d in pool if is_checked(d)]

    def key(d):
        r = recs.get(d["id"])
        worked = bool(r and r["moderate_plus"])
        failed = bool(r and not r["moderate_plus"] and r["none_or_worse"])
        return (0 if worked else (2 if failed else 1), d.get("ease", 9), d["id"])

    out = []
    for d in sorted(shown, key=key):
        p = public(d, hand)
        r = recs.get(d["id"])
        p["record"] = r
        # 前回の記録（日付と判定）。割合は出さない（確率のふりになる）
        p["record_note"] = None
        if r and r["moderate_plus"]:
            p["record_note"] = "worked_before"
        elif r and r["none_or_worse"]:
            p["record_note"] = "not_worked_before"
        out.append(p)
    return {"drills": out, "n_matching": len(pool), "n_unchecked": len(pool) - len(shown)}
