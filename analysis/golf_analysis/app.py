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

from . import checkpoints, checkup, coaching, config, drills, focus_test, gist, narrative, video
from .checkpoints import judge as cp_judge
from .checkpoints import measure as cp_measure
from .checkpoints import vision as cp_vision
from .checkpoints import ideal as cp_ideal
from . import video_candidates
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


class NarrativeIn(BaseModel):
    """1範囲ぶんのつなぎの文の依頼。input は /v1/report の narrative_inputs の1つ（Go がそのまま渡す）。"""

    input: dict[str, Any]
    input_hash: str = ""


class ExperimentIn(BaseModel):
    target_metric: str
    goal: str
    blocks: list[dict[str, Any]] = Field(default_factory=list)


class PlanCandidatesIn(BaseModel):
    """候補とドリル。history はこの選手の練習の記録（{drill_id, issue, grade, date}）、plans は前のプラン（{plan_id, issue, drill_id, status, state}）。"""

    shots: list[dict[str, Any]] = Field(default_factory=list)
    handedness: str = "R"
    history: list[dict[str, Any]] = Field(default_factory=list)
    plans: list[dict[str, Any]] = Field(default_factory=list)


class PlanBuildIn(BaseModel):
    """プランを作る（保存は Go）。window は Go の physics.Band の窓（{face_min, face_max, path}・右打ちの座標）。"""

    shots: list[dict[str, Any]] = Field(default_factory=list)
    handedness: str = "R"
    scope_id: str
    candidate_id: str
    drill_id: str | None = None
    club: str | None = None
    window: dict[str, Any] | None = None
    cue: str | None = None
    variant: str = "standard"


class PlanEvaluateIn(BaseModel):
    """1回の練習の評価。plan は plans の行（club・target_metric・goal・params）。

    blocks と reference（1回目の練習の評価）を直に渡すか、Go の形（run・experiment・past_runs）で渡す。
    Go の形なら past_runs の1件目を物差しにし、状態の移り方（progress）も付けて返す。"""

    plan: dict[str, Any]
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    reference: dict[str, Any] | None = None
    experiment: dict[str, Any] | None = None
    run: dict[str, Any] | None = None
    past_runs: list[dict[str, Any]] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)
    continue_after_stop: bool = False
    continue_after_run: int | None = None
    handedness: str = "R"


class PlanProgressIn(BaseModel):
    """進捗と状態。runs は古い順の {run_id, session_id, date, evaluation}。history はほかのプラン（{issue, drill_id, state}）。"""

    plan: dict[str, Any]
    runs: list[dict[str, Any]] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)
    continue_after_stop: bool = False
    handedness: str = "R"


class CompareIn(BaseModel):
    a: list[dict[str, Any]] = Field(default_factory=list)
    b: list[dict[str, Any]] = Field(default_factory=list)


class ScreenshotIn(BaseModel):
    media_type: str
    data: str  # base64


class VerifyIn(BaseModel):
    tsv: str


class CheckpointMeasureIn(BaseModel):
    """1スイングのコマ（姿勢の点・タップ・ボール）。形は checkpoints/measure.py の先頭。保存しない。"""

    swing: dict[str, Any]


class VideoCheckpointsIn(BaseModel):
    """動画の姿勢の時系列（数値だけ）。形は video.py の先頭。保存しない。"""

    view: str
    handedness: str = "R"
    fps: float = 0
    width: float
    height: float
    frames: list[dict[str, Any]] = Field(default_factory=list)
    roi: list[Any] | None = None
    ball_seen: list[dict[str, Any]] = Field(default_factory=list)
    club_taps: list[dict[str, Any]] = Field(default_factory=list)
    # 全部が素振りに見えたとき外さずに返すか（端末がスイングの組に分けて送るときは、端末がまとめてから決めるので false）
    practice_fallback: bool = True


class CheckpointIdealIn(BaseModel):
    """理想との比較（§7.2）: 1スイング（measure と同じ形）と課題の項目1つ。保存しない。"""

    swing: dict[str, Any]
    item_id: str


