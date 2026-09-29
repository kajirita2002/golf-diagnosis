"""画面の最初に出す要点（gist.py）。利用者の方針（2026-09-29）: 数字も専門用語も使わず、
「いま」「課題」「理想との差」「意識すること・やること」の4つの塊を簡潔に。根拠（数字入りの定型文）は id で引く。"""

import copy
import json
import os

import pytest

from golf_analysis import config, drills, gist
from golf_analysis.report import build_report

DATA = os.path.join(os.path.dirname(__file__), "data", "real_2026-09-17_shots.json")


@pytest.fixture(scope="module")
def shots():
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module", params=["R", "L"])
def rep(request, shots):
    return build_report(shots, request.param)


def _sc(rep, sid):
    return next(s for s in rep["scopes"] if s["scope_id"] == sid)


def _g(rep, sid):
    return _sc(rep, sid)["gist"]


def _block(g, bid):
    return next(b for b in g["blocks"] if b["id"] == bid)


def test_本体の範囲にだけ要点がある(rep):
    for s in rep["scopes"]:
        assert (s["gist"] is not None) == (s["kind"] == "main"), s["scope_id"]


def test_実データで4つの塊が出る(rep):
    mains = [s for s in rep["scopes"] if s["kind"] == "main"]
    assert len(mains) == 3
    for s in mains:
        g = s["gist"]
        assert [b["id"] for b in g["blocks"]] == ["now", "issue", "gap", "action"], s["scope_id"]
        assert [b["title"] for b in g["blocks"]] == ["いま", "課題", "理想との差", "意識すること・やること"]
        for b in g["blocks"]:
            assert 1 <= len(b["lines"]) <= 4, (s["scope_id"], b["id"])
        gap = _block(g, "gap")
        assert gap["figure"] == "C1" and "C1" in s["figures"]
        assert [r["id"] for r in gap["rows"]] == ["flight", "strike", "face", "path"]


def test_最初に出す文に数字も専門用語も無い(rep):
    for s in rep["scopes"]:
        if not s["gist"]:
            continue
        ts = gist.texts(s["gist"])
        assert len(ts) >= 12
        for t in ts:
            assert gist.check_plain(t) == [], (s["scope_id"], t)
            # 念のため、禁じた語を直に見る（表から語が消えても気づけるように）
            for w in ("パス", "フェース", "°", "度", "%", "R²", "帯", "キャリー", "ヒール", "トゥ"):
                assert w not in t, (s["scope_id"], t)
        # 比べる図の最初に見える文字（見出し・件数・注記）にも、件数のほかの数字や専門用語を出さない
        c1 = s["figures"]["C1"]
        for t in [c1["title"], c1["note"], c1["desc"], *c1["labels"].values()]:
            assert gist.check_plain(t) == [], t
        for cap in (c1["now"]["caption"], c1["ideal"]["caption"]):
            assert gist.check_plain(cap.translate(str.maketrans("", "", "0123456789"))) == [], cap


def test_各文に根拠のidがあり同じ範囲の定型文を指す(rep):
    for s in rep["scopes"]:
        g = s["gist"]
        if not g:
            continue
        ids = {c["id"] for sec in s["sections"] for c in sec["claims"]} | {c["id"] for c in rep["cross_club"]["claims"]}
        for ln in gist.lines(g) + [g["focus"]]:
            assert ln["evidence"], (s["scope_id"], ln)
            assert set(ln["evidence"]) <= ids, (s["scope_id"], set(ln["evidence"]) - ids)
            assert all(e.startswith(s["scope_id"] + "/") or e.startswith("cross/") for e in ln["evidence"])


