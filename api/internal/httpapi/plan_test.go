package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"github.com/kajirita2002/golf-diagnosis/api/internal/analysis"
	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
)

// 偽の分析サービスのプランの口。評価は入力の形だけを返す（数字は作らない）。
type planFake struct {
	evalIns   []analysis.PlanEvalInput
	drillsOut string
	evalDown  bool
	// drillsHand は最後に Drills に渡された利き手。buildIns は PlanBuild の入力。
	drillsHand model.Handedness
	buildIns   []analysis.PlanBuildInput
	buildOut   string
	// evalState があれば、評価に分析サービスの形の grade と progress（状態）を入れて返す。
	evalState string
	// evalExtra は評価に足すキー（progress.next_counts など）。
	evalExtra map[string]any
	candIns   []analysis.PlanCandidatesInput
	candOut   string
	// version は分析サービスの版（/healthz）。空なら "analysis/test plan/test"。
	version string
}

func (f *fakeAnalyzer) PlanCandidates(_ context.Context, in analysis.PlanCandidatesInput) (json.RawMessage, error) {
	p := f.pf()
	if f.down {
		return nil, analysis.ErrUnavailable
	}
	p.candIns = append(p.candIns, in)
	if p.candOut != "" {
		return json.RawMessage(p.candOut), nil
	}
	return json.RawMessage(`{"scopes":[]}`), nil
}

func (f *fakeAnalyzer) Versions(_ context.Context) (string, error) {
	if f.down {
		return "", analysis.ErrUnavailable
	}
	if v := f.pf().version; v != "" {
		return v, nil
	}
	return "analysis/test plan/test", nil
}

// PlanBuild は分析サービスの /v1/plan/build の偽物。buildOut があればそれを、無ければ入力を写した形を返す。
func (f *fakeAnalyzer) PlanBuild(_ context.Context, in analysis.PlanBuildInput) (json.RawMessage, error) {
	p := f.pf()
	if f.down {
		return nil, analysis.ErrUnavailable
	}
	p.buildIns = append(p.buildIns, in)
	if p.buildOut != "" {
		return json.RawMessage(p.buildOut), nil
	}
	drill := in.DrillID
	b, _ := json.Marshal(map[string]any{
		"plan_version": "plan/0.1",
		"plan": map[string]any{
			"issue": in.CandidateID, "title": "打点をヒールから真ん中へ", "lever": nil, "drill_id": drill, "catalog_version": "drills/0.1",
			"club": "9 Iron", "target_metric": "impact_offset", "goal": "reduce_abs",
			"template": smallTemplate, "params": map[string]any{"metric": "impact_offset", "window": in.Window},
			"trigger": map[string]any{"scope_id": in.ScopeID, "n": len(in.Shots)}, "rationale": map[string]any{"slot": "now"},
			"engine_version": "plan/0.1",
		},
		"drill":        nil,
		"intervention": in.Cue,
		"claims":       []map[string]any{{"id": in.ScopeID + "/now.hypothesis", "text": "仮説: 9番で打点が真ん中へ寄る"}},
		"notes":        []string{"確かめ済みのドリルがまだ無いので、本番で意識する1点を自分で書いてください。"},
	})
	return b, nil
}

func (f *fakeAnalyzer) pf() *planFake { return &f.plan }

func (f *fakeAnalyzer) PlanEvaluate(_ context.Context, in analysis.PlanEvalInput) (json.RawMessage, error) {
	p := f.pf()
	if f.down || p.evalDown {
		return nil, analysis.ErrUnavailable
	}
	p.evalIns = append(p.evalIns, in)
	out := map[string]any{"fake": "plan_eval", "run_id": in.Run.ID, "index": in.Run.Index, "n_past": len(in.PastRuns), "call": len(p.evalIns), "state": "checking"}
	if p.evalState != "" {
		out["grade"], out["counts_as_worked"] = "moderate", true
		out["progress"] = map[string]any{"state": p.evalState, "title": "効いたかも", "record": map[string]any{"runs": len(in.PastRuns) + 1, "moderate_plus": len(in.PastRuns) + 1}}
	}
	if in.ContinueAfterRun != nil {
		out["continue_after_run"] = *in.ContinueAfterRun
	}
	for k, v := range p.evalExtra {
		out[k] = v
	}
	b, _ := json.Marshal(out)
	return b, nil
}

