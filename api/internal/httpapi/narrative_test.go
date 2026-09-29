package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/kajirita2002/golf-diagnosis/api/internal/analysis"
)

// 偽の分析サービスのつなぎの文の口。Claude の代わりに決まった答えを返す（本物の API は呼ばない）。
type narrFake struct {
	mu    sync.Mutex
	ins   []analysis.NarrativeInput
	gate  chan struct{} // あれば、閉じるまで答えない（生成に時間がかかる、の代わり）
	ctxOK []bool        // 答える直前に ctx が生きていたか
	down  bool
	model string // 答えたモデル（空なら頼んだモデル）
	// called=0 にする（キーが無い・off のとき、分析サービスは Claude を呼ばずに定型文を返す）
	notCalled bool
	// refuse は Claude に断られた（呼んで払ったが使える答えが無い。cacheable=false・reason=refusal）
	refuse bool
}

func (f *fakeAnalyzer) Narrative(ctx context.Context, in analysis.NarrativeInput) (json.RawMessage, error) {
	n := &f.narr
	n.mu.Lock()
	n.ins = append(n.ins, in)
	gate, down, mdl, notCalled, refuse := n.gate, n.down, n.model, n.notCalled, n.refuse
	n.mu.Unlock()
	if gate != nil {
		select {
		case <-gate:
		case <-ctx.Done():
		}
	}
	n.mu.Lock()
	n.ctxOK = append(n.ctxOK, ctx.Err() == nil)
	n.mu.Unlock()
	if down {
		return nil, analysis.ErrUnavailable
	}
	var inp struct {
		ScopeID string `json:"scope_id"`
	}
	_ = json.Unmarshal(in.Input, &inp)
	if mdl == "" {
		mdl = "claude-opus-5-5"
	}
	out := map[string]any{
		"scope_id": inp.ScopeID, "generated_by": "narrative", "called": 1, "model": mdl, "cacheable": true, "reason": nil,
		"sections":          []any{map[string]any{"id": "s1", "blocks": []any{map[string]any{"t": "bridge", "id": "", "text": "まず、この節の見どころを確かめます。"}, map[string]any{"t": "claim", "id": inp.ScopeID + "/summary.top_cell", "text": ""}}}},
		"fallback_sections": []any{},
		"usage":             map[string]any{"input_tokens": 1200, "output_tokens": 300, "cost_usd": 0.0108},
		"validation":        []any{map[string]any{"attempt": 1, "global": []any{}, "sections": map[string]any{}}},
	}
	if refuse {
		out["generated_by"], out["cacheable"], out["reason"] = "template", false, "refusal"
	}
	if notCalled {
		out["generated_by"], out["called"], out["model"], out["cacheable"], out["reason"] = "template", 0, nil, false, "no_key"
		out["usage"] = map[string]any{"input_tokens": 0, "output_tokens": 0, "cost_usd": 0}
	}
	b, _ := json.Marshal(out)
	return b, nil
}

func (n *narrFake) calls() int {
	n.mu.Lock()
	defer n.mu.Unlock()
	return len(n.ins)
}

// 分析サービスの /v1/report の見本（本体の範囲2つ・畳んだ1本1つ）。hash を変えると入力が変わった扱い。
func narrativeReport(ironHash string) string {
	return fmt.Sprintf(`{"report_version":"report/0.1","generated_by":"template",
 "scopes":[
  {"scope_id":"group:iron","kind":"main","label":"アイアン（まとめ）","sections":[{"id":"s1","claims":[{"id":"group:iron/summary.top_cell","text":"x"}]}],"narrative":{"input_hash":%q,"prompt_version":"narrative/0.1"}},
  {"scope_id":"club:9 Iron","kind":"folded","label":"9 Iron","sections":[]},
  {"scope_id":"club:5 Wood","kind":"main","label":"5 Wood","sections":[{"id":"s1","claims":[{"id":"club:5 Wood/summary.top_cell","text":"y"}]}],"narrative":{"input_hash":"hash-wood","prompt_version":"narrative/0.1"}}
 ],
 "narrative_inputs":{"group:iron":{"scope_id":"group:iron","sections":[]},"club:5 Wood":{"scope_id":"club:5 Wood","sections":[]}},
 "narrative_model":"claude-opus-5-5","narrative_prompt_version":"narrative/0.1"}`, ironHash)
}

