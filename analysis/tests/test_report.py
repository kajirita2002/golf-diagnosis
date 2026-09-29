"""解説レポート（Phase 1a）の実データのテスト。docs/DESIGN_coaching.md §11。

入力は tests/data/real_2026-09-17_shots.json（本人の57球を本物の Go API で取り込み、/shots の出力を
分析サービスが受け取る形で保存したもの。作り直しは tests/data/make_real_fixture.py）。
区間などは手で写した値と突き合わせず、実装した関数の出力を固定する。
"""

import copy
import json
import os
import re
import time

import pytest
from fastapi.testclient import TestClient

from golf_analysis import config, plan, stats
from golf_analysis.app import app
from golf_analysis.claims import Claim, ClaimError, Facts, build_claim, render
from golf_analysis.profile import metric_summary
from golf_analysis.report import build_report
from golf_analysis.session import analyze_session

DATA = os.path.join(os.path.dirname(__file__), "data", "real_2026-09-17_shots.json")


@pytest.fixture(scope="module")
def shots():
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def an(shots):
    return analyze_session(shots)


@pytest.fixture(scope="module")
def rep(shots):
    return build_report(shots, "R")


def _group(an, cat="iron"):
    return next(g for g in an["groups"] if g["club_category"] == cat)


def _club(an, name):
    return next(c for c in an["clubs"] if c["club"] == name)


def _scope(rep, sid):
    return next(s for s in rep["scopes"] if s["scope_id"] == sid)


def _claims(scope):
    return [c for sec in scope["sections"] for c in sec["claims"]]


def _all_claims(rep):
    return [c for s in rep["scopes"] for c in _claims(s)] + rep["cross_club"]["claims"]


def _by_seq(shots):
    return {s["seq"]: s for s in shots}


# ---------------------------------------------------------------- 版と既存の直し（§12）


def test_版はanalysis_0_5(an):
    # 0.5 は動画のチェックポイントの測る・判定を足した版（docs/DESIGN_v2.md §13.4）
    assert config.ENGINE_VERSION == "analysis/0.5"
    assert an["engine_version"] == "analysis/0.5"


def test_極端な打点のフェースなしとパスなしを分けて数える(an):
    f = next(x for x in an["findings"] if x["id"] == "group:アイアン（まとめ）:extreme_strike:heel_extreme")
    ev = f["evidence"]
    assert ev["count"] == 5 and ev["no_face"] == 4 and ev["no_face_and_path"] == 0
    assert ev["no_club_data"] == ev["no_face"]  # 旧名は画面を直すまで残す


def test_平均とSDもミスヒットの候補を除く(an):
    ut = _club(an, "4 Hybrid")
    face = ut["variability"]["face_angle"]
    assert face["n"] == 7 and face["mishit_excluded"] == 1
    assert face["mean"] == pytest.approx(4.2571, abs=1e-3)


def test_findingsにidが付く(an):
    assert all("id" in f for f in an["findings"])
    assert "club:4 Hybrid:mishit_candidates" in {f["id"] for f in an["findings"]}


def test_ばらつきの主因は単回帰のR2で出す(an):
    f = next(x for x in an["findings"] if x["id"] == "group:アイアン（まとめ）:dispersion_driver:face_angle")
    assert f["evidence"]["r2_single"] == pytest.approx(0.97, abs=0.01)


def test_bootstrapはベクトル化して同じ入力に同じ答え():
    v = [1.0, 2.0, 3.5, -0.5, 2.2, 4.1]
    a, b = stats.bootstrap_mean_ci(v), stats.bootstrap_mean_ci(v)
    assert a == b and a[0] < sum(v) / len(v) < a[1]
    t = time.perf_counter()
    for _ in range(40):
        stats.bootstrap_mean_ci(list(range(26)))
    assert (time.perf_counter() - t) / 40 < 0.05  # 1範囲あたり数十ms が目標


# ---------------------------------------------------------------- アイアン（まとめ・26球）


def test_アイアンの一番多い枠と原因の内訳(an):
    t = _group(an)["profile"]["tendency"]
    assert t["top_cell"] == "push|right"
    cell = next(c for c in t["cells"] if c["key"] == "push|right")
    assert cell["n"] == 12 and t["n"] == 26
    assert {b["cause"]: b["n"] for b in cell["breakdown"]} == {"extreme_strike": 5, "face_to_path": 7}
    assert t["start"] == {"push": 13, "straight": 11, "pull": 2, "unknown": 0}
    assert (t["curve"]["measured"], t["curve"]["curved"], t["curve"]["right"], t["curve"]["fade"], t["curve"]["slice"]) == (23, 17, 14, 5, 9)