// 既定のドリル集: 中身はあるが、人が確かめていない（checked_by が空）。
const uncheckedCatalog = `{"catalog_version":"drills/0.1","drills":[
 {"id":"strike.two_balls","title":"2球並べ","cue_transfer":"外にボールがあるつもりで打つ","checked_by":null},
 {"id":"face.start_spot","title":"打ち出しの印","checked_by":""}]}`

func (f *fakeAnalyzer) Drills(_ context.Context, hand model.Handedness) (json.RawMessage, error) {
	f.pf().drillsHand = hand
	if f.down {
		return nil, analysis.ErrUnavailable
	}
	if s := f.pf().drillsOut; s != "" {
		return json.RawMessage(s), nil
	}
	return json.RawMessage(uncheckedCatalog), nil
}

// 9番（プランのクラブ）の球の間に 6番を2球はさんだセッション。9番は16球（#1〜#18 のうち #5・#11 が 6番）。
func planCSV() string {
	var b strings.Builder
	b.WriteString("Club,Club Speed [mph],Carry [yds],Face Angle [deg],Club Path [deg]\n")
	for i := 1; i <= 18; i++ {
		club := "9 Iron"
		if i == 5 || i == 11 {
			club = "6 Iron"
		}
		fmt.Fprintf(&b, "%s,%d,%d,%.1f,0.5\n", club, 75+i%3, 130+i, float64(i%5)-1)
	}
	return b.String()
}

// warmup 2 → A 3 → drill 2 → B 3 → A 3（合計13球）
var smallTemplate = []map[string]any{
	{"kind": "warmup", "n": 2}, {"kind": "baseline", "n": 3}, {"kind": "drill", "n": 2},
	{"kind": "intervention", "n": 3}, {"kind": "baseline", "n": 3},
}

func planBody(extra map[string]any) map[string]any {
	b := map[string]any{
		"issue": "strike_heel", "club": "9 Iron", "target_metric": "impact_offset", "goal": "reduce_abs",
		"cue": "外にボールがあるつもりで打つ", "template": smallTemplate,
		"params": map[string]any{"k": 0.314, "side_pct": 0.05}, "rationale": map[string]any{"gates": []string{"G1"}},
		"engine_version": "plan/0.1",
	}
	for k, v := range extra {
		b[k] = v
	}
	return b
}

func num(v any) int { return int(v.(float64)) }

func (e *env) playerOf(sid int) int {
	return num(e.do("GET", fmt.Sprintf("/v1/sessions/%d", sid), nil, 200)["player_id"])
}

func Testドリル集は確かめ済みのものだけ出す(t *testing.T) {
	e := newEnv(t)
	out := e.do("GET", "/v1/drills", nil, 200)
	if ds := out["drills"].([]any); len(ds) != 0 {
		t.Fatalf("checked_by の無いドリルが出た: %v", ds)
	}
	if out["catalog_version"] != "drills/0.1" {
		t.Fatalf("ほかのキーが落ちた: %v", out)
	}
	// 人が確かめたら出る（配列の形でも受ける）
	e.an.pf().drillsOut = `[{"id":"a","checked_by":"rita"},{"id":"b"}]`
	raw := e.do("GET", "/v1/drills", nil, 200)["_raw"].(string)
	if !strings.Contains(raw, `"a"`) || strings.Contains(raw, `"b"`) {
		t.Fatalf("%s", raw)
	}
}

