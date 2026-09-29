package httpapi

import (
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/kajirita2002/golf-diagnosis/api/internal/analysis"
	"github.com/kajirita2002/golf-diagnosis/api/internal/physics"
)

func (f *fakeAnalyzer) Report(_ context.Context, in analysis.ReportInput) (json.RawMessage, error) {
	if f.down {
		return nil, analysis.ErrUnavailable
	}
	if f.reportErr != nil {
		return nil, f.reportErr
	}
	f.reportIn = &in
	if f.reportOut == "" {
		return json.RawMessage(`{"fake":"report"}`), nil
	}
	return json.RawMessage(f.reportOut), nil
}

// 帯の依頼を2か所（範囲の直下と図の中）に置いた、分析サービスの応答の見本。
const reportWithBand = `{"report_version":"report/0.1","facts_hash":"abc",
 "scopes":[
  {"id":"group:iron","n":26,"band_request":{"carry":118.9,"spin_loft":24.3,"side_pct":0.05,"side_min_m":5.0,"k":0.314,"category":"iron","path_min":-2,"path_max":2,"path_step":0.5,"window_path":0.4},
   "figures":{"F3":{"band_request":{"carry":118.9,"spin_loft":24.3,"lim":5.945,"k":0.314,"category":"iron","paths":[0.4]}}}},
  {"id":"club:5 Wood","band_request":{"carry":170,"spin_loft":6,"lim":10,"k":0.3,"category":"wood","paths":[0]}},
  {"id":"club:4 Hybrid","band_request":{"carry":150,"spin_loft":20,"k":0.375,"category":"hybrid","paths":[0]}}
 ],
 "big":12345678901234567890}`

func TestレポートはPythonへ中継して帯の形を足す(t *testing.T) {
	e := newEnv(t)
	e.an.reportOut = reportWithBand
	sid := e.setup("L")
	e.importCSV(sid, dummyCSV(t), "", 201)
	e.do("POST", fmt.Sprintf("/v1/sessions/%d/experiments", sid),
		map[string]any{"hypothesis": "x", "target_metric": "face_to_path", "goal": "reduce_abs"}, 201)

	out := e.do("GET", fmt.Sprintf("/v1/sessions/%d/report", sid), nil, 200)
	if out["available"] != true || out["handedness"] != "L" || out["physics_version"] != physics.EngineVersion {
		t.Fatalf("%v", out)
	}
	// 分析サービスへは /analysis と同じ形の shots と、利き手・実験を渡す
	in := e.an.reportIn
	if in == nil || len(in.Shots) != 30 || in.Handedness != "L" || len(in.Experiments) != 1 {
		t.Fatalf("分析サービスへの入力: %+v", in)
	}
	if in.Shots[0].Decomposition.EngineVersion != physics.EngineVersion {
		t.Fatal("分析サービスへ分解を渡していない")
	}
	if shots := out["shots"].([]any); len(shots) != 30 {
		t.Fatalf("1球ごとの分解 %d 球", len(shots))
	}

	rep := out["report"].(map[string]any)
	if rep["report_version"] != "report/0.1" {
		t.Fatalf("分析サービスの中身が落ちた: %v", rep)
	}
	scopes := rep["scopes"].([]any)
	iron := scopes[0].(map[string]any)
	shape := iron["band_shape"].(map[string]any)
	if shape["ok"] != true || len(shape["ranges"].([]any)) != 9 {
		t.Fatalf("アイアンの帯: %v", shape)
	}
	if math.Abs(shape["lim"].(float64)-5.945) > 1e-9 {
		t.Fatalf("帯の幅は max(side_min_m, side_pct × carry): %v", shape["lim"])
	}
	wnd := shape["window"].(map[string]any)
	if lo, hi := wnd["face_min"].(float64), wnd["face_max"].(float64); math.Abs(lo+1.9) > 0.2 || math.Abs(hi-2.1) > 0.2 {
		t.Fatalf("窓 %.2f〜%.2f", lo, hi)
	}
	// 図の中に置いた依頼にも足す
	f3 := iron["figures"].(map[string]any)["F3"].(map[string]any)["band_shape"].(map[string]any)
	if f3["ok"] != true || f3["window"] != nil {
		t.Fatalf("F3 の帯: %v", f3)
	}
	// 薄い当たりの代表値・依頼の欠けは、落とさずに理由を返す
	for i, want := range []string{"薄い当たり", "帯の幅"} {
		sh := scopes[i+1].(map[string]any)["band_shape"].(map[string]any)
		if sh["ok"] != false || !containsStr(sh["reason"], want) {
			t.Fatalf("scope %d: %v", i+1, sh)
		}
	}
}