def test_アイアンのL1(an):
    l1 = _group(an)["profile"]["l1"]
    assert l1["face_angle"]["bias"] == "right" and l1["face_angle"]["n"] == 19
    assert l1["club_path"]["spread"] == "stable" and l1["club_path"]["bias"] is None
    assert l1["impact_offset"]["bias"] == "heel"
    lo, hi = l1["face_angle"]["ci95"]
    assert 0 < lo < l1["face_angle"]["mean"] < hi


def test_アイアンの欠け(an):
    cov = _group(an)["profile"]["coverage"]
    assert (cov["face"]["n"], cov["n"], cov["face"]["missing"], cov["face"]["missing_extreme"]) == (19, 26, 7, 4)
    assert cov["extreme"]["n"] == 5 and cov["extreme"]["with_path"] == 5
    assert sorted(cov["extreme"]["seqs"]) == [5, 13, 18, 20, 22]


def test_アイアンのミスの内訳(an, shots):
    p = _group(an)["profile"]
    mb = p["miss_budget"]
    by = {b["cause"]: b for b in mb["by_cause"]}
    assert set(by) == {"extreme_only", "extreme_and_face", "face_to_path", "unknown"}
    assert (by["extreme_only"]["n"], round(by["extreme_only"]["excess_m"], 1)) == (4, 105.3)
    assert (by["extreme_and_face"]["n"], round(by["extreme_and_face"]["excess_m"], 1), by["extreme_and_face"]["seqs"]) == (1, 32.4, [18])
    assert (by["face_to_path"]["n"], round(by["face_to_path"]["excess_m"], 1)) == (10, 124.4)
    assert (by["face_to_path"]["no_strike"]["n"], round(by["face_to_path"]["no_strike"]["excess_m"], 1)) == (3, 56.2)
    assert (by["unknown"]["n"], round(by["unknown"]["excess_m"], 1)) == (2, 8.5)
    assert round(mb["total_m"], 1) == 270.7
    # 合計は全部の球のはみ出しの和と一致する
    assert mb["total_m"] == pytest.approx(sum(x["excess_m"] for x in mb["per_shot"]))
    assert mb["total_m"] == pytest.approx(sum(b["excess_m"] for b in mb["by_cause"]))
    # 件数ははみ出し0の球を数えない（曲がりの原因がフェース・トゥ・パスの球は12球あるが、はみ出したのは10球）
    assert mb["n_over"] == 17 == sum(b["n"] for b in mb["by_cause"])
    assert [round(x, 1) for x in mb["strike_range_m"]] == [105.3, 137.7]


def test_アイアンの感度と典型の1球とGood(an):
    p = _group(an)["profile"]
    assert p["sensitivity"]["r2_single"]["face_angle"] == pytest.approx(0.97, abs=0.005)
    assert p["sensitivity"]["r2_single"]["club_path"] == pytest.approx(0.21, abs=0.005)
    assert p["sensitivity"]["coef"]["face_angle"] == pytest.approx(0.023, abs=0.001)
    assert p["representative"]["seq"] == 2 and p["representative"]["cell"] == "push|right"
    assert p["good_reference"]["lenient"] == [9, 10]
    assert sorted(p["good_reference"]["good_seqs"]) == [3, 9, 10, 23]


def test_アイアンの帯(an):
    iron = an["bands"]["iron"]
    assert iron["k"] == pytest.approx(0.314, abs=0.01) and iron["k_source"] == "own" and iron["n_fit"] == 23
    assert iron["resid_sd"] == pytest.approx(2.1, abs=0.05) and iron["n_resid"] == 19 and iron["usable"]
    b = _group(an)["profile"]["band"]
    assert (b["counted"], b["in"], b["out_right"], b["out_left"]) == (18, 7, 9, 2)
    assert b["in_landed"] == 7 and b["out_landed"] == 1
    assert b["in_side_sd_m"] == pytest.approx(3.6, abs=0.05)
    st18 = next(x for x in b["statuses"] if x["seq"] == 18)
    assert st18["counted"] is False and st18["reason"] == "extreme_strike"
    assert b["ideal_type"]["kind"] == "trim" and b["ideal_type"]["side"] == "right" and b["ideal_type"]["shown"]


def test_打ち出しの予測との差のSD(an):
    lr = _group(an)["profile"]["launch_residual_sd"]
    assert lr["value"] == pytest.approx(0.30, abs=0.01) and lr["n"] == 19 and lr["below_info"]
    assert any(f["kind"] == "face_maybe_computed" for f in an["findings"])


# ---------------------------------------------------------------- 4番UT（8球・候補 #30）


def test_4UTは候補を除くとフェースが右に偏る_含めると変わる(an, shots):
    p = _club(an, "4 Hybrid")["profile"]
    assert p["mishit_seqs"] == [30]
    assert p["l1"]["face_angle"]["bias"] == "right" and p["l1"]["face_angle"]["n"] == 7
    all_face = [s["metrics"]["face_angle"] for s in shots if s["club"] == "4 Hybrid"]
    assert len(all_face) == 8
    assert metric_summary(all_face, "face_angle")["bias"] is None  # #30 を入れると結論が変わる


