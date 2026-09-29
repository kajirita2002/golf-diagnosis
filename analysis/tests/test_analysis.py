import random

from fastapi.testclient import TestClient

from golf_analysis import config
from golf_analysis.app import app
from golf_analysis.experiment import evaluate
from golf_analysis.session import analyze_session, judge_good


def shot(i, club="7 Iron", cat="iron", override=None, excluded=False, decomposition=None, **metrics):
    return {
        "id": i,
        "seq": i,
        "club": club,
        "club_category": cat,
        "metrics": metrics,
        "estimated": [],
        "excluded": excluded,
        "good_override": override,
        "decomposition": decomposition or {},
    }


# ---- Good 判定 ----


def test_目標の範囲に入ればGood():
    s = shot(1, side=3.0, carry=150.0, launch_direction=1.0)
    assert judge_good(s, 150.0, "iron") == {"good": True, "by": "auto", "failed": []}


def test_外れた条件を全部返す():
    s = shot(1, side=15.0, carry=130.0, launch_direction=5.0)
    j = judge_good(s, 150.0, "iron")
    assert j["good"] is False
    assert set(j["failed"]) == {"side", "start_line", "carry"}


def test_人の上書きが優先():
    s = shot(1, side=30.0, carry=100.0, override=True)
    assert judge_good(s, 150.0, "iron")["by"] == "override"
    assert judge_good(s, 150.0, "iron")["good"] is True


def test_値が無ければ判定しない():
    assert judge_good(shot(1), None, "iron")["good"] is None


def test_左右が無ければ分解の曲がりで見る():
    s = shot(1, decomposition={"curve": "slice"})
    assert judge_good(s, None, "iron")["failed"] == ["curve"]


# ---- セッション ----


def test_打点が原因の曲がりを見つける():
    shots = [
        shot(i, side=10.0, carry=150.0, launch_direction=1.0, impact_offset=-0.015,
             decomposition={"curve": "fade", "curve_cause": "strike", "miss_type": "straight-fade"})
        for i in range(1, 7)
    ]
    res = analyze_session(shots)
    kinds = [(f["kind"], f.get("cause")) for f in res["findings"]]
    assert ("curve_cause", "strike") in kinds
    strike = next(f for f in res["findings"] if f.get("cause") == "strike")
    assert strike["evidence"] == {"count": 6, "of": 6}
    assert strike["strength"] == "strong"
    assert res["clubs"][0]["flight_groups"][0] == {
        "miss_type": "straight-fade", "curve_cause": "strike", "n": 6, "shot_ids": [1, 2, 3, 4, 5, 6]
    }


def test_少数でもまとまった原因の群は出す():
    # 曲がった20球のうち15球はフェース、5球は打点。打点の5球を埋もれさせない
    shots = [shot(i, decomposition={"curve": "fade", "curve_cause": "face_to_path"}) for i in range(1, 16)]
    shots += [shot(i, impact_offset=-0.015, decomposition={"curve": "fade", "curve_cause": "strike"}) for i in range(16, 21)]
    res = analyze_session(shots)
    strike = [f for f in res["findings"] if f.get("cause") == "strike"]
    assert len(strike) == 1
    assert strike[0]["strength"] == "subset"
    assert strike[0]["shot_ids"] == [16, 17, 18, 19, 20]
    face = next(f for f in res["findings"] if f.get("cause") == "face_to_path")
    assert face["strength"] == "strong"


def test_少数で数も少ない原因は出さない():
    shots = [shot(i, decomposition={"curve": "fade", "curve_cause": "face_to_path"}) for i in range(1, 18)]
    shots += [shot(i, decomposition={"curve": "fade", "curve_cause": "strike"}) for i in range(18, 21)]
    assert not any(f.get("cause") == "strike" for f in analyze_session(shots)["findings"])


def test_球が少なければデータ不足と言う():
    shots = [shot(i, decomposition={"curve": "fade", "curve_cause": "strike"}) for i in range(1, 3)]
    res = analyze_session(shots)
    assert res["findings"] == [
        {"club": "7 Iron", "scope": "club", "kind": "insufficient", "what": "curve_cause", "n": 2, "needed": config.MIN_BLOCK_N}
    ]


