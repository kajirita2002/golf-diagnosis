// Package analysis は Python の分析サービス（深い診断）を呼ぶ。
//
// 分析サービスは状態を持たない。必要な球は毎回ここから渡す。
// 分析サービスが落ちていても、1球ごとの物理分解（速い診断）は Go だけで返せる
// ―― 2段の速さに分けた理由（docs/DESIGN.md）。
package analysis

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
	"github.com/kajirita2002/golf-diagnosis/api/internal/physics"
)

// ErrUnavailable は分析サービスに届かない。
var ErrUnavailable = errors.New("分析サービスに接続できません")

// ShotPayload は分析サービスへ渡す1球。分解は Go で計算したものを渡す
// （同じ式を2か所に書かない）。
type ShotPayload struct {
	ID            int64                 `json:"id"`
	Seq           int                   `json:"seq"`
	Club          string                `json:"club"`
	ClubCategory  string                `json:"club_category"`
	Metrics       model.Metrics         `json:"metrics"`
	Estimated     []string              `json:"estimated"`
	Manual        []string              `json:"manual"`
	Excluded      bool                  `json:"excluded"`
	GoodOverride  *bool                 `json:"good_override"`
	Decomposition physics.Decomposition `json:"decomposition"`
	// BlockKind はプランの練習（plan_runs）のブロックの種類（warmup / baseline / drill / intervention）。
	// そのセッションの run の実験のブロックからだけ取り、実験のクラブに合う球にだけ付ける
	// （docs/DESIGN_coaching.md §10.2）。プランの外の球・プランの無いセッションでは付けない（キーごと無い）。
	// 分析サービスは warmup / drill の球を診断から外す。
	BlockKind model.BlockKind `json:"block_kind,omitempty"`
}

// BlockPayload は実験の1ブロック。
type BlockPayload struct {
	Kind    model.BlockKind `json:"kind"`
	SeqFrom int             `json:"seq_from"`
	SeqTo   int             `json:"seq_to"`
	Shots   []ShotPayload   `json:"shots"`
}

// ServiceError は分析サービスが理由つきで断ったとき（画像の形式が違う・API キーが無いなど）。
// 理由は利用者に見せてよい文なので、そのまま返す。
type ServiceError struct {
	Status  int
	Message string
}

func (e *ServiceError) Error() string { return e.Message }

// Client は分析サービスのクライアント。
type Client struct {
	BaseURL string
	HTTP    *http.Client
}

// New はクライアントを作る。
func New(baseURL string) *Client {
	// スクリーンショットの読み取りは Claude の応答を待つので長めにとる
	return &Client{BaseURL: baseURL, HTTP: &http.Client{Timeout: 3 * time.Minute}}
}

// Session はセッション全体の分析を頼む。返り値は分析サービスの JSON をそのまま返す。
func (c *Client) Session(ctx context.Context, shots []ShotPayload) (json.RawMessage, error) {
	return c.post(ctx, "/v1/session", map[string]any{"shots": shots})
}

// ReportInput は解説レポート（POST /v1/report）への入力。shots は Session と同じ形。
// handedness は定型文の向きの語（右／左）を入れるため、experiments はプランの材料
// （docs/DESIGN_coaching.md §10.1）。
type ReportInput struct {
	Shots       []ShotPayload      `json:"shots"`
	Handedness  model.Handedness   `json:"handedness"`
	Experiments []model.Experiment `json:"experiments"`
}

// Report は解説レポートを頼む。返り値は分析サービスの JSON をそのまま返す
// （帯の形は呼び出し側が physics.Band で足す）。
func (c *Client) Report(ctx context.Context, in ReportInput) (json.RawMessage, error) {
	if in.Shots == nil {
		in.Shots = []ShotPayload{}
	}
	if in.Experiments == nil {
		in.Experiments = []model.Experiment{}
	}
	return c.post(ctx, "/v1/report", in)
}

