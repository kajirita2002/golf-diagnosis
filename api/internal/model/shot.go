// Package model は 1球（Shot）を中心にしたデータの形を持つ。
//
// 保存する値はすべて SI 単位（m/s・m・度・rpm）で、右打ちの座標に揃えてある。
// 左打ちは取り込みのときに左右の角度の符号を反転する（ingest.Canonicalize）。
//
// 符号の決まり（右打ち基準・TrackMan と同じ向き）:
//
//	LaunchDirection / ClubPath / FaceAngle / FaceToPath / SpinAxis / Side
//	  + = ターゲットの右（プッシュ・オープン・フェード側）
//	ImpactOffset
//	  + = トゥ寄り、- = ヒール寄り
package model

import (
	"encoding/json"
	"time"
)

// Metrics は計測器が出した1球ぶんの値。nil は「計測されていない」。
// 0 と区別するためにポインタで持つ。
type Metrics struct {
	ClubSpeed       *float64 `json:"club_speed,omitempty"`       // m/s
	BallSpeed       *float64 `json:"ball_speed,omitempty"`       // m/s
	SmashFactor     *float64 `json:"smash_factor,omitempty"`     // 比
	AttackAngle     *float64 `json:"attack_angle,omitempty"`     // 度（+ = アッパー）
	ClubPath        *float64 `json:"club_path,omitempty"`        // 度
	FaceAngle       *float64 `json:"face_angle,omitempty"`       // 度
	FaceToPath      *float64 `json:"face_to_path,omitempty"`     // 度
	DynamicLoft     *float64 `json:"dynamic_loft,omitempty"`     // 度
	SpinLoft        *float64 `json:"spin_loft,omitempty"`        // 度
	SwingPlane      *float64 `json:"swing_plane,omitempty"`      // 度
	SwingDirection  *float64 `json:"swing_direction,omitempty"`  // 度
	LowPoint        *float64 `json:"low_point,omitempty"`        // m（+ = ボールより先）
	ImpactOffset    *float64 `json:"impact_offset,omitempty"`    // m（+ = トゥ）
	ImpactHeight    *float64 `json:"impact_height,omitempty"`    // m（+ = 上）
	LaunchAngle     *float64 `json:"launch_angle,omitempty"`     // 度
	LaunchDirection *float64 `json:"launch_direction,omitempty"` // 度
	SpinRate        *float64 `json:"spin_rate,omitempty"`        // rpm
	SpinAxis        *float64 `json:"spin_axis,omitempty"`        // 度
	Carry           *float64 `json:"carry,omitempty"`            // m
	Total           *float64 `json:"total,omitempty"`            // m
	Side            *float64 `json:"side,omitempty"`             // m（着地の左右）
	Height          *float64 `json:"height,omitempty"`           // m（最高到達点）
	Curve           *float64 `json:"curve,omitempty"`            // m（曲がり幅。+ = 右）
	LandingAngle    *float64 `json:"landing_angle,omitempty"`    // 度
	HangTime        *float64 `json:"hang_time,omitempty"`        // 秒
	DynamicLie      *float64 `json:"dynamic_lie,omitempty"`      // 度（インパクトのライ角）
}

// Handedness は利き手。保存する値は常に右打ちの座標。
type Handedness string

const (
	RightHanded Handedness = "R"
	LeftHanded  Handedness = "L"
)

// Player は打つ人。
type Player struct {
	ID         int64      `json:"id"`
	Name       string     `json:"name"`
	Handedness Handedness `json:"handedness"`
	// Prefs は画面の設定（距離の単位・比べる相手の既定など）。形は httpapi が検査する
	Prefs     json.RawMessage `json:"prefs"`
	CreatedAt time.Time       `json:"created_at"`
}

// Session は1回の練習。
type Session struct {
	ID        int64     `json:"id"`
	PlayerID  int64     `json:"player_id"`
	Date      string    `json:"date"` // YYYY-MM-DD
	Location  string    `json:"location,omitempty"`
	Source    string    `json:"source,omitempty"` // 計測器（trackman など）
	CreatedAt time.Time `json:"created_at"`
	// NShots・Clubs は一覧のときだけ入る（記録の画面の「この日に入っているもの」）。クラブは多い順
	NShots int         `json:"n_shots"`
	Clubs  []ClubCount `json:"clubs,omitempty"`
}

