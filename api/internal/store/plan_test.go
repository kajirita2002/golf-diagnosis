package store

import (
	"context"
	"errors"
	"os"
	"testing"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
)

// openTest は SQLite のメモリで開く。PostgreSQL（TEST_DATABASE_URL）は httpapi のテストが同じ経路を通す。
// ここでも PostgreSQL を使うと、go test ./... がパッケージを並べて走らせるので、同じ golf スキーマを
// httpapi のテストと取り合って消し合う。STORE_PG_TEST=1 のときだけ PostgreSQL で開く
// （go test -p 1 ./... か、このパッケージだけを走らせるときに使う）。
func openTest(t *testing.T) *Store {
	t.Helper()
	dsn := ":memory:"
	if pg := os.Getenv("TEST_DATABASE_URL"); pg != "" && os.Getenv("STORE_PG_TEST") == "1" {
		if err := DropForTest(pg); err != nil {
			t.Fatal(err)
		}
		dsn = pg
	}
	st, err := Open(dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	return st
}

func testPlan(pid int64) *model.Plan {
	return &model.Plan{PlayerID: pid, Issue: "strike_heel", Club: "9 Iron", TargetMetric: "impact_offset", Goal: model.GoalReduceAbs,
		Cue: "x", Template: []model.PlanBlock{{Kind: model.BlockBaseline, N: 8}, {Kind: model.BlockIntervention, N: 8}}}
}

// 動いているプランは選手ごとに1つ。最後の守りは部分索引（plans_one_active）で、SQL を直に打っても通らない。
func Test動いているプランは1つだけ(t *testing.T) {
	st := openTest(t)
	ctx := context.Background()
	pl := model.Player{Name: "a", Handedness: model.RightHanded}
	if err := st.CreatePlayer(ctx, &pl); err != nil {
		t.Fatal(err)
	}
	p1 := testPlan(pl.ID)
	if err := st.CreatePlan(ctx, p1, false); err != nil {
		t.Fatal(err)
	}
	var ce *ConflictError
	if err := st.CreatePlan(ctx, testPlan(pl.ID), false); !errors.As(err, &ce) || ce.ExistingID != p1.ID || !errors.Is(err, ErrConflict) {
		t.Fatalf("%v", err)
	}
	_, err := st.db.ExecContext(ctx, `INSERT INTO {s}plans(player_id, issue, drill_id, catalog_version, club, target_metric, goal, template_json, params_json, trigger_json, rationale_json, status, engine_version, created_at)
		VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, pl.ID, "x", "", "", "9 Iron", "carry", "increase", "[]", "{}", "{}", "{}", "active", "", now())
	if !isUniqueViolation(err) {
		t.Fatalf("部分索引が効いていない: %v", err)
	}
	// 閉じたものはいくつあってもよい
	if _, err := st.SetPlanStatus(ctx, p1.ID, model.PlanDone, "定着した"); err != nil {
		t.Fatal(err)
	}
	p2 := testPlan(pl.ID)
	if err := st.CreatePlan(ctx, p2, false); err != nil {
		t.Fatal(err)
	}
	if err := st.CreatePlan(ctx, testPlan(pl.ID), true); err != nil {
		t.Fatal(err)
	}
	if got, _ := st.GetPlan(ctx, p2.ID); got.Status != model.PlanSwitched || got.ClosedAt == nil {
		t.Fatalf("%+v", got)
	}
}

func Test1セッションにrunは1つとブロックの置き換え(t *testing.T) {
	st := openTest(t)
	ctx := context.Background()
	pl := model.Player{Name: "a", Handedness: model.RightHanded}
	_ = st.CreatePlayer(ctx, &pl)
	se := model.Session{PlayerID: pl.ID, Date: "2026-09-29"}
	_ = st.CreateSession(ctx, &se)
	p := testPlan(pl.ID)
	_ = st.CreatePlan(ctx, p, false)
	e := &model.Experiment{SessionID: se.ID, Hypothesis: "h", TargetMetric: p.TargetMetric, Goal: p.Goal, Club: p.Club}
	run, err := st.CreatePlanRun(ctx, p.ID, e, []int{8, 8})
	if err != nil {
		t.Fatal(err)
	}
	if run.SessionDate != "2026-09-29" || e.ID == 0 || run.ExperimentID != e.ID {
		t.Fatalf("%+v", run)
	}
	var ce *ConflictError
	if _, err := st.CreatePlanRun(ctx, p.ID, &model.Experiment{SessionID: se.ID, Hypothesis: "h", TargetMetric: "carry", Goal: model.GoalIncrease}, nil); !errors.As(err, &ce) || ce.ExistingID != run.ID {
		t.Fatalf("%v", err)
	}
	// 2つ目の run の実験は残らない（1トランザクション）
	if es, _ := st.ListExperiments(ctx, se.ID); len(es) != 1 {
		t.Fatalf("実験 %d 個", len(es))
	}
	if err := st.SavePlanRunEvaluation(ctx, run.ID, "k", []byte(`{"a":1}`)); err != nil {
		t.Fatal(err)
	}
	bs := []model.Block{{Kind: model.BlockWarmup, SeqFrom: 1, SeqTo: 5}, {Kind: model.BlockBaseline, SeqFrom: 6, SeqTo: 13}}
	if _, err := st.ReplaceBlocks(ctx, e.ID, bs); err != nil {
		t.Fatal(err)
	}
	if got, _ := st.GetPlanRun(ctx, run.ID); string(got.Evaluation) != "null" || got.EvaluationKey != "" {
		t.Fatalf("境目を動かしたのに評価が残った: %s", got.Evaluation)
	}
	if _, err := st.ReplaceBlocks(ctx, e.ID, []model.Block{{Kind: model.BlockDrill, SeqFrom: 1, SeqTo: 3}}); err != nil {
		t.Fatal(err)
	}
	got, _ := st.GetExperiment(ctx, e.ID)
	if len(got.Blocks) != 1 || got.Blocks[0].Kind != model.BlockDrill {
		t.Fatalf("前のブロックが残った: %+v", got.Blocks)
	}
	if _, err := st.ReplaceBlocks(ctx, e.ID, []model.Block{{Kind: model.BlockBaseline, SeqFrom: 1, SeqTo: 5}, {Kind: model.BlockDrill, SeqFrom: 5, SeqTo: 6}}); err == nil {
		t.Fatal("重なりを通した")
	}
	if got, _ := st.GetExperiment(ctx, e.ID); len(got.Blocks) != 1 {
		t.Fatalf("失敗した置き換えで消えた: %+v", got.Blocks)
	}
	if _, err := st.ReplaceBlocks(ctx, 999, nil); !errors.Is(err, ErrNotFound) {
		t.Fatalf("%v", err)
	}
}
