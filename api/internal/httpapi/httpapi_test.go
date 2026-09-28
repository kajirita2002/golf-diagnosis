package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/kajirita2002/golf-diagnosis/api/internal/analysis"
	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
	"github.com/kajirita2002/golf-diagnosis/api/internal/store"
)

// fakeAnalyzer は渡された中身を覚えて、決まった JSON を返す。
type fakeAnalyzer struct {
	sessionShots []analysis.ShotPayload
	blocks       []analysis.BlockPayload
	down         bool
}

func (f *fakeAnalyzer) Session(_ context.Context, shots []analysis.ShotPayload) (json.RawMessage, error) {
	if f.down {
		return nil, analysis.ErrUnavailable
	}
	f.sessionShots = shots
	return json.RawMessage(`{"fake":"session"}`), nil
}

func (f *fakeAnalyzer) Experiment(_ context.Context, _ *model.Experiment, blocks []analysis.BlockPayload) (json.RawMessage, error) {
	f.blocks = blocks
	return json.RawMessage(`{"fake":"experiment"}`), nil
}

type env struct {
	t  *testing.T
	ts *httptest.Server
	an *fakeAnalyzer
}

func newEnv(t *testing.T) *env {
	st, err := store.Open(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	an := &fakeAnalyzer{}
	ts := httptest.NewServer(New(st, an).Handler())
	t.Cleanup(ts.Close)
	return &env{t: t, ts: ts, an: an}
}

func (e *env) do(method, path string, body any, want int) map[string]any {
	e.t.Helper()
	var r io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		r = bytes.NewReader(b)
	}
	req, _ := http.NewRequest(method, e.ts.URL+path, r)
	req.Header.Set("Content-Type", "application/json")
	return e.send(req, want)
}

func (e *env) send(req *http.Request, want int) map[string]any {
	e.t.Helper()
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		e.t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != want {
		e.t.Fatalf("%s %s: %d（%d を期待）: %s", req.Method, req.URL.Path, resp.StatusCode, want, raw)
	}
	var out map[string]any
	if len(raw) > 0 && raw[0] == '{' {
		_ = json.Unmarshal(raw, &out)
	} else {
		out = map[string]any{"_raw": string(raw)}
	}
	return out
}

func (e *env) list(path string) []map[string]any {
	e.t.Helper()
	resp, err := http.Get(e.ts.URL + path)
	if err != nil {
		e.t.Fatal(err)
	}
	defer resp.Body.Close()
	var out []map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		e.t.Fatal(err)
	}
	return out
}

func (e *env) importCSV(sessionID int, csv string, query string, want int) map[string]any {
	e.t.Helper()
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	fw, _ := mw.CreateFormFile("file", "trackman.csv")
	_, _ = fw.Write([]byte(csv))
	mw.Close()
	req, _ := http.NewRequest("POST", fmt.Sprintf("%s/v1/sessions/%d/import%s", e.ts.URL, sessionID, query), &buf)
	req.Header.Set("Content-Type", mw.FormDataContentType())
	return e.send(req, want)
}

func (e *env) setup(handedness string) int {
	p := e.do("POST", "/v1/players", map[string]any{"name": "Rita", "handedness": handedness}, 201)
	s := e.do("POST", "/v1/sessions", map[string]any{"player_id": p["id"], "date": "2026-09-27", "location": "練習場"}, 201)
	return int(s["id"].(float64))
}

