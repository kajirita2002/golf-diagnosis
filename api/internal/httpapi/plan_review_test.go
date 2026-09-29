package httpapi

import (
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"strings"
	"testing"
)

// newEnvSrv は newEnv と同じだが、Server も返す（覚えた版を古くするため）。
func newEnvSrv(t *testing.T) (*env, *Server) {
	st := openStore(t)
	t.Cleanup(func() { st.Close() })
	an := &fakeAnalyzer{}
	srv := New(st, an)
	ts := httptest.NewServer(srv.Handler())
	t.Cleanup(ts.Close)
	return &env{t: t, ts: ts, an: an}, srv
}

// レビューの指摘（2026-09-29）で足した口と規則。

// きっかけの診断のセッションと、プランを作る前の球が入ったセッションは練習にできない
// （その球に「準備」「ドリル」が付いて診断から消え、解説と候補が変わる）。
func Test練習にできないセッションは断る(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("R")
	e.importCSV(sid, planCSV(), "", 201)
	pid := e.playerOf(sid)
	plan := e.do("POST", fmt.Sprintf("/v1/players/%d/plans", pid), planBody(map[string]any{"from_session": sid}), 201)
	out := e.do("POST", fmt.Sprintf("/v1/plans/%d/runs", num(plan["id"])), map[string]any{"session_id": sid}, 400)
	if !strings.Contains(out["error"].(string), "きっかけ") {
		t.Fatalf("%v", out)
	}
	old := e.do("POST", "/v1/sessions", map[string]any{"player_id": pid, "date": "2026-01-10"}, 201)
	e.importCSV(num(old["id"]), planCSV(), "", 201)
	e.do("POST", fmt.Sprintf("/v1/plans/%d/runs", num(plan["id"])), map[string]any{"session_id": old["id"]}, 400)
	// 球の無い古い日付のセッション（前の晩に作っておいた）は使える
	empty := e.do("POST", "/v1/sessions", map[string]any{"player_id": pid, "date": "2026-01-11"}, 201)
	e.do("POST", fmt.Sprintf("/v1/plans/%d/runs", num(plan["id"])), map[string]any{"session_id": empty["id"]}, 201)
}

// 「止める」のあと「続ける」を選ぶと、その時点の最後の run の id を覚えて評価に渡す（§8.6）。
func Test止めるのあと続けるを選べる(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("R")
	pid := e.playerOf(sid)
	plan := e.do("POST", fmt.Sprintf("/v1/players/%d/plans", pid), planBody(nil), 201)
	planID := num(plan["id"])
	e.do("POST", fmt.Sprintf("/v1/plans/%d/continue", planID), nil, 400) // 練習がまだ無い
	run := e.do("POST", fmt.Sprintf("/v1/plans/%d/runs", planID), map[string]any{"session_id": sid}, 201)
	e.importCSV(sid, planCSV(), "", 201)
	e.do("GET", fmt.Sprintf("/v1/plan-runs/%d/evaluation", num(run["id"])), nil, 200)
	pf := e.an.pf()
	if pf.evalIns[0].ContinueAfterRun != nil {
		t.Fatal("選ぶ前から渡している")
	}
	p := e.do("POST", fmt.Sprintf("/v1/plans/%d/continue", planID), nil, 200)
	if num(p["rationale"].(map[string]any)["continue_after_run"]) != num(run["id"]) || p["rationale"].(map[string]any)["gates"] == nil {
		t.Fatalf("rationale: %v", p["rationale"])
	}
	// 入力が変わるので作り直し、続けると選んだ run の id を渡す
	e.do("GET", fmt.Sprintf("/v1/plan-runs/%d/evaluation", num(run["id"])), nil, 200)
	last := pf.evalIns[len(pf.evalIns)-1]
	if len(pf.evalIns) != 2 || last.ContinueAfterRun == nil || *last.ContinueAfterRun != int64(num(run["id"])) {
		t.Fatalf("続けるを渡していない: %d", len(pf.evalIns))
	}
	e.do("PATCH", fmt.Sprintf("/v1/plans/%d", planID), map[string]any{"status": "abandoned"}, 200)
	e.do("POST", fmt.Sprintf("/v1/plans/%d/continue", planID), nil, 409)
}

