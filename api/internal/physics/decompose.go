// Package physics は球筋（L0）をインパクト（L1）に分解する。
//
// ここは相関ではなく物理でほぼ決まる層なので、統計ではなく式で計算する。
// 1球ごとに数ミリ秒で終わるので、打った直後に返す「速い診断」はここだけで作る。
//
//   - 曲がり（スピン軸）: D-plane（クラブの進む向きとフェースの向きが張る面）で
//     フェース・トゥ・パスとスピンロフトから予測し、予測で説明できない残りを
//     打点（ギア効果）と計測のばらつきに回す。
//   - 打ち出し方向: フェースの向きが大半を決め、残りをクラブパスが決める。
//
// 閾値や重みは「初期値」。実データで較正したら EngineVersion を上げる。
package physics

import (
	"fmt"
	"math"
	"strings"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
)

// EngineVersion は分解の式と閾値の版。保存する診断には必ず付ける。
const EngineVersion = "physics/0.2"

// 閾値（初期値・要較正）。
const (
	StraightStartDeg = 2.0  // 打ち出しがこれ以内なら「まっすぐ」
	StraightCurveDeg = 3.0  // スピン軸がこれ以内なら曲がらない
	BigCurveDeg      = 10.0 // これを超えたらスライス／フック
	// AxisNoiseDeg はスピン軸の予測と実測の差のうち、打点のせいにしない幅。
	AxisNoiseDeg = 2.0
	// StrikeShareMax は、フェース・トゥ・パスの説明の割合がこれ未満なら打点が主因。
	StrikeShareMax = 0.35
	// StrikeHitM は打点のずれをギア効果の確認に使う最小の大きさ（約5mm）。
	StrikeHitM = 0.005
	// MinReliableSpinLoftDeg より小さいスピンロフトでは D-plane の予測を使わない。
	// トップ・薄い当たりでは sin F / tan SL の分母が 0 に近づいて発散し、
	// 実データ（2026-09-17 の5番ウッド）で予測 -62° のような外れ値が出た。
	MinReliableSpinLoftDeg = 8.0
	// CenterStrikeM より内側は「芯」、ExtremeStrikeM より外は「極端」（ネック・先端寄り）。
	// 実データでは、ヒール 33〜37mm の球で TrackMan がクラブのデータを取れず、
	// ボールは右へ 38〜42m 飛び出した（アイアンで一番悪かった4球）。
	CenterStrikeM  = 0.010
	ExtremeStrikeM = 0.030
)

// Decomposition は1球ぶんの分解の結果。
type Decomposition struct {
	EngineVersion string `json:"engine_version"`

	// 打ち出し
	StartLine          string   `json:"start_line"` // push / straight / pull / unknown
	FaceWeight         float64  `json:"face_weight"`
	PredictedLaunchDir *float64 `json:"predicted_launch_direction,omitempty"`
	LaunchDirResidual  *float64 `json:"launch_direction_residual,omitempty"`
	StartCause         string   `json:"start_cause"` // face / path / face_and_path / unknown

	// 曲がり
	Curve            string   `json:"curve"` // hook / draw / straight / fade / slice / unknown
	PredictedAxisFTP *float64 `json:"predicted_axis_from_face_to_path,omitempty"`
	AxisResidual     *float64 `json:"axis_residual,omitempty"`
	FaceToPathShare  *float64 `json:"face_to_path_share,omitempty"` // 0..1
	CurveCause       string   `json:"curve_cause"`                  // face_to_path / strike / mixed / none / unknown
	StrikeConsistent *bool    `json:"strike_consistent,omitempty"`  // 残りの向きが打点のギア効果と合うか
	MissType         string   `json:"miss_type"`                    // 例: push-fade

	// 当たり方
	Contact string   `json:"contact"`         // center / heel / toe / heel_extreme / toe_extreme / unknown
	Flags   []string `json:"flags,omitempty"` // thin（薄い当たり・トップ） / no_club_data（クラブの値が取れていない）
	Notes   []string `json:"notes,omitempty"`
}

// FaceWeight は打ち出し方向に効くフェースの割合（初期値・要較正）。
// 残り（1 - w）はクラブパスが決める。ロフトが大きいほどパスの影響が増える。
func FaceWeight(category string) float64 {
	switch category {
	case "driver", "wood":
		return 0.85
	case "hybrid":
		return 0.80
	case "iron":
		return 0.75
	case "wedge":
		return 0.70
	}
	return 0.75
}