def test_4UTはまとめたkで帯を判定し型の文を出さない(an, rep):
    h = an["bands"]["hybrid"]
    assert h["k_source"] == "pooled" and h["k"] == pytest.approx(0.375, abs=0.01)
    assert h["pooled"]["n"] == 30 and h["pooled"]["categories"] == ["hybrid", "iron"]
    b = _club(an, "4 Hybrid")["profile"]["band"]
    assert (b["in"], b["counted"]) == (2, 7)
    assert b["ideal_type"]["shown"] is False
    ids = {c["id"] for c in _claims(_scope(rep, "club:4 Hybrid"))}
    assert "club:4 Hybrid/ideal.type" not in ids and "club:4 Hybrid/ideal.type_small" in ids


# ---------------------------------------------------------------- 5番ウッド（23球・候補4球）


def test_5番ウッドの型(an):
    p = _club(an, "5 Wood")["profile"]
    assert p["mishit_seqs"] == [39, 45, 46, 51]
    assert p["l1"]["club_path"]["bias"] == "right"
    assert p["l1"]["face_angle"]["bias"] is None and p["l1"]["impact_offset"]["bias"] is None
    assert p["sensitivity"]["r2_single"]["face_angle"] == pytest.approx(0.86, abs=0.005)


def test_5番ウッドは式が使えず_まとめたkにも入れない(an):
    w = an["bands"]["wood"]
    assert w["usable"] is False and w["reason"] == "residual_too_large"
    assert w["resid_sd_own"] == pytest.approx(12.7, abs=0.05) and w["n_resid"] == 18
    assert "wood" not in w["pooled"]["categories"]
    assert _club(an, "5 Wood")["profile"]["band"]["used"] is False


# ---------------------------------------------------------------- cross_club


def test_クラブをまたぐ傾向(an, rep):
    entries = an["cross_club"]["entries"]

    def find(metric, kind):
        return [e for e in entries if e["metric"] == metric and e["kind"] == kind]

    heel = find("impact_offset", "common")
    assert len(heel) == 1 and heel[0]["direction"] == "heel"
    assert {u["scope_id"] for u in heel[0]["units"]} == {"group:iron", "club:4 Hybrid"}
    face = find("face_angle", "common")
    assert len(face) == 1 and face[0]["direction"] == "right"
    assert {u["scope_id"] for u in face[0]["units"]} == {"group:iron", "club:4 Hybrid"}
    unclear = find("impact_offset", "same_direction_unclear")
    assert [u["scope_id"] for u in unclear[0]["units"]] == ["club:5 Wood"]
    diff = find("club_path", "different_type")
    assert [u["scope_id"] for u in diff[0]["units"]] == ["club:5 Wood"]
    ut = next(u for u in heel[0]["units"] if u["scope_id"] == "club:4 Hybrid")
    assert ut["center_inside"] is True and ut["n"] == 7


def test_だけとは言わない_4UTをヒール寄りの当たりと呼ばない(rep):
    # ⑤（クラブをまたぐ傾向）で「〜だけ」と言わない。ほかで区間が0をまたぐのは、無いことの証拠ではない
    for c in rep["cross_club"]["claims"]:
        assert "だけ" not in c["text"], c["id"]
    ut_text = " ".join(c["text"] for c in _claims(_scope(rep, "club:4 Hybrid")))
    assert "ヒール寄りの当たり" not in ut_text
    cross_ut = [c["text"] for c in rep["cross_club"]["claims"] if "4 Hybrid" in c["text"]]
    assert cross_ut and all("ヒール寄りの当たり" not in t for t in cross_ut)
    assert any("芯の範囲の中で、ややヒール側" in t for t in cross_ut)


# ---------------------------------------------------------------- candidates


def _cands(rep, sid):
    return _scope(rep, sid)["candidates"]


def test_アイアンの候補(rep):
    c = _cands(rep, "group:iron")
    first, second = c["candidates"][0], c["candidates"][1]
    assert c["now"] == "strike_heel" and first["id"] == "strike_heel"
    assert first["why_first"]["gates"] == ["G1"] and not first["blocked_by"]
    assert c["gates"]["G1"]["hit"] and c["gates"]["G1"]["extreme"] == 5 and c["gates"]["G1"]["of"] == 26
    assert c["gates"]["G2"]["hit"] is False
    assert second["id"] == "face" and second["issue_names"] == ["curve_var", "start_right"]
    assert second["lever"] == "face" and second["kpi"] == {"metric": "face_angle", "goal": "decrease"}
    cv = next(i for i in second["issues"] if i["issue"] == "curve_var")
    assert cv["borderline"] is True and cv["alt_issue"] == "curve_right"
    assert [b["gate"] for b in second["blocked_by"]] == ["G1"]