// NarrativeInput は POST /v1/report/narrative（1範囲のつなぎの文）への入力。Input は /v1/report の
// narrative_inputs の1つをそのまま渡す（Go は中身を読まない。facts の数値は入っていない）。
type NarrativeInput struct {
	Input     json.RawMessage `json:"input"`
	InputHash string          `json:"input_hash"`
}

// Narrative はつなぎの文を頼む（Claude の応答を待つので時間がかかる）。返り値は分析サービスの JSON そのまま。
// ctx は要求とは切り離したものを渡す（スマホの画面が消えて要求が切れても、払った生成を捨てない。§5.8）。
//
// HTTP の待ち時間（3分）はここでは使わない。分析サービスは Claude を最大2回（100秒×2）呼ぶので、3分で Go だけが
// 切ると、払った生成を捨てて枠も返してしまう。長さは ctx（LLMConfig.Timeout・既定5分）だけで切る。
func (c *Client) Narrative(ctx context.Context, in NarrativeInput) (json.RawMessage, error) {
	b, err := json.Marshal(in)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/v1/report/narrative", bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	long := &http.Client{Transport: c.HTTP.Transport, Timeout: 0}
	return c.doWith(long, req)
}

// Compare は2つのセッションの比較を頼む。a が比べる元（昨日）、b が今回。
func (c *Client) Compare(ctx context.Context, a, b []ShotPayload) (json.RawMessage, error) {
	return c.post(ctx, "/v1/compare", map[string]any{"a": a, "b": b})
}

// Screenshot はスクリーンショットの表を読ませる（取り込みはしない）。
// Claude の API を呼ぶので時間がかかる。
func (c *Client) Screenshot(ctx context.Context, mediaType string, image []byte) (json.RawMessage, error) {
	return c.post(ctx, "/v1/screenshot", map[string]any{
		"media_type": mediaType,
		"data":       base64.StdEncoding.EncodeToString(image),
	})
}

// VerifyTSV は人が直した表をもう一度検算させる。
func (c *Client) VerifyTSV(ctx context.Context, tsv string) (json.RawMessage, error) {
	return c.post(ctx, "/v1/screenshot/verify", map[string]any{"tsv": tsv})
}

// Experiment は実験の評価を頼む。
func (c *Client) Experiment(ctx context.Context, e *model.Experiment, blocks []BlockPayload) (json.RawMessage, error) {
	return c.post(ctx, "/v1/experiment", map[string]any{
		"target_metric": e.TargetMetric,
		"goal":          e.Goal,
		"blocks":        blocks,
	})
}

// PlanEvalInput はプランの run の評価（POST /v1/plan/evaluate）への入力。
//
//	plan       … プラン（params は作った時点で固定した値をそのまま）
//	run        … 評価する run（何回目か・予定の球数・ブロックの出どころ）
//	experiment … その run の実験。/v1/experiment の本文と同じ形（target_metric・goal・blocks）
//	past_runs  … それより前の run の要約（練習の順。1件目の A がプランの物差し）
type PlanEvalInput struct {
	Plan       PlanPayload       `json:"plan"`
	Run        RunPayload        `json:"run"`
	Experiment ExperimentPayload `json:"experiment"`
	PastRuns   []PastRunPayload  `json:"past_runs"`
	// Handedness は選手の利き手（R / L）。評価の定型文の向きの語を入れ替えるのに使う（R9）。
	Handedness model.Handedness `json:"handedness"`
	// History はこの選手のほかのプラン（{issue, drill_id, state}）。同じ issue でドリル2つとも
	// 効かなければ「詰まった」にするのに使う（§8.6）。
	History []HistoryItem `json:"history"`
	// ContinueAfterRun は「止める」のあとに本人が「続ける」を選んだ時点で最後だった run の id。
	// ガードレールの崩れはそれより後の回だけ数える（§8.6「続けるか本人に選ばせる」）。無ければ省く。
	ContinueAfterRun *int64 `json:"continue_after_run,omitempty"`
}

