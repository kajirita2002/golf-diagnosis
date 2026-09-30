"""アクションプラン（Phase 1b）の分析サービス側のテスト。docs/DESIGN_coaching.md §5.7・§8・§11。

- ドリル集の形（checked_by が空のものは出さない）
- ブロックの球数の式（打点 SD 11.4mm・MMD 4mm → 条件ごと16球）
- 準備とドリルの球が評価にも analyze_session にも入らない
- A-B-B-A の同じ種類のブロックはまとめて評価される
- 1回目の A を物差しにする（段の目標・定着）
- 状態の移り方（効いた＝別の日に2回続けて moderate 以上 …）
- 「効果なし＋練習の中で 4mm 良くなる」で2回の練習を評価しても、A-B-B-A なら「効いた」にならない（種を固定）
- params は作った時点で固定（あとから閾値を変えてもプランの判定は動かない）
"""

import copy
import importlib.util
import json
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from golf_analysis import coaching, config, drills, plan
from golf_analysis.app import app
from golf_analysis.report import build_report
from golf_analysis.session import analyze_session

HERE = os.path.dirname(__file__)
DATA = os.path.join(HERE, "data", "real_2026-09-17_shots.json")
SIM_PATH = os.path.join(HERE, "..", "..", "scripts", "sim_block_order.py")


