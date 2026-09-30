package model

import (
	"encoding/json"
	"time"
)

// FocusTest は10球テスト1回（docs/DESIGN_v2.md §8.2）。Passed は判定できたスイングが8に満たなければ nil。
type FocusTest struct {
	ID             int64           `json:"id"`
	PlayerID       int64           `json:"player_id"`
	PlanID         *int64          `json:"plan_id"`
	SessionID      *int64          `json:"session_id"`
	Date           string          `json:"date"` // セッションの日付（読むときだけ）
	ItemID         string          `json:"item_id"`
	CatalogVersion string          `json:"catalog_version"`
	View           string          `json:"view"`
	Block          string          `json:"block"` // test（⑥）/ baseline（①）
	SwingIDs       []int64         `json:"swing_ids"`
	SelfRating     json.RawMessage `json:"self_rating"`
	InRange        int             `json:"in_range"`
	Judged         int             `json:"judged"`
	Passed         *bool           `json:"passed"`
	Result         json.RawMessage `json:"result"`
	CreatedAt      time.Time       `json:"created_at"`
}

// Checkup は TrackMan の2日の再確認（§8.4）。Items は作った時点で固定する。
type Checkup struct {
	ID                int64           `json:"id"`
	PlayerID          int64           `json:"player_id"`
	BaselineSessionID int64           `json:"baseline_session_id"`
	RecheckSessionID  int64           `json:"recheck_session_id"`
	PlanID            *int64          `json:"plan_id"`
	Scope             json.RawMessage `json:"scope"`
	Items             json.RawMessage `json:"items"`
	Result            json.RawMessage `json:"result"`
	ResultKey         string          `json:"-"`
	CreatedAt         time.Time       `json:"created_at"`
}
