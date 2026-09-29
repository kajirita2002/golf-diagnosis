package physics

import (
	"errors"
	"fmt"
	"math"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
)

// 帯（docs/DESIGN_coaching.md §5.2）。
//
// 「インパクトの数字だけから計算した左右が、目標帯に入る組み合わせだったか」を見る。
//
//	左右 = キャリー × sin(予測の打ち出し) + k × キャリー × sin(予測のスピン軸)
//	  予測の打ち出し = フェースの重み × フェース + (1 − 重み) × パス（Decompose と同じ）
//	  予測のスピン軸 = PredictAxis(フェース − パス, スピンロフト)（D-plane。Decompose と同じ）
//	帯に入る = |左右| ≤ lim
//
// k（スピン軸が左右をどれだけ動かすか）と lim（帯の半幅 m）は分析サービスが実測から決めて渡す。
// 閾値（GOOD_TARGETS）をここに書き写さない。式は Decompose と同じ関数を使い、2か所に書かない。
//
// これは TrackMan の数字の上での計算で、今日の球の範囲の外（フェース ±10° など）では当てにならない。

// bandFaceLimitDeg は帯の端を探すフェースの範囲。これより外は「今日の球の範囲の外」なので探さない。
const bandFaceLimitDeg = 30.0

// maxBandPaths は1回の Band で計算するパスの数の上限（図の多角形の頂点。数十で足りる）。
const maxBandPaths = 400

// FaceRange はパス1つぶんの帯の断面（帯に入るフェースの範囲）。
type FaceRange struct {
	Path       float64 `json:"path"`        // 度
	OK         bool    `json:"ok"`          // false なら探す範囲（±30°）の中に帯の端が無い
	FaceMin    float64 `json:"face_min"`    // 度。これ以上なら左へ外れない
	FaceMax    float64 `json:"face_max"`    // 度。これ以下なら右へ外れない
	FaceCenter float64 `json:"face_center"` // 度。計算の上で左右 0 に落ちるフェース
	// 中央（FaceCenter）での予測の打ち出しとスピン軸。図 F4 の「窓の中央の1球」に使う。
	LaunchAtCenter float64 `json:"predicted_launch_direction"`
	AxisAtCenter   float64 `json:"predicted_spin_axis"`
}

// sideL1 は、フェースとパスからインパクトの数字だけで計算した左右（m）。
func sideL1(face, path, carry, spinLoft, k, w float64) (float64, bool) {
	axis, ok := PredictAxis(face-path, spinLoft)
	if !ok {
		return 0, false
	}
	launch := w*face + (1-w)*path
	return carry*sinDeg(launch) + k*carry*sinDeg(axis), true
}

func sinDeg(d float64) float64 { return math.Sin(d * math.Pi / 180) }

// Band は、パスの値ごとに帯に入るフェースの範囲を返す。図（F3）の帯の多角形と窓の断面に使う。
//
// carry・spinLoft は描く範囲の代表値（中央値）、lim は帯の半幅（m）、k は分析サービスが当てた係数。
// 判定は1球ずつそれぞれのキャリーとスピンロフトで行う（SideL1）ので、図の帯は目安になる。
// スピンロフトが MinReliableSpinLoftDeg 未満（薄い当たり）では D-plane の予測を使わないので断る。
func Band(carry, spinLoft, lim, k float64, category string, paths []float64) ([]FaceRange, error) {
	switch {
	case !finite(carry) || carry <= 0:
		return nil, errors.New("キャリーが無いので帯を計算できません")
	case !finite(spinLoft) || spinLoft < MinReliableSpinLoftDeg:
		return nil, fmt.Errorf("スピンロフトが %.0f° 未満（薄い当たり）なので帯を計算しません", MinReliableSpinLoftDeg)
	case !finite(lim) || lim <= 0:
		return nil, errors.New("帯の幅が不正です")
	case !finite(k) || k < 0:
		// k が負だと「右へ曲がるほど左へ落ちる」ことになり、式が物理として成り立っていない
		return nil, errors.New("係数 k が不正です")
	case len(paths) == 0:
		return nil, errors.New("パスの値がありません")
	case len(paths) > maxBandPaths:
		return nil, fmt.Errorf("パスの値が多すぎます（%d まで）", maxBandPaths)
	}
	w := FaceWeight(category)
	out := make([]FaceRange, 0, len(paths))
	for _, p := range paths {
		if !finite(p) {
			return nil, errors.New("パスの値が不正です")
		}
		out = append(out, faceRange(p, carry, spinLoft, lim, k, w))
	}
	return out, nil
}

