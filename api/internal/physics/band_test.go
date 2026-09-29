package physics

import (
	"math"
	"strings"
	"testing"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
)

// 帯の数字は docs/DESIGN_coaching.md §5.2・§11 のテスト節と同じ値で固定する。
// 帯の幅（lim）は本番では分析サービスの GOOD_TARGETS から来る。ここでは入力として
// 同じ大きさ（アイアン: max(5m, キャリーの5%)・ユーティリティ: max(6m, 6%)）を渡すだけで、
// physics の中には書かない。

func ironLim(carry float64) float64   { return math.Max(5, 0.05*carry) }
func hybridLim(carry float64) float64 { return math.Max(6, 0.06*carry) }

func TestBandはアイアンの中央値でパス04のとき窓がマイナス19からプラス21(t *testing.T) {
	carry := 118.9
	r, err := Band(carry, 24.3, ironLim(carry), 0.314, "iron", []float64{0.4})
	if err != nil {
		t.Fatal(err)
	}
	if len(r) != 1 || !r[0].OK {
		t.Fatalf("%+v", r)
	}
	w := r[0]
	if math.Abs(w.FaceMin-(-1.9)) > 0.2 || math.Abs(w.FaceMax-2.1) > 0.2 {
		t.Fatalf("窓 %.2f〜%.2f（設計書は −1.9〜+2.1）", w.FaceMin, w.FaceMax)
	}
	if !(w.FaceMin < w.FaceCenter && w.FaceCenter < w.FaceMax) {
		t.Fatalf("中央が窓の中に無い: %+v", w)
	}
	// 中央では左右がほぼ 0 に落ちる（打ち出しの右とスピン軸の左が打ち消し合う）
	side, _ := sideL1(w.FaceCenter, 0.4, carry, 24.3, 0.314, FaceWeight("iron"))
	if math.Abs(side) > 1e-6 {
		t.Fatalf("中央の左右 %.4f", side)
	}
	if math.Abs(w.LaunchAtCenter-(0.75*w.FaceCenter+0.25*0.4)) > 1e-9 {
		t.Fatalf("中央の打ち出し %v", w.LaunchAtCenter)
	}
	if ax, _ := PredictAxis(w.FaceCenter-0.4, 24.3); math.Abs(ax-w.AxisAtCenter) > 1e-9 {
		t.Fatalf("中央のスピン軸 %v", w.AxisAtCenter)
	}
}

func TestBandはフェースイコールパスの対角線ではなく傾き約031(t *testing.T) {
	carry := 118.9
	r, err := Band(carry, 24.3, ironLim(carry), 0.314, "iron", []float64{-2, 0, 2})
	if err != nil {
		t.Fatal(err)
	}
	slope := (r[2].FaceCenter - r[0].FaceCenter) / 4
	if math.Abs(slope-0.31) > 0.03 {
		t.Fatalf("帯の傾き %.3f（設計書: フェース ≈ 0.31×パス）", slope)
	}
	for _, x := range r {
		if half := (x.FaceMax - x.FaceMin) / 2; math.Abs(half-2.0) > 0.1 {
			t.Fatalf("パス %.1f の半幅 %.2f°（設計書: ±2.0°）", x.Path, half)
		}
	}
}

func TestBandは計算できない入力を断る(t *testing.T) {
	cases := []struct {
		name              string
		carry, sl, lim, k float64
		paths             []float64
		want              string
	}{
		{"薄い当たり", 118.9, 7.9, 5.9, 0.314, []float64{0}, "薄い当たり"},
		{"キャリーなし", 0, 24.3, 5.9, 0.314, []float64{0}, "キャリー"},
		{"幅なし", 118.9, 24.3, 0, 0.314, []float64{0}, "幅"},
		{"k が負", 118.9, 24.3, 5.9, -0.1, []float64{0}, "k"},
		{"k が NaN", 118.9, 24.3, 5.9, math.NaN(), []float64{0}, "k"},
		{"パスなし", 118.9, 24.3, 5.9, 0.314, nil, "パス"},
		{"パスが NaN", 118.9, 24.3, 5.9, 0.314, []float64{math.NaN()}, "パス"},
	}
	for _, c := range cases {
		if _, err := Band(c.carry, c.sl, c.lim, c.k, "iron", c.paths); err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: %v", c.name, err)
		}
	}
}