def test_アイアンの番手は9番_仮説は9番の値(rep):
    c = _cands(rep, "group:iron")
    first = c["candidates"][0]
    assert first["club"]["club"] == "9 Iron" and first["club"]["measured"] == 8 and first["club"]["heel_extreme"] == 3
    # 組み方は番手1本で打つので、番手のばらつき（9番の打点 SD 11.1mm）で出す。群（11.4mm）なら16球
    assert first["design_scope"] == "club" and first["design"]["n_per_condition"] == 15 and first["design"]["per_block"] == 8
    assert first["design_group"]["n_per_condition"] == 16 and first["design_group"]["per_block"] == 8
    scope = _scope(rep, "group:iron")
    hyp = next(x for x in _claims(scope) if x["id"] == "group:iron/now.hypothesis")
    assert hyp["text"].startswith("9 Ironで") and "仮説:" in hyp["text"] and "8球" in hyp["text"] and "3球" in hyp["text"]
    assert all(scope["facts"][f]["scope_id"] == "club:9 Iron" for f in hyp["facts"])
    # 9番は群に畳まれるが、プランの番手なので③の数字を足す
    nine = _scope(rep, "club:9 Iron")
    assert nine["kind"] == "folded" and [s["id"] for s in nine["sections"]] == ["s1", "s3", "s8"]


def test_5番ウッドの候補はG2でぎりぎり(rep):
    c = _cands(rep, "club:5 Wood")
    assert c["now"] == "strike_scatter"
    assert c["candidates"][0]["why_first"]["gates"] == ["G2"]
    assert c["gates"]["G2"]["hit"] and c["gates"]["G2"]["borderline"]
    assert c["next"] == "face" and c["candidates"][1]["kpi"]["goal"] == "reduce_sd"
    assert c["reference"] and c["reference"][0]["thin"] == 3 and c["reference"][0]["of"] == 23
    text = " ".join(x["text"] for x in _claims(_scope(rep, "club:5 Wood")) if x["section"] == "s7")
    assert "フェースから始めてもかまいません" in text


def test_フェースがプラスでもパスがもっと右なら左への曲がりに入る():
    shots = []
    for i in range(1, 13):
        face, path = 1.0 + 0.1 * (i % 3), 3.5 + 0.1 * (i % 2)
        shots.append(
            {
                "id": i, "seq": i, "club": "7 Iron", "club_category": "iron", "excluded": False, "good_override": None, "estimated": [],
                "metrics": {"face_angle": face, "club_path": path, "face_to_path": face - path, "side": -8.0, "carry": 150.0, "launch_direction": 1.4, "spin_axis": -5.0},
                "decomposition": {"start_line": "straight", "curve": "draw", "curve_cause": "face_to_path", "contact": "unknown"},
            }
        )
    an = analyze_session(shots)
    p = an["clubs"][0]["profile"]
    raised = {i["issue"] for i in plan.issues(p, shots)}
    assert "curve_left" in raised and not any(x.startswith("start_") for x in raised)
    assert plan.assign_shot(shots[0], raised) == "curve_left"


def test_打点が取れた球が60パーセント未満ならG0で計測プランになる(shots):
    s2 = copy.deepcopy(shots)
    for s in s2:
        if s["club_category"] == "iron" and s["seq"] % 2 == 0:
            s["metrics"].pop("impact_offset", None)
    c = _cands(build_report(s2), "group:iron")
    strike = next(x for x in c["candidates"] if x["id"] == "strike_heel")
    assert strike["kind"] == "measure" and strike["gate0"]["share"] < 0.6
    assert strike["measure"] == "measure.impact_offset"


# ---------------------------------------------------------------- 定型文


def test_全部の主張がfactsを持ち_factは範囲を持つ(rep):
    for s in rep["scopes"]:
        for c in _claims(s):
            for f in c["facts"]:
                fact = s["facts"][f]
                assert fact["scope_id"] and fact["n"] is not None, f
            if c["layer"] in ("L0", "L1"):
                assert c["facts"] or c["finding_ids"], c["id"]
    for c in rep["cross_club"]["claims"]:
        assert c["facts"]


def test_範囲の無い値は差し込めない():
    fs = Facts()
    with pytest.raises(ClaimError):
        fs.add("x", 3.3, "deg_lat", scope_id="", n=19)
    with pytest.raises(ClaimError):
        fs.add("x", 3.3, "deg_lat", scope_id="group:iron", n=None)
    with pytest.raises(ClaimError):
        render("フェースは {v:nope}", fs, "R")
    with pytest.raises(ClaimError):
        build_claim(Claim(id="t", section="s3", layer="L1", template="パスは {v:nope}"), fs, "R")


