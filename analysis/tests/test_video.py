"""動画から P を自動で取り出す（docs/DESIGN_v2.md §6.3・§6.4・§15 段2b のテスト）。LLM を使わない。

合成した手首の軌跡（tests/synthetic_swing.py の motion。本物の動画・人は使わない）で、P1〜P10・中間・t₀ を固定する。
本当の時刻は、§6.4 の規則がちょうど当たるように置いた棒人間の時刻。コマの間隔の分だけずれてよい（許すのは2コマ）。
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from golf_analysis import config, video  # noqa: E402
from golf_analysis.app import app  # noqa: E402
from golf_analysis.checkpoints import judge as cj  # noqa: E402
from golf_analysis.checkpoints import measure as cm  # noqa: E402

import synthetic_swing as syn  # noqa: E402

MAIN = ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9")


def ps_of(sw: dict) -> dict:
    return {p["p"]: p for p in sw["ps"]}


def near(sw: dict, truth: dict, fps: float, names=MAIN, frames: float = 2.0) -> None:
    got = ps_of(sw)
    # 許すのは2コマか、手をなめらかにする窓の半分（折れ目の近くでは、なめらかにしたぶん少しずれる）の大きいほう
    tol = max(frames / fps, config.VIDEO_SMOOTH_S) + 1e-6
    for p in names:
        assert got[p]["t"] is not None, (p, got[p])
        # P1 は「手がまだ構えの位置にある最後のコマ」なので、動き始めの少し後ろになる（胴の長さの 0.04 ぶん）
        extra = 0.03 if p == "P1" else 0.0
        assert abs(got[p]["t"] - truth[p]) <= tol + extra, (p, got[p]["t"], truth[p])


@pytest.mark.parametrize("view", ["dtl", "fo"])
@pytest.mark.parametrize("fps", [60.0, 240.0, 30.0])
def test_合成の軌跡でPと始まりが当たる(view, fps):
    body, truth = syn.motion(view, fps=fps)
    r = video.detect(body)
    assert r["video_version"] == "video/0.2"
    assert len(r["swings"]) == 1 and not r["excluded"]
    sw = r["swings"][0]
    near(sw, truth[0], fps)
    got = ps_of(sw)
    # P10 は手の速さが落ちて止まった最初のコマ（棒人間は最後の形の少し前でゆっくりになる）
    assert got["P10"]["t"] is not None and abs(got["P10"]["t"] - truth[0]["P10"]) <= 0.15
    # t₀ は P1 のあと・P2 の前
    assert got["P1"]["t"] < sw["t0"] < got["P2"]["t"]
    # 中間は両端の真ん中のコマ（目安）
    for mid, a, b in (("P5_5", "P5", "P6"), ("P6_5", "P6", "P7")):
        assert got[mid]["status"] == "estimated" and got[mid]["method"] == "midpoint"
        assert abs(got[mid]["t"] - (got[a]["t"] + got[b]["t"]) / 2) <= 1 / fps + 1e-6
    # 順番どおり
    ts = [got[p]["t"] for p in ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10")]
    assert ts == sorted(ts)
    assert sw["check"] == [] and sw["warnings"] == []
    # 後ろからの P2・P6 は代わりの規則なので目安。P7 はボールのまわりの変化で挟む
    assert got["P2"]["status"] == "estimated" and got["P6"]["status"] == "estimated"
    assert got["P7"]["method"] == "ball_roi" and got["P7"]["status"] == "ok"
    # 後ろからの P3 は腕が胴に隠れるので目安、正面は腕が水平で決める
    assert got["P3"]["status"] == ("estimated" if view == "dtl" else "ok")


def test_何本もあるスイングを順に取り出す():
    body, truth = syn.motion("dtl", n_swings=3)
    r = video.detect(body)
    assert [s["index"] for s in r["swings"]] == [1, 2, 3]
    for sw, t in zip(r["swings"], truth):
        near(sw, t, 60.0)
        w0, w1 = sw["window"]
        assert w0 <= t["P1"] and w1 >= t["P9"]


def test_ワッグルはスイングにしない():
    body, truth = syn.motion("dtl", waggle=True)
    r = video.detect(body)
    assert len(r["swings"]) == 1
    near(r["swings"][0], truth[0], 60.0)
    # 構えはワッグルのあとの静かな構え
    assert ps_of(r["swings"][0])["P1"]["t"] > 1.0


def test_素振りはボールが動かないので外す():
    body, truth = syn.motion("dtl", n_swings=3, practice=(2,))
    r = video.detect(body)
    assert [s["index"] for s in r["swings"]] == [1, 2]
    assert len(r["excluded"]) == 1 and r["excluded"][0]["kind"] == "practice"
    ex = ps_of(r["excluded"][0])
    assert abs(ex["P4"]["t"] - truth[1]["P4"]) <= 2 / 60
    assert ex["P7"]["reason"] == "no_ball_change"
    # 本当のスイングは1本目と3本目
    assert abs(ps_of(r["swings"][1])["P4"]["t"] - truth[2]["P4"]) <= 2 / 60


def test_ボールのまわりが無ければ素振りを外さずP7は目安():
    body, truth = syn.motion("dtl", n_swings=2, practice=(2,), roi=False)
    r = video.detect(body)
    assert len(r["swings"]) == 2 and not r["excluded"]
    p7 = ps_of(r["swings"][0])["P7"]
    assert p7["status"] == "estimated" and p7["method"] == "rule"
    assert abs(p7["t"] - truth[0]["P7"]) <= 2 / 60


def test_左打ちは右打ちと同じ時刻():
    for view in ("dtl", "fo"):
        body_r, truth = syn.motion(view)
        body_l, _ = syn.motion(view, hand="L")
        r, l = video.detect(body_r), video.detect(body_l)
        assert len(l["swings"]) == 1
        pr, pl = ps_of(r["swings"][0]), ps_of(l["swings"][0])
        for p in video.PS_ALL:
            assert pr[p]["t"] == pl[p]["t"], (view, p)
            assert pr[p]["status"] == pl[p]["status"]


def test_短い点の欠けはつないで確かめるに回す():
    body, truth = syn.motion("dtl", gaps=((1.5, 0.08),))
    sw = video.detect(body)["swings"][0]
    near(sw, truth[0], 60.0)
    assert "P3" in sw["check"]
    assert ps_of(sw)["P3"]["confidence"] == "low"


def test_長い点の欠けの中のPは見つからないにして止めない():
    body, truth = syn.motion("dtl", gaps=((1.9, 0.3),))
    sw = video.detect(body)["swings"][0]
    got = ps_of(sw)
    assert got["P5"]["status"] == "failed" and got["P5"]["reason"] == "low_visibility"
    assert got["P6"]["status"] == "failed"
    # 欠けの前の P は当たる。P7 はボールのまわりの変化で決まる（体の点が無いので確かめる）
    near(sw, truth[0], 60.0, names=("P1", "P2", "P3", "P4"))
    assert abs(got["P7"]["t"] - truth[0]["P7"]) <= 2 / 60 and got["P7"]["confidence"] == "low"
    assert {"P5", "P6", "P7"} <= set(sw["check"])
    # 振り終わって手を下ろしたコマを、下ろしの P として拾わない
    assert all(got[p]["t"] is None or got[p]["t"] < truth[0]["P10"] for p in ("P5", "P6"))


def test_順番の違反は見つからない():
    body, truth = syn.motion("dtl")
    s = video.Series(body)
    sws = video.find_swings(s)
    one = video.detect_one(s, sws[0])
    assert all(p["status"] != "failed" for p in one["ps"])
    # P5 の規則を壊して、トップより前のコマを返すようにする → P5 は見つからない
    orig = video._first

    def broken(ss, a, b, cond):
        i = orig(ss, a, b, cond)
        if i is not None and cond is not None and getattr(broken, "hit", 0) == 3:
            broken.hit += 1
            return 0
        broken.hit = getattr(broken, "hit", 0) + 1
        return i

    broken.hit = 0
    video._first = broken
    try:
        bad = video.detect_one(s, sws[0])
    finally:
        video._first = orig
    failed = [p for p in bad["ps"] if p["status"] == "failed" and p["reason"] == "order"]
    assert failed and "order" in bad["warnings"]


def test_時間がふつうと違えば警告して確かめる():
    body, truth = syn.motion("dtl")
    # 時刻を全部2倍に伸ばす（上げが約1.5秒・下ろしが約0.64秒）
    for f in body["frames"]:
        f["t"] = round(f["t"] * 2.0, 5)
    body["fps"] = 30.0
    sw = video.detect(body)["swings"][0]
    assert "down_time" in sw["warnings"]
    assert "P7" in sw["check"]


def test_正面でクラブをタップするとP2とP6が寄せ直される():
    body, truth = syn.motion("fo")
    base = ps_of(video.detect(body)["swings"][0])
    fr = body["frames"]
    taps = []
    for p in ("P2", "P6"):
        i = base[p]["frame"]
        # 前後のコマにタップ: 2コマ前は水平から20・1コマ後は3（一番水平）・3コマ後は10・6コマ後（範囲の外）は0
        for k, dy in ((-2, 36), (1, 5), (3, 18), (6, 0)):
            taps.append({"t": fr[i + k]["t"], "grip": [600, 400], "head": [500, 400 - dy]})
    body["club_taps"] = taps
    got = ps_of(video.detect(body)["swings"][0])
    for p in ("P2", "P6"):
        assert got[p]["method"] == "club_tap" and got[p]["status"] == "ok"
        assert got[p]["frame"] == base[p]["frame"] + 1
    # 後ろからは寄せ直さない（シャフトがカメラを向いて短く写る）
    body_d, _ = syn.motion("dtl")
    base_d = ps_of(video.detect(body_d)["swings"][0])
    body_d["club_taps"] = [{"t": body_d["frames"][base_d["P2"]["frame"] + 1]["t"], "grip": [600, 400], "head": [500, 400]}]
    assert ps_of(video.detect(body_d)["swings"][0])["P2"]["method"] == "rule"


def test_テンポの幅がガイドの目安をまたぐと言葉にしない():
    # 60fps・上げ 48コマ・下ろし 16.6コマ（比 約2.89）。±1コマで 2.8 と 3.0 の両方を含む
    tp = video.tempo(0.0, 48 / 60, 48 / 60 + 16.6 / 60, 1 / 60)
    assert tp["lo"] < 2.8 and tp["hi"] > 3.0
    for cls in ("iron", "driver"):
        assert video.tempo_word(tp, cls)[0] is None
    # 240fps で比 2.0（アイアンの目安 2.8 − 余白 0.3 より丸ごと下）→「ゆっくりめ」
    tp2 = video.tempo(0.0, 0.6, 0.9, 1 / 240)
    assert video.tempo_word(tp2, "iron")[0] == "slow"
    # 比 3.6（上げに比べて下ろしが短い）→「急ぎ気味」
    tp3 = video.tempo(0.0, 0.9, 1.15, 1 / 240)
    assert video.tempo_word(tp3, "iron")[0] == "fast"
    # 目安の無い番手（ウッド・ユーティリティ）は言葉にしない
    assert video.tempo_word(tp3, "wood")[0] is None


def test_時系列の項目が測れる():
    body, _ = syn.motion("dtl")
    det = video.detect(body)["swings"][0]
    r = {x["id"]: x for x in cm.measure_swing(syn.measure_input(body, det))["items"]}
    loop = r["path.loop"]
    assert loop["state"] == "in_range" and loop["basis"] == "measured_approx", loop
    assert loop["value"]["v"] > 0 and loop["value"]["side"] == "lo_only"
    tempo = r["tempo.ratio"]
    assert tempo["state"] == "reference" and tempo["value"]["v"] > 1
    # 上から下りる手（「高い所からそのまま下りる」）
    body2, _ = syn.motion("dtl", faults=("loop_over",))
    det2 = video.detect(body2)["swings"][0]
    r2 = {x["id"]: x for x in cm.measure_swing(syn.measure_input(body2, det2))["items"]}
    assert r2["path.loop"]["state"] == "out_range" and r2["path.loop"]["fault"] == "over"


def test_骨盤と胸の戻しは正面の時系列で測る():
    body, _ = syn.motion("fo")
    det = video.detect(body)["swings"][0]
    r = {x["id"]: x for x in cm.measure_swing(syn.measure_input(body, det))["items"]}
    assert r["pow.recenter"]["state"] == "in_range", r["pow.recenter"]
    body2, _ = syn.motion("fo", faults=("recenter_stay",))
    det2 = video.detect(body2)["swings"][0]
    r2 = {x["id"]: x for x in cm.measure_swing(syn.measure_input(body2, det2))["items"]}
    assert r2["pow.recenter"]["state"] == "out_range" and r2["pow.recenter"]["fault"] == "stay"
    # 左打ちも同じ
    body3, _ = syn.motion("fo", hand="L", faults=("recenter_stay",))
    det3 = video.detect(body3)["swings"][0]
    r3 = {x["id"]: x for x in cm.measure_swing(syn.measure_input(body3, det3))["items"]}
    assert r3["pow.recenter"]["state"] == "out_range"


def test_時系列が無ければ時系列の項目は判断できない():
    sw = syn.swing("dtl")
    r = {x["id"]: x for x in cm.measure_swing(sw)["items"]}
    assert r["path.loop"]["state"] == "unknown" and r["path.loop"]["reason"] == "no_series"
    assert r["tempo.ratio"]["state"] == "reference" and r["tempo.ratio"]["value"] is None
    swf = syn.swing("fo")
    rf = {x["id"]: x for x in cm.measure_swing(swf)["items"]}
    assert rf["pow.recenter"]["state"] == "unknown" and rf["pow.recenter"]["reason"] == "no_series"


def test_テンポは参考で課題にならない():
    body, _ = syn.motion("dtl", n_swings=3)
    r = video.detect(body)
    sws = []
    for i, det in enumerate(r["swings"]):
        m = cm.measure_swing(syn.measure_input(body, det))
        sws.append({"swing_id": i + 1, "view": "dtl", "items": m["items"]})
    a = cj.aggregate(sws)
    by = {x["id"]: x for x in a["items"]}
    assert by["tempo.ratio"]["state"] == "reference" and not by["tempo.ratio"]["candidate"]
    # 手の通り道の輪は3本とも測れて範囲の中
    assert by["path.loop"]["state"] == "in_range" and by["path.loop"]["n_judged"] == 3


def test_口から呼べる():
    c = TestClient(app)
    body, truth = syn.motion("fo")
    r = c.post("/v1/video/checkpoints", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["swings"][0]["series"]["t0"] == j["swings"][0]["t0"]
    assert c.post("/v1/video/checkpoints", json={**body, "view": "side"}).status_code == 400
    assert c.post("/v1/video/checkpoints", json={**body, "roi": [0.0]}).status_code == 400
    assert c.get("/healthz").json()["video_version"] == config.VIDEO_VERSION


def test_体の点が無ければスイングを探さない():
    r = video.detect({"view": "dtl", "handedness": "R", "fps": 60, "width": 1280, "height": 720, "frames": [{"t": 0.0, "lm": None}]})
    assert r["swings"] == [] and r["reason"]


# ------------------------------------------------------------ 段2b のレビューで見つかったこと


def test_新しいボールが丸から横に置かれても本物のスイングを素振りとして外さない():
    # 練習場では、2本目からのボールがタップした丸から少し横に置かれることが多い。丸の中は打っても変わらない
    body, truth = syn.motion("dtl", n_swings=3, ball_away=(2, 3))
    r = video.detect(body)
    assert len(r["swings"]) == 3 and not r["excluded"]
    assert [s["practice_known"] for s in r["swings"]] == [True, False, False]
    # 丸の中が最初のボールと似ているときだけ、素振りとして外す（前のテストの形）
    body2, _ = syn.motion("dtl", n_swings=3, practice=(2,))
    assert len(video.detect(body2)["excluded"]) == 1
    # ボールの絵の印が無ければ、丸の中が変わらなくても外さない
    body3, _ = syn.motion("dtl", n_swings=3, practice=(2,))
    del body3["ball_seen"]
    r3 = video.detect(body3)
    assert len(r3["swings"]) == 3 and not r3["excluded"]


def test_全部が素振りに見えたら外さずに返す():
    # 丸がずれていた・変わり方が小さい: 1本だけの動画で外すと先へ進めない（行き止まり）
    body, _ = syn.motion("dtl", n_swings=1, practice=(1,))
    r = video.detect(body)
    assert len(r["swings"]) == 1 and not r["excluded"]
    sw = r["swings"][0]
    assert sw["kind"] == "swing" and not sw["practice_known"] and "practice_unsure" in sw["warnings"]
    assert ps_of(sw)["P7"]["reason"] != "no_ball_change"


def test_点の値が数でなければそのコマの欠けにする():
    body, _ = syn.motion("dtl")
    body["frames"][10]["lm"][15][0] = "x"
    body["frames"][11]["lm"][16][1] = {"a": 1}
    r = video.detect(body)
    assert len(r["swings"]) == 1
    c = TestClient(app)
    assert c.post("/v1/video/checkpoints", json=body).status_code == 200


def test_細かく取る区間はP1の少し前から():
    # 構えが長い（3秒）動画でも、細かく取る区間の頭は P1 の 0.5秒前まで
    body, truth = syn.motion("dtl", lead_in=3.0)
    sw = video.detect(body)["swings"][0]
    assert sw["window"][0] >= ps_of(sw)["P1"]["t"] - config.VIDEO_HEAD_S - 1e-6


def test_正面の斜めのタップ一つでP2をokにしない():
    body, _ = syn.motion("fo")
    base = ps_of(video.detect(body)["swings"][0])
    # 45°のタップが P2 と同じ時刻に一つ
    body["club_taps"] = [{"t": base["P2"]["t"], "grip": [600, 370], "head": [500, 270]}]
    assert ps_of(video.detect(body)["swings"][0])["P2"]["status"] == "estimated"
    # 水平のタップでも一つだけなら目安のまま（前後がそろわないと「一番水平に近い」と言えない）
    body["club_taps"] = [{"t": base["P2"]["t"], "grip": [600, 400], "head": [500, 400]}]
    assert ps_of(video.detect(body)["swings"][0])["P2"]["status"] == "estimated"
    # 前後がそろっても、一番水平に近いものが斜め（30°）なら寄せ直さない
    fr = body["frames"]
    i = base["P2"]["frame"]
    body["club_taps"] = [{"t": fr[i + k]["t"], "grip": [600, 400], "head": [500, 400 - dy]} for k, dy in ((-1, 90), (0, 58), (1, 80))]
    assert ps_of(video.detect(body)["swings"][0])["P2"]["method"] == "rule"


def test_テンポの近くは幅が丸ごと目安の近くにあるときだけ():
    # 30fps の比 4.0 は幅が 2.95〜5.82（目安をまたぐ）→ 言葉も「近く」も出さない
    tp = video.tempo(0.0, 1.0, 1.25, 1 / 30)
    word, _, near_ = video.tempo_word(tp, "iron")
    assert word is None and not near_
    # 60fps の比 2.2（合成のスイング）も幅が 1.91〜2.56 で目安 2.8±0.3 をまたがない・丸ごと入らない → 近くと言わない
    tp2 = video.tempo(0.0, 0.7333, 0.7333 + 0.3333, 1 / 60)
    assert video.tempo_word(tp2, "iron")[2] is False
    # 240fps で比 2.8 → 幅が丸ごと 2.5〜3.1 に入るので近くと言える
    tp3 = video.tempo(0.0, 0.84, 1.14, 1 / 240)
    assert video.tempo_word(tp3, "iron") == (None, 2.8, True)
    # 実際の間隔が 1/120（240fps を間引いて取った）なら幅が広がり、言い切れなくなる
    tp4 = video.tempo(0.0, 0.84, 1.14, (1 / 120, 1 / 120, 1 / 120))
    assert tp4["hi"] - tp4["lo"] > tp3["hi"] - tp3["lo"]


def test_コマの少ない動画はテンポを判断しない():
    body, _ = syn.motion("dtl", fps=30)
    det = video.detect(body)["swings"][0]
    r = {x["id"]: x for x in cm.measure_swing(syn.measure_input(body, det))["items"]}
    t = r["tempo.ratio"]
    assert t["state"] == "reference" and t.get("value") is None and t["ref_reason"] == "fps"


def test_テンポの幅は実際に取ったコマの間隔で作る():
    # 240fps の動画を 120Hz で取った時系列: 幅は 1/120 で作る（1/240 で作ると半分になり、言葉が出やすい）
    body, _ = syn.motion("dtl", fps=240)
    body["frames"] = body["frames"][::2]
    body["roi"] = body["roi"][::2]
    body["fps"] = 240
    det = video.detect(body)["swings"][0]
    r = {x["id"]: x for x in cm.measure_swing(syn.measure_input(body, det))["items"]}
    v = r["tempo.ratio"]["value"]
    assert all(abs(d - 1000 / 120) < 0.6 for d in v["dt_ms"]), v["dt_ms"]


def test_テンポの言葉は中立で一本だけなら一本だけの見立て():
    import json
    cat = json.load(open(os.path.join(os.path.dirname(cm.__file__), "catalog.json")))
    items = cat["items"] if isinstance(cat, dict) else cat
    it = next(x for x in items if x["id"] == "tempo.ratio")
    labels = [f["label"] for f in it["faults"]]
    assert all("上げに比べて下ろしが" in x for x in labels), labels
    body, _ = syn.motion("dtl", n_swings=1)
    det = video.detect(body)["swings"][0]
    m = cm.measure_swing(syn.measure_input(body, det))
    by = {x["id"]: x for x in cj.aggregate([{"swing_id": 1, "view": "dtl", "items": m["items"]}])["items"]}
    assert by["tempo.ratio"]["single"] and by["tempo.ratio"]["n_ref"] == 1
    assert by["tempo.ratio"]["ref_near"] is False  # 比 2.2 の幅は目安の近くに丸ごとは入らない


def test_組に分けて送るときは全部素振りでも外したまま返す():
    body, _ = syn.motion("dtl", n_swings=1, practice=(1,))
    body["practice_fallback"] = False
    r = video.detect(body)
    assert not r["swings"] and len(r["excluded"]) == 1
    c = TestClient(app)
    j = c.post("/v1/video/checkpoints", json=body).json()
    assert not j["swings"] and len(j["excluded"]) == 1
