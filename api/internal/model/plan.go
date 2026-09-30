package model

import (
	"encoding/json"
	"time"
)

// プラン（docs/DESIGN_coaching.md §8・§10.3）。
//
//	Plan    ＝ issue × ドリル × クラブ1本 × 主 KPI × ブロックの型 × 段の目標（params は作った時点で固定）
//	PlanRun ＝ 練習の日ごとの1回。その日のセッションに既存の Experiment を1つ作って紐づける
//
// 基準（プランの1回目の A）は保存しない。plan_runs の1件目の評価から毎回読む（同じ値を2か所に持たない）。

// PlanStatus はプランの状態。動かせる（active）のは選手ごとに1つだけ。
type PlanStatus string

const (
	PlanActive    PlanStatus = "active"
	PlanDone      PlanStatus = "done"      // 定着した
	PlanSwitched  PlanStatus = "switched"  // 別のプランに切り替えた
	PlanBlocked   PlanStatus = "blocked"   // TrackMan で見える範囲では詰まった（動画かコーチへ）
	PlanAbandoned PlanStatus = "abandoned" // やめた
)

// Valid は知っている状態か。
func (s PlanStatus) Valid() bool {
	switch s {
	case PlanActive, PlanDone, PlanSwitched, PlanBlocked, PlanAbandoned:
		return true
	}
	return false
}

// PlanBlock はブロックの型の1段（種類と予定の球数）。
type PlanBlock struct {
	Kind BlockKind `json:"kind"`
	N    int       `json:"n"`
}

// Plan は何日もかけて確かめる1つのプラン。
type Plan struct {
	ID             int64       `json:"id"`
	PlayerID       int64       `json:"player_id"`
	Issue          string      `json:"issue"`
	Lever          string      `json:"lever,omitempty"`
	DrillID        string      `json:"drill_id"` // 空なら「意識する1点を自分で書く」（確かめ済みのドリルが無いとき）
	CatalogVersion string      `json:"catalog_version"`
	Club           string      `json:"club"`
	TargetMetric   string      `json:"target_metric"`
	Goal           Goal        `json:"goal"`
	Hypothesis     string      `json:"hypothesis"` // 「仮説: 〜すると、〜が減る」（定型文。画面が候補から写す）
	Cue            string      `json:"cue"`        // 介入のブロックで意識する1点（ドリルの cue_transfer か本人の文）
	Template       []PlanBlock `json:"template"`
	// Params は作った時点で固定する値（k・帯・中央値・段の目標の規則など）。中身は分析サービスが決める。
	Params json.RawMessage `json:"params"`
	// Trigger はきっかけの診断（どのセッション・どの群・何球）。
	Trigger json.RawMessage `json:"trigger"`
	// Rationale は先にする理由の材料（ゲート・件数）。
	Rationale     json.RawMessage `json:"rationale"`
	Status        PlanStatus      `json:"status"`
	EngineVersion string          `json:"engine_version"`
	CreatedAt     time.Time       `json:"created_at"`
	ClosedAt      *time.Time      `json:"closed_at,omitempty"`
	CloseReason   string          `json:"close_reason,omitempty"`
	// Kind は ball（球のプラン）か motion（動きのプラン。motion_plans に行がある。docs/DESIGN_v2.md §8.3）
	Kind   string      `json:"kind"`
	Motion *MotionPlan `json:"motion,omitempty"`
}

// MotionPlan は動きのプランの固定部分（作った時点で固定。10球テストで取り出す向きと P）。
type MotionPlan struct {
	ItemID         string `json:"cp_item_id"`
	CatalogVersion string `json:"cp_catalog_version"`
	View           string `json:"view"`
	Checkpoint     string `json:"checkpoint"`
}

// PlanRun は練習の日ごとの1回。1セッションに1つまで。
type PlanRun struct {
	ID           int64  `json:"id"`
	PlanID       int64  `json:"plan_id"`
	SessionID    int64  `json:"session_id"`
	SessionDate  string `json:"session_date"`
	ExperimentID int64  `json:"experiment_id"`
	// Counts は型の各段の予定の球数（Template と同じ長さ）。境目を動かしたら変わる。
	Counts []int `json:"counts"`
	// Evaluation は保存した評価（分析サービスの応答そのまま）。無ければ null。
	Evaluation    json.RawMessage `json:"evaluation"`
	EvaluationKey string          `json:"-"`
	CreatedAt     time.Time       `json:"created_at"`
}
