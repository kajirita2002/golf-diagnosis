package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
)

// ---- 偽の分析サービス（段4 の口） ----

type recheckFake struct {
	judged    []map[string]any
	checkups  []map[string]any
	progress  [][]any
	extraItem bool // 既定の項目を作るとき、増えた項目も足す（あとから増えないことを確かめる）
}

func (f *fakeAnalyzer) Call(_ context.Context, path string, body any) (json.RawMessage, error) {
	b, _ := json.Marshal(body)
	var m map[string]any
	_ = json.Unmarshal(b, &m)
	switch path {
	case "/v1/focus-test/judge":
		f.rc.judged = append(f.rc.judged, m)
		in, judged := 0, 0
		marks := []map[string]any{}
		for _, x := range m["swings"].([]any) {
			st := x.(map[string]any)["state"]
			if st == "in_range" {
				in++
			}
			if st == "in_range" || st == "out_range" {
				judged++
			}
			marks = append(marks, map[string]any{"state": st})
		}
		var passed any
		if judged >= 8 {
			passed = in >= 8
		}
		out, _ := json.Marshal(map[string]any{"in_range": in, "judged": judged, "passed": passed, "marks": marks, "next_hint": "bolder"})
		return out, nil
	case "/v1/plan/motion-progress":
		ts, _ := m["tests"].([]any)
		f.rc.progress = append(f.rc.progress, ts)
		days := map[string]bool{}
		for _, x := range ts {
			t := x.(map[string]any)
			if t["passed"] == true && t["block"] == "test" {
				days[fmt.Sprint(t["date"])] = true
			}
		}
		st := "checking"
		if len(days) == 1 {
			st = "maybe"
		} else if len(days) >= 2 {
			st = "worked"
		}
		return json.RawMessage(`{"kind":"motion","state":"` + st + `"}`), nil
	case "/v1/checkup":
		f.rc.checkups = append(f.rc.checkups, m)
		items := m["items"]
		if items == nil {
			its := []any{map[string]any{"metric": "carry", "goal": "increase", "club": "9 Iron"}}
			if f.rc.extraItem {
				its = append(its, map[string]any{"metric": "impact_offset", "goal": "reduce_abs", "club": "9 Iron"})
			}
			items = its
		}
		out, _ := json.Marshal(map[string]any{"version": "checkup/0.1", "items": items, "results": []any{}})
		return out, nil
	}
	return nil, fmt.Errorf("知らない口 %s", path)
}

// ---- 道具 ----

// swingWith は測った判定を直に入れたスイング（項目の状態を決めて数えを確かめる）。
func (c *cpEnv) swingWith(t *testing.T, sid int64, view, version, state, fault string) int64 {
	t.Helper()
	sw := &model.Swing{SessionID: sid, View: view, Club: "7 Iron", ClubClass: "iron", Width: 1280, Height: 720}
	if err := c.st.CreateSwing(context.Background(), sw); err != nil {
		t.Fatal(err)
	}
	if err := c.st.PutSwingChecks(context.Background(), sw.ID, version, nil, []model.SwingCheck{
		{ItemID: "iron.p2.dtl.head_vs_hands", State: state, FaultID: fault, Basis: "measured_tap", Evidence: json.RawMessage(`{}`)},
	}); err != nil {
		t.Fatal(err)
	}
	return sw.ID
}

func (c *cpEnv) session(date string) int64 {
	se := c.do("POST", "/v1/sessions", map[string]any{"player_id": c.pid(), "date": date}, 201)
	return int64(se["id"].(float64))
}

func (c *cpEnv) pid() int64 {
	se, err := c.st.GetSession(context.Background(), c.sid)
	if err != nil {
		c.t.Fatal(err)
	}
	return se.PlayerID
}

const cpv = "checkpoints/1.0-pgag"

func (c *cpEnv) motionPlan(t *testing.T) int64 {
	t.Helper()
	p := c.do("POST", fmt.Sprintf("/v1/players/%d/plans", c.pid()), map[string]any{"kind": "motion", "cp_item_id": "iron.p2.dtl.head_vs_hands",
		"view": "dtl", "checkpoint": "P2", "club": "7 Iron", "fault": "inside", "drill_id": "cp.p2_head_inside", "cue": "手のひらは返さない",
		"title": "クラブの先が内側に回る", "from_session": c.sid}, 201)
	if p["kind"] != "motion" || p["target_metric"] != "cp:iron.p2.dtl.head_vs_hands" || p["goal"] != "in_range" {
		t.Fatalf("動きのプラン: %v", p)
	}
	return int64(p["id"].(float64))
}