// HistoryItem はほかのプランの要約（最後に保存した評価の状態）。
type HistoryItem struct {
	PlanID  int64  `json:"plan_id"`
	Issue   string `json:"issue"`
	DrillID string `json:"drill_id"`
	State   string `json:"state"`
}

// PlanPayload は評価に渡すプラン。
type PlanPayload struct {
	ID             int64             `json:"id"`
	Issue          string            `json:"issue"`
	Lever          string            `json:"lever"`
	DrillID        string            `json:"drill_id"`
	CatalogVersion string            `json:"catalog_version"`
	Club           string            `json:"club"`
	TargetMetric   string            `json:"target_metric"`
	Goal           model.Goal        `json:"goal"`
	Template       []model.PlanBlock `json:"template"`
	Params         json.RawMessage   `json:"params"`
	Trigger        json.RawMessage   `json:"trigger"`
	Rationale      json.RawMessage   `json:"rationale"`
	Status         model.PlanStatus  `json:"status"`
	EngineVersion  string            `json:"engine_version"`
	CreatedAt      time.Time         `json:"created_at"`
}

// RunPayload は評価する run。index は 0 始まりの練習の回（0 がプランの1回目）。
// blocks_source は saved（人が確かめて保存した境目）か planned（予定の球数で区切った仮の境目）。
type RunPayload struct {
	ID           int64  `json:"id"`
	Index        int    `json:"index"`
	SessionID    int64  `json:"session_id"`
	Date         string `json:"date"`
	Counts       []int  `json:"counts"`
	BlocksSource string `json:"blocks_source"`
	// ClubShots は実験のクラブの球の数（除外した球も含む。打った数）。Planned は予定の球数の合計。
	ClubShots int `json:"club_shots"`
	Planned   int `json:"planned"`
}

// ExperimentPayload は /v1/experiment の本文と同じ形。
type ExperimentPayload struct {
	TargetMetric string         `json:"target_metric"`
	Goal         model.Goal     `json:"goal"`
	Blocks       []BlockPayload `json:"blocks"`
}

// PastRunPayload は前の run の要約。evaluation はその run の保存した評価（分析サービスが前に返した
// JSON そのまま）。前の run は評価の前に先に評価し直すので、入力が変わっていれば新しい値になっている。
type PastRunPayload struct {
	RunID      int64           `json:"run_id"`
	Index      int             `json:"index"`
	SessionID  int64           `json:"session_id"`
	Date       string          `json:"date"`
	Evaluation json.RawMessage `json:"evaluation"`
}

// PlanEvaluate はプランの run の評価（判定・状態・次の手）を頼む。返り値は分析サービスの JSON そのまま。
func (c *Client) PlanEvaluate(ctx context.Context, in PlanEvalInput) (json.RawMessage, error) {
	if in.PastRuns == nil {
		in.PastRuns = []PastRunPayload{}
	}
	if in.Experiment.Blocks == nil {
		in.Experiment.Blocks = []BlockPayload{}
	}
	if in.History == nil {
		in.History = []HistoryItem{}
	}
	return c.post(ctx, "/v1/plan/evaluate", in)
}

// PlanBuildInput は POST /v1/plan/build（候補から1つ選んでプランの中身を作る）への入力。
// window は Go の physics.Band の窓（{face_min, face_max, path}・右打ちの座標）。無ければ省く。
type PlanBuildInput struct {
	Shots       []ShotPayload    `json:"shots"`
	Handedness  model.Handedness `json:"handedness"`
	ScopeID     string           `json:"scope_id"`
	CandidateID string           `json:"candidate_id"`
	DrillID     string           `json:"drill_id,omitempty"`
	Club        string           `json:"club,omitempty"`
	Window      map[string]any   `json:"window,omitempty"`
	Cue         string           `json:"cue,omitempty"`
	// Variant は型（standard ＝ A-B-B-A / alternate ＝「移せていない」の次の、ドリルと本番を1球ずつ交互）。空なら standard。
	Variant string `json:"variant,omitempty"`
}

