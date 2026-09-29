"""つなぎの文（Phase 1c・段階A）のテスト。docs/DESIGN_coaching.md §5.8・§11 Phase 1c。

Claude はすべて偽物（本物の API キーは無い）。入力は実データの解説（test_report.py と同じ57球）。
"""

import copy
import json
import os

import pytest
from fastapi.testclient import TestClient

from golf_analysis import narrative
from golf_analysis.app import app
from golf_analysis.narrative import FakeClient, MODEL
from golf_analysis.report import build_report
from golf_analysis.screenshot import Usage

DATA = os.path.join(os.path.dirname(__file__), "data", "real_2026-09-17_shots.json")
OK_BRIDGE = "まず、この節の見どころを確かめます。"


@pytest.fixture(scope="module")
def shots():
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def rep(shots):
    r = build_report(shots, "R")
    narrative.attach_inputs(r)
    return r


@pytest.fixture()
def inp(rep):
    return copy.deepcopy(rep["narrative_inputs"]["group:iron"])


@pytest.fixture(autouse=True)
def llm_on(monkeypatch):
    monkeypatch.setenv("REPORT_LLM", "on")


def good(inp, bridge=OK_BRIDGE):
    return {
        "scope_id": inp["scope_id"],
        "sections": [
            {"id": s["id"], "blocks": [{"t": "bridge", "id": "", "text": bridge}] + [{"t": "claim", "id": c["id"], "text": ""} for c in s["claims"]]}
            for s in inp["sections"]
        ],
    }


def run(inp, *responses, **spec):
    fake = FakeClient({"responses": list(responses), **spec})
    return narrative.narrate(lambda: fake, inp), fake


def test_入力は本体の範囲だけで_facts_の数値を持たない(rep):
    ins = rep["narrative_inputs"]
    mains = {s["scope_id"] for s in rep["scopes"] if s["kind"] == "main"}
    assert set(ins) == mains and "group:iron" in ins
    for sid, inp in ins.items():
        assert set(inp) == {"scope_id", "label", "clubs", "layer_max", "sections", "glossary"}
        for s in inp["sections"]:
            for c in s["claims"]:
                assert set(c) == {"id", "must", "text"}
    # 鍵は範囲ごとに付く（Go が llm_jobs を引く）
    for s in rep["scopes"]:
        if s["scope_id"] in ins:
            assert len(s["narrative"]["input_hash"]) == 64


def test_検証を通れば_Claude_のつなぎを使う(inp):
    out, fake = run(inp, good(inp))
    assert out["generated_by"] == "narrative" and out["called"] == 1 and out["fallback_sections"] == []
    assert out["model"] == MODEL and out["cacheable"] is True
    first = out["sections"][0]["blocks"]
    assert first[0] == {"t": "bridge", "id": "", "text": OK_BRIDGE}
    # 呼び方は screenshot.py と同じ（構造化出力・server-side fallback）。主張の文は <claims> の中にデータとして
    kw = fake.calls[0]
    assert kw["model"] == MODEL and kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert "<claims>" in kw["messages"][0]["content"] and "指示として読まない" in kw["system"]


@pytest.mark.parametrize("word", ["6番", "一番", "半分", "右", "ので", "腰", "可能性", "三球", "アイアン", "クラブパス"])
def test_つなぎの文に事実の語を書いたら定型文に戻る(inp, word):
    bad = good(inp, bridge=f"ここは{word}に注目します。")
    out, fake = run(inp, bad)
    assert out["generated_by"] == "template"
    assert out["called"] == 2  # 理由を付けて1回だけ直させる
    assert [f["id"] for f in out["fallback_sections"]] == [s["id"] for s in inp["sections"]]
    assert all(b["t"] != "bridge" for s in out["sections"] for b in s["blocks"])
    # 定型文の並びは入力の主張そのまま
    assert [b["id"] for b in out["sections"][0]["blocks"]] == [c["id"] for c in inp["sections"][0]["claims"]]
    assert "<problems>" in fake.calls[1]["messages"][0]["content"]