func (c *cpEnv) tenSwings(t *testing.T, sid int64, in int, unknown int) {
	for i := 0; i < 10; i++ {
		st, fault := "out_range", "inside"
		if i < in {
			st, fault = "in_range", ""
		} else if i < in+unknown {
			st, fault = "unknown", ""
		}
		c.swingWith(t, sid, "dtl", cpv, st, fault)
	}
}

// ---- テスト ----

func Test動きのプランを作って読むと種類と固定の項目が返る(t *testing.T) {
	c := newCPEnv(t, "R")
	id := c.motionPlan(t)
	got := c.do("GET", fmt.Sprintf("/v1/plans/%d", id), nil, 200)
	m := got["motion"].(map[string]any)
	if got["kind"] != "motion" || m["cp_item_id"] != "iron.p2.dtl.head_vs_hands" || m["view"] != "dtl" || m["checkpoint"] != "P2" || m["cp_catalog_version"] != cpv {
		t.Fatalf("読み直し: %v", got)
	}
	tpl := got["template"].([]any)
	if len(tpl) != 7 || tpl[6].(map[string]any)["n"].(float64) != 10 {
		t.Fatalf("型: %v", tpl)
	}
	// 球のプランの練習の口は使わない
	c.do("POST", fmt.Sprintf("/v1/plans/%d/runs", id), map[string]any{"session_id": c.sid}, 400)
	// 向き・P の検査
	c.do("POST", fmt.Sprintf("/v1/players/%d/plans?replace=1", c.pid()), map[string]any{"kind": "motion", "cp_item_id": "x", "view": "side", "checkpoint": "P2", "club": "7 Iron", "cue": "a"}, 400)
	c.do("POST", fmt.Sprintf("/v1/players/%d/plans?replace=1", c.pid()), map[string]any{"kind": "motion", "cp_item_id": "x", "view": "dtl", "checkpoint": "P11", "club": "7 Iron", "cue": "a"}, 400)
	// 動いているプランがあれば 409、replace=1 で置き換える
	c.do("POST", fmt.Sprintf("/v1/players/%d/plans", c.pid()), map[string]any{"kind": "motion", "cp_item_id": "x", "view": "dtl", "checkpoint": "P2", "club": "7 Iron", "cue": "a"}, 409)
	// 今日の練習に動きの記録が付く
	td := c.do("GET", fmt.Sprintf("/v1/players/%d/today", c.pid()), nil, 200)
	if td["motion"] == nil || td["motion"].(map[string]any)["test_block"].(float64) != 6 {
		t.Fatalf("今日の練習: %v", td["motion"])
	}
}

func Test10球テストは8で合格_7でまだ_判定できたのが7なら判定できない(t *testing.T) {
	c := newCPEnv(t, "R")
	plan := c.motionPlan(t)
	cases := []struct {
		date        string
		in, unknown int
		want        any
	}{{"2026-10-01", 8, 0, true}, {"2026-10-02", 7, 0, false}, {"2026-10-03", 7, 3, nil}}
	for _, k := range cases {
		sid := c.session(k.date)
		c.tenSwings(t, sid, k.in, k.unknown)
		out := c.do("POST", "/v1/focus-tests", map[string]any{"plan_id": plan, "session_id": sid, "self_rating": map[string]any{"count": 8, "feel": "too_little"}}, 201)
		ft := out["test"].(map[string]any)
		if ft["passed"] != k.want || int(ft["in_range"].(float64)) != k.in {
			t.Fatalf("%s: %v", k.date, ft)
		}
		if k.want == nil && ft["judged"].(float64) != 7 {
			t.Fatalf("判定できた数: %v", ft)
		}
	}
	// 外れの向きと自己評価を分析サービスへ渡す
	last := c.an.rc.judged[len(c.an.rc.judged)-1]
	if last["target_fault"] != "inside" || last["self_rating"].(map[string]any)["feel"] != "too_little" || len(last["swings"].([]any)) != 10 {
		t.Fatalf("数えへの入力: %v", last)
	}
	// 前回（同じ項目・版・向き）と状態
	list := c.do("GET", fmt.Sprintf("/v1/players/%d/focus-tests?item=iron.p2.dtl.head_vs_hands", c.pid()), nil, 200)
	ts := list["tests"].([]any)
	if len(ts) != 3 || ts[0].(map[string]any)["date"] != "2026-10-01" {
		t.Fatalf("推移: %v", ts)
	}
	id := int64(ts[2].(map[string]any)["id"].(float64))
	got := c.do("GET", fmt.Sprintf("/v1/focus-tests/%d", id), nil, 200)
	if got["previous"].(map[string]any)["date"] != "2026-10-02" || got["progress"].(map[string]any)["state"] != "maybe" {
		t.Fatalf("前回・状態: %v", got)
	}
	pr := c.do("GET", fmt.Sprintf("/v1/plans/%d/progress", plan), nil, 200)
	if pr["motion"].(map[string]any)["state"] != "maybe" || len(pr["tests"].([]any)) != 3 {
		t.Fatalf("進捗: %v", pr)
	}
}