def test_打点が無い曲がりは入力を促す():
    shots = [shot(i, decomposition={"curve": "fade", "curve_cause": "mixed"}) for i in range(1, 7)]
    res = analyze_session(shots)
    assert any(f["kind"] == "need_data" and f["field"] == "impact_offset" for f in res["findings"])


def test_除外した球は数えない():
    res = analyze_session([shot(1, excluded=True, carry=150.0), shot(2, carry=150.0)])
    assert res["n_excluded"] == 1
    assert res["clubs"][0]["n"] == 1


def test_左右のばらつきの主因を回帰で出す():
    rng = random.Random(1)
    shots = []
    for i in range(1, 25):
        face = rng.gauss(2, 2.0)
        path = rng.gauss(2, 0.3)
        shots.append(shot(i, face_angle=face, club_path=path, side=2.5 * face + 0.3 * path + rng.gauss(0, 0.5), carry=150.0))
    res = analyze_session(shots)
    d = res["clubs"][0]["dispersion_drivers"]
    assert d["status"] == "ok"
    assert d["contributions"][0]["metric"] == "face_angle"
    assert any(f["kind"] == "dispersion_driver" and f["metric"] == "face_angle" for f in res["findings"])


def test_回帰は球数が足りなければしない():
    shots = [shot(i, face_angle=1.0 * i, club_path=0.5, side=1.0 * i) for i in range(1, 6)]
    d = analyze_session(shots)["clubs"][0]["dispersion_drivers"]
    assert d["status"] == "insufficient" and d["needed"] == config.MIN_DRIVER_N


# ---- 実験 ----


def blocks(base, inter, retention=None, metric="face_to_path"):
    out = [
        {"kind": "baseline", "shots": [shot(i, **{metric: v}) for i, v in enumerate(base)]},
        {"kind": "intervention", "shots": [shot(100 + i, **{metric: v}) for i, v in enumerate(inter)]},
    ]
    if retention is not None:
        out.append({"kind": "retention", "shots": [shot(200 + i, **{metric: v}) for i, v in enumerate(retention)]})
    return out


def test_はっきり効いた介入はstrong():
    rng = random.Random(2)
    base = [rng.gauss(3.0, 1.0) for _ in range(10)]
    inter = [rng.gauss(0.5, 1.0) for _ in range(10)]
    r = evaluate("face_to_path", "reduce_abs", blocks(base, inter))
    c = r["intervention_vs_baseline"]
    assert c["grade"] == "strong", c
    assert c["ci95"][0] > 0
    assert c["improvement"] >= config.MMD["face_to_path"]


def test_差が無ければstrongにしない():
    rng = random.Random(3)
    base = [rng.gauss(2.0, 1.5) for _ in range(8)]
    inter = [rng.gauss(2.0, 1.5) for _ in range(8)]
    c = evaluate("face_to_path", "reduce_abs", blocks(base, inter))["intervention_vs_baseline"]
    assert c["grade"] in ("weak", "none", "worse")


def test_悪くなればworse():
    base = [0.5, -0.4, 0.2, 0.1, -0.3, 0.4]
    inter = [3.1, 2.8, 3.5, 2.9, 3.3, 3.0]
    c = evaluate("face_to_path", "reduce_abs", blocks(base, inter))["intervention_vs_baseline"]
    assert c["grade"] == "worse"


def test_球が足りなければinsufficient():
    c = evaluate("face_to_path", "reduce_abs", blocks([3, 3, 3], [0, 0, 0, 0, 0]))["intervention_vs_baseline"]
    assert c == {"grade": "insufficient", "n": [3, 5], "needed": config.MIN_BLOCK_N}


def test_ばらつきを減らす目標():
    rng = random.Random(4)
    base = [rng.gauss(1.0, 3.0) for _ in range(12)]
    inter = [rng.gauss(1.0, 0.5) for _ in range(12)]
    c = evaluate("face_to_path", "reduce_sd", blocks(base, inter))["intervention_vs_baseline"]
    assert c["grade"] in ("strong", "moderate")