def test_アイアンの要点(rep):
    g = _g(rep, "group:iron")
    right = "右" if rep["handedness"] == "R" else "左"
    now = [x["text"] for x in _block(g, "now")["lines"]]
    assert now[0] == f"ボールは{right}に出て{right}に曲がることが多いです。"  # 12/26
    # パスは揃い（stable）、フェースが右の bias。単回帰 R² 0.97 対 0.21 なので「主な原因」
    assert now[1] == f"振る方向はもう安定していて、今日の球では、左右のずれの主な原因は当たる瞬間にクラブの面が{right}を向きやすいことです。"
    # 14/17球がヒール側 → 「ほとんど」、極端な5/26球 → 「ときどき」。極端な球は右へ飛んだ（右打ちの座標で）
    assert now[2] == f"当たる場所はネック寄りがほとんどで、ときどき大きくネック寄りに外れて当たり、大きく{right}へ飛びます。"
    issue = [x["text"] for x in _block(g, "issue")["lines"]]
    assert issue[0].startswith("まず「ネック寄りの当たりを減らす」。") and "読めない" in issue[0]  # G1 の理由
    assert issue[1] == f"その次に「クラブの面が{right}を向きすぎる球を減らす」。"
    gap = _block(g, "gap")
    assert gap["lines"][0]["text"] == f"理想は、今日すでに打てている狙いどおりの球です。真ん中の球は今のままでよく、{right}へ外れる球を減らせば近づきます。"
    row = {r["id"]: r for r in gap["rows"]}
    assert row["face"]["status"] == "gap" and f"{right}へ外れる球だけが減る" in row["face"]["ideal"]  # 型が trim
    assert row["path"]["status"] == "keep" and row["path"]["now"] == "安定しています。"
    assert row["strike"]["now"].startswith("ネック寄りがほとんどです。")
    act = [x["text"] for x in _block(g, "action")["lines"]]
    assert act[0] == "意識する一点: 飛んだ先より、クラブのどこに当たったかだけを見る。"
    assert "確かめ済みのドリルはまだありません。意識する一点だけで進めます。" in act
    assert _block(g, "action")["drill"] is None and _block(g, "action")["start"] == "strike_heel"
    assert g["headline"] == "まず取り組むのは「ネック寄りの当たりを減らす」です。"


def test_比べる図は今日すでに打てている球から理想を作る(rep):
    c1 = _sc(rep, "group:iron")["figures"]["C1"]
    # インパクトが帯に入った7球（§4.1）が理想。いまは左右とキャリーが取れた全部の球
    assert c1["band_used"] and c1["ideal_from"] == "band" and c1["ideal"]["n"] == 7
    assert c1["now"]["caption"] == f"{c1['now']['n']}球中{c1['now']['in_target']}球が狙いの幅"
    assert c1["ideal"]["caption"] == "今日すでに打てた7球"
    assert all(abs(v) <= config.CENTER_STRIKE_M * 1000 for v in c1["strike"]["ideal"])  # 理想の当たる場所は芯に当たった球だけ
    assert len(c1["face"]["ideal"]) == 7 and c1["face"]["show"]
    assert c1["strike"]["heel_on"] == ("left" if rep["handedness"] == "L" else "right")
    # 5番ウッドは帯を使わないので、実際に狙いの幅に落ちた球を理想にする（計算の話をしない）
    w = _sc(rep, "club:5 Wood")["figures"]["C1"]
    assert not w["band_used"] and w["ideal_from"] == "landed"
    assert w["ideal"]["n"] == sum(1 for pt in w["now"]["points"] if pt["in"] and not pt["mishit"])


def test_5番ウッドは型が違う(rep):
    g = _g(rep, "club:5 Wood")
    now = [x["text"] for x in _block(g, "now")["lines"]]
    right = "右" if rep["handedness"] == "R" else "左"
    assert now[0] == "ボールは決まった形がなく、左右に散っています。"  # 一番多い枠が同数
    assert now[1].startswith(f"振る方向は{right}に偏っていて")
    assert now[2] == "当たる場所が毎回ばらつきます。"
    assert g["focus"]["candidate_id"] == "strike_scatter" and "一緒に動いて" in g["focus"]["why"]  # G2


def test_4番UTは当たる場所を今は触らない(rep):
    g = _g(rep, "club:4 Hybrid")
    row = {r["id"]: r for r in _block(g, "gap")["rows"]}
    assert row["strike"]["status"] == "ok" and row["strike"]["ideal"] == "このままでよい"
    assert "当たる場所・振る方向は、今は触らずそのままにします。" in [x["text"] for x in _block(g, "action")["lines"]]