func Test10球テストは向きや版が違うスイングを混ぜない(t *testing.T) {
	c := newCPEnv(t, "R")
	plan := c.motionPlan(t)
	sid := c.session("2026-10-01")
	a := c.swingWith(t, sid, "dtl", cpv, "in_range", "")
	b := c.swingWith(t, sid, "fo", cpv, "in_range", "")
	c.do("POST", "/v1/focus-tests", map[string]any{"plan_id": plan, "session_id": sid, "swing_ids": []int64{a, b}}, 400)
	d := c.swingWith(t, sid, "dtl", "checkpoints/2.0", "in_range", "")
	c.do("POST", "/v1/focus-tests", map[string]any{"plan_id": plan, "session_id": sid, "swing_ids": []int64{a, d}}, 400)
	// 指定しなければプランの向きのスイングだけを数える（正面は入らない）。版の違うものがあれば断る
	sid2 := c.session("2026-10-02")
	c.swingWith(t, sid2, "fo", cpv, "in_range", "")
	for i := 0; i < 3; i++ {
		c.swingWith(t, sid2, "dtl", cpv, "in_range", "")
	}
	out := c.do("POST", "/v1/focus-tests", map[string]any{"plan_id": plan, "session_id": sid2}, 201)
	if out["test"].(map[string]any)["passed"] != nil || len(out["test"].(map[string]any)["swing_ids"].([]any)) != 3 {
		t.Fatalf("向きの絞り込み: %v", out["test"])
	}
	// 11本は断る
	ids := []int64{}
	for i := 0; i < 11; i++ {
		ids = append(ids, c.swingWith(t, sid2, "dtl", cpv, "in_range", ""))
	}
	c.do("POST", "/v1/focus-tests", map[string]any{"plan_id": plan, "session_id": sid2, "swing_ids": ids}, 400)
}

func Test別の日の動画の比較は版と向きが違えば比べない(t *testing.T) {
	c := newCPEnv(t, "R")
	a, b := c.session("2026-10-01"), c.session("2026-10-02")
	for i := 0; i < 8; i++ {
		st := "out_range"
		if i < 2 {
			st = "in_range"
		}
		c.swingWith(t, a, "dtl", cpv, st, "inside")
	}
	c.tenSwings(t, b, 8, 0)
	got := c.do("GET", fmt.Sprintf("/v1/checks/compare?a=%d&b=%d", a, b), nil, 200)
	its := got["items"].([]any)
	if got["comparable"] != true || len(its) != 1 || its[0].(map[string]any)["label"] != "範囲の外（8本中6本） → 範囲の中（10本中8本）" {
		t.Fatalf("比較: %v", got)
	}
	f := c.session("2026-10-03")
	c.swingWith(t, f, "fo", cpv, "in_range", "")
	got = c.do("GET", fmt.Sprintf("/v1/checks/compare?a=%d&b=%d", a, f), nil, 200)
	if got["comparable"] != false || got["reason"] != "view" {
		t.Fatalf("向き: %v", got)
	}
	v := c.session("2026-10-04")
	c.swingWith(t, v, "dtl", "checkpoints/2.0", "in_range", "")
	got = c.do("GET", fmt.Sprintf("/v1/checks/compare?a=%d&b=%d", a, v), nil, 200)
	if got["comparable"] != false || got["reason"] != "version" || !strings.Contains(got["text"].(string), "基準が新しく") {
		t.Fatalf("版: %v", got)
	}
}