func TestBandは探す範囲の外に端があればそのパスだけokがfalse(t *testing.T) {
	// 曲がりを数えない（k=0）とき、パス +130° ではフェースを ±30° のどこにしても右へ外れる
	r, err := Band(118.9, 24.3, 5.9, 0, "iron", []float64{0, 130})
	if err != nil {
		t.Fatal(err)
	}
	if !r[0].OK || r[1].OK {
		t.Fatalf("%+v", r)
	}
}

func Test1球の帯は薄い当たりとフェースなしと極端な打点を数えない(t *testing.T) {
	base := func() model.Metrics {
		return model.Metrics{FaceAngle: f(1), ClubPath: f(0.4), FaceToPath: f(0.6), SpinLoft: f(24),
			Carry: f(120), LaunchDirection: f(0.9), SpinAxis: f(1.5), ImpactOffset: f(-0.005)}
	}
	if in, ok := InBand(base(), "iron", 6, 0.314); !ok || !in {
		t.Fatalf("まっすぐの球が帯に入らない: in=%v ok=%v", in, ok)
	}

	thin := base()
	thin.SpinLoft = f(5)
	noFace := base()
	noFace.FaceAngle, noFace.FaceToPath = nil, nil
	extreme := base() // #18 のようにフェースが取れていても、極端な打点は打点の段で数える
	extreme.ImpactOffset = f(-0.034)
	noPath := base()
	noPath.ClubPath, noPath.FaceToPath = nil, nil
	noCarry := base()
	noCarry.Carry = nil
	for _, c := range []struct {
		name string
		m    model.Metrics
		skip string
	}{{"thin", thin, "thin"}, {"フェースなし", noFace, "no_face"}, {"極端な打点", extreme, "extreme_strike"},
		{"パスなし", noPath, "no_prediction"}, {"キャリーなし", noCarry, "no_carry"}} {
		if _, ok := InBand(c.m, "iron", 6, 0.314); ok {
			t.Errorf("%s: ok=true", c.name)
		}
		d := Decompose(c.m, "iron")
		if d.BandEligible || d.BandSkip != c.skip {
			t.Errorf("%s: eligible=%v skip=%q", c.name, d.BandEligible, d.BandSkip)
		}
	}
}

func Test1球の分解に帯の2つの項が入る(t *testing.T) {
	m := model.Metrics{FaceAngle: f(6.2), ClubPath: f(0.2), FaceToPath: f(6.0), SpinLoft: f(24),
		Carry: f(130), LaunchDirection: f(5.2), SpinAxis: f(13.2)}
	d := Decompose(m, "iron")
	if !d.BandEligible || d.BandSkip != "" || d.PredictedSideLaunch == nil || d.PredictedSideCurveUnit == nil {
		t.Fatalf("%+v", d)
	}
	if want := 130 * sinDeg(*d.PredictedLaunchDir); math.Abs(*d.PredictedSideLaunch-want) > 1e-9 {
		t.Fatalf("打ち出しの項 %v（期待 %v）", *d.PredictedSideLaunch, want)
	}
	if want := 130 * sinDeg(*d.PredictedAxisFTP); math.Abs(*d.PredictedSideCurveUnit-want) > 1e-9 {
		t.Fatalf("曲がりの項 %v（期待 %v）", *d.PredictedSideCurveUnit, want)
	}
	side, ok := SideL1(m, "iron", 0.314)
	if !ok || math.Abs(side-(*d.PredictedSideLaunch+0.314**d.PredictedSideCurveUnit)) > 1e-9 {
		t.Fatalf("SideL1 %v", side)
	}
	// 図の帯と1球の判定が同じ式であること（中央値の球で帯の端を踏むと ±lim に落ちる）
	r, _ := Band(130, 24, 6.5, 0.314, "iron", []float64{0.2})
	edge := m
	edge.FaceAngle = f(r[0].FaceMax)
	edge.FaceToPath = f(r[0].FaceMax - 0.2)
	if s, _ := SideL1(edge, "iron", 0.314); math.Abs(s-6.5) > 1e-6 {
		t.Fatalf("帯の右端の球の左右 %.4f（期待 6.5）", s)
	}
}