func Testプランは選手ごとに1つだけ動かせる(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("R")
	pid := e.playerOf(sid)
	base := fmt.Sprintf("/v1/players/%d/plans", pid)

	// 確かめ済みのドリルが無いときは、意識する1点を書かないと作れない
	e.do("POST", base, planBody(map[string]any{"cue": ""}), 400)
	// 確かめていないドリルは選べない
	e.do("POST", base, planBody(map[string]any{"drill_id": "strike.two_balls"}), 400)
	e.do("POST", base, planBody(map[string]any{"template": []map[string]any{{"kind": "baseline", "n": 5}}}), 400)
	e.do("POST", base, planBody(map[string]any{"template": []map[string]any{{"kind": "baseline", "n": 13}, {"kind": "intervention", "n": 5}}}), 400)
	e.do("POST", base, planBody(map[string]any{"target_metric": "nope"}), 400)
	e.do("POST", base, planBody(map[string]any{"club": ""}), 400)
	e.do("POST", base, planBody(map[string]any{"params": []int{1}}), 400)

	p1 := e.do("POST", base, planBody(map[string]any{"from_session": sid}), 201)
	if p1["status"] != "active" || p1["drill_id"] != "" || !strings.Contains(p1["hypothesis"].(string), "仮説") {
		t.Fatalf("%v", p1)
	}
	if tr := p1["trigger"].(map[string]any); num(tr["session_id"]) != sid {
		t.Fatalf("きっかけのセッション: %v", tr)
	}
	if p1["params"].(map[string]any)["k"].(float64) != 0.314 {
		t.Fatalf("params が固定されていない: %v", p1["params"])
	}

	// 2つ目は 409（相手の id つき）
	c := e.do("POST", base, planBody(nil), 409)
	if num(c["existing_id"]) != num(p1["id"]) {
		t.Fatalf("%v", c)
	}
	// ?replace=1 なら前のものを switched にして作る
	p2 := e.do("POST", base+"?replace=1", planBody(nil), 201)
	old := e.do("GET", fmt.Sprintf("/v1/plans/%d", num(p1["id"])), nil, 200)
	if old["status"] != "switched" || old["closed_at"] == nil {
		t.Fatalf("前のプラン: %v", old)
	}
	// 前のものを active に戻すのも 409
	e.do("PATCH", fmt.Sprintf("/v1/plans/%d", num(p1["id"])), map[string]any{"status": "active"}, 409)
	e.do("PATCH", fmt.Sprintf("/v1/plans/%d", num(p2["id"])), map[string]any{"status": "finished"}, 400)
	done := e.do("PATCH", fmt.Sprintf("/v1/plans/%d", num(p2["id"])), map[string]any{"status": "done", "close_reason": "定着した"}, 200)
	if done["status"] != "done" || done["close_reason"] != "定着した" || done["closed_at"] == nil {
		t.Fatalf("%v", done)
	}
	// 動いているものが無くなれば戻せる（closed_at は消える）
	back := e.do("PATCH", fmt.Sprintf("/v1/plans/%d", num(p1["id"])), map[string]any{"status": "active"}, 200)
	if back["status"] != "active" || back["closed_at"] != nil {
		t.Fatalf("%v", back)
	}
	if ps := e.list(base); len(ps) != 2 || num(ps[0]["id"]) != num(p2["id"]) {
		t.Fatalf("一覧は新しい順: %v", ps)
	}
	// 別の選手は別に1つ持てる
	other := e.do("POST", "/v1/players", map[string]any{"name": "B"}, 201)
	e.do("POST", fmt.Sprintf("/v1/players/%d/plans", num(other["id"])), planBody(nil), 201)
	e.do("POST", "/v1/players/999/plans", planBody(nil), 404)
}

func Test確かめ済みのドリルならドリルでプランを作れる(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("R")
	e.an.pf().drillsOut = `{"catalog_version":"drills/0.2","drills":[{"id":"strike.two_balls","cue_transfer":"外にボールがあるつもりで打つ","checked_by":"rita 2026-09-29"}]}`
	p := e.do("POST", fmt.Sprintf("/v1/players/%d/plans", e.playerOf(sid)), planBody(map[string]any{"drill_id": "strike.two_balls", "cue": ""}), 201)
	if p["catalog_version"] != "drills/0.2" || p["cue"] != "外にボールがあるつもりで打つ" {
		t.Fatalf("%v", p)
	}
}