func Test2日の再確認は項目を作った時点で固定する(t *testing.T) {
	e := newEnv(t)
	s1 := e.setup("R")
	e.importCSV(s1, planCSV(), "", 201)
	p := e.do("GET", fmt.Sprintf("/v1/sessions/%d", s1), nil, 200)
	s2 := e.do("POST", "/v1/sessions", map[string]any{"player_id": p["player_id"], "date": "2026-10-01"}, 201)
	sid2 := int(s2["id"].(float64))
	e.importCSV(sid2, planCSV(), "", 201)
	pid := int64(p["player_id"].(float64))
	c := e.do("POST", fmt.Sprintf("/v1/players/%d/checkups", pid), map[string]any{"baseline_session_id": s1, "recheck_session_id": sid2}, 201)
	if len(c["items"].([]any)) != 1 || e.an.rc.checkups[0]["items"] != nil {
		t.Fatalf("作るとき: %v %v", c, e.an.rc.checkups[0])
	}
	if len(e.an.rc.checkups[0]["a"].([]any)) != 18 || len(e.an.rc.checkups[0]["b"].([]any)) != 18 {
		t.Fatalf("両日の球を渡していない")
	}
	// あとから既定の項目が増えても、保存した項目だけで計算し直す
	e.an.rc.extraItem = true
	id := int64(c["id"].(float64))
	got := e.do("GET", fmt.Sprintf("/v1/checkups/%d", id), nil, 200)
	if len(got["items"].([]any)) != 1 || len(e.an.rc.checkups[1]["items"].([]any)) != 1 {
		t.Fatalf("項目が増えた: %v", got["items"])
	}
	res := got["result"].(map[string]any)
	if len(res["items"].([]any)) != 1 {
		t.Fatalf("結果の項目: %v", res)
	}
	e.do("POST", fmt.Sprintf("/v1/players/%d/checkups", pid), map[string]any{"baseline_session_id": s1, "recheck_session_id": s1}, 400)
	other := e.do("POST", "/v1/players", map[string]any{"name": "other", "handedness": "R"}, 201)
	e.do("POST", fmt.Sprintf("/v1/players/%d/checkups", int64(other["id"].(float64))), map[string]any{"baseline_session_id": s1, "recheck_session_id": sid2}, 400)
}

func Test2日の再確認はプランの評価をそのまま渡す(t *testing.T) {
	e := newEnv(t)
	s1 := e.setup("R")
	e.importCSV(s1, planCSV(), "", 201)
	p := e.do("GET", fmt.Sprintf("/v1/sessions/%d", s1), nil, 200)
	pid := int64(p["player_id"].(float64))
	s2 := e.do("POST", "/v1/sessions", map[string]any{"player_id": pid, "date": "2026-10-01"}, 201)
	e.importCSV(int(s2["id"].(float64)), planCSV(), "", 201)
	body := planBody(map[string]any{"from_session": s1})
	pl := e.do("POST", fmt.Sprintf("/v1/players/%d/plans", pid), body, 201)
	e.do("POST", fmt.Sprintf("/v1/players/%d/checkups", pid), map[string]any{"baseline_session_id": s1, "recheck_session_id": s2["id"], "plan_id": pl["id"]}, 201)
	got := e.an.rc.checkups[0]
	if got["plan"].(map[string]any)["target_metric"] != "impact_offset" || got["baseline_is_diagnosis"] != true {
		t.Fatalf("プランを渡していない: %v", got["plan"])
	}
}

// 見た目（AI）だけの項目は、十球テスト（判定できた8本が要る）で合格まで行けないので、プランを組ませない
func Test見た目だけの項目では動きのプランを作らない(t *testing.T) {
	c := newVisEnv(t, -1)
	c.withFrames(t, "dtl")
	c.an.cp.focusOut = `{"focus":"iron.p1.dtl.align","items":[{"id":"iron.p1.dtl.align","basis":"visual","state":"out_range"}]}`
	out := c.do("POST", fmt.Sprintf("/v1/players/%d/plans", c.pid()), map[string]any{"kind": "motion", "cp_item_id": "iron.p1.dtl.align",
		"view": "dtl", "checkpoint": "P1", "club": "7 Iron", "cue": "体の線をそろえる", "title": "向きのそろい", "from_session": c.sid}, 400)
	if !strings.Contains(fmt.Sprint(out["error"]), "見た目") {
		t.Fatalf("%v", out)
	}
	// 測った項目なら作れる
	c.an.cp.focusOut = ""
	c.do("POST", fmt.Sprintf("/v1/players/%d/plans", c.pid()), map[string]any{"kind": "motion", "cp_item_id": "iron.p2.dtl.head_vs_hands",
		"view": "dtl", "checkpoint": "P2", "club": "7 Iron", "cue": "手のひらは返さない", "title": "クラブの先", "from_session": c.sid}, 201)
}
