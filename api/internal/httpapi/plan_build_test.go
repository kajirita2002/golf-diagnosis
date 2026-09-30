package httpapi

import (
	"fmt"
	"testing"
)

// 候補から作る（本線）: 型・params は分析サービスの /v1/plan/build が作り、Go はそのまま保存する。
func Test候補からプランを作ると分析サービスの型をそのまま保存する(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("L")
	e.importCSV(sid, planCSV(), "", 201)
	pid := e.playerOf(sid)
	base := fmt.Sprintf("/v1/players/%d/plans", pid)
	pf := e.an.pf()

	body := map[string]any{"from_session": sid, "scope_id": "club:9 Iron", "candidate_id": "strike_heel",
		"cue": "向こうにボールがあるつもりで打つ", "window": map[string]any{"ok": true, "face_min": -1.9, "face_max": 2.1, "path": 0.4, "face_center": 0.1}}
	// 意識する1点が無ければ、分析サービスを呼ぶ前に断る
	e.do("POST", base, map[string]any{"from_session": sid, "scope_id": "club:9 Iron", "candidate_id": "strike_heel"}, 400)
	e.do("POST", base, map[string]any{"scope_id": "club:9 Iron", "candidate_id": "strike_heel", "cue": "x"}, 400)
	if len(pf.buildIns) != 0 {
		t.Fatalf("断る前に分析サービスを呼んだ: %d", len(pf.buildIns))
	}
	p := e.do("POST", base, body, 201)
	if len(pf.buildIns) != 1 {
		t.Fatalf("build を呼んでいない")
	}
	in := pf.buildIns[0]
	if in.Handedness != "L" || in.ScopeID != "club:9 Iron" || len(in.Shots) != 18 || in.DrillID != "" {
		t.Fatalf("build の入力: %+v", in)
	}
	if len(in.Window) != 3 || in.Window["face_min"] != -1.9 {
		t.Fatalf("窓は数だけ渡す: %v", in.Window)
	}
	if p["issue"] != "strike_heel" || p["club"] != "9 Iron" || p["cue"] != "向こうにボールがあるつもりで打つ" || p["hypothesis"] != "仮説: 9番で打点が真ん中へ寄る" {
		t.Fatalf("%v", p)
	}
	if tpl := p["template"].([]any); len(tpl) != 5 {
		t.Fatalf("型: %v", tpl)
	}
	tr := p["trigger"].(map[string]any)
	if tr["title"] != "打点をヒールから真ん中へ" || num(tr["session_id"]) != sid || tr["scope_id"] != "club:9 Iron" {
		t.Fatalf("trigger: %v", tr)
	}
	if b := p["build"].(map[string]any); len(b["notes"].([]any)) != 1 {
		t.Fatalf("注記: %v", b)
	}
	// 窓の形が変なら渡さない（見張りは unknown になるだけ）
	e.do("POST", base+"?replace=1", map[string]any{"from_session": sid, "scope_id": "club:9 Iron", "candidate_id": "strike_heel", "cue": "x",
		"window": map[string]any{"ok": false, "face_min": -1.9, "face_max": 2.1, "path": 0.4}}, 201)
	if pf.buildIns[1].Window != nil {
		t.Fatalf("ok でない窓を渡した: %v", pf.buildIns[1].Window)
	}
	if pf.buildIns[0].DiagDrills || pf.buildIns[1].DiagDrills {
		t.Fatal("頼んでいないのに診断のドリルのブロックを頼んだ")
	}
	// 診断レポートから作るときは、診断のドリルのブロックを残すよう分析サービスに頼む
	e.do("POST", base+"?replace=1", map[string]any{"from_session": sid, "scope_id": "club:9 Iron", "candidate_id": "strike_heel", "cue": "x", "diag_drills": true}, 201)
	if !pf.buildIns[2].DiagDrills {
		t.Fatal("diag_drills を分析サービスへ渡していない")
	}
	// 確かめていないドリルは、分析サービスが通しても Go が断る
	e.do("POST", base+"?replace=1", map[string]any{"from_session": sid, "scope_id": "club:9 Iron", "candidate_id": "strike_heel", "drill_id": "strike.two_balls"}, 400)
	// 別の選手のセッションからは作れない
	other := e.do("POST", "/v1/players", map[string]any{"name": "B"}, 201)
	e.do("POST", fmt.Sprintf("/v1/players/%d/plans", num(other["id"])), body, 400)
}

func Testドリル集は利き手を渡す(t *testing.T) {
	e := newEnv(t)
	e.do("GET", "/v1/drills?handedness=L", nil, 200)
	if e.an.pf().drillsHand != "L" {
		t.Fatalf("%q", e.an.pf().drillsHand)
	}
	e.do("GET", "/v1/drills", nil, 200)
	if e.an.pf().drillsHand != "R" {
		t.Fatalf("%q", e.an.pf().drillsHand)
	}
	e.do("GET", "/v1/drills?handedness=X", nil, 400)
}

// 「あなたの記録」は保存した評価を並べるだけ（分析サービスを呼ばない）。ほかのプランの状態は評価の入力（history）に入る。
func Testあなたの記録とほかのプランの状態(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("L")
	pid := e.playerOf(sid)
	pf := e.an.pf()
	pf.evalState = "failed"
	p1 := e.do("POST", fmt.Sprintf("/v1/players/%d/plans", pid), planBody(nil), 201)
	run := e.do("POST", fmt.Sprintf("/v1/plans/%d/runs", num(p1["id"])), map[string]any{"session_id": sid}, 201)
	e.importCSV(sid, planCSV(), "", 201)
	e.do("GET", fmt.Sprintf("/v1/plan-runs/%d/evaluation", num(run["id"])), nil, 200)
	if in := pf.evalIns[0]; in.Handedness != "L" || len(in.History) != 0 {
		t.Fatalf("利き手・履歴: %q %v", in.Handedness, in.History)
	}
	calls := len(pf.evalIns)
	rec := e.list(fmt.Sprintf("/v1/players/%d/record", pid))
	if len(pf.evalIns) != calls {
		t.Fatal("記録で分析サービスを呼んだ")
	}
	if len(rec) != 1 || rec[0]["state"] != "failed" || len(rec[0]["runs"].([]any)) != 1 {
		t.Fatalf("%v", rec)
	}
	if r0 := rec[0]["runs"].([]any)[0].(map[string]any); r0["grade"] != "moderate" || r0["evaluated"] != true {
		t.Fatalf("%v", r0)
	}
	// 2つ目のプランの評価には、1つ目の状態が history で入る
	p2 := e.do("POST", fmt.Sprintf("/v1/players/%d/plans?replace=1", pid), planBody(nil), 201)
	s2 := e.do("POST", "/v1/sessions", map[string]any{"player_id": pid, "date": "2026-09-30"}, 201)
	e.importCSV(num(s2["id"]), planCSV(), "", 201)
	r2 := e.do("POST", fmt.Sprintf("/v1/plans/%d/runs", num(p2["id"])), map[string]any{"session_id": s2["id"]}, 201)
	e.do("GET", fmt.Sprintf("/v1/plan-runs/%d/evaluation", num(r2["id"])), nil, 200)
	h := pf.evalIns[len(pf.evalIns)-1].History
	if len(h) != 1 || h[0].State != "failed" || h[0].Issue != "strike_heel" || h[0].PlanID != int64(num(p1["id"])) {
		t.Fatalf("history: %+v", h)
	}
	e.do("GET", "/v1/players/999/record", nil, 404)
}