class CheckpointVisionIn(BaseModel):
    """見た目の項目を Claude に聞く（§6.7）。swings[].frames は P → 長辺 1024px の JPEG（base64）。保存しない。"""

    swings: list[dict[str, Any]] = Field(default_factory=list)
    seed: int | None = None


class CheckpointFocusIn(BaseModel):
    """スイングごとの判定（measure の結果に swing_id を付けたもの）→ 項目ごとの状態・課題。"""

    swings: list[dict[str, Any]] = Field(default_factory=list)
    handedness: str = "R"
    prefs: dict[str, Any] = Field(default_factory=dict)
    symptoms: list[str] = Field(default_factory=list)


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
    # plan_version / engine_version は Go が保存した評価の版と比べる（版が変われば作り直す。§8.5）
    return {"ok": True, "engine_version": config.ENGINE_VERSION, "plan_version": config.PLAN_VERSION,
            "checkpoints_version": checkpoints.version(), "judge_version": config.JUDGE_VERSION, "checkpoints_stamp": checkpoints.stamp(),
            "video_version": config.VIDEO_VERSION}


@app.get("/v1/checkpoints/stamp")
def checkpoints_stamp() -> dict:
    """測った条件の指紋（カタログの中身と判定の版）。Go が古い判定を測り直すかどうかに使う。"""
    return {"stamp": checkpoints.stamp(), "catalog_version": checkpoints.version(), "judge_version": config.JUDGE_VERSION}


@app.get("/v1/checkpoints")
def checkpoints_catalog(handedness: str = "") -> dict:
    """チェックポイントのカタログ（版・見本の線画つき）。docs/DESIGN_v2.md §5.1。
    handedness を渡すと {lead} / {trail} を差し込んで返す（渡さなければそのまま）。"""
    return checkpoints.public(handedness if handedness in ("R", "L") else None)


@app.post("/v1/checkpoints/measure")
def checkpoints_measure(body: CheckpointMeasureIn) -> dict:
    """1スイングを測って項目ごとに判定する（§5.4・§6.6。LLM を使わない・保存しない）。"""
    sw = body.swing
    if sw.get("view") not in ("dtl", "fo"):
        raise HTTPException(400, "view は dtl か fo です")
    _hand(sw.get("handedness") or "R")
    if not isinstance(sw.get("frames"), dict):
        raise HTTPException(400, "frames が要ります")
    return cp_measure.measure_swing(sw)


@app.post("/v1/checkpoints/ideal")
def checkpoints_ideal(body: CheckpointIdealIn) -> dict:
    """課題の項目1つの範囲の形と、範囲の外の部位だけを範囲に入れた線（§7.2。LLM を使わない・保存しない）。"""
    sw = body.swing
    if sw.get("view") not in ("dtl", "fo"):
        raise HTTPException(400, "view は dtl か fo です")
    _hand(sw.get("handedness") or "R")
    if not isinstance(sw.get("frames"), dict):
        raise HTTPException(400, "frames が要ります")
    return cp_ideal.ideal(sw, body.item_id)


@app.post("/v1/video/checkpoints")
def video_checkpoints(body: VideoCheckpointsIn) -> dict:
    """姿勢の時系列 → スイングの区間と P1〜P10・中間・t₀（§6.3・§6.4。video/0.3。スイングが無ければ diag。LLM を使わない・保存しない）。"""
    if body.view not in ("dtl", "fo"):
        raise HTTPException(400, "view は dtl か fo です")
    _hand(body.handedness)
    if body.width < 16 or body.height < 16:
        raise HTTPException(400, "width / height が不正です")
    if len(body.frames) > config.VIDEO_MAX_FRAMES:
        raise HTTPException(400, f"コマが多すぎます（{config.VIDEO_MAX_FRAMES}まで）")
    if body.roi is not None and len(body.roi) != len(body.frames):
        raise HTTPException(400, "roi は frames と同じ数です")
    return video.detect(body.model_dump())


