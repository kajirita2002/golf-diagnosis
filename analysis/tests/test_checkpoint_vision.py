"""見た目の評価（Claude）と、球の症状との対応（docs/DESIGN_v2.md §6.6・§6.7・§9.1・§15 段2c）。

本物の API は呼ばない（偽の Claude）。画像は JPEG の頭だけを持つ合成のバイト列。
"""

from __future__ import annotations

import base64
import copy
import json
import os
import re
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from golf_analysis import checkpoints as cp  # noqa: E402
from golf_analysis import config, drills, video_candidates  # noqa: E402
from golf_analysis.app import app  # noqa: E402
from golf_analysis.checkpoints import judge as cj  # noqa: E402
from golf_analysis.checkpoints import measure as cm  # noqa: E402
from golf_analysis.checkpoints import vision as cv  # noqa: E402

import synthetic_swing as syn  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
B64 = base64.b64encode(JPEG).decode()
PS = ["P1", "P2", "P3", "P4", "P5", "P6", "P7"]


def swing_in(view="dtl", club_class="iron", swing_id=11, ps=PS, fps=240.0):
    return {"swing_id": swing_id, "view": view, "club": "", "club_class": club_class, "handedness": "R", "fps": fps, "frames": {p: B64 for p in ps}}


class Fake:
    """呼ばれるたびに answer(qset_from_request, n) を返す偽物。依頼の本文も残す。"""

    def __init__(self, answer, model="claude-opus-5-5"):
        self.answer = answer
        self.requests: list[dict] = []
        self.model = model
        self.beta = type("B", (), {})()
        self.beta.messages = type("M", (), {})()
        self.beta.messages.create = self._create

    def _create(self, **kw):
        from types import SimpleNamespace

        self.requests.append(kw)
        ans = self.answer(parse(kw), len(self.requests))
        return SimpleNamespace(model=self.model, stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(ans, ensure_ascii=False))],
                               usage=SimpleNamespace(input_tokens=12000, output_tokens=2000))


def parse(kw) -> dict:
    """依頼の本文から、スイングごとの質問と P を読み戻す（偽物が答えを作るため）。"""
    out: dict = {}
    for b in kw["messages"][0]["content"]:
        if b.get("type") != "text":
            continue
        m = re.match(r"スイング(\d+) (P[0-9.]+)（", b["text"])
        if m:
            out.setdefault(int(m.group(1)), {"ps": [], "qs": []})["ps"].append(m.group(2).replace(".5", "_5"))
        m = re.search(r'<questions swing="(\d+)">\n(.*?)\n</questions>', b["text"], re.S)
        if m:
            out.setdefault(int(m.group(1)), {"ps": [], "qs": []})["qs"] = json.loads(m.group(2))
    return out


def good_answer(req: dict, pick=lambda q: q["options"][0]) -> dict:
    frames, answers = [], []
    for n, s in req.items():
        frames += [{"swing": n, "p": p, "is_phase": "yes", "note": ""} for p in s["ps"]]
        answers += [{"swing": n, "item": q["item"], "option": pick(q)["id"], "visibility": "clear", "visual": "手元と先の重なりを見ました"} for q in s["qs"]]
    return {"frames": frames, "answers": answers, "extra": []}


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-real")
    monkeypatch.delenv("VISION_FAKE_RESPONSE", raising=False)


def run(answer, swings=None, seed=1):
    fake = Fake(answer)
    out = cv.review(lambda: fake, swings or [swing_in()], seed)
    return out, fake


# ------------------------------------------------------------ 渡すもの・渡さないもの

def test_要求の本文に数字の基準や球の話や日付が入っていない():
    out, fake = run(lambda req, n: good_answer(req), swings=[swing_in("dtl"), swing_in("fo", swing_id=12)])
    kw = fake.requests[0]
    text = kw["system"] + "\n".join(b["text"] for b in kw["messages"][0]["content"] if b.get("type") == "text")
    # TrackMan の数字・症状・l1_links・日付
    for w in ("Club Speed", "carry", "club_path", "face_angle", "impact_offset", "attack_angle", "l1_links", "S1", "S3", "S5", "S7", "TrackMan", "キャリー", "打点", "フェース", "パス"):
        assert w not in text, w
    assert not re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}日", text)
    for s in video_candidates.SYMPTOMS:
        assert s["title"] not in text
    # カタログの title・良い側の文・項目 id・範囲の数値・度は渡さない
    asked = {i for s in out["swings"] for i in s["asked"]}
    assert len(asked) >= 10
    for iid in asked:
        it = cp.by_id(iid)
        assert iid not in text
        assert cp.fill(it["ok_text"], "R") not in text
        assert cp.fill(it["title"], "R") not in text
    for q in (q for s in parse(kw).values() for q in s["qs"]):
        assert not re.search(r"[0-9０-９°度]", q["question"] + "".join(o["label"] for o in q["options"]))
    assert "°" not in text and "範囲" not in text
    # 画像は渡すが、返す結果には入れない
    assert any(b.get("type") == "image" for b in kw["messages"][0]["content"])
    assert B64[:40] not in json.dumps(out)


