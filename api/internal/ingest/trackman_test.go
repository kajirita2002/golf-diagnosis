package ingest

import (
	"math"
	"os"
	"strings"
	"testing"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
	"github.com/kajirita2002/golf-diagnosis/api/internal/units"
)

func near(t *testing.T, name string, got *float64, want, tol float64) {
	t.Helper()
	if got == nil {
		t.Fatalf("%s: nil（%v を期待）", name, want)
	}
	if math.Abs(*got-want) > tol {
		t.Fatalf("%s: %v（%v を期待）", name, *got, want)
	}
}

func parse(t *testing.T, csv string, opt Options) *Result {
	t.Helper()
	res, err := TrackMan{}.Parse(strings.NewReader(csv), opt)
	if err != nil {
		t.Fatal(err)
	}
	return res
}

func TestダミーのセッションCSVを読める(t *testing.T) {
	f, err := os.Open("../../../testdata/trackman_dummy_session.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	res, err := TrackMan{}.Parse(f, Options{})
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Shots) != 30 {
		t.Fatalf("30球のはずが %d 球", len(res.Shots))
	}
	if res.Skipped != 1 {
		t.Fatalf("集計行1つを飛ばすはずが %d", res.Skipped)
	}
	for _, k := range []string{"club_speed", "dynamic_loft", "face_to_path", "spin_axis", "impact_offset", "low_point"} {
		if _, ok := res.Columns[k]; !ok {
			t.Errorf("%s の列を見つけていない（%v）", k, res.Columns)
		}
	}
	s := res.Shots[0]
	if s.Club != "7 Iron" || s.HitAt == nil {
		t.Fatalf("クラブ・時刻: %q %v", s.Club, s.HitAt)
	}
	near(t, "club_speed", s.Metrics.ClubSpeed, 79.0*0.44704, 1e-9)
	near(t, "carry", s.Metrics.Carry, 157.6*0.9144, 1e-9)
	near(t, "height(ft)", s.Metrics.Height, 93*0.3048, 1e-9)
	near(t, "low_point(in)", s.Metrics.LowPoint, 2.2*0.0254, 1e-9)
	// 使っていない列は黙って捨てずに返す
	found := false
	for _, h := range res.Ignored {
		if h == "Player" {
			found = true
		}
	}
	if !found {
		t.Fatalf("Player が ignored に無い: %v", res.Ignored)
	}
}

func Test列名の中の単位を読む(t *testing.T) {
	res := parse(t, "Club,Club Speed (m/s),Ball Speed [km/h],Carry (m),Spin Rate\n7i,36.5,172.8,148.3,6150\n", Options{Units: units.Imperial})
	s := res.Shots[0].Metrics
	near(t, "club_speed", s.ClubSpeed, 36.5, 1e-9)
	near(t, "ball_speed", s.BallSpeed, 48.0, 1e-9)
	near(t, "carry", s.Carry, 148.3, 1e-9)
}

func Test単位が無い列は指定の単位系で読む(t *testing.T) {
	csv := "Club,Club Speed,Carry\n7i,36.5,148.3\n"
	m := parse(t, csv, Options{Units: units.Metric}).Shots[0].Metrics
	near(t, "metric club_speed", m.ClubSpeed, 36.5, 1e-9)
	i := parse(t, csv, Options{Units: units.Imperial}).Shots[0].Metrics
	near(t, "imperial carry", i.Carry, 148.3*0.9144, 1e-9)
}

func Testセミコロン区切りと小数のカンマ(t *testing.T) {
	csv := "sep=;\nClub;Club Speed [mph];Face Angle [deg];Carry [yds]\n7i;81,6;-1,5;160,2\n"
	m := parse(t, csv, Options{}).Shots[0].Metrics
	near(t, "club_speed", m.ClubSpeed, 81.6*0.44704, 1e-9)
	near(t, "face_angle", m.FaceAngle, -1.5, 1e-9)
}

func Test向き付きの値を読む(t *testing.T) {
	csv := "Club,Club Speed [mph],Carry [yds],Side [yds],Low Point [in]\n7i,80,150,5.2 L,2.1 B\n"
	m := parse(t, csv, Options{}).Shots[0].Metrics
	near(t, "side", m.Side, -5.2*0.9144, 1e-9)
	near(t, "low_point", m.LowPoint, -2.1*0.0254, 1e-9)
}

func Test左打ちは右打ちの座標に揃える(t *testing.T) {
	csv := "Club,Club Speed [mph],Club Path [deg],Face Angle [deg],Spin Axis [deg],Side [yds],Impact Offset [in]\n7i,80,-3,-5,-6,-10,0.4\n"
	m := parse(t, csv, Options{Handedness: model.LeftHanded}).Shots[0].Metrics
	near(t, "club_path", m.ClubPath, 3, 1e-9)
	near(t, "face_angle", m.FaceAngle, 5, 1e-9)
	near(t, "spin_axis", m.SpinAxis, 6, 1e-9)
	near(t, "side", m.Side, 10*0.9144, 1e-9)
	// 打点はクラブに対する向きなので反転しない
	near(t, "impact_offset", m.ImpactOffset, 0.4*0.0254, 1e-9)
}

func Test優先度の高い列名を採る(t *testing.T) {
	// Carry Flat より Carry を採る（並び順に関係なく）
	res := parse(t, "Club,Club Speed,Carry Flat [yds],Carry [yds]\n7i,80,155,150\n", Options{})
	near(t, "carry", res.Shots[0].Metrics.Carry, 150*0.9144, 1e-9)
	if res.Columns["carry"] != "Carry [yds]" {
		t.Fatalf("columns: %v", res.Columns)
	}
}

func Test種類に合わない単位はエラー(t *testing.T) {
	_, err := TrackMan{}.Parse(strings.NewReader("Club,Club Speed [yds],Carry\n7i,80,150\n"), Options{})
	if err == nil {
		t.Fatal("速度に yds を許してしまった")
	}
}

func Test見出しが無ければエラー(t *testing.T) {
	_, err := TrackMan{}.Parse(strings.NewReader("a,b,c\n1,2,3\n"), Options{})
	if err == nil {
		t.Fatal("見出しの無い CSV を受けてしまった")
	}
}

func TestFillは計算で埋めた項目を返す(t *testing.T) {
	f, p, dl, aoa := 4.0, 1.0, 22.0, -4.0
	m := model.Metrics{FaceAngle: &f, ClubPath: &p, DynamicLoft: &dl, AttackAngle: &aoa}
	derived := Fill(&m)
	near(t, "face_to_path", m.FaceToPath, 3, 1e-9)
	near(t, "spin_loft", m.SpinLoft, 26, 1e-9)
	if len(derived) != 2 {
		t.Fatalf("derived: %v", derived)
	}
	// 計測器が出しているなら上書きしない
	ftp := 2.5
	m2 := model.Metrics{FaceAngle: &f, ClubPath: &p, FaceToPath: &ftp}
	Fill(&m2)
	near(t, "face_to_path（実測を残す）", m2.FaceToPath, 2.5, 1e-9)
}