// PlanCandidatesInput は POST /v1/plan/candidates（候補と勧めるドリル）への入力。
//
//	history … 練習の記録（1回の練習ごとに {drill_id, issue, grade, counts_as_worked, date}）。記録のあるドリルを先に置く
//	plans   … この選手の前のプラン（{plan_id, issue, drill_id, status, state}）。「詰まった」issue を後ろへ回す
type PlanCandidatesInput struct {
	Shots      []ShotPayload    `json:"shots"`
	Handedness model.Handedness `json:"handedness"`
	History    []map[string]any `json:"history"`
	Plans      []map[string]any `json:"plans"`
}

// PlanCandidates は候補（いま・次…）と勧めるドリルを頼む。返り値は分析サービスの JSON そのまま。
func (c *Client) PlanCandidates(ctx context.Context, in PlanCandidatesInput) (json.RawMessage, error) {
	if in.Shots == nil {
		in.Shots = []ShotPayload{}
	}
	if in.History == nil {
		in.History = []map[string]any{}
	}
	if in.Plans == nil {
		in.Plans = []map[string]any{}
	}
	return c.post(ctx, "/v1/plan/candidates", in)
}

// Versions は分析サービスの版（"<engine_version> <plan_version>"）。保存した評価の指紋に入れて、
// 判定の規則を変えたら（版が上がったら）評価を作り直すのに使う（§8.5「入力の指紋と版と一緒に保存する」）。
func (c *Client) Versions(ctx context.Context) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+"/healthz", nil)
	if err != nil {
		return "", err
	}
	raw, err := c.do(req)
	if err != nil {
		return "", err
	}
	var v struct {
		Engine string `json:"engine_version"`
		Plan   string `json:"plan_version"`
	}
	if err := json.Unmarshal(raw, &v); err != nil || v.Engine == "" || v.Plan == "" {
		return "", errors.New("分析サービスの版を読めません")
	}
	return v.Engine + " " + v.Plan, nil
}

// PlanBuild はプランの中身（型・params を作った時点で固定）を頼む。保存は Go。
func (c *Client) PlanBuild(ctx context.Context, in PlanBuildInput) (json.RawMessage, error) {
	if in.Shots == nil {
		in.Shots = []ShotPayload{}
	}
	return c.post(ctx, "/v1/plan/build", in)
}

// Drills はドリル集（GET /v1/drills）を取る。Go は中継するだけ（持ち主は分析サービス）。
// 文の向きの語と図は利き手で入れ替わる（R9）ので、利き手を渡す。
func (c *Client) Drills(ctx context.Context, hand model.Handedness) (json.RawMessage, error) {
	if hand != model.LeftHanded {
		hand = model.RightHanded
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+"/v1/drills?handedness="+string(hand), nil)
	if err != nil {
		return nil, err
	}
	return c.do(req)
}

func (c *Client) post(ctx context.Context, path string, body any) (json.RawMessage, error) {
	b, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+path, bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	return c.do(req)
}

func (c *Client) do(req *http.Request) (json.RawMessage, error) { return c.doWith(c.HTTP, req) }

func (c *Client) doWith(hc *http.Client, req *http.Request) (json.RawMessage, error) {
	resp, err := hc.Do(req)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrUnavailable, err)
	}
	defer resp.Body.Close()
	out, err := io.ReadAll(io.LimitReader(resp.Body, 16<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		var d struct {
			Detail any `json:"detail"`
		}
		if json.Unmarshal(out, &d) == nil {
			if msg, ok := d.Detail.(string); ok && msg != "" {
				return nil, &ServiceError{Status: resp.StatusCode, Message: msg}
			}
		}
		return nil, fmt.Errorf("分析サービスが %d を返しました: %s", resp.StatusCode, truncate(string(out), 500))
	}
	if !json.Valid(out) {
		return nil, fmt.Errorf("分析サービスの応答が JSON ではありません")
	}
	return out, nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