func Test極端な打点の注記は実際に欠けた値を書く(t *testing.T) {
	note := func(m model.Metrics) string {
		for _, n := range Decompose(m, "iron").Notes {
			if strings.Contains(n, "が取れていません") {
				return n
			}
		}
		return ""
	}
	heel := f(-0.034)
	if n := note(model.Metrics{ClubPath: f(0.5), ImpactOffset: heel}); !strings.Contains(n, "、フェースが取れていません") {
		t.Fatalf("パスがある球: %q", n)
	}
	if n := note(model.Metrics{ImpactOffset: heel}); !strings.Contains(n, "フェースとパスが取れていません") {
		t.Fatalf("両方ない球: %q", n)
	}
	if n := note(model.Metrics{FaceAngle: f(1), ImpactOffset: heel}); !strings.Contains(n, "、パスが取れていません") {
		t.Fatalf("フェースだけある球: %q", n)
	}
	if n := note(model.Metrics{FaceToPath: f(1), ImpactOffset: heel}); !strings.Contains(n, "スピンロフトが取れていません") {
		t.Fatalf("スピンロフトが無い球: %q", n)
	}
}

// ---- 実データ（2026-09-17）----

func Test実データ_アイアンのインパクトが帯に入ったのは18球中7球(t *testing.T) {
	all := loadReal(t)
	in, right, left, skipped := 0, 0, 0, map[string]int{}
	var resid []float64
	for _, k := range []string{"6i", "7i", "8i", "9i"} {
		for _, s := range all[k] {
			side, ok := SideL1(s.m, "iron", 0.314)
			if !ok {
				skipped[s.d.BandSkip]++
				continue
			}
			switch lim := ironLim(*s.m.Carry); {
			case math.Abs(side) <= lim:
				in++
			case side > 0:
				right++
			default:
				left++
			}
			resid = append(resid, *s.m.Side-side)
		}
	}
	if in != 7 || right != 9 || left != 2 {
		t.Fatalf("帯の中 %d・右 %d・左 %d（設計書: 7・9・2）", in, right, left)
	}
	// 26球 = 帯に数えた18 + フェースなし7 + 極端な打点でフェースあり1（#18）
	if skipped["no_face"] != 7 || skipped["extreme_strike"] != 1 {
		t.Fatalf("数えなかった球: %v", skipped)
	}
	// 予測だけで出した左右の残差（設計書は 2.1m。#18 を含めた19球で計算している）
	if sd := sampleSD(resid); math.Abs(sd-2.1) > 0.2 {
		t.Fatalf("残差の SD %.2fm", sd)
	}
}

func Test実データ_4番UTはまとめたkなら7球中2球で自分だけのkなら1球(t *testing.T) {
	count := func(k float64) (in, n int) {
		for _, s := range loadReal(t)["4h"] {
			side, ok := SideL1(s.m, "hybrid", k)
			if !ok {
				continue // #30（4H の4球目）は thin で自動的に外れる
			}
			n++
			if math.Abs(side) <= hybridLim(*s.m.Carry) {
				in++
			}
		}
		return
	}
	if in, n := count(0.375); in != 2 || n != 7 {
		t.Fatalf("まとめた k: %d/%d（設計書: 2/7）", in, n)
	}
	if in, n := count(0.487); in != 1 || n != 7 {
		t.Fatalf("4番UT だけの k: %d/%d（設計書: 1/7）", in, n)
	}
}

func sampleSD(v []float64) float64 {
	var mu float64
	for _, x := range v {
		mu += x
	}
	mu /= float64(len(v))
	var ss float64
	for _, x := range v {
		ss += (x - mu) * (x - mu)
	}
	return math.Sqrt(ss / float64(len(v)-1))
}