def test_L1までの主張に体の言葉が出ない(rep):
    for c in _all_claims(rep):
        if c["layer"] in ("L0", "L1"):
            assert not config.layer_terms_in(c["text"]), (c["id"], c["text"])
    fs = Facts()
    with pytest.raises(ClaimError):
        build_claim(Claim(id="t", section="s3", layer="L1", template="腰が回っていません"), fs, "R")


def test_左打ちで向きの語が入れ替わり_トゥとヒールは入れ替わらない(shots):
    r = build_report(shots, "L")
    iron_r = build_report(shots, "R")
    text_l = " ".join(c["text"] for c in _claims(_scope(r, "group:iron")))
    text_r = " ".join(c["text"] for c in _claims(_scope(iron_r, "group:iron")))
    assert "左に出て左に曲がる" in text_l and "右に出て右に曲がる" in text_r
    assert "中央値は左 9.9m" in text_l and "中央値は右 9.9m" in text_r
    assert "ヒール 21mm" in text_l and "ヒール 21mm" in text_r  # 打点は反転しない
    assert "トゥ" not in re.sub(r"トゥ・パス|トゥ側は|トゥ側に", "", text_l)
    fs = Facts()
    fs.add("a", 3.3, "deg_lat", scope_id="group:iron", n=19)
    fs.add("b", -0.021, "strike", scope_id="group:iron", n=17)
    assert render("{dir:right}{lead}{trail} {v:a} {v:b}", fs, "R") == "右左右 +3.3° ヒール 21mm"
    assert render("{dir:right}{lead}{trail} {v:a} {v:b}", fs, "L") == "左右左 −3.3° ヒール 21mm"


def test_見本の文が出る(rep):
    text = " ".join(c["text"] for c in _claims(_scope(rep, "group:iron")))
    assert "一番多いのは右に出て右に曲がる球で、26球中12球です。" in text
    assert "105.3m〜137.7m（全体 270.7m）" in text
    assert "19球中10球が +2° より右を向いて当たりました" in text
    assert "左右のずれのばらつきの 97% を説明できます（19球・単回帰。パスだけなら 21%）" in text
    assert "典型の1球は #2 です" in text
    assert "7球とも狙いの幅に落ちました" in text
    assert "真ん中の球は今のままでよく、右へ外れた球を減らすのが近道です" in text
    assert "今は触らないこと: パス" in text


def test_まだ言えないことは空にしない(rep):
    for s in rep["scopes"]:
        s8 = next(x for x in s["sections"] if x["id"] == "s8")
        assert s8["claims"]


def test_帯の窓の文はGoが埋める差し込み口を持つ(rep):
    iron = _scope(rep, "group:iron")
    w = next(c for c in _claims(iron) if c["id"] == "group:iron/ideal.window")
    req = iron["band_request"]
    assert w["needs_band"] is True and "{band:lo}" in w["text"] and "{band:hi}" in w["text"]
    assert req["carry"] == pytest.approx(118.9) and req["spin_loft"] == pytest.approx(24.3)
    assert req["lim"] == pytest.approx(5.945) and req["k"] == pytest.approx(0.314, abs=0.01)
    assert iron["figures"]["F3"]["band_ref"] == req["id"] and iron["figures"]["F4"]["window_center"]["band_ref"] == req["id"]
    # Go は "band_request" というキーを持つオブジェクトを全部たどって band_shape を足すので、ほかの場所に同じキーを置かない
    blob = json.dumps({k: v for k, v in iron.items() if k != "band_request"}, ensure_ascii=False)
    assert '"band_request"' not in blob
    assert req["window_path"] == pytest.approx(0.4)
    assert req["paths"][0] <= -2.0 and req["paths"][-1] >= 2.0
    assert _scope(rep, "club:5 Wood")["band_request"] is None


# ---------------------------------------------------------------- 範囲の出し方と図


def test_本体の範囲と畳む範囲(rep):
    kinds = {s["scope_id"]: s["kind"] for s in rep["scopes"]}
    assert kinds == {
        "group:iron": "main", "club:5 Wood": "main", "club:4 Hybrid": "main",
        "club:9 Iron": "folded", "club:6 Iron": "folded", "club:8 Iron": "folded", "club:7 Iron": "short",
    }
    assert [s["id"] for s in _scope(rep, "group:iron")["sections"]] == ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"]
    assert [s["id"] for s in _scope(rep, "club:7 Iron")["sections"]] == ["s1", "s8"]
    assert _scope(rep, "club:7 Iron")["figures"] == {} and _scope(rep, "club:6 Iron")["figures"] == {}