def test_定着も評価する():
    rng = random.Random(5)
    r = evaluate(
        "face_to_path",
        "reduce_abs",
        blocks([rng.gauss(3, 1) for _ in range(8)], [rng.gauss(0.5, 1) for _ in range(8)], [rng.gauss(3, 1) for _ in range(6)]),
    )
    assert "retention_vs_baseline" in r
    assert r["retention_vs_baseline"]["grade"] in ("weak", "none", "worse")


def test_同じ入力には同じ答え():
    b = blocks([3.1, 2.5, 3.8, 2.2, 3.0, 2.9], [0.4, 1.1, -0.3, 0.9, 0.2, 0.7])
    assert evaluate("face_to_path", "reduce_abs", b) == evaluate("face_to_path", "reduce_abs", b)


def test_推定値は注意を付ける():
    b = blocks([3.0] * 5, [0.0] * 5, metric="club_path")
    b[0]["shots"][0]["estimated"] = ["club_path"]
    r = evaluate("club_path", "reduce_abs", b)
    assert any("推定値" in n for n in r["notes"])


# ---- HTTP ----


def test_HTTPの入口():
    c = TestClient(app)
    assert c.get("/healthz").json()["ok"] is True
    r = c.post("/v1/session", json={"shots": [shot(1, carry=150.0)]})
    assert r.status_code == 200 and r.json()["n_shots"] == 1
    assert c.post("/v1/experiment", json={"target_metric": "face_to_path", "goal": "bogus", "blocks": []}).status_code == 400
    assert c.get("/docs").status_code == 404


# ---- セッションの比較 ----

from golf_analysis.compare import compare_sessions  # noqa: E402


def _session(n, rng, ftp_mu, offset_mu=0.0, start=0):
    out = []
    for i in range(n):
        ftp = rng.gauss(ftp_mu, 0.8)
        off = rng.gauss(offset_mu, 0.002)
        axis = 2.2 * ftp - 400 * off + rng.gauss(0, 0.6)  # 粗い模型: ヒール（マイナス）でスライス側
        out.append(shot(start + i, face_to_path=ftp, impact_offset=off, spin_axis=axis, face_angle=ftp + 1, club_path=1.0))
    return out


def test_スライスが増えた理由をフェーストゥパスで説明する():
    rng = random.Random(7)
    a = _session(12, rng, 0.5)
    b = _session(12, rng, 3.0, start=100)
    r = compare_sessions(a, b)["clubs"]["7 Iron"]
    assert r["l0"]["spin_axis"]["status"] == "changed"
    ex = next(e for e in r["explanations"] if e["outcome"] == "spin_axis")
    assert ex["explained_by"][0]["metric"] == "face_to_path"
    assert r["l1"]["impact_offset"]["status"] == "same"


def test_フェースが同じならスライスの理由は打点():
    rng = random.Random(8)
    a = _session(12, rng, 0.5, offset_mu=0.0)
    b = _session(12, rng, 0.5, offset_mu=-0.015, start=100)  # ヒールに15mm
    r = compare_sessions(a, b)["clubs"]["7 Iron"]
    ex = next(e for e in r["explanations"] if e["outcome"] == "spin_axis")
    assert [c["metric"] for c in ex["explained_by"]] == ["impact_offset"]


def test_インパクトで説明できない変化はそう言う():
    rng = random.Random(9)
    a = [shot(i, spin_axis=rng.gauss(0, 0.5), face_to_path=rng.gauss(0.5, 0.5)) for i in range(8)]
    b = [shot(100 + i, spin_axis=rng.gauss(6, 0.5), face_to_path=rng.gauss(0.5, 0.5)) for i in range(8)]
    ex = compare_sessions(a, b)["clubs"]["7 Iron"]["explanations"][0]
    assert ex["unexplained"] is True
    assert "impact_offset" in ex["unmeasured"]


def test_比較も球が足りなければ言わない():
    a = [shot(i, spin_axis=0.0) for i in range(3)]
    b = [shot(100 + i, spin_axis=8.0) for i in range(3)]
    r = compare_sessions(a, b)["clubs"]["7 Iron"]
    assert r["l0"]["spin_axis"]["status"] == "insufficient"
    assert r["explanations"] == []


def test_片方にしか無いクラブは分ける():
    r = compare_sessions([shot(1)], [shot(2, club="Driver", cat="driver")])
    assert r["clubs"] == {} and r["only_in_a"] == ["7 Iron"] and r["only_in_b"] == ["Driver"]


