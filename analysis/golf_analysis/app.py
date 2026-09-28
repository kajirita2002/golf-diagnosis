"""分析サービスの HTTP 入口。状態を持たない（球は毎回 Go の API から渡される）。

起動: uv run uvicorn golf_analysis.app:app --port 8001
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import config
from .compare import compare_sessions
from .experiment import VALID_GOALS, evaluate
from .session import analyze_session

app = FastAPI(title="golf-analysis", version=config.ENGINE_VERSION, docs_url=None, redoc_url=None, openapi_url=None)


class SessionIn(BaseModel):
    shots: list[dict[str, Any]] = Field(default_factory=list)


class ExperimentIn(BaseModel):
    target_metric: str
    goal: str
    blocks: list[dict[str, Any]] = Field(default_factory=list)


class CompareIn(BaseModel):
    a: list[dict[str, Any]] = Field(default_factory=list)
    b: list[dict[str, Any]] = Field(default_factory=list)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "engine_version": config.ENGINE_VERSION}


@app.post("/v1/session")
def session(body: SessionIn) -> dict:
    return analyze_session(body.shots)


@app.post("/v1/experiment")
def experiment(body: ExperimentIn) -> dict:
    if body.goal not in VALID_GOALS:
        raise HTTPException(400, f"goal {body.goal!r} は使えません")
    return evaluate(body.target_metric, body.goal, body.blocks)


@app.post("/v1/compare")
def compare(body: CompareIn) -> dict:
    return compare_sessions(body.a, body.b)