func dummyCSV(t *testing.T) string {
	b, err := os.ReadFile("../../../testdata/trackman_dummy_session.csv")
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func Test取り込みから分析まで(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("R")

	res := e.importCSV(sid, dummyCSV(t), "", 201)
	if res["imported"].(float64) != 30 || res["seq_from"].(float64) != 1 || res["seq_to"].(float64) != 30 {
		t.Fatalf("%v", res)
	}

	shots := e.list(fmt.Sprintf("/v1/sessions/%d/shots", sid))
	if len(shots) != 30 {
		t.Fatalf("%d 球", len(shots))
	}
	d := shots[0]["decomposition"].(map[string]any)
	if d["engine_version"] == "" || d["miss_type"] == "" {
		t.Fatalf("分解が付いていない: %v", d)
	}
	// ダミーの 26〜30 球目はヒール打ち（打点が原因の曲がり）
	strike := 0
	for _, s := range shots[25:] {
		if s["decomposition"].(map[string]any)["curve_cause"] == "strike" {
			strike++
		}
	}
	if strike < 4 {
		t.Fatalf("ヒール打ち5球のうち打点が原因と出たのが %d 球", strike)
	}

	out := e.do("GET", fmt.Sprintf("/v1/sessions/%d/analysis", sid), nil, 200)
	if out["fake"] != "session" || len(e.an.sessionShots) != 30 {
		t.Fatalf("分析サービスに30球を渡していない: %v %d", out, len(e.an.sessionShots))
	}
	if e.an.sessionShots[0].Decomposition.EngineVersion == "" {
		t.Fatal("分析サービスへ分解を渡していない")
	}
}

func Test2回取り込むと続きの番号になる(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("R")
	csv := "Club,Club Speed [mph],Carry [yds]\n7i,80,150\n7i,81,152\n"
	e.importCSV(sid, csv, "", 201)
	res := e.importCSV(sid, csv, "", 201)
	if res["seq_from"].(float64) != 3 || res["seq_to"].(float64) != 4 {
		t.Fatalf("%v", res)
	}
}

func Test左打ちは取り込みで右打ちに揃える(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("L")
	e.importCSV(sid, "Club,Club Speed [mph],Face Angle [deg],Club Path [deg]\n7i,80,-4,-1\n", "", 201)
	m := e.list(fmt.Sprintf("/v1/sessions/%d/shots", sid))[0]["metrics"].(map[string]any)
	if m["face_angle"].(float64) != 4 || m["face_to_path"].(float64) != 3 {
		t.Fatalf("%v", m)
	}
}

func Test壊れたCSVは400で何も残さない(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("R")
	e.importCSV(sid, "Club,Club Speed [yds],Carry\n7i,80,150\n", "", 400)
	if n := len(e.list(fmt.Sprintf("/v1/sessions/%d/shots", sid))); n != 0 {
		t.Fatalf("%d 球残った", n)
	}
	e.importCSV(sid, "a,b\n1,2\n", "", 400)
	e.importCSV(sid, "Club,Club Speed\n7i,80\n", "?units=furlongs", 400)
	e.importCSV(sid, "Club,Club Speed\n7i,80\n", "?source=foresight", 400)
}

func Test打点を手で入れると分解が変わる(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("R")
	// フェース・トゥ・パスは小さいのにスライス回転
	e.importCSV(sid, "Club,Club Speed [mph],Face To Path [deg],Spin Loft [deg],Spin Axis [deg]\n7i,80,0.3,25,8\n", "", 201)
	shot := e.list(fmt.Sprintf("/v1/sessions/%d/shots", sid))[0]
	id := int(shot["id"].(float64))
	if _, ok := shot["decomposition"].(map[string]any)["strike_consistent"]; ok {
		t.Fatal("打点が無いのに整合を判定している")
	}
	out := e.do("PATCH", fmt.Sprintf("/v1/shots/%d", id), map[string]any{"impact_offset_mm": -15}, 200)
	if out["decomposition"].(map[string]any)["strike_consistent"] != true {
		t.Fatalf("%v", out["decomposition"])
	}
	if !strings.Contains(fmt.Sprint(out["manual"]), "impact_offset") {
		t.Fatalf("手入力の印が無い: %v", out["manual"])
	}
	e.do("PATCH", fmt.Sprintf("/v1/shots/%d", id), map[string]any{"impact_offset_mm": 200}, 400)
	e.do("PATCH", fmt.Sprintf("/v1/shots/%d", id), map[string]any{"carry": 200}, 400)
}

func TestGoodの上書きとnullで自動に戻す(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("R")
	e.importCSV(sid, "Club,Club Speed [mph],Carry [yds]\n7i,80,150\n", "", 201)
	id := int(e.list(fmt.Sprintf("/v1/sessions/%d/shots", sid))[0]["id"].(float64))
	out := e.do("PATCH", fmt.Sprintf("/v1/shots/%d", id), map[string]any{"good_override": true, "excluded": true, "club": "PW"}, 200)
	if out["good_override"] != true || out["excluded"] != true || out["club_category"] != "wedge" {
		t.Fatalf("%v", out)
	}
	out = e.do("PATCH", fmt.Sprintf("/v1/shots/%d", id), map[string]any{"good_override": nil}, 200)
	if _, ok := out["good_override"]; ok {
		t.Fatalf("null で消えていない: %v", out)
	}
}

func Test実験はブロックの球だけを分析サービスへ渡す(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("R")
	e.importCSV(sid, dummyCSV(t), "", 201)
	ex := e.do("POST", fmt.Sprintf("/v1/sessions/%d/experiments", sid), map[string]any{
		"hypothesis":    "フェース・トゥ・パスが開いているのでプッシュフェードになる",
		"intervention":  "左手甲を目標に向けたまま振る",
		"target_metric": "face_to_path",
		"goal":          "reduce_abs",
		"club":          "7 Iron",
	}, 201)
	eid := int(ex["id"].(float64))
	e.do("POST", fmt.Sprintf("/v1/experiments/%d/blocks", eid), map[string]any{"kind": "baseline", "seq_from": 1, "seq_to": 10}, 201)
	e.do("POST", fmt.Sprintf("/v1/experiments/%d/blocks", eid), map[string]any{"kind": "intervention", "seq_from": 11, "seq_to": 20}, 201)
	e.do("POST", fmt.Sprintf("/v1/experiments/%d/blocks", eid), map[string]any{"kind": "retention", "seq_from": 18, "seq_to": 25}, 400) // 重なり

	// 除外した球は渡さない
	shots := e.list(fmt.Sprintf("/v1/sessions/%d/shots", sid))
	e.do("PATCH", fmt.Sprintf("/v1/shots/%d", int(shots[2]["id"].(float64))), map[string]any{"excluded": true}, 200)

	e.do("GET", fmt.Sprintf("/v1/experiments/%d/evaluation", eid), nil, 200)
	if len(e.an.blocks) != 2 || len(e.an.blocks[0].Shots) != 9 || len(e.an.blocks[1].Shots) != 10 {
		t.Fatalf("blocks: %d / %d / %d", len(e.an.blocks), len(e.an.blocks[0].Shots), len(e.an.blocks[1].Shots))
	}
	got := e.do("GET", fmt.Sprintf("/v1/experiments/%d", eid), nil, 200)
	if len(got["blocks"].([]any)) != 2 {
		t.Fatalf("%v", got)
	}
}

func Test実験の入力を検める(t *testing.T) {
	e := newEnv(t)
	sid := e.setup("R")
	path := fmt.Sprintf("/v1/sessions/%d/experiments", sid)
	e.do("POST", path, map[string]any{"hypothesis": "", "target_metric": "face_to_path", "goal": "reduce_abs"}, 400)
	e.do("POST", path, map[string]any{"hypothesis": "x", "target_metric": "pelvis_turn", "goal": "reduce_abs"}, 400)
	e.do("POST", path, map[string]any{"hypothesis": "x", "target_metric": "face_to_path", "goal": "better"}, 400)
	e.do("POST", "/v1/sessions/999/experiments", map[string]any{"hypothesis": "x", "target_metric": "face_to_path", "goal": "reduce_abs"}, 404)
}

func Test分析サービスが落ちていても1球の分解は返る(t *testing.T) {
	e := newEnv(t)
	e.an.down = true
	sid := e.setup("R")
	e.importCSV(sid, "Club,Club Speed [mph],Carry [yds]\n7i,80,150\n", "", 201)
	e.do("GET", fmt.Sprintf("/v1/sessions/%d/analysis", sid), nil, 503)
	if len(e.list(fmt.Sprintf("/v1/sessions/%d/shots", sid))) != 1 {
		t.Fatal("速い診断まで止まった")
	}
}

func Test知らないidは404(t *testing.T) {
	e := newEnv(t)
	e.do("GET", "/v1/sessions/42", nil, 404)
	e.do("GET", "/v1/players/abc", nil, 400)
	e.do("POST", "/v1/sessions", map[string]any{"player_id": 99, "date": "2026-09-27"}, 404)
	e.do("POST", "/v1/players", map[string]any{"name": "x", "handedness": "X"}, 400)
	e.do("POST", "/v1/players", map[string]any{"name": "x", "unknown": 1}, 400)
}
