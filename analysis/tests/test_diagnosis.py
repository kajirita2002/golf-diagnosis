"""診断（diagnosis）と知識ベース（coaching_kb.json）の検査。

利用者の声（2026-09-30）「現在はこんな課題があって、それはこの動きが原因で、理想の動きはこうだから、
こう直しましょう、を構造化して」「見るだけでは結果は変わらない。アクションプランが要る」。
"""

import copy
import json
import os
import re

import pytest

from golf_analysis import config, diagnosis, drills, gist
from golf_analysis.checkpoints import by_id, items as cp_items
from golf_analysis.report import build_report

HERE = os.path.dirname(__file__)


@pytest.fixture(scope="module")
def shots():
    with open(os.path.join(HERE, "data", "real_2026-09-17_shots.json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def rep(shots):
    return build_report(shots, "R")


@pytest.fixture(scope="module")
def rep_l(shots):
    return build_report(shots, "L")


def _swings(items, club_class="iron", n=3, view="dtl"):
    return [{"swing_id": i, "view": view, "club_class": club_class, "items": copy.deepcopy(items)} for i in range(1, n + 1)]


EXT_OUT = [
    {"id": "err.early_ext.dtl", "state": "out_range", "fault": "ext", "basis": "measured", "reason": ""},
    {"id": "iron.p1.dtl.hands", "state": "in_range", "fault": None, "basis": "measured", "reason": ""},
]


# ---------------------------------------------------------------- 知識ベース

def test_知識ベースの形と文が通る():
    assert diagnosis.validate() == []


def test_知識ベースの検査は誤りを拾う():
    kb = copy.deepcopy(diagnosis.load())
    kb["symptoms"][0]["impact"] = "右へ曲がり、打点が 10 ずれる"
    kb["symptoms"][0]["fix"]["drills"] = ["nai"]
    kb["causes"][0]["items"][0]["faults"] = ["nai"]
    kb["drills"][0]["source"] = {"kind": "guide", "pages": ["300"]}
    errs = "\n".join(diagnosis.validate(kb))
    for w in ("直書き", "最初の面に出せない語", "知らないドリル", "外れの向き", "ノートの節の範囲"):
        assert w in errs


def test_プランの課題の語彙をすべて症状に当てている():
    kb = diagnosis.load()
    covered = {i for s in kb["symptoms"] for i in s["plan_issues"]}
    assert set(drills.ISSUES) <= covered


def test_ガイドのよくあるミスと上げ始めのケーススタディを原因に持つ():
    mapped = {(it["id"], f) for c in diagnosis.load()["causes"] for it in c["items"] for f in it["faults"]}
    must = [
        ("err.steep.p5", "steep"), ("err.steep.p6", "steep"), ("err.early_release.p6", "early"),
        ("err.early_ext.dtl", "ext"), ("err.early_ext.fo", "ext"), ("err.early_rot.p6", "early"),
        ("err.sway.p4", "trail"), ("err.sway.p7", "trail"), ("err.trail_elbow.p4", "off"),
        ("iron.p2.dtl.head_vs_hands", "inside"), ("iron.p2.dtl.head_vs_hands", "outside"),
        ("iron.p1.dtl.hands", "in"), ("iron.p1.dtl.hands", "out"), ("iron.p4.dtl.shaft_vs_forearm", "off"),
        ("iron.p5.dtl.shaft", "steep"), ("iron.p5.dtl.shaft", "shallow"), ("iron.p6.dtl.head_vs_hands", "inside"),
        ("iron.p7.dtl.hands", "out"), ("iron.p7.dtl.hand_height", "up"), ("path.loop", "over"), ("path.loop", "under"),
    ]
    missing = [m for m in must if m not in mapped]
    assert not missing, missing


def test_症状の原因は事前の表の項目とつながる():
    """球の症状（S1〜S7）がある症状は、原因の候補のどれかがその症状に結びついた項目を持つ（§9.1 の事前の表）。"""
    for s in diagnosis.load()["symptoms"]:
        if not s["video_symptoms"]:
            continue
        linked = {it["id"] for it in cp_items() if set(it.get("l1_links") or []) & set(s["video_symptoms"])}
        own = {it["id"] for link in s["causes"] for it in (diagnosis.cause(link["id"]) or {}).get("items") or []}
        assert linked & own, s["id"]


def test_ドリルは札つきで安全の注意を持つ():
    for d in diagnosis.load()["drills"]:
        assert d["safety"].strip()
        out = diagnosis._drill_out(d["id"], "R", "test")
        if d["source"]["kind"] == "guide":
            assert out["checked"] is True and out["checked_by"].startswith("PGAガイド p.")
        else:
            assert out["checked"] is False and out["checked_by"] == "一般的な練習法"
        assert diagnosis.LABEL_RE.match(out["checked_by"])


def test_立てた物を打ち出しの先に置かない():
    for d in diagnosis.load()["drills"]:
        text = d["place"] + "".join(d["steps"])
        if "立て" in text:
            assert "立てない" in text or "立てず" in text, d["id"]


def test_漢数字():
    assert [diagnosis.kanji(n) for n in (1, 8, 10, 12, 20, 44, 100, 118)] == ["一", "八", "十", "十二", "二十", "四十四", "百", "百十八"]


def test_番手のラベル():
    assert diagnosis.club_label("5 Wood") == "5番ウッド"
    assert diagnosis.club_label("4 Hybrid") == "4番ユーティリティ"
    assert diagnosis.club_label("アイアン（まとめ）") == "アイアン（まとめ）"
    assert diagnosis.check_club_scope("アイアン（まとめ）・4番ユーティリティ")
    assert not diagnosis.check_club_scope("7番 と 8番")


# ---------------------------------------------------------------- 実データ（動画なし）

def test_実データで診断が出る(rep):
    d = rep["diagnosis"]
    assert d and d["version"] == "diagnosis/1"
    assert d["summary"] and 1 <= len(d["strengths"]) <= 2
    assert 1 <= len(d["issues"]) <= diagnosis.MAX_ISSUES
    assert [x["rank"] for x in d["issues"]] == list(range(1, len(d["issues"]) + 1))
    assert d["video_needed"] is True and d["video_hint"]


def test_課題は優先順で主の範囲の二つが先(rep):
    iss = rep["diagnosis"]["issues"]
    # アイアン（まとめ）のプランの now → next（ネック寄り → 面が右）。ほかの範囲の課題はそのあと
    assert [x["id"] for x in iss[:2]] == ["strike_heel", "face_right"]
    assert iss[0]["plan_candidate"] == "strike_heel" and iss[0]["now_or_next"] == "now"
    assert iss[1]["club_scope"] == "アイアン（まとめ）・4番ユーティリティ"  # 同じ症状のユーティリティを束ねる
    assert "ユーティリティでも同じ傾向" in iss[1]["now"]
    assert iss[2]["id"] == "strike_scatter" and iss[2]["club_scope"] == "5番ウッド"


def test_課題ごとに五段が全部そろう(rep):
    for x in rep["diagnosis"]["issues"]:
        assert x["title"] and x["now"] and x["impact"] and x["club_state"] and x["check"]
        assert 1 <= len(x["causes"]) <= diagnosis.MAX_CAUSES
        for c in x["causes"]:
            assert c["title"] and c["explain"] and c["basis_text"] and c["basis"] in diagnosis.BASIS
            assert c["source_label"] in ("PGAガイド", "一般的な見方")
        assert x["ideal"]["text"]
        fx = x["fix"]
        assert fx["setup"] and fx["cue_move"] and fx["cue_why"] and fx["cue"].startswith(fx["cue_move"])
        assert 1 <= len(fx["drills"]) <= diagnosis.MAX_DRILLS
        for dr in fx["drills"]:
            assert dr["steps"] and dr["how"] and dr["why"] and dr["safety"] and dr["reps"] > 0 and dr["place"] and dr["equipment"]
        kinds = [m["kind"] for m in fx["menu"]]
        assert kinds[0] == "baseline" and kinds[-1] == "baseline" and "main" in kinds and "drill" in kinds
        main = next(m for m in fx["menu"] if m["kind"] == "main")
        assert fx["cue_move"] in main["what"]
        assert "球" in fx["practice"] and "十球のうち八球" in fx["practice"]


def test_本番は見るではなく体をこう動かす(rep):
    for x in rep["diagnosis"]["issues"]:
        cue = x["fix"]["cue_move"]
        assert not cue.endswith("見る。") and "だけを見る" not in cue


def test_最初の面の文に数字も専門用語も無い(rep, rep_l):
    for r in (rep, rep_l):
        ts = diagnosis.texts(r["diagnosis"])
        assert len(ts) > 100
        bad = {t: gist.check_plain(t) for t in ts if gist.check_plain(t)}
        assert not bad, bad


def test_構造のラベルは決めた形だけ(rep):
    labs = diagnosis.labels(rep["diagnosis"])
    assert labs
    for k, v in labs:
        if k == "club_scope":
            assert diagnosis.check_club_scope(v), v
        else:
            assert diagnosis.LABEL_RE.match(v), (k, v)


def test_数字は根拠の中だけ(rep):
    ev = [e for x in rep["diagnosis"]["issues"] for e in x["evidence"]]
    assert ev and any(re.search(r"\d", e["value_text"]) for e in ev)
    have = {c["id"] for s in rep["scopes"] for sec in s["sections"] for c in sec["claims"]}
    assert all(e["claim_id"] in have for e in ev if e.get("claim_id"))


def test_動画が無ければ原因は可能性と書き動画を促す(rep):
    for x in rep["diagnosis"]["issues"]:
        assert all(c["basis"] == "likely" for c in x["causes"])
        assert all("可能性" in c["basis_text"] for c in x["causes"])
        assert "確かな事実" in x["club_state"]
    first = rep["diagnosis"]["issues"][0]
    assert first["video_cta"] and first["video_cta"]["button"] == "動画を撮って原因を確かめる"
    assert "P6" in first["video_cta"]["p"]


def test_動画が無くてもアクションプランは空にしない(rep):
    first = rep["diagnosis"]["issues"][0]
    ds = first["fix"]["drills"]
    assert ds[0]["for"] == "symptom:strike_heel" and not ds[0]["tentative"]
    # 候補の動き（腰が前に出る）に効く練習は、札を付けて最後に置く
    assert ds[-1]["tentative"] and ds[-1]["for"].startswith("cause:") and "まだ動画で確かめていません" in ds[-1]["tentative_note"]
    # 練習の組み方には、確かめる道具（当たった場所の跡）ではなく練習を並べる
    drill_menu = [m["drill_id"] for m in first["fix"]["menu"] if m["kind"] == "drill"]
    assert "spray" not in drill_menu and len(drill_menu) == 2


def test_ガイドの練習法はページつきで出る(rep):
    face = next(x for x in rep["diagnosis"]["issues"] if x["id"] == "face_right")
    guide = [d for d in face["fix"]["drills"] if d["source"]["kind"] == "guide"]
    assert guide and all(d["checked_by"].startswith("PGAガイド p.") for d in guide)


# ---------------------------------------------------------------- 動画あり（合成）

def test_動画で測れた原因が先頭に来てその動きのドリルが先(shots):
    d = build_report(shots, "R", swings=_swings(EXT_OUT))["diagnosis"]
    assert d["video_needed"] is False
    heel = next(x for x in d["issues"] if x["id"] == "strike_heel")
    assert heel["causes"][0]["id"] == "early_ext" and heel["causes"][0]["basis"] == "measured"
    assert "可能性" not in heel["causes"][0]["basis_text"] and "動画の" in heel["causes"][0]["basis_text"]
    # 動画で範囲の中だった動き（構えの手の位置）は、原因の候補から外して理由を残す
    assert all(c["id"] != "setup_close" for c in heel["causes"])
    assert heel["causes_ruled_out"]
    assert heel["fix"]["drills"][0]["for"] == "cause:early_ext" and heel["fix"]["drills"][0]["id"] == "chair"
    assert "お尻" in heel["fix"]["cue_move"]
    assert heel["ideal"]["figure"]["kind"] == "checkpoint" and heel["ideal"]["item_id"] == "err.early_ext.dtl"
    assert heel["video_cta"] is None
    assert any(e.get("item_id") == "err.early_ext.dtl" for e in heel["evidence"])
    assert not [t for t in diagnosis.texts(d) if gist.check_plain(t)]


def test_球の課題に結びつかない動きは動きの課題として先頭に(shots):
    items = [{"id": "iron.p2.dtl.head_vs_hands", "state": "out_range", "fault": "inside", "basis": "measured_tap", "reason": ""}]
    d = build_report(shots, "R", swings=_swings(items))["diagnosis"]
    first = d["issues"][0]
    assert first["id"] == "motion:p2_inside" and first["from"] == "video"
    assert first["causes"][0]["basis"] == "measured"
    assert first["fix"]["drills"][0]["id"] == "case_p2_in" and first["fix"]["drills"][0]["checked_by"].startswith("PGAガイド p.158")
    assert len(d["issues"]) == diagnosis.MAX_ISSUES


def test_見た目の判定は_seen(shots):
    # err.early_rot.p6 は iron.p6.dtl.hips と束ねてある（判定は相手の項目に出る）
    items = [{"id": "iron.p6.dtl.hips", "state": "out_range", "fault": "early", "basis": "visual", "reason": ""},
             {"id": "err.early_rot.p6", "state": "out_range", "fault": "early", "basis": "visual", "reason": ""}]
    d = build_report(shots, "R", swings=_swings(items))["diagnosis"]
    face = next(x for x in d["issues"] if x["id"] == "face_right")
    assert face["causes"][0]["id"] == "body_open_early" and face["causes"][0]["basis"] == "seen"
    assert "見た目" in face["causes"][0]["basis_text"]


def test_一本だけの見立ては原因に上げない(shots):
    d = build_report(shots, "R", swings=_swings(EXT_OUT, n=1))["diagnosis"]
    heel = next(x for x in d["issues"] if x["id"] == "strike_heel")
    assert all(c["basis"] == "likely" for c in heel["causes"] if c["id"] == "early_ext")


def test_別の種類のクラブの動画は当てない(shots):
    d = build_report(shots, "R", swings=_swings(EXT_OUT, club_class="driver"))["diagnosis"]
    heel = next(x for x in d["issues"] if x["id"] == "strike_heel")
    assert all(c["basis"] == "likely" for c in heel["causes"])
    assert "別の種類のクラブ" in heel["causes"][0]["basis_text"]


# ---------------------------------------------------------------- 左打ち

def test_左打ちで向きの語が入れ替わる(rep, rep_l):
    r_face = next(x for x in rep["diagnosis"]["issues"] if x["id"] == "face_right")
    l_face = next(x for x in rep_l["diagnosis"]["issues"] if x["id"] == "face_right")
    assert "右を向く" in r_face["title"] and "左を向く" in l_face["title"]
    assert "左手首" in r_face["causes"][0]["title"] and "右手首" in l_face["causes"][0]["title"]
    r_chair = next(d for x in rep["diagnosis"]["issues"] for d in x["fix"]["drills"] if d["id"] == "chair")
    l_chair = next(d for x in rep_l["diagnosis"]["issues"] for d in x["fix"]["drills"] if d["id"] == "chair")
    assert "左側のお尻" in r_chair["how"] and "右側のお尻" in l_chair["how"]
    # 当たる場所（ネック／先）は入れ替えない
    assert rep_l["diagnosis"]["issues"][0]["title"] == "ネック寄りに当たる"


# ---------------------------------------------------------------- 口

def test_報告の口が診断を返し動画のチェックを受け取る(shots):
    from fastapi.testclient import TestClient

    from golf_analysis.app import app

    c = TestClient(app)
    r = c.post("/v1/report", json={"shots": shots, "handedness": "R"})
    assert r.status_code == 200 and r.json()["diagnosis"]["issues"]
    r = c.post("/v1/report", json={"shots": shots, "handedness": "R", "swings": _swings(EXT_OUT)})
    heel = next(x for x in r.json()["diagnosis"]["issues"] if x["id"] == "strike_heel")
    assert heel["causes"][0]["basis"] == "measured"


def test_診断が作れなくても解説は出す(shots, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(diagnosis, "build", boom)
    r = build_report(shots, "R")
    assert r["diagnosis"] is None and r["scopes"]


def test_球が少なければ診断は空ではなく理由を言う():
    r = build_report([], "R")
    assert r["diagnosis"] is None or r["diagnosis"]["issues"] == []
