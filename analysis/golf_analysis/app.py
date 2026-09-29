"""分析サービスの HTTP 入口。状態を持たない（球は毎回 Go の API から渡される）。

起動: uv run uvicorn golf_analysis.app:app --port 8001
"""

from __future__ import annotations

import base64
import binascii
import os
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import config
from .compare import compare_sessions
from .experiment import VALID_GOALS, evaluate
from .report import build_report
from .screenshot import ExtractError, read_screenshot, verify_tsv
from .session import analyze_session

app = FastAPI(title="golf-analysis", version=config.ENGINE_VERSION, docs_url=None, redoc_url=None, openapi_url=None)


class SessionIn(BaseModel):
    shots: list[dict[str, Any]] = Field(default_factory=list)


class ReportIn(BaseModel):
    """/v1/session と同じ球の並び ＋ 利き手（R / L）。experiments は 1b で使う（いまは読まない）。"""

    shots: list[dict[str, Any]] = Field(default_factory=list)
    handedness: str = "R"
    experiments: list[dict[str, Any]] = Field(default_factory=list)


class ExperimentIn(BaseModel):
    target_metric: str
    goal: str
    blocks: list[dict[str, Any]] = Field(default_factory=list)


class CompareIn(BaseModel):
    a: list[dict[str, Any]] = Field(default_factory=list)
    b: list[dict[str, Any]] = Field(default_factory=list)


class ScreenshotIn(BaseModel):
    media_type: str
    data: str  # base64


class VerifyIn(BaseModel):
    tsv: str


class _FakeClient:
    """テストと画面の確認用。SCREENSHOT_FAKE_RESPONSE のファイルの中身を Claude の答えとして返す。
    本物の API は呼ばない（本番では設定しないこと）。"""

    def __init__(self, path: str):
        text = open(path, encoding="utf-8").read()
        msg = SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=0, output_tokens=0),
        )
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **_: msg))


def _client():
    fake = os.environ.get("SCREENSHOT_FAKE_RESPONSE")
    if fake:
        return _FakeClient(fake)
    # SDK は認証情報が無くても作れてしまい、送るときに TypeError で落ちる（1.x）。
    # 先に見て、利用者に分かる理由で断る。サーバーでは環境変数で渡す約束。
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise HTTPException(503, "Claude の API キーが設定されていません（ANTHROPIC_API_KEY を設定してください）")
    import anthropic

    return anthropic.Anthropic()


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "engine_version": config.ENGINE_VERSION}


@app.post("/v1/session")
def session(body: SessionIn) -> dict:
    return analyze_session(body.shots)


@app.post("/v1/report")
def report(body: ReportIn) -> dict:
    """解説レポート（定型文・図の中身・理想・候補）。docs/DESIGN_coaching.md §6・§10.1。
    帯の形と窓の数字は Go が各範囲の band_request を見て band_shape を足す。"""
    if body.handedness not in ("R", "L"):
        raise HTTPException(400, "handedness は R か L です")
    return build_report(body.shots, body.handedness, body.experiments)


@app.post("/v1/experiment")
def experiment(body: ExperimentIn) -> dict:
    if body.goal not in VALID_GOALS:
        raise HTTPException(400, f"goal {body.goal!r} は使えません")
    return evaluate(body.target_metric, body.goal, body.blocks)


@app.post("/v1/compare")
def compare(body: CompareIn) -> dict:
    return compare_sessions(body.a, body.b)


@app.post("/v1/screenshot")
def screenshot(body: ScreenshotIn) -> dict:
    """スクリーンショットの表を読む。取り込みはしない（人が確認してから取り込む）。"""
    try:
        image = base64.b64decode(body.data, validate=True)
    except (binascii.Error, ValueError) as e:
        raise HTTPException(400, "画像のデータが壊れています") from e
    import anthropic

    try:
        return read_screenshot(_client(), image, body.media_type)
    except ExtractError as e:
        raise HTTPException(422, str(e)) from e
    except anthropic.AuthenticationError as e:
        raise HTTPException(503, "Claude の API キーが無効です") from e
    except anthropic.RateLimitError as e:
        raise HTTPException(503, "Claude の API が混んでいます。少し待ってからもう一度") from e
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"Claude の API が {e.status_code} を返しました") from e
    except anthropic.APIConnectionError as e:
        raise HTTPException(502, "Claude の API に接続できません") from e


@app.post("/v1/screenshot/verify")
def screenshot_verify(body: VerifyIn) -> dict:
    """人が直した TSV をもう一度検算する。"""
    return verify_tsv(body.tsv)