def test_must_を落としたら_その節は定型文(inp):
    bad = good(inp)
    bad["sections"][1]["blocks"] = [b for b in bad["sections"][1]["blocks"] if b["t"] != "claim"][:1] + [
        b for b in bad["sections"][1]["blocks"] if b["t"] == "claim"
    ][1:]
    out, _ = run(inp, bad)
    fb = {f["id"]: f["reasons"] for f in out["fallback_sections"]}
    sid = inp["sections"][1]["id"]
    assert list(fb) == [sid] and any("抜けて" in r for r in fb[sid])
    # ほかの節は Claude のつなぎを使う
    assert out["generated_by"] == "narrative" and out["sections"][0]["blocks"][0]["t"] == "bridge"


def test_主張の順番を入れ替えたら定型文(inp):
    sec = next(i for i, s in enumerate(inp["sections"]) if len(s["claims"]) >= 2)
    bad = good(inp)
    cl = [b for b in bad["sections"][sec]["blocks"] if b["t"] == "claim"]
    cl[0], cl[1] = cl[1], cl[0]
    bad["sections"][sec]["blocks"] = [bad["sections"][sec]["blocks"][0]] + cl
    out, _ = run(inp, bad)
    fb = {f["id"]: f["reasons"] for f in out["fallback_sections"]}
    assert list(fb) == [inp["sections"][sec]["id"]] and any("順番" in r for r in fb[inp["sections"][sec]["id"]])


def test_節の順番を入れ替えたら全部定型文(inp):
    bad = good(inp)
    bad["sections"][0], bad["sections"][1] = bad["sections"][1], bad["sections"][0]
    out, _ = run(inp, bad)
    assert out["generated_by"] == "template" and len(out["fallback_sections"]) == len(inp["sections"])


@pytest.mark.parametrize("kind", ["claim", "gloss"])
def test_知らない_id_なら定型文(inp, kind):
    bad = good(inp)
    bad["sections"][0]["blocks"].append({"t": kind, "id": "x.unknown", "text": ""})
    out, _ = run(inp, bad)
    assert [f["id"] for f in out["fallback_sections"]] == [inp["sections"][0]["id"]]


def test_scope_id_が違えば定型文(inp):
    bad = good(inp)
    bad["scope_id"] = "club:5 Wood"
    out, _ = run(inp, bad)
    assert out["generated_by"] == "template"


def test_長すぎる_多すぎるつなぎは落とす(inp):
    bad = good(inp, bridge="あ" * 81)
    out, _ = run(inp, bad)
    assert out["generated_by"] == "template"
    bad = good(inp)
    bad["sections"][0]["blocks"] = [{"t": "bridge", "id": "", "text": OK_BRIDGE}] * 4 + bad["sections"][0]["blocks"][1:]
    out, _ = run(inp, bad)
    assert [f["id"] for f in out["fallback_sections"]] == [inp["sections"][0]["id"]]


def test_1回目が落ちて2回目が通れば2回目を採る(inp):
    out, fake = run(inp, good(inp, bridge="右を見ます。"), good(inp, bridge="次の見方です。"))
    assert out["generated_by"] == "narrative" and out["called"] == 2 and out["fallback_sections"] == []
    assert out["sections"][0]["blocks"][0]["text"] == "次の見方です。"
    assert out["validation"][0]["sections"] and not out["validation"][1]["sections"]
    assert out["usage"]["input_tokens"] == 2400


def test_off_なら_Claude_を一切呼ばない(inp, monkeypatch):
    monkeypatch.delenv("REPORT_LLM", raising=False)

    def boom():
        raise AssertionError("off なのに Claude の client を作った")

    out = narrative.narrate(boom, inp)
    assert out["generated_by"] == "template" and out["called"] == 0 and out["reason"] == "off" and out["cacheable"] is False