// PredictAxis は D-plane からスピン軸（度）を予測する。
//
// クラブの進む向きを v、フェースの向きを n とすると、スピン軸は v×n の向き。
// 上下の角をスピンロフト SL、左右の角をフェース・トゥ・パス F とすると、
// 水平からの傾きは atan( sin F / tan SL )。
// スピンロフトが小さい（ドライバー）ほど、同じ F でも大きく傾く。
func PredictAxis(faceToPathDeg, spinLoftDeg float64) (float64, bool) {
	if spinLoftDeg <= 1 { // 0 に近いと発散する。まともな計測ではまず起きない
		return 0, false
	}
	f := faceToPathDeg * math.Pi / 180
	sl := spinLoftDeg * math.Pi / 180
	return math.Atan(math.Sin(f)/math.Tan(sl)) * 180 / math.Pi, true
}

// ClubCategory はクラブ名から種類を決める（driver / wood / hybrid / iron / wedge / putter / unknown）。
func ClubCategory(club string) string {
	c := strings.ToLower(strings.ReplaceAll(club, " ", ""))
	switch {
	case c == "":
		return "unknown"
	case strings.Contains(c, "driver") || c == "dr" || c == "1w":
		return "driver"
	case strings.Contains(c, "putt"):
		return "putter"
	case strings.Contains(c, "wood") || (strings.HasSuffix(c, "w") && len(c) <= 3 && c[0] >= '2' && c[0] <= '9'):
		return "wood"
	case strings.Contains(c, "hybrid") || strings.Contains(c, "utility") || strings.HasSuffix(c, "h") || strings.HasSuffix(c, "ut"):
		return "hybrid"
	case strings.Contains(c, "wedge") || c == "pw" || c == "gw" || c == "aw" || c == "sw" || c == "lw" || c == "uw" ||
		(len(c) == 2 && c[0] >= '4' && c[0] <= '6' && c[1] >= '0' && c[1] <= '9'): // 52 / 56 / 60 度
		return "wedge"
	case strings.Contains(c, "iron") || strings.HasSuffix(c, "i"):
		return "iron"
	}
	return "unknown"
}

func ptr(v float64) *float64 { return &v }

// Decompose は1球を分解する。足りない値があれば、その部分だけ unknown にする。
func Decompose(m model.Metrics, category string) Decomposition {
	d := Decomposition{
		EngineVersion: EngineVersion,
		StartLine:     "unknown",
		StartCause:    "unknown",
		Curve:         "unknown",
		CurveCause:    "unknown",
		FaceWeight:    FaceWeight(category),
		Contact:       classifyContact(m.ImpactOffset),
	}
	if m.FaceAngle == nil && m.ClubPath == nil && m.DynamicLoft == nil && m.ClubSpeed == nil {
		d.Flags = append(d.Flags, "no_club_data")
	}
	thin := m.SpinLoft != nil && *m.SpinLoft < MinReliableSpinLoftDeg
	if thin {
		d.Flags = append(d.Flags, "thin")
	}

	// ---- 打ち出し ----
	if m.LaunchDirection != nil {
		d.StartLine = classifyStart(*m.LaunchDirection)
	}
	if m.FaceAngle != nil && m.ClubPath != nil {
		w := d.FaceWeight
		pred := w**m.FaceAngle + (1-w)**m.ClubPath
		d.PredictedLaunchDir = ptr(pred)
		if m.LaunchDirection != nil {
			d.LaunchDirResidual = ptr(*m.LaunchDirection - pred)
		}
		faceContrib := math.Abs(w * *m.FaceAngle)
		pathContrib := math.Abs((1 - w) * *m.ClubPath)
		switch {
		case d.StartLine == "straight":
			d.StartCause = "none"
		case faceContrib >= 2*pathContrib:
			d.StartCause = "face"
		case pathContrib >= 2*faceContrib:
			d.StartCause = "path"
		default:
			d.StartCause = "face_and_path"
		}
	}

	// ---- 曲がり ----
	if m.SpinAxis != nil {
		d.Curve = classifyCurve(*m.SpinAxis)
	}
	switch {
	case thin:
		d.Notes = append(d.Notes, "スピンロフトが小さい（薄い当たり・トップ）ので、曲がりの原因は判定しません")
	case m.FaceToPath != nil && m.SpinLoft != nil:
		if pred, ok := PredictAxis(*m.FaceToPath, *m.SpinLoft); ok {
			d.PredictedAxisFTP = ptr(pred)
			if m.SpinAxis != nil {
				resid := *m.SpinAxis - pred
				d.AxisResidual = ptr(resid)
				d.CurveCause, d.FaceToPathShare = curveCause(*m.SpinAxis, pred, resid)
				d.StrikeConsistent = strikeConsistent(resid, m.ImpactOffset, category)
				if d.StrikeConsistent != nil && !*d.StrikeConsistent && d.CurveCause != "face_to_path" && d.CurveCause != "none" {
					d.Notes = append(d.Notes, "予測との差の向きが打点のギア効果と逆です。計測か入力を確かめてください")
				}
			}
		} else {
			d.Notes = append(d.Notes, "スピンロフトが小さすぎてスピン軸を予測できません")
		}
	case d.Contact == "heel_extreme" || d.Contact == "toe_extreme":
		// フェースとパスが取れていなくても、打点が極端なら原因は打点と言える。
		// 極端なヒール（ネック寄り）はボールが右へ飛び出し、TrackMan もクラブを見失いやすい。
		d.CurveCause = "strike"
		d.Notes = append(d.Notes, "打点が極端（ネック・先端寄り）で、フェースとパスが取れていません。原因は打点です")
	}
	if d.Contact == "heel_extreme" {
		d.Notes = append(d.Notes, fmt.Sprintf("ヒール %.0fmm（ネック寄り）の当たりです", -*m.ImpactOffset*1000))
	} else if d.Contact == "toe_extreme" {
		d.Notes = append(d.Notes, fmt.Sprintf("トゥ %.0fmm（先端寄り）の当たりです", *m.ImpactOffset*1000))
	}
	if m.ImpactOffset == nil && (d.CurveCause == "strike" || d.CurveCause == "mixed") {
		d.Notes = append(d.Notes, "打点のデータがありません。インパクトテープの結果を入れると打点が原因かを確かめられます")
	}
	d.MissType = missType(d.StartLine, d.Curve)
	return d
}