// 「移せていない」の次の型（ドリルと本番を1球ずつ交互）を API から頼める（§8.6）。
func Test交互の型を頼める(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("R")
	e.importCSV(sid, planCSV(), "", 201)
	base := fmt.Sprintf("/v1/players/%d/plans", e.playerOf(sid))
	body := map[string]any{"from_session": sid, "scope_id": "club:9 Iron", "candidate_id": "strike_heel", "cue": "x", "variant": "alternate"}
	e.do("POST", base, body, 201)
	if in := e.an.pf().buildIns[0]; in.Variant != "alternate" {
		t.Fatalf("variant: %q", in.Variant)
	}
	body["variant"] = "zigzag"
	e.do("POST", base+"?replace=1", body, 400)
}

// 「足りない」のとき、分析サービスが出した次の型の球数を今日の練習に返す（§8.6）。
func Test足りないなら次の型の球数を返す(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("R")
	pid := e.playerOf(sid)
	plan := e.do("POST", fmt.Sprintf("/v1/players/%d/plans", pid), planBody(nil), 201)
	run := e.do("POST", fmt.Sprintf("/v1/plans/%d/runs", num(plan["id"])), map[string]any{"session_id": sid}, 201)
	e.importCSV(sid, planCSV(), "", 201)
	pf := e.an.pf()
	pf.evalExtra = map[string]any{"progress": map[string]any{"state": "insufficient", "next_counts": []int{2, 5, 2, 5, 5}}}
	e.do("GET", fmt.Sprintf("/v1/plan-runs/%d/evaluation", num(run["id"])), nil, 200)
	td := e.do("GET", fmt.Sprintf("/v1/players/%d/today", pid), nil, 200)
	if fmt.Sprint(td["next_counts"]) != "[2 5 2 5 5]" || td["next_counts_reason"] != "more_shots" {
		t.Fatalf("next_counts: %v", td["next_counts"])
	}
	// 段の数が型と違えば使わない（型のまま）
	pf.evalExtra = map[string]any{"progress": map[string]any{"state": "insufficient", "next_counts": []int{5, 5}}}
	e.do("GET", fmt.Sprintf("/v1/plan-runs/%d/evaluation?refresh=1", num(run["id"])), nil, 200)
	td = e.do("GET", fmt.Sprintf("/v1/players/%d/today", pid), nil, 200)
	if fmt.Sprint(td["next_counts"]) != "[2 3 2 3 3]" {
		t.Fatalf("next_counts: %v", td["next_counts"])
	}
}

// 分析サービスの版が変われば、保存した評価を作り直す（§8.5）。届かなければ保存したものを使う。
func Test分析サービスの版が変われば評価を作り直す(t *testing.T) {
	e, srv := newEnvSrv(t)
	sid := e.setup("R")
	pid := e.playerOf(sid)
	plan := e.do("POST", fmt.Sprintf("/v1/players/%d/plans", pid), planBody(nil), 201)
	run := e.do("POST", fmt.Sprintf("/v1/plans/%d/runs", num(plan["id"])), map[string]any{"session_id": sid}, 201)
	e.importCSV(sid, planCSV(), "", 201)
	path := fmt.Sprintf("/v1/plan-runs/%d/evaluation", num(run["id"]))
	pf := e.an.pf()
	e.do("GET", path, nil, 200)
	if ev := e.do("GET", path, nil, 200); ev["cached"] != true || len(pf.evalIns) != 1 {
		t.Fatalf("同じ版なら保存を使う: %v", ev["cached"])
	}
	pf.version = "analysis/9 plan/9"
	srv.ver.at = srv.ver.at.Add(-versionTTL * 2) // 覚えた版を古くする
	if ev := e.do("GET", path, nil, 200); ev["cached"] != false || len(pf.evalIns) != 2 {
		t.Fatalf("版が変わったのに作り直していない: %v", ev["cached"])
	}
	// 今日の練習も、版が変わっていれば作り直してから出す
	pf.version = "analysis/10 plan/10"
	srv.ver.at = srv.ver.at.Add(-versionTTL * 2)
	e.do("GET", fmt.Sprintf("/v1/players/%d/today", pid), nil, 200)
	if len(pf.evalIns) != 3 {
		t.Fatalf("今日の練習で作り直していない: %d", len(pf.evalIns))
	}
	// 分析サービスに届かなければ、版が分からないので入力の指紋だけで保存を使う
	e.an.down = true
	srv.ver.at = srv.ver.at.Add(-versionTTL * 2)
	srv.ver.ver = ""
	if ev := e.do("GET", path, nil, 200); ev["cached"] != true {
		t.Fatalf("届かないときに保存を使わない: %v", ev)
	}
	e.an.down = false
}

