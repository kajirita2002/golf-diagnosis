package model

import (
	"encoding/json"
	"time"
)

// Claude の呼び出しの記録（docs/DESIGN_coaching.md §5.8・§10.3）。
//
// 生成は数十秒〜1分以上かかりうるので、要求とは切り離したジョブにする。無料プランは再起動で
// メモリが消えるので、状態は DB に置く（起動時に走っていたはずのジョブは failed にする）。

// LLMJobKind はジョブの種類。
type LLMJobKind string

const (
	LLMNarrative   LLMJobKind = "narrative"    // 解説のつなぎの文（1c）
	LLMVideoReview LLMJobKind = "video_review" // 動画の質問票（2）
	LLMVideoPair   LLMJobKind = "video_pair"   // 動画の比較（2）
	LLMCPReview    LLMJobKind = "cp_review"    // チェックポイントの見た目の項目（docs/DESIGN_v2.md §6.7・段2c）
)

// LLMJobStatus はジョブの状態。
type LLMJobStatus string

const (
	LLMQueued  LLMJobStatus = "queued"
	LLMRunning LLMJobStatus = "running"
	LLMDone    LLMJobStatus = "done"   // 結果をキャッシュとして使い回してよい
	LLMFailed  LLMJobStatus = "failed" // 使い回さない（キーが無い・API が落ちていた・分析サービスに届かない・再起動）
)

// LLMJob は llm_jobs の1行。
//
// Model は**実際に答えたモデル**（応答の model）。頼んだ時点では頼んだモデルを入れておき、答えが
// 返ったら書き換える。キャッシュの鍵は (kind, input_hash, model) ―― フォールバックで別のモデルが
// 答えたものを、頼んだモデルの答えとして使い回さない。
type LLMJob struct {
	ID            int64           `json:"id"`
	Kind          LLMJobKind      `json:"kind"`
	SessionID     *int64          `json:"session_id"`
	ScopeID       string          `json:"scope_id"`
	InputHash     string          `json:"input_hash"`
	Status        LLMJobStatus    `json:"status"`
	Result        json.RawMessage `json:"result"`
	Validation    json.RawMessage `json:"validation"`
	Usage         json.RawMessage `json:"usage"`
	CostUSD       *float64        `json:"cost_usd"`
	Error         string          `json:"error,omitempty"`
	Model         string          `json:"model"`
	PromptVersion string          `json:"prompt_version"`
	CreatedAt     time.Time       `json:"created_at"`
	UpdatedAt     time.Time       `json:"updated_at"`
}

// LLMUsage は1日・1種類ぶんの使った量（llm_usage の1行）。Calls は上限に数える回数
// （つなぎの文は範囲ごとに1回。直させた2回目は数えない）。
type LLMUsage struct {
	Day          string  `json:"day"`
	Kind         string  `json:"kind"`
	Calls        int     `json:"calls"`
	InputTokens  int64   `json:"input_tokens"`
	OutputTokens int64   `json:"output_tokens"`
	CostUSD      float64 `json:"cost_usd"`
}
