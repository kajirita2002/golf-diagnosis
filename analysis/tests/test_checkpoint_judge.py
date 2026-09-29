"""測る・判定・課題の選び方（docs/DESIGN_v2.md §4.4・§5.4〜§5.6・§6.6・§15 段2a のテスト）。LLM を使わない。"""

from __future__ import annotations

import copy
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from golf_analysis import checkpoints as cp  # noqa: E402
from golf_analysis import config  # noqa: E402
from golf_analysis.app import app  # noqa: E402
from golf_analysis.checkpoints import judge as cj  # noqa: E402
from golf_analysis.checkpoints import measure as cm  # noqa: E402

import synthetic_swing as syn  # noqa: E402

E = config.CP_ANGLE_ERR_DEG


def states(r: dict) -> dict:
    return {x["id"]: x for x in r["items"]}


def agg(swings: list[dict], **kw) -> dict:
    out = []
    for i, sw in enumerate(swings):
        r = cm.measure_swing(sw)
        r["swing_id"] = i + 1
        out.append(r)
    return cj.aggregate(out, **kw)


# ------------------------------------------------------------ 値の幅と範囲

def test_値の幅と範囲で中と外と境目():
    assert cm.judge_value(35, 5, 30, 40) == "in"
    assert cm.judge_value(47, 5, 30, 40) == "out_hi"
    assert cm.judge_value(20, 5, 30, 40) == "out_lo"
    assert cm.judge_value(38, 5, 30, 40) == "border"   # 幅が上の端をまたぐ
    assert cm.judge_value(32, 5, 30, 40) == "border"


def test_端の値():
    assert cm.judge_value(30, 0, 30, 40) == "in"        # 端ちょうどは範囲の中
    assert cm.judge_value(40, 0, 30, 40) == "in"
    assert cm.judge_value(30, 5, 30, 40) == "border"    # 誤差があれば境目
    assert cm.judge_value(44.9, 5, 30, 40) == "border"   # 幅の端が範囲の端をまたぐ
    assert cm.judge_value(45.02, 5, 30, 40) == "out_hi"


def test_片側だけの基準():
    assert cm.judge_value(80, 5, 30, None, "lo_only") == "in"
    assert cm.judge_value(24, 5, 30, None, "lo_only") == "out_lo"
    assert cm.judge_value(32, 5, 30, None, "lo_only") == "border"
    assert cm.judge_value(-40, 5, None, 30, "hi_only") == "in"
    assert cm.judge_value(36, 5, None, 30, "hi_only") == "out_hi"
    with pytest.raises(ValueError):
        cm.judge_value(1, 1, None, None, "both")


# ------------------------------------------------------------ 1スイング

def test_合成の棒人間で測れる項目が出る():
    r = cm.measure_swing(syn.swing("dtl", faults=("p2_inside",)))
    s = states(r)
    assert r["camera"]["ok"] is True and r["scale"]["ok"] is True
    x = s["iron.p2.dtl.head_vs_hands"]
    assert x["state"] == "out_range" and x["fault"] == "inside" and x["basis"] == "measured_tap"
    assert s["iron.p1.dtl.hands"]["state"] == "in_range" and s["iron.p1.dtl.hands"]["basis"] == "measured_approx"
    assert s["iron.p3.dtl.shaft"]["state"] == "in_range"      # 範囲の真ん中（35）は誤差 ±5 でも中
    # 見た目の項目は段2c まで判断できない（見た目の評価はまだ）
    assert s["iron.p2.dtl.knees"]["state"] == "unknown" and s["iron.p2.dtl.knees"]["reason"] == "vision_pending"
    # 撮っていない向き
    assert s["iron.p3.fo.lead_arm"]["reason"] == "view_fo"


