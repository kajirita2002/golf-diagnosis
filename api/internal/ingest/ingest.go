// Package ingest は計測器の書き出し（CSV）を 1球ずつの正規化された値にする。
//
// 計測器ごとの違いは Adapter が吸収する。いまは TrackMan だけ。
// 保存する値は SI・右打ちの座標（model パッケージの約束）。
package ingest

import (
	"io"
	"time"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
	"github.com/kajirita2002/golf-diagnosis/api/internal/units"
)

// Options は取り込みの指定。
type Options struct {
	// Units は CSV に単位が書いていない列に使う単位系。
	Units units.System
	// Handedness が左なら、左右の角度・距離の符号を反転して右打ちに揃える。
	Handedness model.Handedness
	// Club は CSV にクラブの列が無いときに全部の球へ付けるクラブ名。
	// TrackMan の画面の表はクラブ名を表の上にだけ出すので、貼り付けだと列が無い。
	Club string
	// ImpactOffsetToeNegative は、計測器の打点の左右が「トゥ = マイナス」のとき true。
	// 実物の CSV で確かめるまで既定（トゥ = プラス）のまま扱う。
	ImpactOffsetToeNegative bool
}

// ShotInput は取り込んだ1球。まだ ID も Seq も無い。
type ShotInput struct {
	Club      string
	HitAt     *time.Time
	Metrics   model.Metrics
	Estimated []string
	Raw       map[string]string
}

// Result は取り込みの結果。
type Result struct {
	Source   string
	Version  string
	Shots    []ShotInput
	Columns  map[string]string // 正規化した項目名 → CSV の列名
	Ignored  []string          // 読まなかった列（黙って捨てずに返す）
	Skipped  int               // 平均・標準偏差などの集計行や空行
	Warnings []string
}

// Adapter は計測器1種類ぶんの読み手。
type Adapter interface {
	Name() string
	Version() string
	Parse(r io.Reader, opt Options) (*Result, error)
}

// horizontalFields は左打ちのときに符号を反転する項目。
// 打点（トゥ／ヒール）はクラブに対する向きなので反転しない。
var horizontalFields = []func(*model.Metrics) **float64{
	func(m *model.Metrics) **float64 { return &m.ClubPath },
	func(m *model.Metrics) **float64 { return &m.FaceAngle },
	func(m *model.Metrics) **float64 { return &m.FaceToPath },
	func(m *model.Metrics) **float64 { return &m.SwingDirection },
	func(m *model.Metrics) **float64 { return &m.LaunchDirection },
	func(m *model.Metrics) **float64 { return &m.SpinAxis },
	func(m *model.Metrics) **float64 { return &m.Side },
	func(m *model.Metrics) **float64 { return &m.Curve },
}

// Canonicalize は左打ちの値を右打ちの座標に揃える。右打ちなら何もしない。
func Canonicalize(m *model.Metrics, h model.Handedness) {
	if h != model.LeftHanded {
		return
	}
	for _, f := range horizontalFields {
		p := f(m)
		if *p != nil {
			v := -**p
			*p = &v
		}
	}
}

// Fill は計測器が出していないが、ほかの値から一意に決まる項目を埋める。
// 埋めた項目は derived に入れて返す（実測と区別するため）。
func Fill(m *model.Metrics) (derived []string) {
	if m.FaceToPath == nil && m.FaceAngle != nil && m.ClubPath != nil {
		v := *m.FaceAngle - *m.ClubPath
		m.FaceToPath = &v
		derived = append(derived, "face_to_path")
	}
	if m.SpinLoft == nil && m.DynamicLoft != nil && m.AttackAngle != nil {
		// 本来は3次元の角度だが、左右の角度が小さければこれで十分に近い。
		v := *m.DynamicLoft - *m.AttackAngle
		m.SpinLoft = &v
		derived = append(derived, "spin_loft")
	}
	if m.SmashFactor == nil && m.BallSpeed != nil && m.ClubSpeed != nil && *m.ClubSpeed > 0 {
		v := *m.BallSpeed / *m.ClubSpeed
		m.SmashFactor = &v
		derived = append(derived, "smash_factor")
	}
	return derived
}