def test_量の言葉は固定の閾値():
    assert gist.freq(14, 17) == "ほとんど" and gist.freq(12, 26) == "多い" and gist.freq(4, 26) == "ときどき"
    assert gist.freq(1, 26) == "まれ" and gist.freq(0, 26) == "ない" and gist.freq(3, 4) is None
    assert gist.freq(13, 26) == "半分以上"
    assert [w for _, w in config.PLAIN_FREQ] == ["ほとんど", "半分以上", "多い", "ときどき", "まれ"]


def test_平易な言葉の表は1か所():
    # 専門用語 → 平易な言葉の対応（画面の言葉はここからだけ作る）
    t = config.PLAIN_TERMS
    assert (t["club_path"], t["face_angle"], t["heel"], t["toe"], t["carry"]) == ("振る方向", "クラブの面の向き", "ネック寄り", "先寄り", "飛んだ距離")
    for v in t.values():
        assert gist.check_plain(v) == [], v


def test_検査は数字と専門用語を拾う():
    assert gist.check_plain("フェースが +3.3° 右") == ["フェース", "°", "数字", "+"]
    assert gist.check_plain("面が右を向きがちです。") == []
    assert "英字" in gist.check_plain("k が 0.3") and "度" in gist.check_plain("三度")
    assert "%" in gist.check_plain("半分（50%）")


def test_確かめ済みのドリルだけを出す(shots, monkeypatch):
    """checked_by の無いドリルは出さない。人が確かめたら（checked_by を埋めたら）意識する1点が出る。"""
    cat = copy.deepcopy(drills.load())
    two = next(d for d in cat["drills"] if d["id"] == "strike.two_balls")
    two["checked_by"] = "テストの人"
    monkeypatch.setattr(drills, "load", lambda: cat)
    g = _g(build_report(shots, "R"), "group:iron")
    act = _block(g, "action")
    assert act["drill"] and act["drill"]["id"] == "strike.two_balls"
    texts = [x["text"] for x in act["lines"]]
    cue = drills.render(two["cue_transfer"], "R")
    if gist.check_plain(cue) == []:
        assert texts[0] == f"意識する一点: 「{cue}」"
    # 名前に数字がある（2球並べ）ので、最初の面には名前を出さず、プランを始めたときに出す
    assert "道具を使う練習もあります（プランを始めると出ます）。" in texts
    assert all(gist.check_plain(t) == [] for t in gist.texts(g))


def test_ドリル集はまだ誰も確かめていない():
    # 中身は書いてあるが、人が向きと安全を確かめるまで checked_by は空（画面は「確かめ済みのドリルはまだありません」）
    assert not [d["id"] for d in drills.load()["drills"] if drills.is_checked(d)]


def test_プランの型にも最初に出す言葉が入る(shots):
    from fastapi.testclient import TestClient

    from golf_analysis.app import app

    r = TestClient(app).post("/v1/plan/build", json={"shots": shots, "handedness": "R", "scope_id": "group:iron",
                                                   "candidate_id": "strike_heel", "cue": "向こうにボールがあるつもり"})
    assert r.status_code == 200, r.text
    plain = r.json()["plan"]["trigger"]["plain"]
    assert plain["title"] == "ネック寄りの当たりを減らす" and "読めない" in plain["why"]
    assert all(gist.check_plain(t) == [] for t in plain.values())


def test_構造のラベルは決めた形だけ():
    ok = [("P2", "p"), ("P5.5", "p"), ("10回中8回", "count"), ("26球中9球", "count"), ("2回目", "count"), ("8/10", "count"),
          ("9月29日", "date"), ("9月29日（今日）", "date"), ("9月17日（木）", "date"), ("約$0.2", "cost"), ("約40分", "duration"),
          ("9番アイアン", "club"), ("ドライバー", "club"), ("Swing Lab", "brand")]
    for text, kind in ok:
        assert gist.check_plain_label(text, (kind,)), (text, kind)
    # 所見の文や、ほかの数字は通さない（角度・割合・英字）
    for text in ["右へ3度", "8/10で合格", "P2 クラブが内側", "30%", "7 Iron", "Swing", "９月", "約40ヤード"]:
        assert not gist.check_plain_label(text, ("p", "count", "date", "cost", "duration", "club", "brand")), text
    # 種類を絞ると、その形だけ
    assert not gist.check_plain_label("9月29日", ("count",))
