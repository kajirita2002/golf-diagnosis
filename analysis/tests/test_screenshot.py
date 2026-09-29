"""スクリーンショット読み取り。本物の API は呼ばない（偽の Claude を渡す）。

答えの元は testdata/real/2026-09-17（人が書き写し、Average の行で確かめた実データ）。
"""

import base64
import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from golf_analysis import screenshot as ss
from golf_analysis.app import app

REAL = os.path.join(os.path.dirname(__file__), "..", "..", "testdata", "real", "2026-09-17")
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def table_from_tsv(name: str, club: str) -> dict:
    rows = [r.split("\t") for r in open(os.path.join(REAL, name), encoding="utf-8").read().strip("\n").split("\n")]
    body = [r for r in rows[2:] if r[0] not in ("Average", "Consistency")]
    avg = next((r for r in rows if r[0] == "Average"), None)
    return {
        "club": club,
        "columns": rows[0][1:],
        "units": rows[1][1:],
        "row_labels": [r[0] for r in body],
        "rows": [r[1:] for r in body],
        "average": avg[1:] if avg else None,
        "consistency": None,
    }


class FakeClient:
    def __init__(self, payload, stop_reason="end_turn"):
        self.calls = []
        text = json.dumps(payload) if not isinstance(payload, str) else payload
        msg = SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)],
                              usage=SimpleNamespace(input_tokens=3000, output_tokens=1500))

        def create(**kw):
            self.calls.append(kw)
            return msg

        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


def test_実データを読めたら検算が全部通る():
    tables = [table_from_tsv(f + ".tsv", c) for f, c in (("6i", "6Iron"), ("5w", "5Wood"), ("9i", "9Iron"))]
    client = FakeClient({"tables": tables})
    out = ss.read_screenshot(client, PNG, "image/png")
    assert [t["club"] for t in out["tables"]] == ["6Iron", "5Wood", "9Iron"]
    for t in out["tables"]:
        assert t["check"]["ok"], t["check"]["problems"]
        assert all(c["status"] == "ok" for c in t["check"]["columns"])
    # 料金: 3000 × $4/M + 1500 × $20/M
    assert out["usage"]["cost_usd"] == pytest.approx(0.042)


def test_API_に渡す中身():
    client = FakeClient({"tables": []})
    ss.read_screenshot(client, PNG, "image/png")
    kw = client.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    img = kw["messages"][0]["content"][0]
    assert img["type"] == "image" and base64.b64decode(img["source"]["data"]) == PNG


@pytest.mark.parametrize(
    "row,col,bad,column",
    [
        (0, 0, "30.7", "Club Speed"),  # 36.7 → 30.7（6 と 0）
        (0, 3, "40.7", "Side"),        # 40.7R の R は右なので同じ。L を落とす例は下
        (1, 3, "28.3L", "Side"),       # R と L の読み違い
        (4, 8, "3.6", "Spin Axis"),    # 31.6 → 3.6（桁落ち）
    ],
)
def test_読み違いを検算で見つける(row, col, bad, column):
    t = table_from_tsv("6i.tsv", "6Iron")
    before = t["rows"][row][col]
    t["rows"][row][col] = bad
    out = ss.read_screenshot(FakeClient({"tables": [t]}), PNG, "image/png")
    check = out["tables"][0]["check"]
    if bad.rstrip("R") == before.rstrip("R"):
        assert check["ok"]  # 表示上同じ（"40.7" と "40.7R" はどちらも右）
        return
    assert not check["ok"]
    assert [c["column"] for c in check["columns"] if c["status"] == "mismatch"] == [column]


def test_数値でない文字と列のずれを見つける():
    t = table_from_tsv("7i.tsv", "7Iron")
    t["rows"][0][2] = "137,4"
    t["rows"][1] = t["rows"][1][:-1]
    check = ss.read_screenshot(FakeClient({"tables": [t]}), PNG, "image/png")["tables"][0]["check"]
    assert not check["ok"]
    assert any("数値として読めません" in p for p in check["problems"])
    assert any("列の数" in p for p in check["problems"])


def test_Average_が無ければ検算できないと言う():
    t = table_from_tsv("7i.tsv", "7Iron")
    t["average"] = None
    check = ss.read_screenshot(FakeClient({"tables": [t]}), PNG, "image/png")["tables"][0]["check"]
    assert not check["ok"] and any("Average" in p for p in check["problems"])


def test_人が直した_TSV_を再検算できる():
    t = table_from_tsv("6i.tsv", "6Iron")
    t["rows"][0][0] = "30.7"
    tsv = ss.to_tsv(ss.Table(**t))
    assert not ss.verify_tsv(tsv)["ok"]
    assert ss.verify_tsv(tsv.replace("\t30.7\t", "\t36.7\t", 1))["ok"]


def test_断られた_途中で切れた_形が不正():
    with pytest.raises(ss.ExtractError, match="断られ"):
        ss.extract(FakeClient({}, stop_reason="refusal"), PNG, "image/png")
    with pytest.raises(ss.ExtractError, match="切れ"):
        ss.extract(FakeClient({}, stop_reason="max_tokens"), PNG, "image/png")
    with pytest.raises(ss.ExtractError, match="形が不正"):
        ss.extract(FakeClient("not json"), PNG, "image/png")
    with pytest.raises(ss.ExtractError, match="形式"):
        ss.extract(FakeClient({"tables": []}), PNG, "application/pdf")
    with pytest.raises(ss.ExtractError, match="大きすぎ"):
        ss.extract(FakeClient({"tables": []}), b"0" * (ss.MAX_IMAGE_BYTES + 1), "image/png")


def test_HTTP_の入口(tmp_path, monkeypatch):
    fake = tmp_path / "fake.json"
    fake.write_text(json.dumps({"tables": [table_from_tsv("7i.tsv", "7Iron")]}), encoding="utf-8")
    monkeypatch.setenv("SCREENSHOT_FAKE_RESPONSE", str(fake))
    c = TestClient(app)
    r = c.post("/v1/screenshot", json={"media_type": "image/png", "data": base64.b64encode(PNG).decode()})
    assert r.status_code == 200 and r.json()["tables"][0]["check"]["ok"]
    assert c.post("/v1/screenshot", json={"media_type": "image/png", "data": "@@@"}).status_code == 400
    assert c.post("/v1/screenshot", json={"media_type": "text/plain", "data": "AAAA"}).status_code == 422
    v = c.post("/v1/screenshot/verify", json={"tsv": r.json()["tables"][0]["tsv"]})
    assert v.json()["ok"]


def test_キーが無ければ503(monkeypatch):
    # 本物の SDK を通す（SDK 1.x は作るときではなく送るときに落ちるので、偽物で試すと見逃す）
    monkeypatch.delenv("SCREENSHOT_FAKE_RESPONSE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    r = TestClient(app).post("/v1/screenshot", json={"media_type": "image/png", "data": base64.b64encode(PNG).decode()})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]
