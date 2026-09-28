package physics

import (
	"math"
	"testing"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
)

func f(v float64) *float64 { return &v }

func TestPredictAxisはスピンロフトが小さいほど大きく傾く(t *testing.T) {
	iron, _ := PredictAxis(3, 25)
	driver, _ := PredictAxis(3, 12)
	if !(driver > iron && iron > 3) {
		t.Fatalf("iron=%.2f driver=%.2f", iron, driver)
	}
	// atan(sin3° / tan25°) ≈ 6.4°
	if math.Abs(iron-6.40) > 0.05 {
		t.Fatalf("iron=%.3f", iron)
	}
	if z, _ := PredictAxis(0, 25); z != 0 {
		t.Fatalf("F=0 なら傾かない: %v", z)
	}
	if neg, _ := PredictAxis(-3, 25); math.Abs(neg+iron) > 1e-9 {
		t.Fatalf("左右対称でない: %v", neg)
	}
	if _, ok := PredictAxis(3, 0.5); ok {
		t.Fatal("スピンロフト≒0 を予測してしまった")
	}
}

func Testフェースが原因のプッシュフェード(t *testing.T) {
	pred, _ := PredictAxis(3, 25)
	m := model.Metrics{
		FaceAngle: f(5.5), ClubPath: f(2.5), FaceToPath: f(3), SpinLoft: f(25),
		LaunchDirection: f(4.8), SpinAxis: f(pred + 0.5),
	}
	d := Decompose(m, "iron")
	if d.MissType != "push-fade" || d.CurveCause != "face_to_path" || d.StartCause != "face" {
		t.Fatalf("%+v", d)
	}
	if d.PredictedLaunchDir == nil || math.Abs(*d.PredictedLaunchDir-(0.75*5.5+0.25*2.5)) > 1e-9 {
		t.Fatalf("predicted launch: %v", d.PredictedLaunchDir)
	}
}

func Testヒール打ちのスライスは打点が原因(t *testing.T) {
	// フェース・トゥ・パスはほぼ0なのに、スピン軸は +8°。ヒールに15mm。
	m := model.Metrics{
		FaceAngle: f(1), ClubPath: f(1), FaceToPath: f(0.3), SpinLoft: f(25),
		LaunchDirection: f(1), SpinAxis: f(8), ImpactOffset: f(-0.015),
	}
	d := Decompose(m, "iron")
	if d.CurveCause != "strike" {
		t.Fatalf("cause=%s share=%v", d.CurveCause, *d.FaceToPathShare)
	}
	if d.StrikeConsistent == nil || !*d.StrikeConsistent {
		t.Fatalf("ヒール → スライス回転は整合するはず: %v", d.StrikeConsistent)
	}
	if d.MissType != "straight-fade" {
		t.Fatalf("miss=%s", d.MissType)
	}
}

func Test打点と逆向きの残りは注意を出す(t *testing.T) {
	// トゥに当たったのにスライス回転が増えている → 計測か入力を疑う
	m := model.Metrics{FaceToPath: f(0.3), SpinLoft: f(25), SpinAxis: f(8), ImpactOffset: f(0.015)}
	d := Decompose(m, "iron")
	if d.StrikeConsistent == nil || *d.StrikeConsistent {
		t.Fatalf("%v", d.StrikeConsistent)
	}
	if len(d.Notes) == 0 {
		t.Fatal("注意が出ていない")
	}
}

func Test打点が無ければ入れるよう促す(t *testing.T) {
	m := model.Metrics{FaceToPath: f(0.3), SpinLoft: f(25), SpinAxis: f(8)}
	d := Decompose(m, "iron")
	if d.CurveCause != "strike" || d.StrikeConsistent != nil || len(d.Notes) == 0 {
		t.Fatalf("%+v", d)
	}
}

func Test足りない値はunknownのまま(t *testing.T) {
	d := Decompose(model.Metrics{}, "iron")
	if d.MissType != "unknown" || d.CurveCause != "unknown" || d.StartCause != "unknown" {
		t.Fatalf("%+v", d)
	}
}

func Testまっすぐ(t *testing.T) {
	m := model.Metrics{FaceAngle: f(0.5), ClubPath: f(0.5), FaceToPath: f(0), SpinLoft: f(25), LaunchDirection: f(0.5), SpinAxis: f(0.8)}
	d := Decompose(m, "iron")
	if d.MissType != "straight" || d.CurveCause != "none" || d.StartCause != "none" {
		t.Fatalf("%+v", d)
	}
}

func Test曲がりの分類(t *testing.T) {
	cases := map[float64]string{-15: "hook", -5: "draw", 0: "straight", 3: "straight", 5: "fade", 12: "slice"}
	for axis, want := range cases {
		if got := classifyCurve(axis); got != want {
			t.Errorf("axis %v: %s（%s を期待）", axis, got, want)
		}
	}
}

func TestClubCategory(t *testing.T) {
	cases := map[string]string{
		"Driver": "driver", "3 Wood": "wood", "3W": "wood", "4 Hybrid": "hybrid", "4H": "hybrid",
		"7 Iron": "iron", "7i": "iron", "PW": "wedge", "Sand Wedge": "wedge", "56": "wedge",
		"Putter": "putter", "": "unknown",
	}
	for in, want := range cases {
		if got := ClubCategory(in); got != want {
			t.Errorf("%q: %s（%s を期待）", in, got, want)
		}
	}
}