func classifyContact(offset *float64) string {
	if offset == nil {
		return "unknown"
	}
	a := math.Abs(*offset)
	switch {
	case a < CenterStrikeM:
		return "center"
	case *offset < 0 && a >= ExtremeStrikeM:
		return "heel_extreme"
	case *offset < 0:
		return "heel"
	case a >= ExtremeStrikeM:
		return "toe_extreme"
	}
	return "toe"
}

func classifyStart(dir float64) string {
	switch {
	case dir > StraightStartDeg:
		return "push"
	case dir < -StraightStartDeg:
		return "pull"
	}
	return "straight"
}

func classifyCurve(axis float64) string {
	a := math.Abs(axis)
	switch {
	case a <= StraightCurveDeg:
		return "straight"
	case axis > 0 && a > BigCurveDeg:
		return "slice"
	case axis > 0:
		return "fade"
	case a > BigCurveDeg:
		return "hook"
	}
	return "draw"
}

// curveCause は曲がりのうち、フェース・トゥ・パスで説明できる割合から原因を決める。
func curveCause(axis, pred, resid float64) (string, *float64) {
	if math.Abs(axis) <= StraightCurveDeg && math.Abs(resid) <= AxisNoiseDeg {
		return "none", nil
	}
	if math.Abs(resid) <= AxisNoiseDeg {
		return "face_to_path", ptr(1)
	}
	share := math.Abs(pred) / (math.Abs(pred) + math.Abs(resid))
	switch {
	case share < StrikeShareMax:
		return "strike", ptr(share)
	case share >= 1-StrikeShareMax && math.Signbit(pred) == math.Signbit(axis):
		return "face_to_path", ptr(share)
	}
	return "mixed", ptr(share)
}

// strikeConsistent は、予測で説明できなかった残りの向きが打点のギア効果と合うかを見る。
// 右打ちでトゥに当たるとフック回転（スピン軸マイナス）、ヒールならスライス回転。
// アイアンはヘッドの重心が浅くギア効果が弱いので、残りが小さければ判断しない。
func strikeConsistent(resid float64, offset *float64, category string) *bool {
	if offset == nil || math.Abs(*offset) < StrikeHitM || math.Abs(resid) <= AxisNoiseDeg {
		return nil
	}
	if category == "wedge" || category == "putter" {
		return nil
	}
	ok := (*offset > 0 && resid < 0) || (*offset < 0 && resid > 0)
	return &ok
}

func missType(start, curve string) string {
	if start == "unknown" && curve == "unknown" {
		return "unknown"
	}
	if start == "straight" && curve == "straight" {
		return "straight"
	}
	return start + "-" + curve
}