@app.post("/v1/checkpoints/focus")
def checkpoints_focus(body: CheckpointFocusIn) -> dict:
    """スイングごとの判定をまとめ、課題を1つと次に見るを選ぶ（§4.4）。

    まずここ・次に見るの項目には、チェックポイント版のドリル（drills/checkpoints.json）と、
    球の症状とつながる候補の印の言葉（§9.1）を添える。"""
    hand = _hand(body.handedness)
    out = cp_judge.aggregate(body.swings, hand, body.prefs, body.symptoms)
    for it in out["items"]:
        if (it.get("focus") or it.get("next")) and it["state"] == "out_range":
            it["drills"] = drills.cp_for(it["id"], it.get("fault"), hand)
        if it.get("linked"):
            it["linked_text"] = video_candidates.LINK_TEXT
    out["symptoms"] = list(body.symptoms)
    return out


@app.get("/v1/checkpoints/vision/status")
def checkpoints_vision_status() -> dict:
    """見た目の評価を呼べるか・料金の見積もり・版（§6.7）。"""
    return cp_vision.status()


@app.post("/v1/checkpoints/vision")
def checkpoints_vision(body: CheckpointVisionIn) -> dict:
    """見た目の項目を Claude に聞く（§6.7）。画像は返さない・保存しない。キーが無ければ called=0 で理由を返す。"""
    try:
        return cp_vision.review(cp_vision.default_client, body.swings, body.seed)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class SymptomsIn(BaseModel):
    """症状を探す（§9.1）。shots は /v1/session と同じ形。"""

    shots: list[dict[str, Any]] = Field(default_factory=list)
    handedness: str = "R"
    clubs: list[str] = Field(default_factory=list)
    matched_seqs: list[int] = Field(default_factory=list)


@app.post("/v1/checkpoints/symptoms")
def checkpoints_symptoms(body: SymptomsIn) -> dict:
    """その日の球の症状（§9.1 の事前の表）と、課題の印に使う症状の id（その球の動画が要るものは対応づけたときだけ）。"""
    found = video_candidates.session_symptoms(body.shots, body.clubs or None)
    return {"version": video_candidates.VERSION, "found": found,
            "for_focus": video_candidates.symptoms_for_focus(found, set(body.matched_seqs))}


@app.post("/v1/session")
def session(body: SessionIn) -> dict:
    return analyze_session(body.shots)


@app.post("/v1/report")
def report(body: ReportIn) -> dict:
    """解説レポート（定型文・図の中身・理想・候補）。docs/DESIGN_coaching.md §6・§10.1。
    帯の形と窓の数字は Go が各範囲の band_request を見て band_shape を足す。"""
    if body.handedness not in ("R", "L"):
        raise HTTPException(400, "handedness は R か L です")
    out = build_report(body.shots, body.handedness, body.experiments)
    # 本体の範囲ごとに、つなぎの文（1c）の入力と鍵を足す。Claude はここでは呼ばない
    narrative.attach_inputs(out)
    return out


@app.post("/v1/report/narrative")
def report_narrative(body: NarrativeIn) -> dict:
    """1範囲の claims → 検証済みの並びとつなぎの文（§5.8・§10.1）。

    REPORT_LLM が on でなければ Claude を呼ばずに定型文の並びを返す。キーが無い・断られた・API が落ちている・
    検証に落ちた、のどれでも定型文に戻して 200 で返す（R10）。called が実際に呼んだ回数、model が実際に答えたモデル。"""
    inp = body.input
    secs = inp.get("sections")
    if not isinstance(inp.get("scope_id"), str) or not isinstance(secs, list) or not all(
        isinstance(s, dict) and isinstance(s.get("id"), str) and isinstance(s.get("claims"), list) for s in secs
    ):
        raise HTTPException(400, "つなぎの文の入力の形が不正です（scope_id と sections）")
    out = narrative.narrate(narrative.default_client, inp)
    out["input_hash"] = body.input_hash
    return out


def _hand(h: str) -> str:
    if h not in ("R", "L"):
        raise HTTPException(400, "handedness は R か L です")
    return h