# ---- ミスヒットの候補・極端な打点・番手をまとめる（2026-09-17 の実データで足した） ----

from golf_analysis.session import mishit_candidates  # noqa: E402


def test_ミスヒットは候補に出すが自動では外さない():
    shots = [shot(i, carry=150.0 + i, side=2.0) for i in range(1, 8)]
    # 極端なヒールで短く右へ（ネック寄り）。直すべきミスなので候補にしない
    shots.append(shot(8, carry=51.7, side=17.4, spin_axis=47.9, decomposition={"contact": "heel_extreme"}))
    # トップ（極端なヒールでもトップなら候補）
    shots.append(shot(9, carry=12.4, side=-1.7, spin_axis=-100.1, decomposition={"flags": ["thin"], "contact": "heel_extreme"}))
    shots.append(shot(10, carry=60.0, side=3.0, decomposition={"flags": ["thin"], "contact": "heel"}))
    c = mishit_candidates(shots, 153.0)
    assert [x["id"] for x in c] == [9, 10]
    assert set(c[0]["reasons"]) == {"thin", "short_carry", "extreme_axis"}
    res = analyze_session(shots)
    assert res["clubs"][0]["n"] == 10  # 外していない
    assert any(f["kind"] == "mishit_candidates" and f["shot_ids"] == [9, 10] for f in res["findings"])


def test_要因分析はミスヒットの候補を除いて計算する():
    rng = random.Random(11)
    shots = []
    for i in range(1, 16):
        face = rng.gauss(2, 2.0)
        shots.append(shot(i, face_angle=face, club_path=rng.gauss(0, 0.3), side=3 * face + rng.gauss(0, 0.5), carry=150.0))
    # トップの1球（外れ値）
    shots.append(shot(99, face_angle=-7.8, club_path=-1.0, side=-1.7, carry=12.4, spin_axis=-100.1, decomposition={"flags": ["thin"]}))
    d = analyze_session(shots)["clubs"][0]["dispersion_drivers"]
    assert d["skipped"] == 1
    assert d["contributions"][0]["metric"] == "face_angle" and d["r2"] > 0.9


def test_極端なヒールの群を出す():
    shots = [shot(i, side=5.0, decomposition={"contact": "heel"}) for i in range(1, 6)]
    shots += [shot(i, side=40.0, decomposition={"contact": "heel_extreme", "flags": ["no_club_data"]}) for i in (6, 7)]
    f = next(x for x in analyze_session(shots)["findings"] if x["kind"] == "extreme_strike")
    assert f["contact"] == "heel_extreme" and f["shot_ids"] == [6, 7]
    assert f["evidence"]["mean_abs_side"] == 40.0 and f["evidence"]["mean_abs_side_others"] == 5.0
    assert f["evidence"]["no_club_data"] == 2


def test_同じ種類のクラブが2本以上ならまとめても見る():
    rng = random.Random(12)
    shots = []
    for i, club in enumerate(["6 Iron"] * 6 + ["9 Iron"] * 6, start=1):
        face = rng.gauss(3, 3.0)
        carry = 140.0 if club == "6 Iron" else 105.0
        shots.append(shot(i, club=club, face_angle=face, club_path=rng.gauss(0, 0.5), carry=carry,
                          side=carry * 0.02 * face + rng.gauss(0, 0.3)))
    shots.append(shot(50, club="5 Wood", cat="wood", carry=180.0))
    res = analyze_session(shots)
    assert [g["name"] for g in res["groups"]] == ["アイアン（まとめ）"]
    g = res["groups"][0]
    assert g["n"] == 12 and set(g["clubs"]) == {"6 Iron", "9 Iron"}
    # 1本ずつ（6球）では足りない要因分析が、まとめると出る
    assert all(c["dispersion_drivers"]["status"] == "insufficient" for c in res["clubs"] if c["club"] != "5 Wood")
    assert g["dispersion_drivers"]["status"] == "ok" and g["dispersion_drivers"]["outcome"] == "side_pct"
    assert g["dispersion_drivers"]["contributions"][0]["metric"] == "face_angle"
    assert any(f["scope"] == "group" and f["kind"] == "dispersion_driver" for f in res["findings"])