// ClubCount はセッションの中のクラブごとの球数。
type ClubCount struct {
	Club string `json:"club"`
	N    int    `json:"n"`
}

// Shot は1球。診断の基本単位。
type Shot struct {
	ID           int64      `json:"id"`
	SessionID    int64      `json:"session_id"`
	Seq          int        `json:"seq"` // セッション内の打った順（1始まり）
	Club         string     `json:"club"`
	ClubCategory string     `json:"club_category"`
	HitAt        *time.Time `json:"hit_at,omitempty"`
	Metrics      Metrics    `json:"metrics"`
	// Estimated は「計測器が実測ではなく推定で出している項目」。
	// 例: 安価な計測器のクラブパスは推定値。診断では重みを下げる。
	Estimated []string `json:"estimated,omitempty"`
	// Derived はほかの値から計算で埋めた項目（面 − パス = フェース・トゥ・パス など）。
	Derived []string `json:"derived,omitempty"`
	// Manual は人が手で入れた項目（インパクトテープで見た打点など）。
	Manual []string `json:"manual,omitempty"`
	// Excluded は診断から外す（空振り・計測エラーなど）。
	Excluded bool `json:"excluded"`
	// GoodOverride は自動の Good 判定を人が上書きした値。nil なら自動。
	GoodOverride   *bool           `json:"good_override,omitempty"`
	Raw            json.RawMessage `json:"raw,omitempty"` // 取り込んだ CSV の行そのまま
	AdapterVersion string          `json:"adapter_version,omitempty"`
}

// Experiment は「仮説 → 介入 → 確かめる」の1回ぶん。
type Experiment struct {
	ID           int64     `json:"id"`
	SessionID    int64     `json:"session_id"`
	Hypothesis   string    `json:"hypothesis"`
	Intervention string    `json:"intervention"`  // ドリル・意識する1点
	TargetMetric string    `json:"target_metric"` // 例: face_to_path
	Goal         Goal      `json:"goal"`
	Club         string    `json:"club,omitempty"` // 空なら全クラブ
	CreatedAt    time.Time `json:"created_at"`
	Blocks       []Block   `json:"blocks"`
}

// Goal は目標の指標をどちらに動かしたいか。
type Goal string

const (
	GoalReduceAbs Goal = "reduce_abs" // 0 に近づける（フェース・トゥ・パスなど）
	GoalReduceSD  Goal = "reduce_sd"  // ばらつきを減らす
	GoalIncrease  Goal = "increase"
	GoalDecrease  Goal = "decrease"
)

// Valid は知っている Goal か。
func (g Goal) Valid() bool {
	switch g {
	case GoalReduceAbs, GoalReduceSD, GoalIncrease, GoalDecrease:
		return true
	}
	return false
}

// BlockKind はブロック実験の区切り。
type BlockKind string

const (
	BlockBaseline     BlockKind = "baseline"     // 介入前
	BlockIntervention BlockKind = "intervention" // ドリル・意識した後
	BlockRetention    BlockKind = "retention"    // 意識を外して戻したあと（定着を見る）
	// 以下の2つは評価（A と B の比較）に入れない（docs/DESIGN_coaching.md §8.3）。
	// 分析サービスの evaluate は知らない種類を最初から無視するので、Go で受け付けるだけでよい。
	BlockWarmup BlockKind = "warmup" // 体を温める球
	BlockDrill  BlockKind = "drill"  // 道具ありのドリルの球（「ドリル中」の参考にだけ使う）
)

// Valid は知っている BlockKind か。
func (k BlockKind) Valid() bool {
	switch k {
	case BlockBaseline, BlockIntervention, BlockRetention, BlockWarmup, BlockDrill:
		return true
	}
	return false
}

// Evaluated は A と B の比較（既存の evaluate）に入る種類か。warmup / drill は入らない。
func (k BlockKind) Evaluated() bool {
	switch k {
	case BlockBaseline, BlockIntervention, BlockRetention:
		return true
	}
	return false
}

// Block は打った順の範囲で区切った球のまとまり。
type Block struct {
	ID           int64     `json:"id"`
	ExperimentID int64     `json:"experiment_id"`
	Kind         BlockKind `json:"kind"`
	SeqFrom      int       `json:"seq_from"`
	SeqTo        int       `json:"seq_to"`
}