@app.get("/v1/drills")
def drills_list(handedness: str = "R") -> dict:
    """ドリル集（checked_by のあるものだけ）。docs/DESIGN_coaching.md §5.7。"""
    return drills.listing(_hand(handedness))


@app.post("/v1/plan/candidates")
def plan_candidates(body: PlanCandidatesIn) -> dict:
    """候補（いま・次…）と、候補ごとに勧めるドリル（確かめ済みだけ・記録のあるものが先）。"""
    return coaching.candidates_with_drills(body.shots, _hand(body.handedness), body.history, body.plans)


@app.post("/v1/plan/build")
@app.post("/v1/plan/new")
def plan_build(body: PlanBuildIn) -> dict:
    """候補から1つ選んでプランの中身を作る。params は作った時点で固定する（§8.1・§7.3）。"""
    try:
        out = coaching.build_plan(body.shots, _hand(body.handedness), body.scope_id, body.candidate_id, body.drill_id, body.club, body.window, body.cue, body.variant)
    except coaching.PlanError as e:
        raise HTTPException(422, str(e)) from e
    # 今日の練習の画面の最初に出す言葉（数字も専門用語も使わない。gist.py）。trigger に入れて plans に残す
    pl = out.get("plan") or {}
    if isinstance(pl.get("trigger"), dict):
        pl["trigger"]["plain"] = {
            "title": gist.plain_title({"id": pl.get("issue"), "lever": pl.get("lever"), "target_metric": pl.get("target_metric"), "goal": pl.get("goal")}, _hand(body.handedness)),
            "why": gist.why_first({"why_first": (pl.get("rationale") or {}).get("why_first")}),
        }
    return out


@app.post("/v1/plan/evaluate")
def plan_evaluate(body: PlanEvaluateIn) -> dict:
    """1回の練習（A-B-B-A）の評価（§8.5）。Go は plan_runs.evaluation_json に入力の指紋と版と一緒に保存する。"""
    try:
        _hand(body.handedness)
        return coaching.evaluate_request(body.model_dump())
    except coaching.PlanError as e:
        raise HTTPException(422, str(e)) from e


@app.post("/v1/plan/progress")
def plan_progress(body: PlanProgressIn) -> dict:
    """推移と状態と次の手（§8.6）。"""
    return coaching.progress(body.plan, body.runs, body.history, body.continue_after_stop, _hand(body.handedness))


class FocusTestIn(BaseModel):
    """10球テスト（§8.2）。swings はスイングごとの {state, fault, basis}（10本まで）。"""

    swings: list[dict[str, Any]] = Field(default_factory=list)
    target_fault: str | None = None
    self_rating: dict[str, Any] | None = None


@app.post("/v1/focus-test/judge")
def focus_test_judge(body: FocusTestIn) -> dict:
    """10球テストを数える（Claude を呼ばない・保存しない）。"""
    try:
        return focus_test.judge(body.swings, body.target_fault, body.self_rating)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class MotionProgressIn(BaseModel):
    tests: list[dict[str, Any]] = Field(default_factory=list)


@app.post("/v1/plan/motion-progress")
def plan_motion_progress(body: MotionProgressIn) -> dict:
    """動きのプランの状態と次の手（§8.3）。"""
    return coaching.motion_progress(body.tests)


class CheckupIn(BaseModel):
    """TrackMan の2日の再確認（§8.4）。a = 基準の日、b = 再確認の日。items が null なら既定で作って返す。"""

    a: list[dict[str, Any]] = Field(default_factory=list)
    b: list[dict[str, Any]] = Field(default_factory=list)
    items: list[dict[str, Any]] | None = None
    plan: dict[str, Any] | None = None
    plan_eval: dict[str, Any] | None = None
    baseline_is_diagnosis: bool = False


@app.post("/v1/checkup")
def checkup_route(body: CheckupIn) -> dict:
    return checkup.checkup(body.a, body.b, body.items, body.plan, body.plan_eval, body.baseline_is_diagnosis)


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