def test_物差しが食い違うとボールの項目は測れたにしない():
    sw = syn.swing("dtl", faults=("p2_inside",))
    base = states(cm.measure_swing(sw))
    assert base["iron.p2.dtl.hands"]["basis"] == "measured_approx"
    # ボールを 30% 大きく押した（靴の長さの物差しと食い違う）
    c = sw["ball"][0][0] + (sw["ball"][1][0] - sw["ball"][0][0]) / 2
    sw["ball"] = [[c - syn.BALL_D * 0.65, 640], [c + syn.BALL_D * 0.65, 640]]
    r = cm.measure_swing(sw)
    assert r["scale"]["ok"] is False
    s = states(r)
    assert s["iron.p2.dtl.hands"]["state"] == "unknown" and s["iron.p2.dtl.hands"]["reason"] == "scale"
    # 見た目の答えがあれば、札は「見た目」に下がる
    r2 = cm.measure_swing({**sw, "vision": {"iron.p2.dtl.head_vs_hands": {"option": "手元より内", "visibility": "clear"}}})
    assert states(r2)["iron.p2.dtl.head_vs_hands"]["basis"] == "visual"
    # 20% ちょうどまでは食い違いにしない
    sw["ball"] = [[c - syn.BALL_D * 0.59, 640], [c + syn.BALL_D * 0.59, 640]]
    assert cm.measure_swing(sw)["scale"]["ok"] is True


def test_撮り方の検査に落ちると角度の項目は判断できない():
    sw = syn.swing("dtl")
    for f in sw["frames"].values():
        for lm in f["landmarks"]:
            lm["y"] = min(0.99, lm["y"] + 0.2)   # 体が画面の下にずれた（腰が真ん中の線から離れる）
    r = cm.measure_swing(sw)
    assert r["camera"]["ok"] is False
    assert any("カメラをもう少し" in c["hint"] for c in r["camera"]["checks"] if not c["ok"])
    s = states(r)
    for iid in ("iron.p3.dtl.shaft", "iron.p5.dtl.shaft", "err.early_ext.dtl"):
        assert s[iid]["state"] == "unknown" and s[iid]["reason"] == "camera", iid


def test_基準を確かめ中は判定を返さない():
    r = cm.measure_swing(syn.swing("dtl", ps=syn.P_ORDER))
    s = states(r)
    assert s["iron.p4.dtl.shoulder"]["state"] == "unknown" and s["iron.p4.dtl.shoulder"]["reason"] == "unclear"
    assert s["iron.p4.dtl.shoulder"]["value"] is None


def test_この撮り方で見えない項目は必ず判断できない():
    for view in ("dtl", "fo"):
        s = states(cm.measure_swing(syn.swing(view, ps=syn.P_ORDER)))
        for iid in ("pow.turn", "pow.sequence", "pow.ground", "setup.weight", "setup.grip_pressure"):
            assert s[iid]["state"] == "unknown" and s[iid]["reason"] == "not_in_2d"


def test_タップが無いとクラブの項目は判断できない():
    sw = syn.swing("dtl", faults=("p2_inside",))
    for f in sw["frames"].values():
        f["taps"] = {}
    s = states(cm.measure_swing(sw))
    assert s["iron.p2.dtl.head_vs_hands"]["reason"] == "no_tap"
    assert s["iron.p1.dtl.hands"]["state"] == "in_range"   # 姿勢だけの項目は出る


def test_写っていないコマの項目は判断できない():
    sw = syn.swing("dtl")
    sw["missing"] = ["P3"]
    s = states(cm.measure_swing(sw))
    assert s["iron.p3.dtl.shaft"]["reason"] == "no_frame"
    assert s["iron.p2.dtl.hands"]["state"] == "in_range"   # 評価は止めない


def test_点が見えないと測らない():
    s = states(cm.measure_swing(syn.swing("dtl", vis=0.3)))
    assert s["iron.p1.dtl.hands"]["reason"] == "low_visibility"
    assert s["iron.p2.dtl.head_vs_hands"]["reason"] == "low_visibility"


def test_遅いコマではP5からP7のクラブの項目を判断しない():
    s = states(cm.measure_swing(syn.swing("dtl", fps=30)))
    assert s["iron.p5.dtl.shaft"]["reason"] == "fps"
    assert s["iron.p6.dtl.head_vs_hands"]["reason"] == "fps"
    assert s["iron.p3.dtl.shaft"]["state"] == "in_range"     # P3 は判断する
    assert s["iron.p5.dtl.hands"]["state"] == "in_range"     # 姿勢だけの項目は判断する