// 1つのプランで練習の日を作り、取り込み → 割り当て → 評価まで。
func Testプランの練習の日ごとの流れ(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("R")
	pid := e.playerOf(sid)
	plan := e.do("POST", fmt.Sprintf("/v1/players/%d/plans", pid), planBody(nil), 201)
	planID := num(plan["id"])

	// run を作ると既存の Experiment ができて紐づく（まだ球が無いのでブロックは空）
	run := e.do("POST", fmt.Sprintf("/v1/plans/%d/runs", planID), map[string]any{"session_id": sid}, 201)
	runID := num(run["id"])
	ex := run["experiment"].(map[string]any)
	if ex["club"] != "9 Iron" || ex["target_metric"] != "impact_offset" || ex["intervention"] != "外にボールがあるつもりで打つ" || num(run["index"]) != 0 {
		t.Fatalf("%v", run)
	}
	if run["blocks_source"] != "planned" || len(run["blocks"].([]any)) != 0 || num(run["planned"]) != 13 {
		t.Fatalf("%v", run)
	}
	if es := e.list(fmt.Sprintf("/v1/sessions/%d/experiments", sid)); len(es) != 1 || num(es[0]["id"]) != num(ex["id"]) {
		t.Fatalf("既存の実験として見えない: %v", es)
	}
	// 1セッションに1つまで
	c := e.do("POST", fmt.Sprintf("/v1/plans/%d/runs", planID), map[string]any{"session_id": sid}, 409)
	if num(c["existing_id"]) != runID {
		t.Fatalf("%v", c)
	}
	e.do("POST", fmt.Sprintf("/v1/plans/%d/runs", planID), map[string]any{"session_id": 999}, 400)
	e.do("POST", fmt.Sprintf("/v1/plans/%d/runs", planID), map[string]any{"session_id": sid, "counts": []int{1}}, 400) // 型と段の数が違う
	if got := e.do("GET", fmt.Sprintf("/v1/sessions/%d/plan-run", sid), nil, 200); num(got["id"]) != runID {
		t.Fatalf("%v", got)
	}

	// 取り込むと、9番の球を打った順に型の球数で区切る（6番の #5・#11 は数えない）
	e.importCSV(sid, planCSV(), "", 201)
	got := e.do("GET", fmt.Sprintf("/v1/plan-runs/%d", runID), nil, 200)
	bs := got["blocks"].([]any)
	want := [][3]any{{"warmup", 1, 2}, {"baseline", 3, 6}, {"drill", 7, 8}, {"intervention", 9, 12}, {"baseline", 13, 15}}
	if len(bs) != len(want) {
		t.Fatalf("%v", bs)
	}
	for i, w := range want {
		b := bs[i].(map[string]any)
		if b["kind"] != w[0] || num(b["seq_from"]) != w[1] || num(b["seq_to"]) != w[2] {
			t.Fatalf("ブロック %d: %v（%v を期待）", i, b, w)
		}
	}
	if got["blocks_source"] != "planned" || got["mismatch"] != true || fmt.Sprint(got["unassigned_seqs"]) != "[16 17 18]" {
		t.Fatalf("予定13球・9番16球: %v %v %v", got["blocks_source"], got["mismatch"], got["unassigned_seqs"])
	}

	// 分析（/analysis）へ渡す球に block_kind が付く。6番の球（ブロックの範囲の中の #5）には付かない
	e.do("GET", fmt.Sprintf("/v1/sessions/%d/analysis", sid), nil, 200)
	kinds := map[int]string{}
	for _, s := range e.an.sessionShots {
		kinds[s.Seq] = string(s.BlockKind)
	}
	if kinds[1] != "warmup" || kinds[7] != "drill" || kinds[9] != "intervention" || kinds[5] != "" || kinds[11] != "" || kinds[16] != "" {
		t.Fatalf("block_kind: %v", kinds)
	}
	// JSON では、ブロックの外の球はキーごと無い
	b, _ := json.Marshal(e.an.sessionShots[4])
	if strings.Contains(string(b), "block_kind") {
		t.Fatalf("%s", b)
	}
	// 解説（/report）にも同じ block_kind
	e.do("GET", fmt.Sprintf("/v1/sessions/%d/report", sid), nil, 200)
	if e.an.reportIn.Shots[0].BlockKind != "warmup" {
		t.Fatalf("%v", e.an.reportIn.Shots[0].BlockKind)
	}

	// 評価: 分析サービスへ /v1/experiment と同じ形の実験を渡す。warmup / drill のブロックも入る
	ev := e.do("GET", fmt.Sprintf("/v1/plan-runs/%d/evaluation", runID), nil, 200)
	pf := e.an.pf()
	if len(pf.evalIns) != 1 || ev["cached"] != false || ev["blocks_source"] != "planned" {
		t.Fatalf("%v %d", ev, len(pf.evalIns))
	}
	in := pf.evalIns[0]
	if in.Plan.ID != int64(planID) || in.Run.ID != int64(runID) || in.Run.Index != 0 || len(in.PastRuns) != 0 || in.Run.ClubShots != 16 || in.Run.Planned != 13 {
		t.Fatalf("入力: %+v", in.Run)
	}
	if in.Experiment.TargetMetric != "impact_offset" || in.Experiment.Goal != "reduce_abs" || len(in.Experiment.Blocks) != 5 {
		t.Fatalf("%+v", in.Experiment)
	}
	if bl := in.Experiment.Blocks[1]; bl.Kind != "baseline" || len(bl.Shots) != 3 || bl.Shots[0].BlockKind != "baseline" {
		t.Fatalf("baseline のブロック（6番の #5 を拾わない）: %+v", bl)
	}
	if string(in.Plan.Params) == "" || !strings.Contains(string(in.Plan.Params), "0.314") {
		t.Fatalf("params: %s", in.Plan.Params)
	}
	// 同じ入力なら分析サービスを呼ばない
	ev = e.do("GET", fmt.Sprintf("/v1/plan-runs/%d/evaluation", runID), nil, 200)
	if len(pf.evalIns) != 1 || ev["cached"] != true {
		t.Fatalf("保存した評価を使っていない: %d", len(pf.evalIns))
	}
	// 球を1つ除外すると入力が変わるので作り直す
	shots := e.list(fmt.Sprintf("/v1/sessions/%d/shots", sid))
	e.do("PATCH", fmt.Sprintf("/v1/shots/%d", num(shots[2]["id"])), map[string]any{"excluded": true}, 200)
	e.do("GET", fmt.Sprintf("/v1/plan-runs/%d/evaluation", runID), nil, 200)
	// 除外した球は印（excluded）つきで送る（分析サービスが数えて「除外した◯球を除く」と書く。R6）
	nExcl := 0
	for _, b := range pf.evalIns[len(pf.evalIns)-1].Experiment.Blocks {
		for _, sp := range b.Shots {
			if sp.Excluded {
				nExcl++
			}
		}
	}
	if len(pf.evalIns) != 2 || len(pf.evalIns[1].Experiment.Blocks[1].Shots) != 3 || nExcl != 1 {
		t.Fatalf("除外した球に印が無い: %d %d", len(pf.evalIns), nExcl)
	}
	e.do("GET", fmt.Sprintf("/v1/plan-runs/%d/evaluation?refresh=1", runID), nil, 200)
	if len(pf.evalIns) != 3 {
		t.Fatal("?refresh=1 で作り直していない")
	}

	// 境目を動かす（counts）: 前のブロックを消して作り直し、保存する
	moved := e.do("PUT", fmt.Sprintf("/v1/plan-runs/%d/blocks", runID), map[string]any{"counts": []int{2, 4, 2, 4, 4}}, 200)
	if moved["blocks_source"] != "saved" || fmt.Sprint(moved["counts"]) != "[2 4 2 4 4]" || moved["mismatch"] != false || fmt.Sprint(moved["unassigned_seqs"]) != "[]" {
		t.Fatalf("%v", moved)
	}
	exp := e.do("GET", fmt.Sprintf("/v1/experiments/%d", num(ex["id"])), nil, 200)
	if n := len(exp["blocks"].([]any)); n != 5 {
		t.Fatalf("保存したブロック %d", n)
	}
	last := exp["blocks"].([]any)[4].(map[string]any)
	if last["kind"] != "baseline" || num(last["seq_from"]) != 15 || num(last["seq_to"]) != 18 {
		t.Fatalf("%v", last)
	}
	e.do("PUT", fmt.Sprintf("/v1/plan-runs/%d/blocks", runID), map[string]any{"counts": []int{2, 4}}, 400)
	e.do("PUT", fmt.Sprintf("/v1/plan-runs/%d/blocks", runID), map[string]any{}, 400)
	// 境目をそのまま（blocks）: 重なりは 400 で、前のブロックは残る
	e.do("PUT", fmt.Sprintf("/v1/plan-runs/%d/blocks", runID), map[string]any{"blocks": []map[string]any{
		{"kind": "baseline", "seq_from": 1, "seq_to": 6}, {"kind": "intervention", "seq_from": 6, "seq_to": 10}}}, 400)
	if n := len(e.do("GET", fmt.Sprintf("/v1/experiments/%d", num(ex["id"])), nil, 200)["blocks"].([]any)); n != 5 {
		t.Fatalf("失敗した置き換えで前のブロックが消えた: %d", n)
	}
	e.do("PUT", fmt.Sprintf("/v1/plan-runs/%d/blocks", runID), map[string]any{"blocks": []map[string]any{
		{"kind": "baseline", "seq_from": 1, "seq_to": 6}, {"kind": "intervention", "seq_from": 7, "seq_to": 12}, {"kind": "baseline", "seq_from": 13, "seq_to": 18}}}, 200)
	// 保存した評価は捨ててあるので、次は作り直す
	ev = e.do("GET", fmt.Sprintf("/v1/plan-runs/%d/evaluation", runID), nil, 200)
	if ev["cached"] != false || ev["blocks_source"] != "saved" || len(pf.evalIns[len(pf.evalIns)-1].Experiment.Blocks) != 3 {
		t.Fatalf("%v", ev)
	}

	// 2回目の練習: 前の run の評価が past_runs に入る（1回目の A がプランの物差し）
	s2 := e.do("POST", "/v1/sessions", map[string]any{"player_id": pid, "date": "2026-09-30"}, 201)
	run2 := e.do("POST", fmt.Sprintf("/v1/plans/%d/runs", planID), map[string]any{"session_id": s2["id"]}, 201)
	if num(run2["index"]) != 1 {
		t.Fatalf("%v", run2["index"])
	}
	e.importCSV(num(s2["id"]), planCSV(), "", 201)
	calls := len(pf.evalIns)
	e.do("GET", fmt.Sprintf("/v1/plan-runs/%d/evaluation", num(run2["id"])), nil, 200)
	if len(pf.evalIns) != calls+1 {
		t.Fatalf("1回目は保存が使えるので、呼ぶのは2回目だけ: %d", len(pf.evalIns)-calls)
	}
	in2 := pf.evalIns[len(pf.evalIns)-1]
	if in2.Run.Index != 1 || len(in2.PastRuns) != 1 || in2.PastRuns[0].RunID != int64(runID) || !strings.Contains(string(in2.PastRuns[0].Evaluation), "plan_eval") {
		t.Fatalf("past_runs: %+v", in2.PastRuns)
	}
	// 1回目の球を直すと、2回目を評価する前に1回目も評価し直す
	e.do("PATCH", fmt.Sprintf("/v1/shots/%d", num(shots[3]["id"])), map[string]any{"excluded": true}, 200)
	calls = len(pf.evalIns)
	e.do("GET", fmt.Sprintf("/v1/plan-runs/%d/evaluation", num(run2["id"])), nil, 200)
	if len(pf.evalIns) != calls+2 || pf.evalIns[calls].Run.ID != int64(runID) {
		t.Fatalf("前の run を先に評価し直していない: %d", len(pf.evalIns)-calls)
	}

	// 進捗: run ごとの評価を練習の順に
	pr := e.do("GET", fmt.Sprintf("/v1/plans/%d/progress", planID), nil, 200)
	if rs := pr["runs"].([]any); len(rs) != 2 || rs[0].(map[string]any)["fresh"] != true || pr["latest"].(map[string]any)["index"].(float64) != 1 {
		t.Fatalf("%v", pr)
	}
	// 分析サービスが落ちていても、保存してある評価で進捗は返す
	e.do("PATCH", fmt.Sprintf("/v1/shots/%d", num(shots[5]["id"])), map[string]any{"excluded": true}, 200)
	pf.evalDown = true
	pr = e.do("GET", fmt.Sprintf("/v1/plans/%d/progress", planID), nil, 200)
	if rs := pr["runs"].([]any); len(rs) != 2 || rs[0].(map[string]any)["fresh"] != false || rs[0].(map[string]any)["evaluation"] == nil {
		t.Fatalf("%v", pr)
	}
	// 1つの run の評価は、分析サービスが落ちていれば 503
	e.do("GET", fmt.Sprintf("/v1/plan-runs/%d/evaluation", runID), nil, 503)
	pf.evalDown = false

	// 今日の練習
	td := e.do("GET", fmt.Sprintf("/v1/players/%d/today", pid), nil, 200)
	if num(td["plan"].(map[string]any)["id"]) != planID || len(td["runs"].([]any)) != 2 || num(td["next_index"]) != 2 || fmt.Sprint(td["next_counts"]) != "[2 3 2 3 3]" || td["last_evaluation"] == nil {
		t.Fatalf("%v", td)
	}
	// 止めたプランには練習を足せない
	e.do("PATCH", fmt.Sprintf("/v1/plans/%d", planID), map[string]any{"status": "abandoned"}, 200)
	s3 := e.do("POST", "/v1/sessions", map[string]any{"player_id": pid, "date": "2026-10-01"}, 201)
	e.do("POST", fmt.Sprintf("/v1/plans/%d/runs", planID), map[string]any{"session_id": s3["id"]}, 409)
	if td := e.do("GET", fmt.Sprintf("/v1/players/%d/today", pid), nil, 200); td["plan"] != nil {
		t.Fatalf("%v", td)
	}
}