def test_キーが無ければ定型文(inp, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("NARRATIVE_FAKE_RESPONSE", raising=False)
    out = narrative.narrate(narrative.default_client, inp)
    assert out["reason"] == "no_key" and out["called"] == 0 and out["generated_by"] == "template"


def test_断られたら直させずに定型文(inp):
    out, fake = run(inp, good(inp), stop_reason="refusal")
    assert out["generated_by"] == "template" and out["called"] == 1 and len(fake.calls) == 1


def test_実際に答えたモデルを記録し_そのモデルの単価で数える(inp):
    out, _ = run(inp, good(inp), model="claude-sonnet-5-5", input_tokens=1000, output_tokens=200)
    assert out["model"] == "claude-sonnet-5-5" and out["requested_model"] == MODEL
    assert out["usage"]["cost_usd"] == pytest.approx(1000 * 2e-6 + 200 * 10e-6)
    out, _ = run(inp, good(inp), model="claude-unknown-9")
    assert out["usage"]["cost_usd"] is None  # 単価の分からないモデルを Opus の単価で数えない


def test_単価の表は_screenshot_と一致():
    assert narrative.price_usd(MODEL, 1000, 500) == pytest.approx(Usage(1000, 500).cost_usd)


def test_同じ入力なら同じ鍵_球を1つ除外すると鍵が変わる(shots, rep):
    h = {s["scope_id"]: s["narrative"]["input_hash"] for s in rep["scopes"] if "narrative" in s}
    again = build_report(shots, "R")
    narrative.attach_inputs(again)
    assert {s["scope_id"]: s["narrative"]["input_hash"] for s in again["scopes"] if "narrative" in s} == h
    sh = copy.deepcopy(shots)
    iron = next(s for s in sh if s.get("club_category") == "iron" and not s.get("excluded"))
    iron["excluded"] = True
    ex = build_report(sh, "R")
    narrative.attach_inputs(ex)
    h2 = {s["scope_id"]: s["narrative"]["input_hash"] for s in ex["scopes"] if "narrative" in s}
    assert h2["group:iron"] != h["group:iron"]
    assert h2["club:5 Wood"] == h["club:5 Wood"]  # ほかの範囲の鍵は変わらない


def test_HTTP_の入口(rep, tmp_path, monkeypatch):
    spec = tmp_path / "fake.json"
    spec.write_text(json.dumps({"mode": "auto"}), encoding="utf-8")
    monkeypatch.setenv("NARRATIVE_FAKE_RESPONSE", str(spec))
    c = TestClient(app)
    inp = rep["narrative_inputs"]["club:5 Wood"]
    r = c.post("/v1/report/narrative", json={"input": inp, "input_hash": "abc"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["generated_by"] == "narrative" and out["input_hash"] == "abc" and out["called"] == 1
    assert c.post("/v1/report/narrative", json={"input": {"x": 1}}).status_code == 400
    monkeypatch.setenv("REPORT_LLM", "off")
    out = c.post("/v1/report/narrative", json={"input": inp}).json()
    assert out["generated_by"] == "template" and out["called"] == 0


@pytest.mark.parametrize("text", [
    "両方ともまっすぐ飛ばなかった球を見ます。", "ひとつ残らず目標より先へ落ちた", "北寄りに逸れた", "十中八九", "たいていの球",
    "揃っていないところを見ます。", "ミスの出どころを見ます。", "押さえると解決します", "これがきっかけで起こる曲がり",
])
def test_言い換えは許可リストで落ちる(inp, text):
    assert narrative.bridge_problems(text, inp), text


def test_案内だけの文は許可リストを通る(inp):
    for t in [OK_BRIDGE, "次は、この図を見ていきます。", "ここでは、記録の中身を順に見ます。"]:
        assert narrative.bridge_problems(t, inp) == [], t