def test_左打ちは右打ちと同じ判定():
    for view, f in (("dtl", ("p2_inside",)), ("fo", ("p3_bent",))):
        a = states(cm.measure_swing(syn.swing(view, faults=f)))
        b = states(cm.measure_swing(syn.swing(view, faults=f, hand="L")))
        for k in a:
            assert (a[k]["state"], a[k].get("fault"), a[k].get("reason")) == (b[k]["state"], b[k].get("fault"), b[k].get("reason")), k
    # 画面の文の左右は利き手で入れ替わる
    assert cp.item_view(cp.by_id("iron.p3.fo.lead_arm"), "R")["title"].count("左") == 1
    assert cp.item_view(cp.by_id("iron.p3.fo.lead_arm"), "L")["title"].count("右") == 1


def test_撮り方の直す向きは左打ちでも本物のカメラの向き():
    sw = syn.swing("fo")
    for f in sw["frames"].values():
        for lm in f["landmarks"]:
            lm["x"] = lm["x"] + 0.25   # 体が画面の右へずれた
    right = cm.measure_swing(sw)["camera"]
    swl = syn.swing("fo", hand="L")
    for f in swl["frames"].values():
        for lm in f["landmarks"]:
            lm["x"] = lm["x"] + 0.25
    left = cm.measure_swing(swl)["camera"]
    hint = lambda c: next(x["hint"] for x in c["checks"] if x["id"] == "cam.fo.vertical")
    assert "右へ" in hint(right) and "右へ" in hint(left)


def test_構えの形から向きを確かめる():
    assert cm.measure_swing(syn.swing("dtl"))["view_check"] == {"ok": True, "guess": "dtl", "spread": pytest.approx(0.06, abs=0.05)}
    assert cm.measure_swing(syn.swing("fo"))["view_check"]["ok"] is True
    wrong = syn.swing("fo")
    wrong["view"] = "dtl"
    v = cm.measure_swing(wrong)["view_check"]
    assert v["ok"] is False and v["guess"] == "fo"


def test_基準の無い番手ではPの項目が参考になる():
    s = states(cm.measure_swing(syn.swing("dtl", club="5 Wood", faults=("p2_inside",))))
    x = s["iron.p2.dtl.head_vs_hands"]
    assert x["state"] == "reference" and x["reason"] == "ref_club"
    assert x["value"] is not None       # 値だけは数字を見るの中に出す
    fo = states(cm.measure_swing(syn.swing("fo", club="5w")))
    assert "setup.ball_pos.wood" in fo and fo["setup.ball_pos.wood"]["state"] != "reference"
    assert "setup.ball_pos.mid_iron" not in fo
    ut = states(cm.measure_swing(syn.swing("fo", club="4h")))
    assert ut["setup.ball_pos.hybrid"]["state"] == "reference"
    assert not any(k.startswith("driver.") for k in ut)


def test_番手の分け方():
    assert cm.club_class("7 Iron") == ("iron", 7)
    assert cm.club_class("Driver") == ("driver", None)
    assert cm.club_class("1W") == ("driver", None)
    assert cm.club_class("5w") == ("wood", 5)
    assert cm.club_class("4 Hybrid") == ("hybrid", 4)
    assert cm.club_class("SW") == ("wedge", None)
    assert cm.club_class("PW") == ("iron", None)
    fo = states(cm.measure_swing(syn.swing("fo", club="9 Iron")))
    assert "setup.ball_pos.short_iron" in fo and "setup.ball_pos.mid_iron" not in fo
    fo = states(cm.measure_swing(syn.swing("fo", club="PW")))
    assert "setup.ball_pos.short_iron" in fo


def test_まとめる項目():
    s = states(cm.measure_swing(syn.swing("fo", faults=("p3_bent",))))
    assert s["pow.width"]["state"] == "out_range"
    assert "iron.p3.fo.lead_arm" in s["pow.width"]["derived_from"]


# ------------------------------------------------------------ 見た目と測った値の合わせ方（§6.6 の表）

def _one(item_id: str, sw: dict, ans: dict | None):
    return states(cm.measure_swing({**sw, "vision": {item_id: ans} if ans else {}}))[item_id]


