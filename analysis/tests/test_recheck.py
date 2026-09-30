"""段4 別の日の再確認: 10球テスト・動きのプラン・TrackMan の2日の再確認（docs/DESIGN_v2.md §8・§15 段4）。"""

from __future__ import annotations

import copy
import json
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from golf_analysis import checkup, coaching, compare, focus_test, gist
from golf_analysis.app import app

HERE = os.path.dirname(__file__)
DATA = os.path.join(HERE, "data", "real_2026-09-17_shots.json")


def sw(state, fault=None, basis="measured_tap"):
    return {"state": state, "fault": fault, "basis": basis}


# ---------------------------------------------------------------- 10球テスト

def test_8回で合格_7回でまだ_判定できたのが7回なら判定できない():
    r = focus_test.judge([sw("in_range")] * 8 + [sw("out_range", "inside")] * 2, "inside")
    assert r["passed"] is True and r["in_range"] == 8 and r["judged"] == 10
    r = focus_test.judge([sw("in_range")] * 7 + [sw("out_range", "inside")] * 3, "inside")
    assert r["passed"] is False
    r = focus_test.judge([sw("in_range")] * 7 + [sw("unknown")] * 3, "inside")
    assert r["passed"] is None and r["judged"] == 7 and r["need_more"] == 1 and r["next_hint"] == "reshoot"
    # 分母を正直に書く
    r = focus_test.judge([sw("in_range")] * 7 + [sw("out_range", "inside")] * 2 + [sw("unknown")], "inside")
    assert r["label"] == "9回中7回が範囲の中（判定できなかった1回を除く）"
    assert r["passed"] is False  # 判定できたのは9回（8以上）なので判定でき、範囲の中が7なのでまだ


def test_逆側に外れた回が多いと減らす_同じ側なら大胆に():
    r = focus_test.judge([sw("in_range")] * 5 + [sw("out_range", "outside")] * 4 + [sw("out_range", "inside")], "inside")
    assert r["next_hint"] == "reduce" and "減らし" in r["next_text"]
    r = focus_test.judge([sw("in_range")] * 5 + [sw("out_range", "inside")] * 5, "inside")
    assert r["next_hint"] == "bolder" and "大胆" in r["next_text"]


def test_自己評価から次の手の文が出る():
    ss = [sw("in_range")] * 6 + [sw("out_range", None, "visual")] * 4
    r = focus_test.judge(ss, None, {"count": 8, "feel": "too_much"})
    assert r["next_hint"] == "reduce" and r["self_compare"] == "できたと思ったのは8回、範囲の中は6回"
    r = focus_test.judge(ss, None, {"count": 3, "feel": "too_little"})
    assert r["next_hint"] == "bolder"
    r = focus_test.judge(ss, None, {"feel": "just"})
    assert r["next_hint"] == "keep" and "self_compare" not in r
    # 範囲の外の値は捨てる
    r = focus_test.judge(ss, None, {"count": 11, "feel": "much"})
    assert "self_rating" not in r


def test_見た目の項目の10球テストに札が付く():
    r = focus_test.judge([sw("in_range", basis="visual")] * 10)
    assert r["basis_label"] == "見た目で数えました"
    r = focus_test.judge([sw("in_range")] * 10)
    assert r["basis_label"] == ""


def test_11本は断る():
    with pytest.raises(ValueError):
        focus_test.judge([sw("in_range")] * 11)


def test_次の手の文は数字も専門用語も使わない():
    for t in list(focus_test.NEXT_TEXT.values()) + list(focus_test.FEEL_WORD.values()):
        assert gist.check_plain(t) == [], t


# ---------------------------------------------------------------- 動きのプランの状態

def t(date, passed, block="test"):
    return {"date": date, "passed": passed, "block": block}


def test_その日の6で合格_別の日にもう一度で効いた_1で合格で定着した():
    assert coaching.motion_progress([])["state"] == "checking"
    assert coaching.motion_progress([t("2026-10-01", True)])["state"] == "maybe"
    # 同じ日の2回目では「効いた」にしない
    assert coaching.motion_progress([t("2026-10-01", True), t("2026-10-01", True)])["state"] == "maybe"
    two = [t("2026-10-01", True), t("2026-10-03", True)]
    r = coaching.motion_progress(two)
    assert r["state"] == "worked" and r["head"] == "効いた"
    assert coaching.motion_progress(two + [t("2026-10-05", True, "baseline")])["state"] == "settled"


def test_合格しない理由が分かる():
    r = coaching.motion_progress([t("2026-10-01", None)])
    assert r["state"] == "insufficient" and "八回" in r["text"]
    # 見出しは「足りない」ではなく「撮り直し」（練習量が足りないように読めるため）
    assert r["head"] == r["title"] == "撮り直し"
    # 2・3回目の不合格は感覚の量を変える、4回目（別の日）で効かなかった
    r = coaching.motion_progress([t("2026-10-01", False), t("2026-10-02", False)])
    assert r["state"] == "checking" and r["next_action"] == "adjust_feel"
    r = coaching.motion_progress([t(f"2026-10-0{i}", False) for i in range(1, 5)])
    assert r["state"] == "failed" and {o["action"] for o in r["next_options"]} == {"switch_drill", "video_or_coach"}
    # 同じ日に4回落ちても「効かなかった」にしない
    assert coaching.motion_progress([t("2026-10-01", False)] * 4)["state"] == "checking"
    # ⑥は合格・①は不合格が2回 → 移せていない
    r = coaching.motion_progress([t("2026-10-01", True), t("2026-10-02", False, "baseline"), t("2026-10-03", False, "baseline")])
    assert r["state"] == "not_transferred"