type narrEnv struct {
	*env
	srv *Server
}

func newNarrEnv(t *testing.T, cfg LLMConfig) *narrEnv {
	st := openStore(t)
	t.Cleanup(func() { st.Close() })
	an := &fakeAnalyzer{reportOut: narrativeReport("hash-iron-1")}
	srv := New(st, an)
	srv.LLM = cfg
	ts := httptest.NewServer(srv.Handler())
	t.Cleanup(ts.Close)
	// 走っている生成を待ってから DB を閉じる（Cleanup はあとに登録したものから走る）
	t.Cleanup(srv.WaitLLMJobs)
	return &narrEnv{env: &env{t: t, ts: ts, an: an}, srv: srv}
}

func onConfig(limit int) LLMConfig {
	c := DefaultLLMConfig()
	c.Enabled, c.DailyLimitNarrative = true, limit
	return c
}

func (e *narrEnv) jobs(out map[string]any) []map[string]any {
	var r []map[string]any
	for _, x := range out["jobs"].([]any) {
		r = append(r, x.(map[string]any))
	}
	return r
}

func (e *narrEnv) waitJob(id any) map[string]any {
	e.t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for {
		j := e.do("GET", fmt.Sprintf("/v1/jobs/%v", id), nil, 200)
		if j["status"] == "done" || j["status"] == "failed" {
			return j
		}
		if time.Now().After(deadline) {
			e.t.Fatalf("ジョブが終わらない: %v", j)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func scopeOf(rep map[string]any, id string) map[string]any {
	for _, x := range rep["scopes"].([]any) {
		if s := x.(map[string]any); s["scope_id"] == id {
			return s
		}
	}
	return nil
}

// REPORT_LLM が off（既定）なら Claude を一切呼ばない。画面へは入力（narrative_inputs）を送らない。
func TestつなぎはoffならClaudeを呼ばない(t *testing.T) {
	e := newNarrEnv(t, DefaultLLMConfig())
	sid := e.setup("R")
	out := e.do("POST", fmt.Sprintf("/v1/sessions/%d/report/narrative", sid), nil, 200)
	if out["enabled"] != false || len(out["jobs"].([]any)) != 0 || e.an.narr.calls() != 0 {
		t.Fatalf("%v calls=%d", out, e.an.narr.calls())
	}
	rep := e.do("GET", fmt.Sprintf("/v1/sessions/%d/report", sid), nil, 200)
	if rep["narrative_enabled"] != false {
		t.Fatalf("%v", rep["narrative_enabled"])
	}
	body := rep["report"].(map[string]any)
	if _, ok := body["narrative_inputs"]; ok {
		t.Fatal("narrative_inputs を画面へ送った")
	}
	if n := scopeOf(body, "group:iron")["narrative"].(map[string]any); n["result"] != nil {
		t.Fatalf("off なのにつなぎの文を付けた: %v", n)
	}
}

// on: 本体の範囲ごとにジョブ（畳んだ1本は作らない）→ 保存 → /report に付く。同じ入力なら呼ばない。
func Testつなぎはジョブで作ってキャッシュする(t *testing.T) {
	e := newNarrEnv(t, onConfig(20))
	sid := e.setup("R")
	out := e.do("POST", fmt.Sprintf("/v1/sessions/%d/report/narrative", sid), nil, 202)
	js := e.jobs(out)
	if out["enabled"] != true || len(js) != 2 || js[0]["scope_id"] != "group:iron" || js[1]["scope_id"] != "club:5 Wood" {
		t.Fatalf("%v", out)
	}
	for _, j := range js {
		got := e.waitJob(j["job_id"])
		if got["status"] != "done" || got["model"] != "claude-opus-5-5" || got["cost_usd"].(float64) != 0.0108 || got["kind"] != "narrative" {
			t.Fatalf("%v", got)
		}
		if res := got["result"].(map[string]any); res["generated_by"] != "narrative" {
			t.Fatalf("%v", res)
		}
	}
	if e.an.narr.calls() != 2 {
		t.Fatalf("呼んだ回数 %d", e.an.narr.calls())
	}
	// 分析サービスへは入力をそのまま・鍵つきで渡す
	if in := e.an.narr.ins[0]; in.InputHash != "hash-iron-1" || !strings.Contains(string(in.Input), `"scope_id":"group:iron"`) {
		t.Fatalf("%+v", in)
	}
	rep := e.do("GET", fmt.Sprintf("/v1/sessions/%d/report", sid), nil, 200)
	if rep["narrative_enabled"] != true {
		t.Fatal("narrative_enabled")
	}
	n := scopeOf(rep["report"].(map[string]any), "group:iron")["narrative"].(map[string]any)
	if n["result"] == nil || n["model"] != "claude-opus-5-5" || n["cost_usd"].(float64) != 0.0108 {
		t.Fatalf("キャッシュを付けていない: %v", n)
	}
	// 同じ入力なら Claude を呼ばない（上限にも数えない）
	again := e.do("POST", fmt.Sprintf("/v1/sessions/%d/report/narrative", sid), nil, 202)
	for _, j := range e.jobs(again) {
		if j["cached"] != true || j["status"] != "done" {
			t.Fatalf("%v", j)
		}
	}
	if e.an.narr.calls() != 2 || again["used"].(float64) != 2 {
		t.Fatalf("同じ入力で呼んだ: calls=%d %v", e.an.narr.calls(), again)
	}
	// 入力の鍵が変われば（球を除外した、など）その範囲だけ作り直す
	e.an.reportOut = narrativeReport("hash-iron-2")
	third := e.do("POST", fmt.Sprintf("/v1/sessions/%d/report/narrative", sid), nil, 202)
	js = e.jobs(third)
	if js[0]["cached"] == true || js[1]["cached"] != true {
		t.Fatalf("%v", js)
	}
	e.waitJob(js[0]["job_id"])
	if e.an.narr.calls() != 3 {
		t.Fatalf("calls=%d", e.an.narr.calls())
	}
}

// 上限を超えた範囲は Claude を呼ばずに定型文だけ。
func Testつなぎは上限を超えると定型文だけ(t *testing.T) {
	e := newNarrEnv(t, onConfig(1))
	sid := e.setup("R")
	out := e.do("POST", fmt.Sprintf("/v1/sessions/%d/report/narrative", sid), nil, 202)
	js := e.jobs(out)
	if js[0]["job_id"] == nil || js[1]["limit"] != true || js[1]["job_id"] != nil || !strings.Contains(js[1]["reason"].(string), "上限") {
		t.Fatalf("%v", js)
	}
	e.waitJob(js[0]["job_id"])
	if e.an.narr.calls() != 1 {
		t.Fatalf("上限を超えて呼んだ: %d", e.an.narr.calls())
	}
	rep := e.do("GET", fmt.Sprintf("/v1/sessions/%d/report", sid), nil, 200)
	if n := scopeOf(rep["report"].(map[string]any), "club:5 Wood")["narrative"].(map[string]any); n["result"] != nil {
		t.Fatalf("%v", n)
	}
	// 0 なら1回も呼ばない
	e2 := newNarrEnv(t, onConfig(0))
	sid2 := e2.setup("R")
	out = e2.do("POST", fmt.Sprintf("/v1/sessions/%d/report/narrative", sid2), nil, 202)
	for _, j := range e2.jobs(out) {
		if j["limit"] != true {
			t.Fatalf("%v", j)
		}
	}
	if e2.an.narr.calls() != 0 {
		t.Fatal("上限 0 で呼んだ")
	}
}

// 要求（スマホの fetch）が切れても、生成は要求と切り離した context で最後まで走って保存される。
func Testつなぎは要求が切れても保存される(t *testing.T) {
	e := newNarrEnv(t, onConfig(20))
	gate := make(chan struct{})
	e.an.narr.gate = gate
	sid := e.setup("R")
	ctx, cancel := context.WithCancel(context.Background())
	req, _ := http.NewRequestWithContext(ctx, "POST", e.ts.URL+fmt.Sprintf("/v1/sessions/%d/report/narrative", sid), nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var out map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&out)
	resp.Body.Close()
	cancel() // 画面が消えた
	if resp.StatusCode != 202 {
		t.Fatalf("%d", resp.StatusCode)
	}
	id := e.jobs(out)[0]["job_id"]
	if j := e.do("GET", fmt.Sprintf("/v1/jobs/%v", id), nil, 200); j["status"] != "queued" && j["status"] != "running" {
		t.Fatalf("%v", j)
	}
	// 二度押ししても、走っているジョブを返して2回払わない
	twice := e.do("POST", fmt.Sprintf("/v1/sessions/%d/report/narrative", sid), nil, 202)
	if e.jobs(twice)[0]["job_id"] != id {
		t.Fatalf("%v", twice)
	}
	close(gate)
	j := e.waitJob(id)
	if j["status"] != "done" || j["result"] == nil {
		t.Fatalf("%v", j)
	}
	e.srv.WaitLLMJobs()
	for _, ok := range e.an.narr.ctxOK {
		if !ok {
			t.Fatal("要求の context を生成に渡している（切れたら払った生成ごと捨てる）")
		}
	}
	if e.an.narr.calls() != 2 {
		t.Fatalf("calls=%d", e.an.narr.calls())
	}
}

// Claude を呼ばなかった（キーが無いなど）結果は使い回さず、上限の枠も返す。分析サービスが落ちていても同じ。
func Testつなぎは呼ばなかったぶんを数えない(t *testing.T) {
	e := newNarrEnv(t, onConfig(20))
	e.an.narr.notCalled = true
	sid := e.setup("R")
	out := e.do("POST", fmt.Sprintf("/v1/sessions/%d/report/narrative", sid), nil, 202)
	for _, j := range e.jobs(out) {
		got := e.waitJob(j["job_id"])
		if got["status"] != "failed" || got["error"] != "no_key" || got["model"] != "claude-opus-5-5" {
			t.Fatalf("%v", got)
		}
	}
	e.an.narr.notCalled, e.an.narr.down = false, true
	out = e.do("POST", fmt.Sprintf("/v1/sessions/%d/report/narrative", sid), nil, 202)
	for _, j := range e.jobs(out) {
		if j["cached"] == true {
			t.Fatal("呼ばなかった結果を使い回した")
		}
		if got := e.waitJob(j["job_id"]); got["status"] != "failed" || !strings.Contains(got["error"].(string), "分析サービス") {
			t.Fatalf("%v", got)
		}
	}
	u, _ := e.srv.Store.GetLLMUsage(context.Background(), llmDay(), "narrative")
	if u.Calls != 0 {
		t.Fatalf("呼ばなかったのに上限に数えた: %+v", u)
	}
	rep := e.do("GET", fmt.Sprintf("/v1/sessions/%d/report", sid), nil, 200)
	if n := scopeOf(rep["report"].(map[string]any), "group:iron")["narrative"].(map[string]any); n["result"] != nil {
		t.Fatalf("%v", n)
	}
}

// フォールバックで別のモデルが答えたら、そのモデルの名前で記録する。もう一度押しても呼び直さず（払い直さない）、
// 答えたモデルの名前のまま出す（Opus の答えとしては出さない）。
func Testつなぎは実際に答えたモデルを記録する(t *testing.T) {
	e := newNarrEnv(t, onConfig(20))
	e.an.narr.model = "claude-sonnet-5-5"
	sid := e.setup("R")
	out := e.do("POST", fmt.Sprintf("/v1/sessions/%d/report/narrative", sid), nil, 202)
	id := e.jobs(out)[0]["job_id"]
	if j := e.waitJob(id); j["model"] != "claude-sonnet-5-5" {
		t.Fatalf("%v", j)
	}
	e.waitJob(e.jobs(out)[1]["job_id"])
	// 画面には答えたモデルの名前で出す
	rep := e.do("GET", fmt.Sprintf("/v1/sessions/%d/report", sid), nil, 200)
	if n := scopeOf(rep["report"].(map[string]any), "group:iron")["narrative"].(map[string]any); n["model"] != "claude-sonnet-5-5" || n["result"] == nil {
		t.Fatalf("%v", n)
	}
	// もう一度押しても呼び直さない（上限の回数も増えない）
	calls := e.an.narr.calls()
	e.an.narr.model = ""
	again := e.do("POST", fmt.Sprintf("/v1/sessions/%d/report/narrative", sid), nil, 202)
	if js := e.jobs(again); js[0]["cached"] != true || js[0]["job_id"] != id {
		t.Fatalf("別のモデルの答えがあるのに呼び直した: %v", js)
	}
	e.srv.WaitLLMJobs()
	if e.an.narr.calls() != calls || num(again["used"]) != num(out["used"]) {
		t.Fatalf("呼び直して払った: %d → %d / used %v → %v", calls, e.an.narr.calls(), out["used"], again["used"])
	}
	rep = e.do("GET", fmt.Sprintf("/v1/sessions/%d/report", sid), nil, 200)
	if n := scopeOf(rep["report"].(map[string]any), "group:iron")["narrative"].(map[string]any); n["model"] != "claude-sonnet-5-5" {
		t.Fatalf("答えたモデルの名前で出す: %v", n)
	}
}

func TestLLMの設定は環境変数から(t *testing.T) {
	get := func(m map[string]string) func(string) string { return func(k string) string { return m[k] } }
	c, w := LLMConfigFromEnv(get(nil))
	if c.Enabled || c.DailyLimitNarrative != 20 || len(w) != 0 {
		t.Fatalf("既定は off・20: %+v %v", c, w)
	}
	c, w = LLMConfigFromEnv(get(map[string]string{"REPORT_LLM": "ON", "LLM_DAILY_LIMIT_NARRATIVE": "-1"}))
	if !c.Enabled || c.DailyLimitNarrative != -1 || len(w) != 0 {
		t.Fatalf("%+v %v", c, w)
	}
	c, w = LLMConfigFromEnv(get(map[string]string{"REPORT_LLM": "yes", "LLM_DAILY_LIMIT_NARRATIVE": "x"}))
	if c.Enabled || c.DailyLimitNarrative != 20 || len(w) != 2 {
		t.Fatalf("読めない値は既定に戻して知らせる: %+v %v", c, w)
	}
	var h map[string]any
	_ = json.Unmarshal(HealthJSON(Status{ReportLLM: true}, "ready", ""), &h)
	if h["report_llm"] != "on" {
		t.Fatalf("%v", h)
	}
}

// 同じ入力が今日断られたら、押し直しても呼ばない（押すたびに払わない）。
func Test断られた入力はその日は呼び直さない(t *testing.T) {
	e := newNarrEnv(t, onConfig(20))
	e.an.narr.refuse = true
	sid := e.setup("R")
	out := e.do("POST", fmt.Sprintf("/v1/sessions/%d/report/narrative", sid), nil, 202)
	for _, j := range e.jobs(out) {
		if w := e.waitJob(j["job_id"]); w["status"] != "failed" {
			t.Fatalf("%v", w)
		}
	}
	calls := e.an.narr.calls()
	again := e.do("POST", fmt.Sprintf("/v1/sessions/%d/report/narrative", sid), nil, 202)
	e.srv.WaitLLMJobs()
	if e.an.narr.calls() != calls {
		t.Fatalf("断られた入力を呼び直した: %d → %d", calls, e.an.narr.calls())
	}
	if js := e.jobs(again); js[0]["reason"] == nil {
		t.Fatalf("理由が無い: %v", js)
	}
}