def test_図の中身(rep):
    figs = _scope(rep, "group:iron")["figures"]
    assert set(figs) == {"F1", "F2", "F3", "F4", "F5", "F6", "F7", "C1"}  # C1 は要点の「理想との差」の比べる図
    f1 = figs["F1"]
    assert len(f1["points"]) == 26 and sum(1 for p in f1["points"] if p["mark"] == "in") == 7
    assert f1["ellipse"]["legend"].endswith("確率の範囲ではありません")
    f3 = figs["F3"]
    assert len(f3["points"]) == 19 and (f3["not_shown"]["n"], f3["not_shown"]["extreme"]) == (7, 4)
    assert f3["notes"][0] == "この図にない球: 7（うちネック寄り 4）"
    assert any(p["seq"] == 18 and p["warn"] for p in f3["points"])
    f5 = figs["F5"]
    assert f5["heel_on"] == "right" and len(f5["points"]) == 17 and f5["missing"] == 9
    assert all(p["offset_mm"] < 0 for p in f5["points"])
    assert [b["cause"] for b in figs["F6"]["bars"]] == ["extreme_only", "extreme_and_face", "face_to_path", "unknown"]
    assert [r["scope_id"] for r in figs["F7"]["rows"]] == ["group:iron", "club:5 Wood", "club:4 Hybrid"]
    assert [c["key"] for c in figs["F2"]["cells"] if c["top"]] == ["push|right"]
    assert _scope(rep, "club:5 Wood")["figures"]["F3"]["band_used"] is False
    assert "desc" in figs["F3"]


def test_レポートは速い(shots):
    t = time.perf_counter()
    build_report(shots)
    assert time.perf_counter() - t < 2.0


def test_HTTPの入口(shots):
    c = TestClient(app)
    r = c.post("/v1/report", json={"shots": shots, "handedness": "R"})
    assert r.status_code == 200
    body = r.json()
    assert body["report_version"] == "report/0.1" and len(body["scopes"]) >= 2
    assert len(body["facts_hash"]) == 64
    assert c.post("/v1/report", json={"shots": shots, "handedness": "X"}).status_code == 400
    assert c.post("/v1/report", json={"shots": []}).status_code == 200


# ---------------------------------------------------------------- レビューの指摘の直し（2026-09-29）


def test_候補の件数ははみ出しが0より大きい球だけ(rep):
    # §5.3: 件数ははみ出しが0より大きい球だけ（④の内訳・F6 と同じ数え方）
    for sid in ("group:iron", "club:5 Wood", "club:4 Hybrid"):
        sc = _scope(rep, sid)
        c = sc["candidates"]
        n_over = next(x for x in [sc] if x)["figures"]["F6"]["n_over"]
        assert sum(x["n_shots"] for x in c["candidates"]) <= n_over, sid
    face = next(x for x in _cands(rep, "group:iron")["candidates"] if x["id"] == "face")
    ftp = next(b for b in _scope(rep, "group:iron")["figures"]["F6"]["bars"] if b["cause"] == "face_to_path")
    assert face["n_members"] == 12 and face["n_shots"] == 10 == ftp["n"]
    text = next(x["text"] for x in _claims(_scope(rep, "group:iron")) if x["id"] == "group:iron/next.next")
    assert "10球・124.4m" in text and "12球" not in text


def test_9番の組み方は9番のばらつきで_1ブロックは12球まで(rep):
    scope = _scope(rep, "group:iron")
    face = next(x for x in scope["candidates"]["candidates"] if x["id"] == "face")
    d = face["design"]
    # 9番だけのフェースの SD 7.8°（7球）→ 条件ごとに118球。群の SD 5.2° なら53球（それを9番の名前で書かない。R2）
    assert face["design_scope"] == "club" and d["n_per_condition"] == 118 and face["design_group"]["n_per_condition"] == 53
    assert d["per_block"] == 12 and d["one_session"] is False and d["sessions"] == 5
    design = next(x for x in _claims(scope) if x["id"] == "group:iron/next.design")
    sd_fact = next(f for f in design["facts"] if f.endswith(".design.sd"))
    assert scope["facts"][sd_fact]["scope_id"] == "club:9 Iron" and scope["facts"][sd_fact]["n"] == 7
    assert "±7.8°・7球" in design["text"] and "約118球" in design["text"] and "各12球" in design["text"]
    for s_ in rep["scopes"]:
        for c in (s_["candidates"] or {}).get("candidates", []):
            if c.get("design"):
                assert c["design"]["per_block"] <= config.BLOCK_N_MAX
                assert all(b["n"] <= config.BLOCK_N_MAX for b in c["design"]["template"])


def test_仮説は番手だけで偏りが言い切れないならそう書く(rep):
    scope = _scope(rep, "group:iron")
    hyp = next(x for x in _claims(scope) if x["id"] == "group:iron/next.hypothesis")
    assert "0をまたぎ、偏りは言い切れません" in hyp["text"]
    assert "アイアン（まとめ）全体では平均 +3.3°（19球）" in hyp["text"]
    scopes = {scope["facts"][f]["scope_id"] for f in hyp["facts"]}
    assert scopes == {"club:9 Iron", "group:iron"}
    # ヒールの仮説の向きは決め打ちにせず、極端な打点の球が落ちた側から入れる（左打ちでも同じ球なら向きが入れ替わる）
    now = next(x for x in _claims(scope) if x["id"] == "group:iron/now.hypothesis")
    assert "右へ大きく外れる球が減る" in now["text"]