def _sim():
    spec = importlib.util.spec_from_file_location("sim_block_order", SIM_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sim = _sim()


@pytest.fixture(scope="module")
def shots():
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def heel_plan(shots):
    return coaching.build_plan(shots, "R", "group:iron", "strike_heel")["plan"]


@pytest.fixture(scope="module")
def face_plan(shots):
    return coaching.build_plan(shots, "R", "group:iron", "face", window={"face_min": -1.86, "face_max": 2.11, "path": 0.4})["plan"]


def _checked(monkeypatch, ids=("strike.two_balls",)):
    """人が確かめたことにしたカタログ（テストの中だけ）。"""
    cat = copy.deepcopy(drills.load())
    for d in cat["drills"]:
        if d["id"] in ids:
            d["checked_by"] = "テスト"
    monkeypatch.setattr(drills, "load", lambda: cat)
    return cat


# ---------------------------------------------------------------- ドリル集（§5.7）


def test_カタログの形():
    assert drills.validate(drills.load()) == []
    assert drills.version() == "drills/0.1"


def test_人が確かめていないドリルは出さない():
    cat = drills.load()
    assert cat["drills"], "中身は書いてある"
    assert all(d["checked_by"] is None for d in cat["drills"]), "checked_by は人が確かめたら埋める（Claude は埋めない）"
    out = drills.listing("R")
    assert out["drills"] == [] and out["n_unchecked"] == len(cat["drills"])
    assert {m["id"] for m in out["measures"]} == {"measure.impact_offset", "measure.low_point"}


def test_形の検査は誤りを見つける():
    cat = copy.deepcopy(drills.load())
    d = cat["drills"][0]
    d["cue_transfer"] = "右の腰を回す"  # 右の直書き・体の言葉
    d["steps"] = ["1", "2", "3", "4"]
    d["issues"] = ["slice"]
    d["kpi"] = {"metric": "bogus", "goal": "reduce_abs"}
    d["safety"] = ""
    errs = " / ".join(drills.validate(cat))
    for want in ("右・左を直書き", "体・クラブの動きの言葉", "steps は1〜3個", "語彙にありません", "config.MMD", "safety が空"):
        assert want in errs, (want, errs)


def test_ヒールのドリルは向こう側に置く():
    """置く側が逆だと逆の動きを罰する（§5.7）。ヒールの定番はトゥ側（向こう側・図の上）。"""
    d = drills.get("strike.two_balls")
    assert "向こう側" in d["steps"][0] and "トゥ" in d["steps"][0]
    svg = open(os.path.join(os.path.dirname(drills.__file__), d["diagram"]), encoding="utf-8").read()
    # 打つボール（g-good）より、置くボール（g-miss）が上（y が小さい＝打つ人から遠い）
    import re

    good = re.search(r'class="g-good" cx="([0-9.]+)" cy="([0-9.]+)"', svg)
    miss = re.search(r'class="g-miss" cx="([0-9.]+)" cy="([0-9.]+)"', svg)
    assert float(miss.group(2)) < float(good.group(2))


def test_確かめたドリルは左打ちで向きの語と図が入れ替わる(monkeypatch):
    _checked(monkeypatch, ("path.closed_stance",))
    r = drills.listing("R")["drills"][0]
    lft = drills.listing("L")["drills"][0]
    assert r["cue_drill"].startswith("右足") and lft["cue_drill"].startswith("左足")
    assert "scale(-1 1)" in lft["diagram_svg"] and "scale(-1 1)" not in r["diagram_svg"]
    assert lft["coach_reviewed"] is False and lft["slow_settle"] is True


def test_候補に勧めるドリルは確かめ済みだけ_記録のあるものが先(shots, monkeypatch):
    out = coaching.candidates_with_drills(shots, "R")
    iron = next(s for s in out["scopes"] if s["scope_id"] == "group:iron")
    heel = next(c for c in iron["candidates"]["candidates"] if c["id"] == "strike_heel")
    assert heel["drills"]["drills"] == [] and heel["drills"]["n_unchecked"] >= 3
    face = next(c for c in iron["candidates"]["candidates"] if c["id"] == "face")
    # フェースが右の候補に、閉じすぎ向けのドリルを当てない
    _checked(monkeypatch, ("face.half_toe_up", "face.hold_off", "face.start_spot"))
    got = [d["id"] for d in drills.for_candidate(face, "iron", "R")["drills"]]
    assert "face.half_toe_up" in got and "face.hold_off" not in got
    hist = [{"drill_id": "face.start_spot", "issue": "face", "grade": "moderate", "date": "2026-09-20"}]
    got = drills.for_candidate(face, "iron", "R", hist)["drills"]
    assert got[0]["id"] == "face.start_spot" and got[0]["record_note"] == "worked_before"


# ---------------------------------------------------------------- 球数の式（§8.3）


def test_ブロックの球数の式():
    d = plan.block_design(0.0114, "impact_offset", 1.0)
    assert d["n_per_condition"] == 16 and d["per_block"] == 8 and d["one_session"]
    f = plan.block_design(5.2, "face_angle", 1.0)
    assert f["n_per_condition"] == 52 and f["per_block"] == config.BLOCK_N_MAX and not f["one_session"] and f["sessions"] == 3


# ---------------------------------------------------------------- プランを作る（params を固定）


def test_プランの中身(heel_plan, face_plan):
    p = heel_plan
    assert p["club"] == "9 Iron" and p["target_metric"] == "impact_offset" and p["goal"] == "reduce_abs"
    assert p["drill_id"] == coaching.CUSTOM_DRILL  # 確かめ済みのドリルが無いあいだ
    assert [b["kind"] for b in p["template"]] == ["warmup", "baseline", "intervention", "intervention", "baseline"]
    assert p["params"]["band"]["k"] == pytest.approx(0.314, abs=0.01)
    assert p["params"]["counts"] == ["heel_extreme"] and p["params"]["stage"] is None
    assert p["params"]["rules"]["strike_advance_m"] == config.CENTER_STRIKE_M
    fp = face_plan["params"]
    assert fp["stage"] == {"goal_share": 0.75, "step": 0.2, "per_block": 12, "add_per_block": 3}
    assert {g["kind"] for g in fp["guards"]} == {"landed_opposite", "face_median_window"}
    assert fp["untouched"] == [{"metric": "club_path"}] and fp["window"]["face_min"] == -1.86


def test_確かめていないドリルではプランを作れない(shots, monkeypatch):
    with pytest.raises(coaching.PlanError, match="確かめていない"):
        coaching.build_plan(shots, "R", "group:iron", "strike_heel", drill_id="strike.two_balls")
    with pytest.raises(coaching.PlanError, match="範囲"):
        coaching.build_plan(shots, "R", "group:wood", "strike_heel")
    _checked(monkeypatch, ("strike.two_balls", "face.hold_off"))
    with pytest.raises(coaching.PlanError, match="当てません"):
        coaching.build_plan(shots, "R", "group:iron", "face", drill_id="face.hold_off")
    out = coaching.build_plan(shots, "R", "group:iron", "strike_heel", drill_id="strike.two_balls")
    assert [b["kind"] for b in out["plan"]["template"]].count("drill") == 2
    assert out["intervention"] == "向こうにボールがあるつもりで打つ"


def test_既定の番手なら解説と同じ仮説の文(shots):
    out = coaching.build_plan(shots, "R", "group:iron", "strike_heel")
    ids = [c["id"] for c in out["claims"]]
    assert "group:iron/now.hypothesis" in ids and "group:iron/now.design" in ids
    other = coaching.build_plan(shots, "R", "group:iron", "strike_heel", club="8 Iron")
    assert other["plan"]["club"] == "8 Iron" and other["claims"] == []  # 9番の数字を8番の名前で書かない


# ---------------------------------------------------------------- 練習の球を診断から外す（§10.2）


def test_準備とドリルの球は診断に入らない(shots):
    tagged = copy.deepcopy(shots)
    nine = [s for s in tagged if s["club"] == "9 Iron"]
    nine[0]["block_kind"] = "warmup"
    nine[1]["block_kind"] = "drill"
    nine[2]["block_kind"] = "intervention"
    an = analyze_session(tagged)
    assert an["n_practice_excluded"] == {"warmup": 1, "drill": 1}
    c9 = next(c for c in an["clubs"] if c["club"] == "9 Iron")
    assert c9["n"] == len(nine) - 2
    f = next(f for f in an["findings"] if f["kind"] == "intervention_shots")
    assert f["club"] == "9 Iron" and f["evidence"]["count"] == 1
    rep = build_report(tagged, "R")
    assert rep["n_practice_excluded"] == {"warmup": 1, "drill": 1}
    iron = next(s for s in rep["scopes"] if s["scope_id"] == "group:iron")
    assert iron["n"] == 26 - 2


def _blocks_from(design, values, club="9 Iron", extra=None):
    """[(kind, [打点…])] → blocks。"""
    out, seq = [], 1
    for kind, vs in zip(design, values):
        sh = []
        for v in vs:
            sh.append({"id": seq, "seq": seq, "club": club, "club_category": "iron",
                       "metrics": {"impact_offset": v, "club_speed": 37.0 + 0.01 * seq, "carry": 118.0, "side": 3.0, **(extra or {})},
                       "decomposition": {"contact": "heel_extreme" if v < -0.03 else "heel"}})
            seq += 1
        out.append({"kind": kind, "seq_from": sh[0]["seq"], "seq_to": sh[-1]["seq"], "shots": sh})
    return out


def test_評価に準備とドリルの球が入らず_同じ種類はまとめて評価される(heel_plan):
    rng = np.random.default_rng(3)
    a = lambda n: list(rng.normal(-0.021, 0.004, n))  # noqa: E731
    b = lambda n: list(rng.normal(-0.004, 0.004, n))  # noqa: E731
    kinds = ["warmup", "baseline", "drill", "intervention", "drill", "intervention", "baseline"]
    vals = [[0.05] * 5, a(8), [0.06] * 5, b(8), [0.06] * 3, b(8), a(8)]  # 準備とドリルはとんでもない値
    ev = coaching.evaluate_run(heel_plan, _blocks_from(kinds, vals))
    assert ev["primary"]["n"] == [16, 16]
    assert ev["excluded"]["warmup"] == 5
    assert ev["stats"]["A"]["n"] == 16 and abs(ev["stats"]["A"]["mean"]) < 0.03  # 0.05 の準備が入っていない
    assert ev["drill_ref"]["reference"] is True and ev["drill_ref"]["n"] == [16, 8]
    assert ev["grade"] in ("strong", "moderate") and ev["counts_as_worked"]
    assert ev["first_a"]["n"] == 8
    ev2 = coaching.evaluate_run(heel_plan, _blocks_from(kinds, vals))
    assert ev2["evaluation_key"] == ev["evaluation_key"]
    assert any(c["id"] == "run/primary" for c in ev["claims"])


def test_他のクラブの球と除外した球は評価に入らない(heel_plan):
    kinds = ["baseline", "intervention"]
    bl = _blocks_from(kinds, [[-0.02] * 6, [-0.01] * 6])
    bl[0]["shots"][0]["club"] = "8 Iron"
    bl[1]["shots"][0]["excluded"] = True
    ev = coaching.evaluate_run(heel_plan, bl)
    assert ev["primary"]["n"] == [5, 5] and ev["excluded"]["other_club"] == 1 and ev["excluded"]["excluded"] == 1


def test_取れた割合が下がったら注意し_ガードレールがworseなら出す(heel_plan):
    kinds = ["baseline", "intervention"]
    bl = _blocks_from(kinds, [[-0.02, -0.021, -0.019, -0.022, -0.018, -0.02, -0.023, -0.017], [-0.01, -0.011, -0.009, -0.012, -0.008]])
    for s in bl[1]["shots"]:
        s["metrics"]["club_speed"] = 33.0  # 当てにいった
    for _ in range(4):  # 本番の打点が4球取れない
        bl[1]["shots"].append({"id": 900 + _, "seq": 900 + _, "club": "9 Iron", "club_category": "iron", "metrics": {"club_speed": 33.0, "carry": 110.0, "side": 2.0}, "decomposition": {}})
    ev = coaching.evaluate_run(heel_plan, bl)
    assert ev["measured"]["dropped"] is True
    assert ev["guardrail_worse"] is True
    texts = " ".join(c["text"] for c in ev["claims"])
    assert "測れた球だけ" in texts and "当てにいっている" in texts


def test_paramsは作った時点で固定する(heel_plan, monkeypatch):
    bl = _blocks_from(["baseline", "intervention"], [[-0.02] * 6, [-0.01] * 6], extra={"side": 5.5})
    before = coaching.evaluate_run(heel_plan, bl)
    monkeypatch.setitem(config.GOOD_TARGETS, "iron", {**config.GOOD_TARGETS["iron"], "side_pct": 0.5, "side_min_m": 50.0})
    after = coaching.evaluate_run(heel_plan, bl)
    assert before["l0"] == after["l0"]  # 帯の幅は params の値（5.9m）で見る
    assert after["l0"]["A"]["landed_in"] == 6


def test_右の球を減らす型では左へ外れる球が増えたら効いたに数えない(face_plan):
    rng = np.random.default_rng(5)
    kinds = ["baseline", "intervention", "intervention", "baseline"]
    blocks = []
    seq = 1
    for kind in kinds:
        sh = []
        for _ in range(12):
            face = rng.normal(3.3, 1.5) if kind == "baseline" else rng.normal(-3.0, 1.0)  # 全体を左へずらした
            side = 4.0 if kind == "baseline" else -15.0
            sh.append({"id": seq, "seq": seq, "club": "9 Iron", "club_category": "iron",
                       "metrics": {"face_angle": face, "club_path": 0.4, "carry": 110.0, "side": side, "club_speed": 37.0},
                       "decomposition": {"contact": "center", "predicted_launch_direction": 0.75 * face, "predicted_axis_from_face_to_path": 2 * (face - 0.4)}})
            seq += 1
        blocks.append({"kind": kind, "seq_from": sh[0]["seq"], "seq_to": sh[-1]["seq"], "shots": sh})
    ev = coaching.evaluate_run(face_plan, blocks)
    assert ev["grade"] in ("strong", "moderate")
    broken = {g["kind"] for g in ev["guards"] if g["broken"]}
    assert broken == {"landed_opposite", "face_median_window"}
    assert ev["counts_as_worked"] is False
    assert ev["stage"] is not None and ev["stage"]["reference_is_this_run"] is True


# ---------------------------------------------------------------- 1回目の A を物差しにする・状態の移り方（§8.6）


def _ev(grade, first=None, worse_guard=False, drill=False, stage=None):
    return {"grade": grade, "counts_as_worked": grade in ("strong", "moderate"), "guardrail_worse": worse_guard,
            "drill_moderate_plus": drill, "first_a": {"values": first or [], "n": len(first or [])},
            "primary": {"grade": grade, "n": [16, 16], "needed": 5}}


def _runs(evs, dates=None):
    return [{"run_id": i + 1, "session_id": 100 + i, "date": (dates or [f"2026-10-{i + 1:02d}" for i in range(len(evs))])[i], "evaluation": e} for i, e in enumerate(evs)]


def test_状態の移り方(heel_plan):
    P = heel_plan
    st = lambda evs, **kw: coaching.progress(P, _runs(evs, kw.pop("dates", None)), **kw)["state"]  # noqa: E731
    assert st([]) == "checking"
    assert st([_ev("weak")]) == "checking"
    assert st([_ev("moderate")]) == "maybe"
    assert st([_ev("moderate"), _ev("strong")]) == "worked"
    assert st([_ev("moderate"), _ev("strong")], dates=["2026-10-01", "2026-10-01"]) == "maybe"  # 同じ日の2回は数えない
    assert st([_ev("moderate", worse_guard=True), _ev("strong")]) == "maybe"
    assert st([_ev("moderate"), _ev("weak"), _ev("moderate")]) == "failed"  # 3回で届かない
    assert st([_ev("worse"), _ev("worse")]) == "failed"
    assert st([_ev("insufficient")]) == "insufficient"
    assert st([_ev("weak", drill=True), _ev("none", drill=True)]) == "not_transferred"
    assert st([_ev("weak", worse_guard=True), _ev("moderate", worse_guard=True)]) == "stop"
    assert st([_ev("weak", worse_guard=True), _ev("moderate", worse_guard=True)], continue_after_stop=True) == "maybe"
    hist = [{"issue": "strike_heel", "drill_id": "strike.two_balls", "state": "failed"}]
    assert st([_ev("none"), _ev("weak"), _ev("none")], history=hist) == "stuck"


def test_定着は1回目のAを物差しにする(heel_plan):
    rng = np.random.default_rng(11)
    bad = list(rng.normal(-0.024, 0.004, 8))  # プランの1回目の最初の A（ひどい）
    good = lambda: list(rng.normal(-0.006, 0.004, 8))  # noqa: E731
    same = lambda: list(rng.normal(-0.024, 0.004, 8))  # noqa: E731
    evs = [_ev("moderate", bad), _ev("strong", same()), _ev("strong", good()), _ev("weak", good())]
    out = coaching.progress(heel_plan, _runs(evs))
    assert out["state"] == "settled", out
    assert out["worked_at_run"] == 2 and out["settle"]["need"] == 2
    # 物差しを2回目の A（同じくらいひどい）ではなく1回目にしていること: 最初の A が1回目と同じなら定着しない。
    # 「効いた」は直近の判定できた2回（別の日）で見るので、効いたあとに weak の回が来たら「確かめ中」に戻る（§8.6）
    evs2 = [_ev("moderate", bad), _ev("strong", same()), _ev("strong", same()), _ev("weak", same())]
    out2 = coaching.progress(heel_plan, _runs(evs2))
    assert out2["state"] == "checking" and out2["worked_at_run"] == 2
    assert any("前に「効いた」" in n for n in out2.get("notes") or [])
    evs3 = [_ev("moderate", bad), _ev("strong", same()), _ev("strong", same())]
    assert coaching.progress(heel_plan, _runs(evs3))["state"] == "worked"


def test_段の目標は1回目のAから作る(face_plan):
    ref = {"first_a": {"l1_in": {"used": True, "in": 3, "counted": 12}}}
    rng = np.random.default_rng(2)
    blocks, seq = [], 1
    for kind in ["baseline", "intervention", "intervention", "baseline"]:
        sh = []
        for _ in range(12):
            face = rng.normal(0.5, 1.0)
            sh.append({"id": seq, "seq": seq, "club": "9 Iron", "club_category": "iron",
                       "metrics": {"face_angle": face, "club_path": 0.4, "carry": 110.0, "side": 1.0, "club_speed": 37.0},
                       "decomposition": {"contact": "center", "predicted_launch_direction": 0.75 * face, "predicted_axis_from_face_to_path": 2 * (face - 0.4)}})
            seq += 1
        blocks.append({"kind": kind, "seq_from": sh[0]["seq"], "seq_to": sh[-1]["seq"], "shots": sh})
    ev = coaching.evaluate_run(face_plan, blocks, reference=ref)
    st = ev["stage"]
    assert st["reference_in"] == 3 and st["reference_counted"] == 12 and st["reference_is_this_run"] is False
    assert st["target_per_block"] == 3 + 3  # 12球あたり、1回目の A の数より3球多く
    own = coaching.evaluate_run(face_plan, blocks)["stage"]
    assert own["reference_is_this_run"] is True and own["reference_counted"] == ev["first_a"]["l1_in"]["counted"]


def test_打点のプランは最初のAが芯10mm以内なら次へ進める(heel_plan):
    out = coaching.progress(heel_plan, _runs([{**_ev("weak", [-0.004] * 8), "first_a": {"values": [-0.004] * 8, "median": -0.004}}]))
    assert out["advance"] == {"ok": True, "by": "first_a_center"}


# ---------------------------------------------------------------- A-B-B-A（§8.3。scripts/sim_block_order.py）


def test_効果なしと練習の中の4mmの改善では_ABBAなら効いたにならない(heel_plan):
    rng = np.random.default_rng(sim.SEED)
    for drift in (0.0, sim.DRIFT_M):
        runs = []
        for i in range(2):
            ev = coaching.evaluate_run(heel_plan, sim.make_run(rng, sim.ABBA, 0.0, drift))
            runs.append({"run_id": i + 1, "session_id": i + 1, "date": f"2026-10-0{i + 1}", "evaluation": ev})
        out = coaching.progress(heel_plan, runs)
        assert out["state"] != "worked", (drift, [r["evaluation"]["grade"] for r in runs])


def test_練習の中の改善はABBAなら打ち消し合う():
    """同じ条件で、A→B の順だと moderate 以上が増えるが、A-B-B-A は変化なしと同じくらい（シミュレーション）。"""
    reps = 150
    ab = sim.simulate(sim.DESIGNS["A16-B16"], sim.DRIFT_M, reps=reps)
    abba = sim.simulate(sim.ABBA, sim.DRIFT_M, reps=reps)
    assert abba < ab
    assert abba <= 0.15


# ---------------------------------------------------------------- HTTP


def test_HTTPの口(shots):
    c = TestClient(app)
    r = c.get("/v1/drills")
    assert r.status_code == 200 and r.json()["drills"] == [] and r.json()["n_unchecked"] == len(drills.load()["drills"])
    assert c.get("/v1/drills?handedness=X").status_code == 400
    r = c.post("/v1/plan/candidates", json={"shots": shots, "handedness": "R"})
    assert r.status_code == 200 and {s["scope_id"] for s in r.json()["scopes"]} == {"group:iron", "club:4 Hybrid", "club:5 Wood"}
    r = c.post("/v1/plan/build", json={"shots": shots, "scope_id": "group:iron", "candidate_id": "strike_heel"})
    assert r.status_code == 200
    p = r.json()["plan"]
    assert c.post("/v1/plan/new", json={"shots": shots, "scope_id": "group:iron", "candidate_id": "strike_heel"}).json()["plan"] == p
    r = c.post("/v1/plan/build", json={"shots": shots, "scope_id": "group:iron", "candidate_id": "strike_heel", "drill_id": "strike.two_balls"})
    assert r.status_code == 422 and "確かめていない" in r.json()["detail"]
    blocks = sim.make_run(np.random.default_rng(1), sim.ABBA, 0.0, 0.0)
    ev = c.post("/v1/plan/evaluate", json={"plan": p, "blocks": blocks})
    assert ev.status_code == 200 and ev.json()["primary"]["n"] == [16, 16]
    pr = c.post("/v1/plan/progress", json={"plan": p, "runs": [{"run_id": 1, "session_id": 1, "date": "2026-10-01", "evaluation": ev.json()}]})
    assert pr.status_code == 200 and pr.json()["state"] in coaching.STATE_TEXT
    assert c.post("/v1/plan/evaluate", json={"plan": {"target_metric": "impact_offset", "goal": "bogus"}, "blocks": []}).status_code == 422


def test_Goの形でも評価し_1件目のrunを物差しにして状態を付ける(heel_plan):
    rng = np.random.default_rng(7)
    c = TestClient(app)
    plan_row = {**heel_plan, "id": 1, "status": "active"}
    past = []
    for i in range(2):
        blocks = sim.make_run(rng, sim.ABBA, 0.012 if i else 0.014, 0.0, seq0=1)
        # 人が境目を確かめて保存した回（予定の37球と打った32球が違っても、保存した境目なら判定する）
        body = {"plan": plan_row, "run": {"id": 10 + i, "index": i, "session_id": 100 + i, "date": f"2026-10-0{i + 1}", "blocks_source": "saved", "club_shots": 32, "planned": 37},
                "experiment": {"target_metric": "impact_offset", "goal": "reduce_abs", "blocks": blocks}, "past_runs": past}
        r = c.post("/v1/plan/evaluate", json=body)
        assert r.status_code == 200, r.text
        ev = r.json()
        assert ev["run"]["index"] == i and ev["planned_mismatch"] is True and ev["blocks_provisional"] is False
        assert ev["progress"]["n_runs"] == i + 1
        if i == 1:
            assert ev["stage"] is None  # 打点のプランには段の目標が無い
            assert ev["progress"]["trend"][0]["first_a"]["n"] == 8
            assert ev["progress"]["state"] == "worked", [x["grade"] for x in ev["progress"]["trend"]]
        past.append({"run_id": 10 + i, "index": i, "session_id": 100 + i, "date": f"2026-10-0{i + 1}", "evaluation": ev})
    # 予定の球数で区切っただけ（planned）で打った数とも合わない境目は判定しない（§8.4）
    body["run"] = {**body["run"], "id": 12, "index": 2, "session_id": 102, "date": "2026-10-03", "blocks_source": "planned"}
    body["past_runs"] = past
    ev = c.post("/v1/plan/evaluate", json=body).json()
    assert ev["pending_boundaries"] is True and ev["counts_as_worked"] is False
    assert ev["progress"]["state"] == "pending" and ev["plain"]["tone"] in ("warn", "none", "good", "bad")


def test_paramsが無いプランは既定値で埋めてそう言う():
    blocks = _blocks_from(["baseline", "intervention"], [[-0.02] * 6, [-0.01] * 6])
    ev = coaching.evaluate_run({"club": "9 Iron", "issue": "strike_heel", "target_metric": "impact_offset", "goal": "reduce_abs", "params": {}}, blocks)
    assert ev["filled_params"] is True and ev["counts"][0]["kind"] == "heel_extreme"
    with pytest.raises(coaching.PlanError):
        coaching.evaluate_run({"club": "9 Iron", "target_metric": "impact_offset", "goal": "bogus"}, blocks)


# ---------------------------------------------------------------- 正直さ（レビューの指摘 2026-09-29）


def test_最初に出す言葉は数字も専門用語も使わない():
    from golf_analysis import gist
    texts = [t for pair in coaching.STATE_TEXT.values() for t in pair] + list(coaching.STOP_TEXT.values()) + list(coaching.NEXT_TEXT.values())
    texts += list(coaching.NOT_COUNTED_PLAIN.values()) + [x[0] for x in coaching.RUN_PLAIN.values()] + ["良く見えましたが、今日は数えません"]
    for t in texts:
        assert gist.check_plain(t) == [], t
        assert "ガードレール" not in t and "帯" not in t, t
    assert set(coaching.NEXT_ACTION.values()) <= set(coaching.NEXT_TEXT)
    assert set(coaching.NOT_COUNTED) == set(coaching.NOT_COUNTED_PLAIN)


def test_良い判定でも数えない回は_良くなったと言わない(heel_plan):
    kinds = ["baseline", "intervention"]
    a = [-0.02, -0.021, -0.019, -0.022, -0.018, -0.02, -0.023, -0.017]
    bl = _blocks_from(kinds, [a, [-0.004, -0.005, -0.003, -0.006, -0.004]])
    for _ in range(4):  # 本番の打点が4球取れない（測れた球だけ良くなっている見込み）
        bl[1]["shots"].append({"id": 900 + _, "seq": 900 + _, "club": "9 Iron", "club_category": "iron", "metrics": {"club_speed": 38.0, "carry": 118.0, "side": 2.0}, "decomposition": {}})
    ev = coaching.evaluate_run(heel_plan, bl)
    assert ev["grade"] in ("strong", "moderate") and ev["counts_as_worked"] is False
    assert ev["plain"]["label"] == "良く見えましたが、今日は数えません" and ev["plain"]["why"]
    assert "良くなりました" not in ev["plain"]["label"]


def _hidden_worst(seed):
    rng = np.random.default_rng(seed)
    a = list(rng.normal(-0.021, 0.006, 16))
    b = sorted(rng.normal(-0.014, 0.006, 16))  # いちばんヒール寄り（悪い）3球を「-」にする
    bl = _blocks_from(["baseline", "intervention"], [a, b[3:]])
    for k in range(3):
        bl[1]["shots"].append({"id": 800 + k, "seq": 800 + k, "club": "9 Iron", "club_category": "iron", "metrics": {"club_speed": 38.0, "carry": 118.0, "side": 2.0}, "decomposition": {}})
    return bl


def test_本番の悪い球が欠けたら_いちばん悪い値に置いても残るときだけ数える(heel_plan):
    ev = coaching.evaluate_run(heel_plan, _hidden_worst(22))
    assert ev["measured"]["dropped"] is False  # 割合の差（13/16）は 20% の手前でも黙って比べない
    wc = ev["measured"]["worst_case"]
    assert ev["grade"] == "strong" and wc["filled"] == 3 and wc["holds"] is False
    assert ev["counts_as_worked"] is False and "measured_worst_case" in ev["not_counted"]
    assert {"run/measured", "run/worst_case"} <= {c["id"] for c in ev["claims"]}, "欠けがあれば取れた割合を毎回並べる"
    ok = coaching.evaluate_run(heel_plan, _hidden_worst(20))
    assert ok["measured"]["worst_case"]["holds"] is True and ok["counts_as_worked"] is True


def test_意味のある差より小さい改善は効いたに数えない(heel_plan):
    rng = np.random.default_rng(4)
    a = list(rng.normal(-0.021, 0.0012, 16))
    b = list(rng.normal(-0.0185, 0.0012, 16))  # 2.5mm（MMD 4mm 未満）
    ev = coaching.evaluate_run(heel_plan, _blocks_from(["baseline", "intervention"], [a, b]))
    assert ev["grade"] in ("strong", "moderate")
    assert ev["counts_as_worked"] is False and "below_mmd" in ev["not_counted"]
    assert any(c["id"] == "run/below_mmd" for c in ev["claims"])


def test_崩れたは効いたと同じ形で数え_測れなければ確かめられないと言う(heel_plan):
    rng = np.random.default_rng(8)
    a = list(rng.normal(-0.021, 0.004, 16))
    b = list(rng.normal(-0.004, 0.004, 16))
    bl = _blocks_from(["baseline", "intervention"], [a, b])
    sp = list(rng.normal(37.0, 0.6, 32))
    for i, s in enumerate(bl[0]["shots"] + bl[1]["shots"]):
        s["metrics"]["club_speed"] = sp[i] - (0.9 if i >= 16 else 0.0)  # 本番で 0.9m/s 落ちる（MMD 0.5 より大きい）
    ev = coaching.evaluate_run(heel_plan, bl)
    g = next(x for x in ev["guardrails"] if x["metric"] == "club_speed")
    assert g["worse"] is True and ev["guardrail_worse"] is True
    for s in bl[0]["shots"] + bl[1]["shots"]:
        s["metrics"].pop("club_speed")
    ev2 = coaching.evaluate_run(heel_plan, bl)
    assert ev2["counts_as_worked"] is False and "guardrail_unknown" in ev2["not_counted"]


def test_止めるの文は崩れた見張りで決まる(heel_plan):
    def evg(metric):
        e = _ev("moderate", worse_guard=True)
        e["guardrails"] = [{"metric": metric, "worse": True}]
        return e
    out = coaching.progress(heel_plan, _runs([evg("club_speed"), evg("club_speed")]))
    assert out["state"] == "stop" and out["text"] == coaching.STOP_TEXT["club_speed"]
    assert out["next_text"] == coaching.NEXT_TEXT["ask_continue"]
    # 続けると選んだあとは、その回より後の崩れだけを数える
    out2 = coaching.progress(heel_plan, _runs([evg("club_speed"), evg("club_speed"), _ev("moderate")]), continue_after_run=2)
    assert out2["state"] != "stop"


def test_本人が除外した球の数を書く(heel_plan):
    bl = _blocks_from(["baseline", "intervention"], [[-0.02] * 6, [-0.01] * 8])
    for s in bl[1]["shots"][:3]:
        s["excluded"] = True
    ev = coaching.evaluate_run(heel_plan, bl)
    assert ev["excluded"]["excluded"] == 3
    assert any(c["id"] == "run/excluded" and "3" in c["text"] for c in ev["claims"])


def test_パスの向きで当てるドリルはパスの平均が合うときだけ():
    d = drills.get("path.outside_cover")
    cand = {"issue_names": ["curve_right"], "lever": "path"}
    assert drills.matches(d, cand, "iron", -1.2) is True
    assert drills.matches(d, cand, "iron", 1.5) is False
    assert drills.matches(d, cand, "iron", None) is False


def test_記録は効いたに数えた回で数える():
    r = drills.records_by_drill([{"drill_id": "x", "grade": "strong", "counts_as_worked": False}, {"drill_id": "x", "grade": "moderate", "counts_as_worked": True}])
    assert r["x"]["moderate_plus"] == 1


def test_診断のドリルを使うプランはドリルのブロックを残す(shots, heel_plan):
    """診断レポートから作るプラン（diag_drills）は、確かめ済みのドリル集のドリルが無くても型にドリルのブロックを残す
    （ドリルの球を「いつも通り」「本番」に混ぜず、練習の画面が診断のドリルの手順をそのブロックに出す）。無ければ今まで通り。"""
    p = coaching.build_plan(shots, "R", "group:iron", "strike_heel", diag_drills=True)["plan"]
    kinds = [b["kind"] for b in p["template"]]
    assert kinds.count("drill") == 2 and kinds.count("intervention") == 2 and kinds.count("baseline") == 2
    assert "drill" not in [b["kind"] for b in heel_plan["template"]]
    assert [b for b in p["template"] if b["kind"] != "drill"] == heel_plan["template"]