def test_動きのプランの文は数字も専門用語も使わない():
    assert set(coaching.STATE_TEXT_MOTION) <= set(coaching.STATE_TEXT)
    assert "stuck" not in coaching.STATE_TEXT_MOTION and "pending" not in coaching.STATE_TEXT_MOTION
    for k, v in coaching.STATE_TEXT_MOTION.items():
        assert gist.check_plain(v) == [], (k, v)
        assert k == "stop" or v != coaching.STATE_TEXT[k][1]  # 球の文を使い回さない（止めるは見張りの話で同じ）
    for v in coaching.MOTION_NEXT_TEXT.values():
        assert gist.check_plain(v) == []


# ---------------------------------------------------------------- TrackMan の2日の再確認

@pytest.fixture(scope="module")
def nine():
    with open(DATA, encoding="utf-8") as f:
        shots = json.load(f)
    return [s for s in shots if s["club"] == "9 Iron"]


def _copy(shots, times, shift=None, speed=None, n=None):
    out = []
    for k in range(times):
        for s in shots:
            x = copy.deepcopy(s)
            x["id"] = len(out) + 1
            mm = x["metrics"]
            if shift is not None and mm.get("impact_offset") is not None:
                mm["impact_offset"] = mm["impact_offset"] + shift
            if speed is not None and mm.get("club_speed") is not None:
                mm["club_speed"] = mm["club_speed"] + speed
            out.append(x)
    return out[:n] if n else out


def _row(res, metric):
    return next(r for r in res["results"] if r["metric"] == metric)


def test_打点を8mmずらした日は良くなった(nine):
    a = _copy(nine, 3)
    b = _copy(nine, 3, shift=0.008)
    r = checkup.checkup(a, b)
    assert _row(r, "impact_offset")["verdict"] == "better" and _row(r, "impact_offset")["word"] == "良くなった"


def test_コピーした日で15球なら判断できない(nine):
    b = _copy(nine, 2, n=15)
    r = _row(checkup.checkup(nine, b), "impact_offset")
    assert r["verdict"] == "unknown"
    assert compare._diff([s["metrics"]["impact_offset"] for s in nine if s["metrics"].get("impact_offset") is not None],
                         [s["metrics"]["impact_offset"] for s in b if s["metrics"].get("impact_offset") is not None], "impact_offset")["status"] == "same"
    # 見積もり: 良くなったを見分けるのに約15球・変わらないと言うのに約60球（SD 11mm・MMD 4mm）
    nn = checkup.need_n(0.0111, 0.004)
    assert 14 <= nn["better"] <= 16 and 58 <= nn["same"] <= 61


def test_60球の合成の日なら変わらない():
    rng = np.random.default_rng(3)
    vals = list(rng.normal(-0.02, 0.009, 60))

    def day(vs):
        return [{"id": i, "club": "9 Iron", "metrics": {"impact_offset": v, "club_speed": 35.0 + (i % 3) * 0.1, "carry": 120.0 + (i % 5)}} for i, v in enumerate(vs)]

    r = _row(checkup.checkup(day(vals), day(vals)), "impact_offset")
    assert r["verdict"] == "same" and r["word"] == "変わらない"


def test_フェースは判断できない(nine):
    a = _copy(nine, 3)
    b = copy.deepcopy(a)
    for s in b:
        if s["metrics"].get("face_angle") is not None:
            s["metrics"]["face_angle"] -= 5
    r = _row(checkup.checkup(a, b), "face_angle")
    assert r["verdict"] == "unknown" and r["reason"] == "face"


def test_クラブスピードが15落ちた日はキャリーが判断できない(nine):
    a = _copy(nine, 3)
    b = _copy(nine, 3, speed=-1.5)
    r = _row(checkup.checkup(a, b), "carry")
    assert r["verdict"] == "unknown" and r["reason"] == "strength"


def test_プランがあるときは差を計算しない(nine, monkeypatch):
    calls = []
    real = compare._diff

    def spy(a, b, metric):
        calls.append(metric)
        return real(a, b, metric)

    monkeypatch.setattr(compare, "_diff", spy)
    plan = {"target_metric": "impact_offset", "goal": "reduce_abs", "club": "9 Iron"}
    r = checkup.checkup(nine, _copy(nine, 2), plan=plan, plan_eval={"state": "maybe"})
    row = _row(r, "impact_offset")
    assert row["source"] == "plan" and row["word"] == "効いたかも"
    assert "impact_offset" not in calls


def test_既定の項目と診断した日の注記(nine):
    r = checkup.checkup(nine, nine, baseline_is_diagnosis=True)
    assert [i["metric"] for i in r["items"]][:2] == ["club_speed", "carry"]
    assert r["notes"] and "良く出やすい" in r["notes"][0]
    # 最初の面に出す言葉は数字・専門用語を使わない
    for x in r["results"]:
        assert gist.check_plain(x["word"]) == [] and gist.check_plain(x["text"]) == [] and gist.check_plain(x["name"]) == [], x


def test_HTTPの口(nine):
    c = TestClient(app)
    r = c.post("/v1/focus-test/judge", json={"swings": [sw("in_range")] * 8 + [sw("unknown")] * 2})
    assert r.status_code == 200 and r.json()["passed"] is True
    assert c.post("/v1/focus-test/judge", json={"swings": [sw("in_range")] * 11}).status_code == 400
    r = c.post("/v1/plan/motion-progress", json={"tests": [t("2026-10-01", True)]})
    assert r.json()["state"] == "maybe"
    r = c.post("/v1/checkup", json={"a": nine, "b": nine})
    assert r.status_code == 200 and r.json()["items"]
    items = r.json()["items"][:1]
    r = c.post("/v1/checkup", json={"a": nine, "b": nine, "items": items})
    assert len(r.json()["results"]) == 1
