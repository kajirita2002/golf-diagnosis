package physics

import (
	"math"
	"os"
	"sort"
	"testing"

	"github.com/kajirita2002/golf-diagnosis/api/internal/ingest"
	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
	"github.com/kajirita2002/golf-diagnosis/api/internal/units"
)

// 実データ（testdata/real/2026-09-17。本人の TrackMan の練習）で、分解の式が
// 本物の球に合っていることを固定する。閾値を変えたらここが落ちるので、
// 落ちたら実データに対して何が変わったかを確かめてから直すこと。

type realShot struct {
	club string
	seq  int
	m    model.Metrics
	d    Decomposition
}

func loadReal(t *testing.T) map[string][]realShot {
	t.Helper()
	clubs := map[string]string{"6i": "6 Iron", "7i": "7 Iron", "8i": "8 Iron", "9i": "9 Iron", "4h": "4 Hybrid", "5w": "5 Wood"}
	out := map[string][]realShot{}
	for file, club := range clubs {
		f, err := os.Open("../../../testdata/real/2026-09-17/" + file + ".tsv")
		if err != nil {
			t.Fatal(err)
		}
		res, err := ingest.TrackMan{}.Parse(f, ingest.Options{Units: units.Metric, Club: club})
		f.Close()
		if err != nil {
			t.Fatalf("%s: %v", file, err)
		}
		for i, in := range res.Shots {
			m := in.Metrics
			ingest.Fill(&m)
			out[file] = append(out[file], realShot{club, i + 1, m, Decompose(m, ClubCategory(club))})
		}
	}
	return out
}

func Test実データ_アイアンとユーティリティは予測が実測に合う(t *testing.T) {
	var res []float64
	for _, k := range []string{"6i", "7i", "8i", "9i", "4h"} {
		for _, s := range loadReal(t)[k] {
			if s.d.AxisResidual != nil {
				res = append(res, math.Abs(*s.d.AxisResidual))
			}
		}
	}
	if len(res) < 20 {
		t.Fatalf("比べられた球が %d しかない", len(res))
	}
	sort.Float64s(res)
	if med := res[len(res)/2]; med > 1.0 {
		t.Fatalf("予測と実測のずれの中央値 %.2f°", med)
	}
	if max := res[len(res)-1]; max > 4.0 {
		t.Fatalf("予測と実測のずれの最大 %.2f°", max)
	}
}

func Test実データ_5番ウッドのギア効果はImpOffsetのマイナスがヒール(t *testing.T) {
	checked := 0
	for _, s := range loadReal(t)["5w"] {
		if s.d.StrikeConsistent == nil {
			continue
		}
		checked++
		if !*s.d.StrikeConsistent {
			t.Errorf("5W #%d: 打点 %.0fmm と予測からのずれ %.1f° の向きがギア効果と逆", s.seq, *s.m.ImpactOffset*1000, *s.d.AxisResidual)
		}
	}
	if checked < 8 {
		t.Fatalf("確かめられた球が %d しかない", checked)
	}
}

func Test実データ_極端なヒール打ちを打点が原因と言う(t *testing.T) {
	want := map[string][]int{"6i": {5}, "8i": {3}, "9i": {4, 6}}
	all := loadReal(t)
	for k, seqs := range want {
		for _, n := range seqs {
			s := all[k][n-1]
			if s.d.Contact != "heel_extreme" || s.d.CurveCause != "strike" {
				t.Errorf("%s #%d: contact=%s cause=%s", k, n, s.d.Contact, s.d.CurveCause)
			}
		}
	}
}

func Test実データ_薄い当たりは原因を判定しない(t *testing.T) {
	all := loadReal(t)
	for _, c := range []struct {
		k string
		n int
	}{{"5w", 5}, {"5w", 12}, {"5w", 17}, {"4h", 4}} {
		s := all[c.k][c.n-1]
		thin := false
		for _, f := range s.d.Flags {
			thin = thin || f == "thin"
		}
		if !thin || s.d.CurveCause != "unknown" {
			t.Errorf("%s #%d: flags=%v cause=%s", c.k, c.n, s.d.Flags, s.d.CurveCause)
		}
	}
}