// 境目を直すと、その run とそれより後の run の保存した評価を捨てる。空の blocks は断る。
func Test境目を直すと後の回の評価も捨てる(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("R")
	pid := e.playerOf(sid)
	plan := e.do("POST", fmt.Sprintf("/v1/players/%d/plans", pid), planBody(nil), 201)
	planID := num(plan["id"])
	r1 := e.do("POST", fmt.Sprintf("/v1/plans/%d/runs", planID), map[string]any{"session_id": sid}, 201)
	e.importCSV(sid, planCSV(), "", 201)
	s2 := e.do("POST", "/v1/sessions", map[string]any{"player_id": pid, "date": "2030-01-02"}, 201)
	r2 := e.do("POST", fmt.Sprintf("/v1/plans/%d/runs", planID), map[string]any{"session_id": s2["id"]}, 201)
	e.importCSV(num(s2["id"]), planCSV(), "", 201)
	e.do("GET", fmt.Sprintf("/v1/plan-runs/%d/evaluation", num(r2["id"])), nil, 200)
	td := e.do("GET", fmt.Sprintf("/v1/players/%d/today", pid), nil, 200)
	if td["last_evaluation"] == nil {
		t.Fatal("評価が保存されていない")
	}
	e.do("PUT", fmt.Sprintf("/v1/plan-runs/%d/blocks", num(r1["id"])), map[string]any{"blocks": []any{}}, 400)
	e.do("PUT", fmt.Sprintf("/v1/plan-runs/%d/blocks", num(r1["id"])), map[string]any{"counts": []int{2, 4, 2, 4, 4}}, 200)
	e.an.down = true // 作り直さずに保存の状態だけを見る
	td = e.do("GET", fmt.Sprintf("/v1/players/%d/today", pid), nil, 200)
	e.an.down = false
	if td["last_evaluation"] != nil {
		t.Fatalf("1回目の境目を直したのに、2回目の古い評価が残った: %v", td["last_evaluation"])
	}
}

// 候補の口: この選手のプランと練習の記録を分析サービスへ渡し、確かめていないドリルは Go でも落とす（§10.2）。
func Test候補の口は記録を渡す(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("R")
	e.importCSV(sid, planCSV(), "", 201)
	pid := e.playerOf(sid)
	e.an.pf().drillsOut = `{"catalog_version":"drills/0.2","drills":[{"id":"strike.two_balls","cue_transfer":"外にボールがあるつもりで打つ","checked_by":"rita"}]}`
	plan := e.do("POST", fmt.Sprintf("/v1/players/%d/plans", pid), planBody(map[string]any{"drill_id": "strike.two_balls", "cue": ""}), 201)
	s2 := e.do("POST", "/v1/sessions", map[string]any{"player_id": pid, "date": "2030-01-02"}, 201)
	run := e.do("POST", fmt.Sprintf("/v1/plans/%d/runs", num(plan["id"])), map[string]any{"session_id": s2["id"]}, 201)
	e.importCSV(num(s2["id"]), planCSV(), "", 201)
	pf := e.an.pf()
	pf.evalState = "failed"
	e.do("GET", fmt.Sprintf("/v1/plan-runs/%d/evaluation", num(run["id"])), nil, 200)
	pf.candOut = `{"scopes":[{"scope_id":"group:iron","candidates":{"now":"strike_heel","candidates":[{"id":"strike_heel","drills":{"drills":[{"id":"strike.two_balls","checked_by":"rita"},{"id":"x","checked_by":null}]}}]}}]}`
	e.do("GET", fmt.Sprintf("/v1/players/%d/plan-candidates", pid), nil, 400)
	rb, _ := json.Marshal(e.do("GET", fmt.Sprintf("/v1/players/%d/plan-candidates?session=%d", pid, sid), nil, 200))
	raw := string(rb)
	if strings.Contains(raw, `"x"`) || !strings.Contains(raw, "strike.two_balls") {
		t.Fatalf("確かめていないドリルが残った: %s", raw)
	}
	in := pf.candIns[0]
	if len(in.Plans) != 1 || in.Plans[0]["state"] != "failed" || in.Plans[0]["issue"] != "strike_heel" {
		t.Fatalf("plans: %v", in.Plans)
	}
	if len(in.History) != 1 || in.History[0]["drill_id"] != "strike.two_balls" || in.History[0]["counts_as_worked"] != true {
		t.Fatalf("history: %v", in.History)
	}
	other := e.do("POST", "/v1/players", map[string]any{"name": "B"}, 201)
	e.do("GET", fmt.Sprintf("/v1/players/%d/plan-candidates?session=%d", num(other["id"]), sid), nil, 400)
}