// faceRange は1つのパスで帯の端と中央を探す。k ≥ 0 なら左右はフェースに対して増える一方なので、
// 二分法で「左右 = −lim / 0 / +lim」になるフェースを探せる。
func faceRange(path, carry, spinLoft, lim, k, w float64) FaceRange {
	r := FaceRange{Path: path}
	side := func(face float64) float64 {
		v, _ := sideL1(face, path, carry, spinLoft, k, w) // spinLoft は Band で確かめ済み
		return v
	}
	lo, okLo := solveFace(side, -lim)
	hi, okHi := solveFace(side, lim)
	mid, okMid := solveFace(side, 0)
	if !okLo || !okHi || !okMid {
		return r
	}
	axis, _ := PredictAxis(mid-path, spinLoft)
	r.OK = true
	r.FaceMin, r.FaceMax, r.FaceCenter = lo, hi, mid
	r.LaunchAtCenter = w*mid + (1-w)*path
	r.AxisAtCenter = axis
	return r
}

// solveFace は side(face) = target になる face を ±bandFaceLimitDeg の中で二分法で探す。
func solveFace(side func(float64) float64, target float64) (float64, bool) {
	a, b := -bandFaceLimitDeg, bandFaceLimitDeg
	fa, fb := side(a)-target, side(b)-target
	if fa > 0 || fb < 0 {
		return 0, false
	}
	for i := 0; i < 60; i++ {
		m := (a + b) / 2
		if side(m)-target < 0 {
			a = m
		} else {
			b = m
		}
	}
	return (a + b) / 2, true
}

// SideL1 は1球の「インパクトの数字だけから計算した左右」（m）。
// 帯の判定に数えない球（Decomposition.BandSkip を参照）では ok=false。
func SideL1(m model.Metrics, category string, k float64) (float64, bool) {
	d := Decompose(m, category)
	if !d.BandEligible || !finite(k) {
		return 0, false
	}
	return *d.PredictedSideLaunch + k**d.PredictedSideCurveUnit, true
}

// InBand は1球のインパクトが帯に入る組み合わせだったか。lim はその球のキャリーでの帯の半幅（m）。
// 数えない球（薄い当たり・フェースが無い・極端な打点など）は ok=false。
func InBand(m model.Metrics, category string, lim, k float64) (in, ok bool) {
	side, ok := SideL1(m, category, k)
	if !ok || !finite(lim) || lim <= 0 {
		return false, false
	}
	return math.Abs(side) <= lim, true
}

// bandTerms は Decompose の最後に、帯の判定の材料（k を掛ける前の2つの項）と数えるかどうかを埋める。
// 数えない理由の順は設計書 §5.2 のとおり: 薄い当たり → フェースが無い → 極端な打点
// （#18 のようにフェースが取れていても打点の段で数える）→ 予測できない → キャリーが無い。
func bandTerms(d *Decomposition, m model.Metrics, thin bool) {
	if m.Carry != nil && *m.Carry > 0 && d.PredictedLaunchDir != nil && d.PredictedAxisFTP != nil {
		d.PredictedSideLaunch = ptr(*m.Carry * sinDeg(*d.PredictedLaunchDir))
		d.PredictedSideCurveUnit = ptr(*m.Carry * sinDeg(*d.PredictedAxisFTP))
	}
	switch {
	case thin:
		d.BandSkip = "thin"
	case m.FaceAngle == nil:
		d.BandSkip = "no_face"
	case d.Contact == "heel_extreme" || d.Contact == "toe_extreme":
		d.BandSkip = "extreme_strike"
	case d.PredictedLaunchDir == nil || d.PredictedAxisFTP == nil:
		d.BandSkip = "no_prediction"
	case d.PredictedSideLaunch == nil:
		d.BandSkip = "no_carry"
	default:
		d.BandEligible = true
	}
}

func finite(v float64) bool { return !math.IsNaN(v) && !math.IsInf(v, 0) }