def test_合わせ方の表():
    sw = syn.swing("dtl", faults=("p2_inside",))
    iid = "iron.p2.dtl.head_vs_hands"  # タップ
    # 測った結果・見た目が同じ向き
    x = _one(iid, sw, {"option": "手元より内", "visibility": "clear"})
    assert x["state"] == "out_range" and x["basis"] == "measured_tap" and x.get("vision_agrees")
    # 見た目が無い・判断できない
    assert _one(iid, sw, None)["state"] == "out_range"
    assert _one(iid, sw, {"option": "判断できない", "visibility": "clear"})["state"] == "out_range"
    # タップと見た目が逆 → タップを採り、食い違いを残す
    x = _one(iid, sw, {"option": "重なる", "visibility": "clear"})
    assert x["state"] == "out_range" and x.get("conflict")
    # 姿勢だけの測った値と見た目が逆 → 判断できない（食い違い）
    pose = copy.deepcopy(cp.by_id("iron.p2.dtl.head_vs_hands"))
    ctx = cm.Ctx(sw)
    it = copy.deepcopy(cp.by_id("iron.p1.dtl.hands"))
    it["vision"] = {"question_id": "q.t", "options": ["中", "外", "判断できない"], "ok_options": ["中"], "fault_of": {"外": "in"}}
    x = cm.judge_item(ctx, it, {"ok": True}, {"ok": True}, {"option": "外", "visibility": "clear"})
    assert x["state"] == "unknown" and x["reason"] == "conflict"
    # 境目・無効で見た目の答えがある → 見た目の状態
    it3 = copy.deepcopy(cp.by_id("iron.p5.dtl.shaft"))
    it3["vision"] = {"question_id": "q.t", "options": ["ちょうど", "立つ", "判断できない"], "ok_options": ["ちょうど"], "fault_of": {"立つ": "steep"}}
    sw2 = copy.deepcopy(sw)
    sw2["frames"]["P5"]["taps"] = {}
    x = cm.judge_item(cm.Ctx(sw2), it3, {"ok": True}, {"ok": True}, {"option": "立つ", "visibility": "clear"})
    assert x["state"] == "out_range" and x["basis"] == "visual" and x["fault"] == "steep"
    # 境目・無効で見た目も無い → 判断できない
    x = cm.judge_item(cm.Ctx(sw2), it3, {"ok": True}, {"ok": True}, None)
    assert x["state"] == "unknown"
    # 見えない（not_visible）は答えなしと同じ
    x = cm.judge_item(cm.Ctx(sw2), it3, {"ok": True}, {"ok": True}, {"option": "立つ", "visibility": "not_visible"})
    assert x["state"] == "unknown"
    del pose


# ------------------------------------------------------------ 何本も・課題

def test_一本だけの項目は課題にならない():
    a = agg([syn.swing("dtl", faults=("p2_inside",))])
    x = next(i for i in a["items"] if i["id"] == "iron.p2.dtl.head_vs_hands")
    assert x["state"] == "out_range" and x["single"] and not x["candidate"]
    assert a["focus"] is None


def test_多数で状態を決め課題を選ぶ():
    sws = [syn.swing("dtl", faults=("p2_inside",)) for _ in range(3)] + [syn.swing("dtl")]
    a = agg(sws)
    x = next(i for i in a["items"] if i["id"] == "iron.p2.dtl.head_vs_hands")
    assert x["state"] == "out_range" and x["n_out"] == 3 and x["n_judged"] == 4 and x["candidate"]
    assert a["focus"] == "iron.p2.dtl.head_vs_hands"
    assert a["rationale"].startswith("スイングの前半ほど")
    # 同数は食い違い（判断できない）
    b = agg([syn.swing("dtl", faults=("p2_inside",)), syn.swing("dtl")])
    y = next(i for i in b["items"] if i["id"] == "iron.p2.dtl.head_vs_hands")
    assert y["state"] == "unknown" and y["reason"] == "split"