def test_選択肢の順はランダムで元のラベルに戻せる():
    orders = []
    for seed in range(6):
        _, fake = run(lambda req, n: good_answer(req), seed=seed)
        qs = parse(fake.requests[0])[1]["qs"]
        orders.append(tuple(tuple(o["label"] for o in q["options"]) for q in sorted(qs, key=lambda q: q["question"])))
    assert len(set(orders)) > 1  # 並びが毎回同じではない
    # どの並びでも、選んだラベルがそのまま戻る
    target = "深く折れている"
    for seed in range(6):
        out, _ = run(lambda req, n: good_answer(req, lambda q: next((o for o in q["options"] if o["label"] == target), q["options"][0])), swings=[swing_in(club_class="driver")], seed=seed)
        a = out["swings"][0]["answers"]["setup.driver.neck"]
        assert a["option"] == target


# ------------------------------------------------------------ 検証

def _mutate(req, fn):
    ans = good_answer(req)
    fn(ans)
    return ans


def test_知らない項目と選択肢の外と数字の文と定義の外のコマを捨てる():
    def bad(req, n):
        def f(a):
            a["answers"].append({"swing": 1, "item": "q99", "option": "o1", "visibility": "clear", "visual": "見ました"})
            p_of = {q["item"]: q["p"] for q in req[1]["qs"]}
            not_p3 = [x for x in a["answers"] if p_of.get(x["item"]) not in ("P3", None)]
            not_p3[0]["option"] = "o9"
            not_p3[1]["visual"] = "約三十度に見えます 35°"
            a["frames"] = [dict(x, is_phase="no") if x["p"] == "P3" else x for x in a["frames"]]
        return _mutate(req, f)

    out, fake = run(bad)
    assert out["called"] == 2  # 指摘を付けて1回だけ頼み直した
    assert "前の答えには次の問題がありました" in fake.requests[1]["messages"][0]["content"][-1]["text"]
    assert len(out["dropped"]) == 2
    ans = out["swings"][0]["answers"]
    assert out["swings"][0]["reselect"] == ["P3"]
    p3 = [i for i in out["swings"][0]["asked"] if cp.by_id(i)["p"] == "P3"]
    assert p3 and all(ans[i] == {"reselect": "P3"} for i in p3)
    assert all("option" not in v for v in ans.values() if v.get("dropped"))


def test_一回目が落ちて二回目が通れば二回目():
    def ans(req, n):
        a = good_answer(req)
        if n == 1:
            a["answers"][0]["visual"] = "フェースが開いて見えます"
        return a

    out, _ = run(ans)
    assert out["called"] == 2 and not out["dropped"] and out["cacheable"]
    assert out["validation"][0]["problems"] and not out["validation"][1]["problems"]


def test_答えが二回とも使えなければ出さない():
    out, _ = run(lambda req, n: "not json")
    assert out["called"] == 2 and not out["cacheable"]  # 使える答えが一つも無いものは使い回さない（次に押せば頼み直せる）
    assert out["reason"] == "validation" and len(out["dropped"]) == out["n_asked"]