def test_5番ウッドの狙いの幅と式の閾値を取り違えない(rep):
    t = next(x["text"] for x in _claims(_scope(rep, "club:5 Wood")) if x["id"] == "club:5 Wood/ideal.no_band")
    assert "狙いの幅 ±11.4m の半分の 5.7m" in t
    ids = [x["id"] for x in _claims(_scope(rep, "club:5 Wood"))]
    assert "club:5 Wood/ideal.good_ref" not in ids  # 帯を使わないので「帯に入った球 0球」と書かない
    s6 = next(x for x in _scope(rep, "club:5 Wood")["sections"] if x["id"] == "s6")
    assert s6["figures"] == []  # 帯の無い図を⑥で繰り返さない
    g = next(x["text"] for x in _claims(_scope(rep, "club:5 Wood")) if x["id"] == "club:5 Wood/ideal.strike_goal")
    assert "いまも芯から 10mm 以内" in g and "→ 目標は芯から" not in g


def test_次の目標は件数で書き_今日の値から作らない(rep):
    iron = _scope(rep, "group:iron")
    st = next(x["text"] for x in _claims(iron) if x["id"] == "group:iron/ideal.stage")
    assert "18球中7球" in st and "%" not in st and "段" not in st
    assert "12球あたり3球多く" in st
    # 10球未満（4番UT の7球）では出さない
    assert "club:4 Hybrid/ideal.stage" not in [x["id"] for x in _claims(_scope(rep, "club:4 Hybrid"))]
    from golf_analysis.profile import stage_goal
    g = stage_goal({"used": True, "counted": 12, "in": 11})
    assert g["reached"] is True and "next_share" not in g  # 今日の値より低い目標を作らない


def test_帯に入った球が落ちていないのに合っていたと書かない(rep):
    ut = {x["id"].split("/", 1)[1]: x["text"] for x in _claims(_scope(rep, "club:4 Hybrid"))}
    assert "合っていた" not in ut["ideal.today_caveat"] and "2球中1球しか" in ut["ideal.today_caveat"]
    assert "左右のばらつき" not in ut["ideal.today"]  # 2球の SD は出さない
    assert "パスが左の球ではフェースも少し左" in ut["ideal.window"]
    iron = {x["id"].split("/", 1)[1]: x["text"] for x in _claims(_scope(rep, "group:iron"))}
    assert "合っていた" in iron["ideal.today_caveat"] and "7球とも" in iron["ideal.today"]


def test_trim型のときは動かさないものと進む条件を出す(rep):
    iron = _scope(rep, "group:iron")
    face = next(x for x in iron["candidates"]["candidates"] if x["id"] == "face")
    kinds = {g["kind"]: g for g in face["guards"]}
    assert kinds["landed_opposite"]["side"] == "left" and kinds["face_median_window"]["edge"] == "lo"
    g = next(x for x in _claims(iron) if x["id"] == "group:iron/next.guards")
    assert g["needs_band"] and "{band:lo} を下回らない" in g["text"]
    adv = {x["id"].split("/", 1)[1]: x["text"] for x in _claims(iron) if x["id"].endswith(".advance")}
    assert "芯から 10mm 以内" in adv["now.advance"] and "実際に帯へ落ちた球が減っていない" in adv["next.advance"]
    # 左打ちは表示の側で窓の端が入れ替わる
    left = build_report(json.load(open(DATA, encoding="utf-8")), "L")
    gl = next(x for x in _claims(_scope(left, "group:iron")) if x["id"] == "group:iron/next.guards")
    assert "{band:hi} を上回らない" in gl["text"]


def test_量の言葉は件数から選ぶ():
    fs = Facts()
    fs.add("k", 7, "count", scope_id="s", n=7)
    fs.add("n", 7, "count", scope_id="s", n=7)
    fs.add("k4", 5, "count", scope_id="s", n=7)
    assert render("{n:n}球{qs:k;n}落ちた", fs, "R") == "7球とも落ちた"
    assert render("{q:k;n}が入った", fs, "R") == "全部が入った"
    with pytest.raises(ClaimError):
        render("{q:k4;n}が入った", fs, "R")  # 5/7 に合う量の言葉は無い
    with pytest.raises(ClaimError):
        build_claim(Claim(id="t", section="s3", layer="L1", template="{q:k;n}が入った", qual="most"), fs, "R")
    assert build_claim(Claim(id="t", section="s3", layer="L1", template="{q:k;n}が入った"), fs, "R")["qual"] == "all"