def test_課題の順番():
    def c(iid, rank, group="position", basis="measured_approx", n_out=3, tags=()):
        return {"id": iid, "domino_rank": rank, "group": group, "basis": basis, "n_out": n_out, "tags": list(tags)}
    # ドミノの順（前半が先）
    f, n, _ = cj.pick_focus([c("b", 4), c("a", 2)])
    assert (f["id"], n["id"]) == ("a", "b")
    # 同じ段ではセットアップが先
    f, _, _ = cj.pick_focus([c("pos", 1), c("set", 1, group="setup")])
    assert f["id"] == "set"
    # 同じ段では手の通り道が先
    f, _, _ = cj.pick_focus([c("pos", 5, basis="measured_tap"), c("path", 5, group="path", basis="visual")])
    assert f["id"] == "path"
    # 同じ段では測れたが見た目より先
    f, _, _ = cj.pick_focus([c("vis", 3, basis="visual", n_out=9), c("mes", 3, basis="measured_tap", n_out=3)])
    assert f["id"] == "mes"
    # それでも並べば範囲の外の回数が多いもの
    f, _, _ = cj.pick_focus([c("x", 3, n_out=3), c("y", 3, n_out=5)])
    assert f["id"] == "y"
    # 飛距離を優先したときだけ distance を先頭へ（その中はドミノ順）
    f, _, why = cj.pick_focus([c("p2", 2), c("p6d", 6, tags=("distance",)), c("p4d", 4, tags=("distance",))], distance_first=True)
    assert f["id"] == "p4d" and "飛距離" in why
    f, _, _ = cj.pick_focus([c("p2", 2), c("p4d", 4, tags=("distance",))])
    assert f["id"] == "p2"


def test_球の症状とつながる印では順番が変わらない():
    sws = [syn.swing("dtl", faults=("p2_inside", "p7_rise")) for _ in range(3)]
    a = agg(sws)
    b = agg(sws, symptoms=["S1"])   # S1 は P7 の手の高さとつながる
    assert a["focus"] == b["focus"] == "iron.p2.dtl.head_vs_hands"
    x = next(i for i in b["items"] if i["id"] == "iron.p7.dtl.hand_height")
    assert x["linked"] and x["candidate"]
    assert b["next"] == a["next"]


def test_参考と見た目の項目は課題にならない():
    sws = [syn.swing("dtl", club="5 Wood", faults=("p2_inside",)) for _ in range(3)]
    a = agg(sws)
    assert a["focus"] is None


def test_件数は判断できないを分母に入れない():
    a = agg([syn.swing("dtl", faults=("p2_inside",)) for _ in range(3)])
    c = a["counts"]
    assert c["judged"] == c["in_range"] + c["out_range"]
    assert c["unknown"] > 0 and c["judged"] > 0
    assert a["unchecked"] == len(a["items"])   # 初版は全部、人が突き合わせていない
    reasons = {r["reason"] for r in a["unknown_reasons"]}
    assert "view_fo" in reasons and "vision_pending" in reasons


# ------------------------------------------------------------ 口

def test_口():
    c = TestClient(app)
    r = c.get("/v1/checkpoints?handedness=R").json()
    assert r["version"] == "checkpoints/1.0-pgag" and r["etag"] and "p1.dtl" in r["svg"]
    assert "{lead}" not in r["items"][0]["title"]
    raw = c.get("/v1/checkpoints").json()
    assert any("{lead}" in it["title"] for it in raw["items"])
    m = c.post("/v1/checkpoints/measure", json={"swing": syn.swing("dtl", faults=("p2_inside",))}).json()
    assert m["catalog_version"] == "checkpoints/1.0-pgag" and m["judge_version"] == "judge/1.0"
    m["swing_id"] = 9
    f = c.post("/v1/checkpoints/focus", json={"swings": [m, {**m, "swing_id": 10}, {**m, "swing_id": 11}], "handedness": "R"}).json()
    assert f["focus"] == "iron.p2.dtl.head_vs_hands"
    x = next(i for i in f["items"] if i["id"] == f["focus"])
    assert x["fault_label"] == "クラブが体の内側に引かれています"
    assert c.post("/v1/checkpoints/measure", json={"swing": {"view": "side", "frames": {}}}).status_code == 400
    assert c.get("/healthz").json()["checkpoints_version"] == "checkpoints/1.0-pgag"