func Testレポートは分析サービスの数字の桁を変えない(t *testing.T) {
	raw, err := addBandShapes(json.RawMessage(reportWithBand))
	if err != nil {
		t.Fatal(err)
	}
	if !json.Valid(raw) || !containsStr(string(raw), `"big":12345678901234567890`) {
		t.Fatalf("%s", raw)
	}
}

func Test分析サービスが落ちていてもレポートは1球の分解を返す(t *testing.T) {
	e := newEnv(t)
	e.an.down = true
	sid := e.setup("R")
	e.importCSV(sid, dummyCSV(t), "", 201)
	out := e.do("GET", fmt.Sprintf("/v1/sessions/%d/report", sid), nil, 200)
	if out["available"] != false || out["report"] != nil || !containsStr(out["reason"], "分析サービス") {
		t.Fatalf("解説を出している: %v", out)
	}
	shots := out["shots"].([]any)
	if len(shots) != 30 {
		t.Fatalf("1球ごとの分解 %d 球", len(shots))
	}
	d := shots[0].(map[string]any)["decomposition"].(map[string]any)
	if d["engine_version"] != physics.EngineVersion {
		t.Fatalf("%v", d)
	}
	if _, ok := d["band_eligible"]; !ok {
		t.Fatalf("帯の材料が分解に無い: %v", d)
	}
}

func Test分析サービスが理由つきで断ったらその理由を出す(t *testing.T) {
	e := newEnv(t)
	e.an.reportErr = &analysis.ServiceError{Status: 422, Message: "球がありません"}
	sid := e.setup("R")
	out := e.do("GET", fmt.Sprintf("/v1/sessions/%d/report", sid), nil, 200)
	if out["available"] != false || out["reason"] != "球がありません" || len(out["shots"].([]any)) != 0 {
		t.Fatalf("%v", out)
	}
	e.do("GET", "/v1/sessions/999/report", nil, 404)
}

func Test分析サービスの応答が壊れていれば解説を出さない(t *testing.T) {
	e := newEnv(t)
	e.an.reportOut = `{"scopes":`
	sid := e.setup("R")
	out := e.do("GET", fmt.Sprintf("/v1/sessions/%d/report", sid), nil, 200)
	if out["available"] != false || out["report"] != nil {
		t.Fatalf("%v", out)
	}
}

func TestReportは分析サービスのv1reportへshotsを渡す(t *testing.T) {
	// 本物のクライアントが /v1/report へ /v1/session と同じ形の shots を送ることを、偽のサーバーで確かめる
	var got map[string]any
	var path string
	srv := newJSONServer(t, func(p string, body map[string]any) string {
		path, got = p, body
		return `{"ok":true}`
	})
	c := analysis.New(srv)
	if _, err := c.Report(context.Background(), analysis.ReportInput{Handedness: "R"}); err != nil {
		t.Fatal(err)
	}
	if path != "/v1/report" || got["handedness"] != "R" {
		t.Fatalf("%s %v", path, got)
	}
	if s, ok := got["shots"].([]any); !ok || len(s) != 0 {
		t.Fatalf("shots は空でも配列で送る: %v", got["shots"])
	}
	if s, ok := got["experiments"].([]any); !ok || len(s) != 0 {
		t.Fatalf("experiments は空でも配列で送る: %v", got["experiments"])
	}
}

func containsStr(v any, sub string) bool {
	s, ok := v.(string)
	return ok && strings.Contains(s, sub)
}

// newJSONServer は JSON を受けて決まった JSON を返す偽の分析サービス。URL を返す。
func newJSONServer(t *testing.T, h func(path string, body map[string]any) string) string {
	t.Helper()
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(h(r.URL.Path, body)))
	}))
	t.Cleanup(ts.Close)
	return ts.URL
}

func Testレポートはgzipを受け付ける相手にgzipで返す(t *testing.T) {
	e := newEnv(t)
	e.an.reportOut = reportWithBand
	sid := e.setup("R")
	e.importCSV(sid, dummyCSV(t), "", 201)
	get := func(enc string) *http.Response {
		req, _ := http.NewRequest("GET", fmt.Sprintf("%s/v1/sessions/%d/report", e.ts.URL, sid), nil)
		req.Header.Set("Accept-Encoding", enc) // 自分で付けると Transport は勝手に解かない
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { resp.Body.Close() })
		return resp
	}
	resp := get("gzip, deflate")
	if resp.Header.Get("Content-Encoding") != "gzip" {
		t.Fatalf("gzip で返していない: %v", resp.Header)
	}
	zr, err := gzip.NewReader(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	var out map[string]any
	if err := json.NewDecoder(zr).Decode(&out); err != nil || out["available"] != true {
		t.Fatalf("gzip の中身が読めない: %v %v", err, out)
	}
	plain := get("identity")
	if plain.Header.Get("Content-Encoding") != "" {
		t.Fatalf("gzip を受け付けない相手に gzip で返した: %v", plain.Header)
	}
	if err := json.NewDecoder(plain.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
}