def test_候補の数はどの節でも総数で書く(rep):
    wood = " ".join(x["text"] for x in _claims(_scope(rep, "club:5 Wood")))
    assert "候補 4球" in wood and "候補 3球を除" not in wood and "候補 1球 #46" not in wood
    miss = next(x["text"] for x in _claims(_scope(rep, "club:5 Wood")) if x["id"] == "club:5 Wood/unknown.strike_missing")
    assert "8球あります" in miss and "うちミスヒットの候補 3球" in miss


def test_一番多い枠が同数なら言い切らない(rep, shots):
    p = _scope(rep, "club:5 Wood")
    t1 = next(x["text"] for x in _claims(p) if x["id"] == "club:5 Wood/summary.top_cell")
    assert "1つに決まりません" in t1 and "6球ずつ" in t1 and "一番多いのは" not in t1
    left = build_report(shots, "L")
    t1l = next(x["text"] for x in _claims(_scope(left, "club:5 Wood")) if x["id"] == "club:5 Wood/summary.top_cell")
    assert t1l == t1.replace("左", "\0").replace("右", "左").replace("\0", "右")  # 右打ちと左打ちで結論が入れ替わらない
    an = analyze_session(shots)
    assert _club(an, "5 Wood")["profile"]["representative"] is None
    assert [c["key"] for c in p["figures"]["F2"]["cells"] if c["top"]] == sorted(_club(an, "5 Wood")["profile"]["tendency"]["top_tie"], key=lambda k: [c["key"] for c in p["figures"]["F2"]["cells"]].index(k))


def test_1本だけなら型が違うと言わない(shots):
    only = [s for s in shots if s["club"] == "5 Wood"]
    r = build_report(only, "R")
    assert not [e for e in r["cross_club"]["entries"] if e["kind"] == "different_type"]
    assert "型が違います" not in json.dumps(r, ensure_ascii=False)


def test_フェースが2球だけの範囲があってもほかの範囲は消えない(shots):
    s2 = copy.deepcopy([s for s in shots if s["club"] in ("9 Iron", "5 Wood")])
    nine = [s for s in s2 if s["club"] == "9 Iron"][:6]
    for s in nine[2:]:
        s["metrics"].pop("face_angle", None)
    r = build_report([s for s in s2 if s["club"] == "5 Wood"] + nine, "R")
    for sc in r["scopes"]:
        assert sc["sections"][0]["id"] != "error", (sc["scope_id"], sc["sections"][0]["lead"])


def test_値が全部同じ指標があっても範囲の解説は消えない(shots):
    s2 = copy.deepcopy(shots)
    for s in s2:
        if s["club_category"] == "iron":
            s["metrics"]["club_path"] = 1.0
    r = build_report(s2, "R")
    iron = _scope(r, "group:iron")
    assert iron["sections"][0]["id"] != "error"
    sens = next(x["text"] for x in _claims(iron) if x["id"] == "group:iron/impact.sensitivity")
    assert "パスだけの説明の割合は出せません" in sens


def test_帯を計算しない理由を球不足にまとめない(shots):
    s2 = copy.deepcopy(shots)
    for s in s2:
        if s["club"] == "4 Hybrid":
            s["metrics"].pop("carry", None)
    an = analyze_session(s2)
    assert an["bands"]["hybrid"]["reason"] == "no_carry"
    r = build_report(s2, "R")
    ut = next(x["text"] for x in _claims(_scope(r, "club:4 Hybrid")) if x["id"] == "club:4 Hybrid/ideal.no_band")
    assert "キャリーが測れていない" in ut and "球が足りない" not in ut
    assert "キャリーが測れていない" in _scope(r, "club:4 Hybrid")["figures"]["F3"]["band_reason"]


def test_測れていない打ち出しを0球と並べない(shots):
    s2 = copy.deepcopy([s for s in shots if s["club"] == "4 Hybrid"])
    for s in s2:
        s["decomposition"]["start_line"] = "unknown"
    r = build_report(s2, "R")
    t = next(x["text"] for x in _claims(_scope(r, "club:4 Hybrid")) if x["id"].endswith("flight.counts"))
    assert "8球とも測れていません" in t and "0球" not in t


def test_共通の測っていない項目は末尾に1回だけ(rep):
    ids = [c["id"] for c in rep["session_unknowns"]]
    assert ids == ["session/unknown.no_impact_height", "session/unknown.no_ball_speed", "session/unknown.no_low_point"]
    for s_ in rep["scopes"]:
        assert not [c for c in _claims(s_) if "/unknown.no_" in c["id"]], s_["scope_id"]


def test_クラブをまたぐ傾向はその範囲のカードにだけ出す(rep):
    ut = [c["id"] for c in _claims(_scope(rep, "club:4 Hybrid")) if c["section"] == "s5"]
    wood = [c["id"] for c in _claims(_scope(rep, "club:5 Wood")) if c["section"] == "s5"]
    assert all("common" in i for i in ut) and len(ut) == 2
    assert all("common" not in i for i in wood) and len(wood) == 3