// プランの外で作った実験のブロックは block_kind に使わない（診断から球が消えない）。
func Testプランの外の実験のブロックでは球を外さない(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("R")
	e.importCSV(sid, planCSV(), "", 201)
	ex := e.do("POST", fmt.Sprintf("/v1/sessions/%d/experiments", sid), map[string]any{"hypothesis": "x", "target_metric": "face_angle", "goal": "decrease"}, 201)
	// warmup / drill も既存のブロックの口で受ける
	e.do("POST", fmt.Sprintf("/v1/experiments/%d/blocks", num(ex["id"])), map[string]any{"kind": "warmup", "seq_from": 1, "seq_to": 3}, 201)
	e.do("POST", fmt.Sprintf("/v1/experiments/%d/blocks", num(ex["id"])), map[string]any{"kind": "drill", "seq_from": 4, "seq_to": 6}, 201)
	e.do("POST", fmt.Sprintf("/v1/experiments/%d/blocks", num(ex["id"])), map[string]any{"kind": "stretch", "seq_from": 7, "seq_to": 8}, 400)
	e.do("GET", fmt.Sprintf("/v1/sessions/%d/analysis", sid), nil, 200)
	for _, s := range e.an.sessionShots {
		if s.BlockKind != "" {
			t.Fatalf("プランの外のブロックで block_kind が付いた: #%d %s", s.Seq, s.BlockKind)
		}
	}
	// 既存の評価には warmup / drill のブロックもそのまま渡す（分析サービスが無視する）
	e.do("GET", fmt.Sprintf("/v1/experiments/%d/evaluation", num(ex["id"])), nil, 200)
	if len(e.an.blocks) != 2 || e.an.blocks[0].Kind != "warmup" || e.an.blocks[0].Shots[0].BlockKind != "warmup" {
		t.Fatalf("%+v", e.an.blocks)
	}
	// PUT /v1/experiments/{id}/blocks で丸ごと差し替える
	got := e.do("PUT", fmt.Sprintf("/v1/experiments/%d/blocks", num(ex["id"])), map[string]any{"blocks": []map[string]any{
		{"kind": "baseline", "seq_from": 1, "seq_to": 8}, {"kind": "intervention", "seq_from": 9, "seq_to": 18}}}, 200)
	if bs := got["blocks"].([]any); len(bs) != 2 || bs[0].(map[string]any)["kind"] != "baseline" {
		t.Fatalf("%v", got)
	}
	e.do("PUT", fmt.Sprintf("/v1/experiments/%d/blocks", num(ex["id"])), map[string]any{"blocks": []map[string]any{{"kind": "baseline", "seq_from": 3, "seq_to": 2}}}, 400)
	e.do("PUT", fmt.Sprintf("/v1/experiments/%d/blocks", num(ex["id"])), map[string]any{}, 400)
	e.do("PUT", "/v1/experiments/999/blocks", map[string]any{"blocks": []any{}}, 404)
	if got := e.do("PUT", fmt.Sprintf("/v1/experiments/%d/blocks", num(ex["id"])), map[string]any{"blocks": []any{}}, 200); len(got["blocks"].([]any)) != 0 {
		t.Fatalf("%v", got)
	}
	e.do("GET", fmt.Sprintf("/v1/sessions/%d/plan-run", sid), nil, 404)
}
