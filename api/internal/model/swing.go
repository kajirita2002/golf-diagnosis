package model

import (
	"encoding/json"
	"time"
)

// 動画のスイングとチェックポイント（docs/DESIGN_v2.md §6・§13.2）。
//
// **動画そのものと全解像度のコマはサーバーに来ない。** 端末で処理し、ここに残すのは
// 長辺 360px のサムネイル・姿勢の点・タップ・判定だけ（§6.3・§8.6・§9.9）。

// Checkpoints は P の名前（swing_frames.checkpoint）。表の CHECK では縛らず、ここで検査する
// （移行の仕組みが無いので、あとで P を足せなくなるのを避ける。付録 A）。
var Checkpoints = []string{"P1", "P2", "P3", "P4", "P5", "P5_5", "P6", "P6_5", "P7", "P8", "P9", "P10"}

// IsCheckpoint は知っている P の名前か。
func IsCheckpoint(p string) bool {
	for _, c := range Checkpoints {
		if c == p {
			return true
		}
	}
	return false
}

// Swing は動画の1スイング（1本の動画は1つの向き）。
type Swing struct {
	ID        int64   `json:"id"`
	SessionID int64   `json:"session_id"`
	View      string  `json:"view"` // dtl（後ろから）/ fo（正面から）
	Club      string  `json:"club"`
	ClubClass string  `json:"club_class"` // iron / driver / wood / hybrid / wedge
	FPS       float64 `json:"fps"`        // 実測（fps_measured）。分からなければ 0
	FPSSource string  `json:"fps_source"` // container（ファイルの中の記録）/ playback（再生して測った）/ ""
	Width     int     `json:"width"`
	Height    int     `json:"height"`
	Duration  float64 `json:"duration"`
	// Ball は構えのコマで押したボールの両端（[[x,y],[x,y]]・画素）。ボール1個の物差し
	Ball    json.RawMessage `json:"ball"`
	Capture json.RawMessage `json:"capture"` // 端末・ファイルの形式・処理の時間など（§6.3 の perf）
	// Missing は本人が「このPは写っていない」を押した P
	Missing          []string        `json:"missing"`
	CPCatalogVersion string          `json:"cp_catalog_version"`
	Measure          json.RawMessage `json:"measure,omitempty"` // 最後に測ったときの撮り方の検査・物差し
	// HasSeries はスイングの区間の時系列（自動の取り出し・段2b）を残しているか
	HasSeries bool      `json:"has_series"`
	CreatedAt time.Time `json:"created_at"`
}

// SwingFrame は1つの P のコマ（姿勢の点とタップ。サムネイルは別に取る）。
type SwingFrame struct {
	Checkpoint string          `json:"checkpoint"`
	T          float64         `json:"t"`     // 動画の中の時刻（秒）
	Frame      int             `json:"frame"` // コマの番号（fps から）
	Source     string          `json:"source"`
	Landmarks  json.RawMessage `json:"landmarks"` // 33点 [{x,y,visibility}]（0〜1 の割合）
	Taps       json.RawMessage `json:"taps"`      // {grip:[x,y], head:[x,y], heel?, toe?}（画素）
	HasThumb   bool            `json:"has_thumb"`
	Thumb      []byte          `json:"-"`
}

// SwingCheck はスイング × 項目の判定（swing_checks）。Evidence は分析サービスの1項目の結果そのまま。
type SwingCheck struct {
	SwingID        int64           `json:"swing_id"`
	ItemID         string          `json:"item_id"`
	CatalogVersion string          `json:"catalog_version"`
	State          string          `json:"state"` // in_range / out_range / unknown / reference
	FaultID        string          `json:"fault_id,omitempty"`
	Basis          string          `json:"basis"`
	Reason         string          `json:"reason"`
	Evidence       json.RawMessage `json:"evidence"`
}