def test_キーが無ければ呼ばずに理由を返す(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    out, fake = run(lambda req, n: good_answer(req))
    assert out["called"] == 0 and not fake.requests and not out["cacheable"] and out["reason"] == "no_key"
    assert "鍵" in out["message"]
    st = TestClient(app).get("/v1/checkpoints/vision/status").json()
    assert st["ready"] is False and st["reason"]


def test_画像の形と数を確かめる():
    with pytest.raises(ValueError):
        cv.decode_swings([dict(swing_in(), frames={"P1": base64.b64encode(b"GIF89a").decode()})])
    with pytest.raises(ValueError):
        cv.decode_swings([swing_in(swing_id=i) for i in range(5)])
    with pytest.raises(ValueError):
        cv.decode_swings([dict(swing_in(), frames={"P1": base64.b64encode(b"\xff\xd8\xff" + b"0" * (2 * 1024 * 1024)).decode()})])


def test_コマが少ないときは細部を判断できないにするよう頼む():
    _, fake = run(lambda req, n: good_answer(req), swings=[swing_in(fps=60)])
    text = "".join(b["text"] for b in fake.requests[0]["messages"][0]["content"] if b.get("type") == "text")
    assert "判断できない" in text and "P6" in text


def test_見た目の文の検査():
    assert cv.text_problems("背中がまっすぐに見えます") == []
    for bad in ("三十度くらい", "約30度", "打点がずれています", "スライスの形", "a" * 5, "あ" * 61):
        assert cv.text_problems(bad), bad


def test_偽物の自動の答えは検証を通る(tmp_path, monkeypatch):
    f = tmp_path / "fake.json"
    f.write_text(json.dumps({"mode": "auto", "pick": "fault", "bad_first": True}), encoding="utf-8")
    monkeypatch.setenv("VISION_FAKE_RESPONSE", str(f))
    r = TestClient(app).post("/v1/checkpoints/vision", json={"swings": [swing_in()], "seed": 3}).json()
    assert r["called"] == 2 and r["cacheable"] and not r["dropped"]
    assert r["usage"]["cost_usd"] > 0 and r["model"]


# ------------------------------------------------------------ 見た目と測った値の合わせ方（§6.6 の表の全マス）

def _item(how: str) -> dict:
    """測れる項目に見た目の質問を足したもの（how: pose / tap）。"""
    base = "iron.p1.dtl.hands" if how == "pose" else "iron.p2.dtl.head_vs_hands"
    it = copy.deepcopy(cp.by_id(base))
    it["vision"] = {"question_id": "q.t", "options": ["中", "外", "判断できない"], "ok_options": ["中"], "fault_of": {"外": it["faults"][0]["id"]}}
    return it


@pytest.mark.parametrize("how", ["pose", "tap"])
@pytest.mark.parametrize("measured", ["in", "out"])
@pytest.mark.parametrize("vision", ["same", "none", "unsure", "opposite"])
def test_合わせ方の表の全マス(monkeypatch, how, measured, vision):
    it = _item(how)
    res = {"res": "in" if measured == "in" else "out_lo", "value": {"v": 1}, "basis": cm.TAP_BASIS if how == "tap" else cm.POSE_BASIS,
           "fault": None if measured == "in" else it["faults"][0]["id"]}
    monkeypatch.setattr(cm, "_measure_item", lambda *a, **k: dict(res))
    ans = {"same": {"option": "中" if measured == "in" else "外", "visibility": "clear"}, "none": None,
           "unsure": {"option": "判断できない", "visibility": "clear"},
           "opposite": {"option": "外" if measured == "in" else "中", "visibility": "clear"}}[vision]
    x = cm.judge_item(cm.Ctx(syn.swing("dtl")), it, {"ok": True}, {"ok": True}, ans)
    want = "in_range" if measured == "in" else "out_range"
    if vision in ("same", "none", "unsure"):
        assert x["state"] == want and x["basis"] == res["basis"]
        assert bool(x.get("vision_agrees")) == (vision == "same")
    elif how == "tap":
        assert x["state"] == want and x.get("conflict") and x["basis"] == cm.TAP_BASIS
    else:
        assert x["state"] == "unknown" and x["reason"] == "conflict" and x["basis"] == "conflict"


@pytest.mark.parametrize("measured", ["border", "invalid"])
@pytest.mark.parametrize("vision", ["in", "out", "none", "unsure", "not_visible"])
def test_境目と無効のときは見た目の状態(monkeypatch, measured, vision):
    it = _item("tap")
    if measured == "border":
        monkeypatch.setattr(cm, "_measure_item", lambda *a, **k: {"res": "border", "value": {"v": 1}, "basis": cm.TAP_BASIS, "fault": None})
    else:
        def raise_(*a, **k):
            raise cm.Invalid("no_tap")
        monkeypatch.setattr(cm, "_measure_item", raise_)
    ans = {"in": {"option": "中", "visibility": "clear"}, "out": {"option": "外", "visibility": "clear"}, "none": None,
           "unsure": {"option": "判断できない", "visibility": "clear"}, "not_visible": {"option": "外", "visibility": "not_visible"}}[vision]
    x = cm.judge_item(cm.Ctx(syn.swing("dtl")), it, {"ok": True}, {"ok": True}, ans)
    if vision in ("in", "out"):
        assert x["state"] == ("in_range" if vision == "in" else "out_range") and x["basis"] == "visual"
    else:
        assert x["state"] == "unknown"
        assert x["reason"] == ("border" if measured == "border" else "no_tap")


def test_見た目だけの項目の答え():
    ctx = cm.Ctx(syn.swing("dtl", club="Driver"))
    it = cp.by_id("setup.driver.neck")
    assert cm.judge_item(ctx, it, {}, {}, {"option": "深く折れている", "visibility": "clear"})["state"] == "out_range"
    assert cm.judge_item(ctx, it, {}, {}, {"option": "なだらか", "visibility": "partial"})["state"] == "in_range"
    assert cm.judge_item(ctx, it, {}, {}, {"option": "判断できない", "visibility": "not_visible"})["reason"] == "vision_unclear"
    assert cm.judge_item(ctx, it, {}, {}, {"dropped": True})["reason"] == "vision_dropped"
    assert cm.judge_item(ctx, it, {}, {}, {"reselect": "P1"})["reason"] == "vision_reselect"
    assert cm.judge_item(ctx, it, {}, {}, None)["reason"] == "vision_pending"


def test_見た目の答えが二本で一致すると課題の候補になる():
    ans = {"iron.p7.fo.hands_ahead": {"option": next(k for k in cp.by_id("iron.p7.fo.hands_ahead")["vision"]["fault_of"]), "visibility": "clear", "visual": "手元が遅れて見えます"}}
    out = []
    for i in range(2):
        r = cm.measure_swing({**syn.swing("fo"), "vision": ans})
        r["swing_id"] = i + 1
        out.append(r)
    a = cj.aggregate(out, symptoms=["S7"])
    x = next(i for i in a["items"] if i["id"] == "iron.p7.fo.hands_ahead")
    assert x["state"] == "out_range" and x["basis"] == "visual" and x["candidate"] and x["linked"]
    assert x["visions"] and x["visions"][0]["visual"] == "手元が遅れて見えます"
    # 一本だけなら候補にしない
    one = cj.aggregate(out[:1])
    assert not next(i for i in one["items"] if i["id"] == "iron.p7.fo.hands_ahead")["candidate"]


# ------------------------------------------------------------ 症状の表（§9.1）

def test_症状の表とl1_linksは両方向で一致する():
    for s in video_candidates.SYMPTOMS:
        assert video_candidates.table_items(s["id"]) == video_candidates.linked_items(s["id"]), s["id"]
        assert len(s["items"]) <= video_candidates.MAX_ITEMS
        for iid in s["items"]:
            assert iid == "setup.ball_pos" or cp.by_id(iid), iid
    used = {x for it in cp.items() for x in it.get("l1_links") or []}
    assert used == {s["id"] for s in video_candidates.SYMPTOMS}


def test_その球の動画が要る症状は対応づけたときだけ印にする():
    found = [{"id": "S1", "seqs": [3, 7]}, {"id": "S2", "value": -0.02, "n": 20}, {"id": "S8"}]
    assert video_candidates.symptoms_for_focus(found) == ["S2"]
    assert video_candidates.symptoms_for_focus(found, {7}) == ["S1", "S2"]
    assert "つながる候補" in video_candidates.LINK_TEXT and "確かめていません" in video_candidates.LINK_TEXT


# ------------------------------------------------------------ ドリル集のチェックポイント版

def test_チェックポイント版のドリルの形():
    assert drills.cp_validate() == []
    ins = drills.cp_for("iron.p2.dtl.head_vs_hands", "inside", "R")
    outs = drills.cp_for("iron.p2.dtl.head_vs_hands", "outside", "L")
    assert [d["id"] for d in ins] == ["cp.p2_head_inside"] and [d["id"] for d in outs] == ["cp.p2_head_outside"]
    assert "左" in ins[0]["what_changes"] and "右" in outs[0]["what_changes"]  # 利き手で差し込む
    assert not ins[0]["checked"]  # 人が確かめるまで「確かめ中」
    assert drills.cp_for("iron.p2.dtl.head_vs_hands", "inside", "R") != drills.cp_for("driver.p2.dtl.head_vs_hands", "outside", "R")


def test_課題の項目にドリルと印の言葉が付く():
    out = []
    for i in range(3):
        r = cm.measure_swing(syn.swing("dtl", faults=("p2_inside",)))
        r["swing_id"] = i + 1
        out.append(r)
    res = TestClient(app).post("/v1/checkpoints/focus", json={"swings": out, "handedness": "R", "symptoms": ["S5"]}).json()
    f = next(i for i in res["items"] if i["id"] == res["focus"])
    assert f["id"] == "iron.p2.dtl.head_vs_hands"
    assert f["drills"] and f["drills"][0]["id"] == "cp.p2_head_inside"
    assert f["linked"] and f["linked_text"] == video_candidates.LINK_TEXT


def test_症状はその日の球から探す():
    shots = []
    for i in range(12):
        shots.append({"id": i + 1, "seq": i + 1, "club": "7i", "club_category": "iron", "excluded": False, "estimated": [], "manual": [],
                      "metrics": {"club_path": 4.0 + (i % 3) * 0.3, "face_angle": 1.0, "attack_angle": -3.0, "impact_offset": -0.001, "carry": 140, "club_speed": 36, "side": 2},
                      "decomposition": {}})
    r = TestClient(app).post("/v1/checkpoints/symptoms", json={"shots": shots, "clubs": ["iron"]}).json()
    assert "S5" in [f["id"] for f in r["found"]] and "S5" in r["for_focus"]
    r2 = TestClient(app).post("/v1/checkpoints/symptoms", json={"shots": shots, "clubs": ["driver"]}).json()
    assert r2["found"] == []
    assert config.MIN_SCOPE_N <= 12
