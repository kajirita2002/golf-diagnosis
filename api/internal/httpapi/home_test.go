package httpapi

import (
	"fmt"
	"strings"
	"testing"
)

// 使う人: 最初の起動で1人だけ作る。2回目は同じ人（選手を画面で選ばせない。docs/DESIGN_v2.md §2.2）。
func Test使う人は最初の起動で1人だけ作る(t *testing.T) {
	e := newEnv(t)
	a := e.do("POST", "/v1/me", map[string]any{"handedness": "L"}, 200)
	b := e.do("POST", "/v1/me", nil, 200)
	pa, pb := a["player"].(map[string]any), b["player"].(map[string]any)
	if a["created"] != true || b["created"] != false || pa["id"] != pb["id"] || pb["handedness"] != "L" {
		t.Fatalf("%v / %v", a, b)
	}
	if ps := e.list("/v1/players"); len(ps) != 1 {
		t.Fatalf("2人目を作った: %v", ps)
	}
	e.do("POST", "/v1/me", map[string]any{"handedness": "X"}, 400)
}

// 設定は知っている鍵と値だけ。書いた値は選手に残り、ほかの鍵は消えない。
func Test設定は知っている値だけ残す(t *testing.T) {
	e := newEnv(t)
	pid := num(e.do("POST", "/v1/me", nil, 200)["player"].(map[string]any)["id"])
	path := fmt.Sprintf("/v1/players/%d", pid)
	p := e.do("PATCH", path, map[string]any{"handedness": "L", "prefs": map[string]any{"dist_unit": "m"}}, 200)
	if p["handedness"] != "L" || p["prefs"].(map[string]any)["dist_unit"] != "m" {
		t.Fatalf("%v", p)
	}
	e.do("PATCH", path, map[string]any{"prefs": map[string]any{"dist_unit": "km"}}, 400)
	e.do("PATCH", path, map[string]any{"prefs": map[string]any{"color": "red"}}, 400)
	e.do("PATCH", path, map[string]any{"handedness": "X"}, 400)
	e.do("PATCH", "/v1/players/999", map[string]any{"handedness": "R"}, 404)
	// 利き手だけ変えても設定は残る
	p = e.do("PATCH", path, map[string]any{"handedness": "R"}, 200)
	if p["handedness"] != "R" || p["prefs"].(map[string]any)["dist_unit"] != "m" {
		t.Fatalf("設定が消えた: %v", p)
	}
}

// 解説の偽物: 本体の範囲1つに要点（課題・意識する一点・次）と比べる図がある
const homeReport = `{"scopes":[
 {"scope_id":"club:9 Iron","kind":"folded","label":"9 Iron"},
 {"scope_id":"group:iron","kind":"main","label":"アイアン（まとめ）","figures":{"C1":{"id":"C1"}},
  "gist":{"focus":{"candidate_id":"strike_heel","title":"ネック寄りの当たりを減らす"},
   "steps":[{"text":"「ネック寄りの当たりを減らす」を確かめる","which":"now"},{"text":"できたら「クラブの面が右を向きすぎる球を減らす」に進む","which":"next"}],
   "blocks":[{"id":"gap","title":"理想との差","lines":[{"text":"理想は"}],"rows":[]},
     {"id":"action","title":"意識すること・やること","start":"strike_heel","lines":[{"text":"意識する一点: 飛んだ先より、当たった場所だけを見る。"}]}]}}]}`

// ホーム: 記録が無い → 球が無い。球を入れる → 課題（要点から）。分析サービスが落ちている → 届かない。
// プランを作る → プランのきっかけの範囲の課題と、プランの中身。
func Testホームの材料(t *testing.T) {
	e := newEnv(t)
	pid := num(e.do("POST", "/v1/me", nil, 200)["player"].(map[string]any)["id"])
	path := fmt.Sprintf("/v1/players/%d/home", pid)
	h := e.do("GET", path, nil, 200)
	if h["focus_state"] != "no_shots" || h["latest"] != nil || h["plan"] != nil || num(h["sessions"]) != 0 {
		t.Fatalf("記録が無いとき: %v", h)
	}
	s := e.do("POST", "/v1/sessions", map[string]any{"player_id": pid, "date": "2026-09-27"}, 201)
	sid := num(s["id"])
	// 球の無いセッションは「最新」に数えない
	if h = e.do("GET", path, nil, 200); h["latest"] != nil || h["focus_state"] != "no_shots" {
		t.Fatalf("球の無いセッションを最新にした: %v", h)
	}
	e.importCSV(sid, dummyCSV(t), "", 201)
	e.an.reportOut = homeReport
	h = e.do("GET", path, nil, 200)
	f, _ := h["focus"].(map[string]any)
	if h["focus_state"] != "found" || f == nil || f["title"] != "ネック寄りの当たりを減らす" || f["scope_id"] != "group:iron" ||
		f["cue"] != "飛んだ先より、当たった場所だけを見る" || f["next_title"] != "クラブの面が右を向きすぎる球を減らす" ||
		f["startable"] != true || f["figure"] == nil || num(f["session_id"]) != sid {
		t.Fatalf("課題: %v", h)
	}
	if l := h["latest"].(map[string]any); num(l["session_id"]) != sid || num(l["n_shots"]) != 30 {
		t.Fatalf("最新: %v", l)
	}
	// 一覧にも球数とクラブが付く
	ss := e.list(fmt.Sprintf("/v1/players/%d/sessions", pid))
	if num(ss[0]["n_shots"]) != 30 || len(ss[0]["clubs"].([]any)) == 0 {
		t.Fatalf("一覧の球数: %v", ss[0])
	}
	e.an.reportOut = `{"scopes":[{"scope_id":"group:iron","kind":"main","gist":{"focus":null}}]}`
	if h = e.do("GET", path, nil, 200); h["focus_state"] != "no_focus" || h["focus"] != nil {
		t.Fatalf("課題の無い要点: %v", h)
	}
	e.an.down = true
	if h = e.do("GET", path, nil, 200); h["focus_state"] != "unavailable" || h["latest"] == nil {
		t.Fatalf("分析サービスが落ちているとき: %v", h)
	}
	e.an.down = false
	e.an.reportOut = homeReport
	e.do("POST", fmt.Sprintf("/v1/players/%d/plans", pid), planBody(map[string]any{"from_session": sid,
		"trigger": map[string]any{"scope_id": "group:iron", "plain": map[string]any{"title": "ネック寄りの当たりを減らす"}}}), 201)
	h = e.do("GET", path, nil, 200)
	p, _ := h["plan"].(map[string]any)
	if p == nil || p["title"] != "ネック寄りの当たりを減らす" || p["cue"] != "外にボールがあるつもりで打つ" || num(p["next_index"]) != 0 ||
		num(p["total"]) != 13 || p["last"] != nil || num(p["trigger_session_id"]) != sid {
		t.Fatalf("プラン: %v", h)
	}
	if f := h["focus"].(map[string]any); f["scope_id"] != "group:iron" {
		t.Fatalf("プランのきっかけの範囲の課題でない: %v", f)
	}
	if strings.Contains(fmt.Sprint(h), "{band:") {
		t.Fatal("埋めていない文")
	}
}
