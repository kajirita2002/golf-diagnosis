package httpapi

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"image"
	"image/color"
	"image/jpeg"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/kajirita2002/golf-diagnosis/api/internal/analysis"
	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
)

// ---- 偽の分析サービス（チェックポイントの口） ----

type cpFake struct {
	measured []analysis.CheckpointSwing
	focus    []analysis.CheckpointFocusInput
	stamp    string // 分析サービスの指紋（空なら stamp-1）
	failNext bool   // 次の測るを 503 にする（保存のあとに測るのが失敗した道）
}

func (f *fakeAnalyzer) Checkpoints(_ context.Context, hand model.Handedness) (json.RawMessage, error) {
	if f.down {
		return nil, analysis.ErrUnavailable
	}
	return json.RawMessage(`{"version":"checkpoints/1.0-pgag","hand":"` + string(hand) + `","items":[]}`), nil
}

func (f *fakeAnalyzer) CheckpointsMeasure(_ context.Context, sw analysis.CheckpointSwing) (json.RawMessage, error) {
	if f.down || f.cp.failNext {
		f.cp.failNext = false
		return nil, analysis.ErrUnavailable
	}
	f.cp.measured = append(f.cp.measured, sw)
	st := f.cp.stamp
	if st == "" {
		st = "stamp-1"
	}
	return json.RawMessage(`{"catalog_version":"checkpoints/1.0-pgag","stamp":"` + st + `","camera":{"ok":true},"scale":{"ok":true},"items":[
		{"id":"iron.p2.dtl.head_vs_hands","state":"out_range","fault":"inside","basis":"measured_tap","reason":""},
		{"id":"iron.p1.dtl.hands","state":"in_range","basis":"measured_approx","reason":""},
		{"id":"err.steep.p6","state":"same_as","reason":"","target":"iron.p6.dtl.head_vs_hands"},
		{"id":"pow.turn","state":"unknown","reason":"not_in_2d"}]}`), nil
}

func (f *fakeAnalyzer) CheckpointsStamp(_ context.Context) (string, error) {
	if f.down {
		return "", analysis.ErrUnavailable
	}
	if f.cp.stamp == "" {
		return "stamp-1", nil
	}
	return f.cp.stamp, nil
}

func (f *fakeAnalyzer) CheckpointsFocus(_ context.Context, in analysis.CheckpointFocusInput) (json.RawMessage, error) {
	f.cp.focus = append(f.cp.focus, in)
	return json.RawMessage(`{"focus":"iron.p2.dtl.head_vs_hands","items":[]}`), nil
}

// ---- 道具 ----

func jpegB64(t *testing.T, w, h int) string {
	t.Helper()
	img := image.NewRGBA(image.Rect(0, 0, w, h))
	for x := 0; x < w; x++ {
		img.Set(x, h/2, color.RGBA{200, 200, 200, 255})
	}
	var b bytes.Buffer
	if err := jpeg.Encode(&b, img, &jpeg.Options{Quality: 70}); err != nil {
		t.Fatal(err)
	}
	return base64.StdEncoding.EncodeToString(b.Bytes())
}

func lm33() []map[string]float64 {
	out := make([]map[string]float64, 33)
	for i := range out {
		out[i] = map[string]float64{"x": 0.5, "y": 0.5, "visibility": 0.9}
	}
	return out
}

type cpEnv struct {
	*env
	sid int64
}

func newCPEnv(t *testing.T, hand string) *cpEnv {
	e := newEnv(t)
	p := e.do("POST", "/v1/players", map[string]any{"name": "me", "handedness": hand}, 201)
	se := e.do("POST", "/v1/sessions", map[string]any{"player_id": p["id"], "date": "2026-09-29"}, 201)
	return &cpEnv{env: e, sid: int64(se["id"].(float64))}
}

func (c *cpEnv) swing(view string) int64 {
	sw := c.do("POST", "/v1/sessions/"+jsonNum(c.sid)+"/swings", map[string]any{"view": view, "club": "7 Iron", "club_class": "iron", "fps": 240, "fps_source": "container",
		"width": 1280, "height": 720, "ball": [][]float64{{832, 640}, {848, 640}}}, 201)
	return int64(sw["id"].(float64))
}

func jsonNum(n int64) string { b, _ := json.Marshal(n); return string(b) }

// ---- テスト ----

func TestスイングとPのコマを入れると測って判定を残す(t *testing.T) {
	c := newCPEnv(t, "L")
	id := c.swing("dtl")
	frames := []map[string]any{}
	for _, p := range []string{"P1", "P2", "P3", "P4", "P5", "P6", "P7"} {
		f := map[string]any{"checkpoint": p, "t": 0.5, "frame": 120, "landmarks": lm33(), "thumb": jpegB64(t, 360, 202)}
		if p == "P2" {
			f["taps"] = map[string]any{"grip": []float64{690, 370}, "head": []float64{640, 360}}
		}
		frames = append(frames, f)
	}
	out := c.do("PUT", "/v1/swings/"+jsonNum(id)+"/frames", map[string]any{"frames": frames, "missing": []string{"P8"}}, 200)
	if out["measure"] == nil {
		t.Fatal("測った結果が無い")
	}
	if len(c.an.cp.measured) != 1 {
		t.Fatalf("測った回数 %d", len(c.an.cp.measured))
	}
	m := c.an.cp.measured[0]
	if m.Handedness != model.LeftHanded || m.View != "dtl" || len(m.Frames) != 7 || m.FPS != 240 || m.Missing[0] != "P8" {
		t.Fatalf("分析サービスへ渡した中身が違う: %+v", m)
	}
	if !strings.Contains(string(m.Frames["P2"]), `"grip":[690,370]`) || strings.Contains(string(m.Frames["P2"]), "thumb") {
		t.Fatalf("タップが渡っていない・サムネイルを渡している: %s", m.Frames["P2"])
	}
	// 判定は swing_checks に残る（束ねた項目も行として残し、理由に印）
	cs, err := c.env.st.ListSwingChecks(context.Background(), id, "checkpoints/1.0-pgag")
	if err != nil || len(cs) != 4 {
		t.Fatalf("判定の行: %v %d", err, len(cs))
	}
	for _, x := range cs {
		if x.ItemID == "err.steep.p6" && (x.State != "unknown" || x.Reason != "same_as") {
			t.Fatalf("束ねた項目の行: %+v", x)
		}
		if x.ItemID == "iron.p2.dtl.head_vs_hands" && (x.State != "out_range" || x.FaultID != "inside" || x.Basis != "measured_tap") {
			t.Fatalf("判定の行: %+v", x)
		}
	}
	// 記録のまとめ: スイングごとの判定を分析サービスに渡して課題を選ぶ
	got := c.do("GET", "/v1/sessions/"+jsonNum(c.sid)+"/checks", nil, 200)
	if got["checks"] == nil || len(c.an.cp.focus) != 1 || len(c.an.cp.focus[0].Swings) != 1 {
		t.Fatalf("まとめ: %v", got)
	}
	if !strings.Contains(string(c.an.cp.focus[0].Swings[0]), `"view":"dtl"`) || c.an.cp.focus[0].Handedness != model.LeftHanded {
		t.Fatalf("まとめへの入力: %s", c.an.cp.focus[0].Swings[0])
	}
	sws := got["swings"].([]any)
	fs := sws[0].(map[string]any)["frames"].([]any)
	if len(fs) != 7 || fs[0].(map[string]any)["checkpoint"] != "P1" || fs[0].(map[string]any)["has_thumb"] != true {
		t.Fatalf("コマの一覧: %v", fs)
	}
	// サムネイルは JPEG で返る
	resp, err := http.Get(c.ts.URL + "/v1/swings/" + jsonNum(id) + "/thumbs/P3")
	if err != nil {
		t.Fatal(err)
	}
	b, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 200 || resp.Header.Get("Content-Type") != "image/jpeg" || len(b) < 100 {
		t.Fatalf("サムネイル: %d %s", resp.StatusCode, resp.Header.Get("Content-Type"))
	}
	c.do("GET", "/v1/swings/"+jsonNum(id)+"/thumbs/P9", nil, 404)
}

func Test全解像度のコマはDBに入らない(t *testing.T) {
	c := newCPEnv(t, "R")
	id := c.swing("dtl")
	big := []map[string]any{{"checkpoint": "P1", "landmarks": lm33(), "thumb": jpegB64(t, 1024, 576)}}
	c.do("PUT", "/v1/swings/"+jsonNum(id)+"/frames", map[string]any{"frames": big}, 400)
	png := []map[string]any{{"checkpoint": "P1", "thumb": base64.StdEncoding.EncodeToString([]byte("\x89PNG\r\n\x1a\nxxxx"))}}
	c.do("PUT", "/v1/swings/"+jsonNum(id)+"/frames", map[string]any{"frames": png}, 400)
	ok := []map[string]any{{"checkpoint": "P1", "landmarks": lm33(), "thumb": jpegB64(t, 202, 360)}}
	c.do("PUT", "/v1/swings/"+jsonNum(id)+"/frames", map[string]any{"frames": ok}, 200)
	sizes, err := c.env.st.SwingBlobSizes(context.Background())
	if err != nil || len(sizes) != 1 || sizes[0] > ThumbMaxBytes {
		t.Fatalf("サムネイルの大きさ: %v %v", err, sizes)
	}
	n, err := c.env.st.TextColumnsWithJPEG(context.Background())
	if err != nil || n != 0 {
		t.Fatalf("JSON の列に画像が紛れている: %v %d", err, n)
	}
	// タップだけ直しても、サムネイルは残る（送り直さない）
	c.do("PUT", "/v1/swings/"+jsonNum(id)+"/taps", map[string]any{"taps": map[string]any{"P1": map[string]any{"grip": []float64{600, 400}, "head": []float64{800, 600}}}}, 200)
	if sizes, _ := c.env.st.SwingBlobSizes(context.Background()); len(sizes) != 1 {
		t.Fatalf("タップを直したらサムネイルが消えた: %v", sizes)
	}
	c.do("PUT", "/v1/swings/"+jsonNum(id)+"/taps", map[string]any{"taps": map[string]any{"P5": map[string]any{"grip": []float64{1, 1}}}}, 400)
}

func Test入力の検査(t *testing.T) {
	c := newCPEnv(t, "R")
	path := "/v1/sessions/" + jsonNum(c.sid) + "/swings"
	c.do("POST", path, map[string]any{"view": "face_on", "width": 1280, "height": 720}, 400)
	c.do("POST", path, map[string]any{"view": "dtl", "width": 1280, "height": 720, "club_class": "putter"}, 400)
	c.do("POST", path, map[string]any{"view": "dtl", "width": 1280, "height": 720, "ball": [][]float64{{1, 1}}}, 400)
	c.do("POST", path, map[string]any{"view": "dtl", "width": 1280, "height": 720, "capture": map[string]string{"img": strings.Repeat("A", 5000)}}, 400)
	c.do("POST", "/v1/sessions/999/swings", map[string]any{"view": "dtl", "width": 1280, "height": 720}, 404)
	id := c.swing("fo")
	fp := "/v1/swings/" + jsonNum(id) + "/frames"
	c.do("PUT", fp, map[string]any{"frames": []map[string]any{{"checkpoint": "P11"}}}, 400)
	c.do("PUT", fp, map[string]any{"frames": []map[string]any{{"checkpoint": "P1"}, {"checkpoint": "P1"}}}, 400)
	c.do("PUT", fp, map[string]any{"frames": []map[string]any{{"checkpoint": "P1", "landmarks": []map[string]float64{{"x": 1}}}}}, 400)
	bad := lm33()
	bad[3]["image"] = 1
	c.do("PUT", fp, map[string]any{"frames": []map[string]any{{"checkpoint": "P1", "landmarks": bad}}}, 400)
	c.do("PUT", fp, map[string]any{"frames": []map[string]any{{"checkpoint": "P2", "taps": map[string]any{"grip": []float64{5000, 1}}}}}, 400)
	c.do("PUT", fp, map[string]any{"frames": []map[string]any{{"checkpoint": "P2", "taps": map[string]any{"elbow": []float64{5, 1}}}}}, 400)
	c.do("PUT", fp, map[string]any{"frames": []map[string]any{{"checkpoint": "P2"}}, "missing": []string{"Px"}}, 400)
	// 消すと見えなくなる
	c.do("DELETE", "/v1/swings/"+jsonNum(id), nil, 204)
	c.do("GET", "/v1/swings/"+jsonNum(id), nil, 404)
	c.do("DELETE", "/v1/swings/"+jsonNum(id), nil, 404)
	if l := c.doList("GET", "/v1/sessions/"+jsonNum(c.sid)+"/swings"); len(l) != 0 {
		t.Fatalf("消したスイングが一覧に出る: %v", l)
	}
}

func (c *cpEnv) doList(method, path string) []any {
	req, _ := http.NewRequest(method, c.ts.URL+path, nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		c.t.Fatal(err)
	}
	defer resp.Body.Close()
	var out []any
	_ = json.NewDecoder(resp.Body).Decode(&out)
	return out
}

func Test番手を直すと測り直す(t *testing.T) {
	c := newCPEnv(t, "R")
	id := c.swing("dtl")
	c.do("PUT", "/v1/swings/"+jsonNum(id)+"/frames", map[string]any{"frames": []map[string]any{{"checkpoint": "P1", "landmarks": lm33()}}}, 200)
	out := c.do("PATCH", "/v1/swings/"+jsonNum(id), map[string]any{"club": "5 Wood", "club_class": "wood"}, 200)
	if out["swing"].(map[string]any)["club_class"] != "wood" || len(c.an.cp.measured) != 2 || c.an.cp.measured[1].ClubClass != "wood" {
		t.Fatalf("番手を直したあと: %v", out["swing"])
	}
	c.do("PATCH", "/v1/swings/"+jsonNum(id), map[string]any{"club_class": "putter"}, 400)
}

func Testカタログは中継してETagで返す(t *testing.T) {
	e := newEnv(t)
	req, _ := http.NewRequest("GET", e.ts.URL+"/v1/checkpoints?handedness=L", nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	etag := resp.Header.Get("ETag")
	if resp.StatusCode != 200 || etag == "" || !strings.Contains(string(b), `"hand":"L"`) {
		t.Fatalf("カタログ: %d %q %s", resp.StatusCode, etag, b)
	}
	req.Header.Set("If-None-Match", etag)
	resp, err = http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusNotModified {
		t.Fatalf("同じ版なのに %d", resp.StatusCode)
	}
	e.an.down = true
	e.do("GET", "/v1/checkpoints", nil, 503)
}

func Test同梱のファイルはimmutableで配る(t *testing.T) {
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "vendor", "x-1.0"), 0o755); err != nil {
		t.Fatal(err)
	}
	_ = os.WriteFile(filepath.Join(dir, "vendor", "x-1.0", "a.js"), []byte("1"), 0o644)
	_ = os.WriteFile(filepath.Join(dir, "index.html"), []byte("<!doctype html>"), 0o644)
	st := openStore(t)
	t.Cleanup(func() { st.Close() })
	srv := New(st, &fakeAnalyzer{})
	srv.Static = http.FileServer(http.Dir(dir))
	ts := httptest.NewServer(srv.Handler())
	t.Cleanup(ts.Close)
	resp, err := http.Get(ts.URL + "/vendor/x-1.0/a.js")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if !strings.Contains(resp.Header.Get("Cache-Control"), "immutable") {
		t.Fatalf("vendor に immutable が無い: %q", resp.Header.Get("Cache-Control"))
	}
	resp, _ = http.Get(ts.URL + "/index.html")
	resp.Body.Close()
	if strings.Contains(resp.Header.Get("Cache-Control"), "immutable") {
		t.Fatal("画面の本体に immutable を付けてはいけない（直した画面が届かなくなる）")
	}
}

func Test参照動画を受ける保存の口は無い(t *testing.T) {
	e := newEnv(t)
	for _, p := range []string{"/v1/refs", "/v1/reference-videos", "/v1/videos", "/v1/swings/1/video"} {
		req, _ := http.NewRequest("POST", e.ts.URL+p, bytes.NewReader([]byte("{}")))
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusNotFound && resp.StatusCode != http.StatusMethodNotAllowed {
			t.Fatalf("%s が %d（動画・参照動画を受ける口を作らない）", p, resp.StatusCode)
		}
	}
}

// 古い判定が残らない（段2a のレビュー）: 利き手を変えた・保存のあとに測るのが失敗した・カタログを直した、のどれでも測り直す
func Test測った条件が変われば一覧を開いたときに測り直す(t *testing.T) {
	c := newCPEnv(t, "R")
	id := c.swing("dtl")
	fp := "/v1/swings/" + jsonNum(id) + "/frames"
	frames := []map[string]any{{"checkpoint": "P1", "landmarks": lm33()}, {"checkpoint": "P2", "landmarks": lm33(), "taps": map[string]any{"grip": []float64{690, 370}, "head": []float64{640, 360}}}}
	c.do("PUT", fp, map[string]any{"frames": frames}, 200)
	checks := "/v1/sessions/" + jsonNum(c.sid) + "/checks"
	c.do("GET", checks, nil, 200)
	if n := len(c.an.cp.measured); n != 1 {
		t.Fatalf("何も変わっていないのに測り直した: %d", n)
	}
	// (a) 利き手を変える
	pid := c.do("GET", "/v1/sessions/"+jsonNum(c.sid), nil, 200)["player_id"]
	c.do("PATCH", "/v1/players/"+jsonNum(int64(pid.(float64))), map[string]any{"handedness": "L"}, 200)
	c.do("GET", checks, nil, 200)
	if n := len(c.an.cp.measured); n != 2 || c.an.cp.measured[1].Handedness != model.LeftHanded {
		t.Fatalf("利き手を変えても測り直さない: %d", n)
	}
	// (b) タップを直したあと測るのが失敗しても、タップは残り、次に開いたときに新しいタップで測る
	c.an.cp.failNext = true
	c.do("PUT", "/v1/swings/"+jsonNum(id)+"/taps", map[string]any{"taps": map[string]any{"P2": map[string]any{"grip": []float64{690, 370}, "head": []float64{692, 368}}}}, 503)
	c.do("GET", checks, nil, 200)
	last := c.an.cp.measured[len(c.an.cp.measured)-1]
	if !strings.Contains(string(last.Frames["P2"]), `"head":[692,368]`) {
		t.Fatalf("直したタップで測り直していない: %s", last.Frames["P2"])
	}
	n := len(c.an.cp.measured)
	// (c) 版の文字列を変えずにカタログを直した（分析サービスの指紋だけが変わる）
	c.an.cp.stamp = "stamp-2"
	c.do("GET", checks, nil, 200)
	if len(c.an.cp.measured) != n+1 {
		t.Fatal("カタログを直しても測り直さない")
	}
	c.do("GET", checks, nil, 200)
	if len(c.an.cp.measured) != n+1 {
		t.Fatal("指紋が同じなのに毎回測り直している")
	}
}

func Test向きを直すと測り直す(t *testing.T) {
	c := newCPEnv(t, "R")
	id := c.swing("fo")
	c.do("PUT", "/v1/swings/"+jsonNum(id)+"/frames", map[string]any{"frames": []map[string]any{{"checkpoint": "P1", "landmarks": lm33()}}}, 200)
	out := c.do("PATCH", "/v1/swings/"+jsonNum(id), map[string]any{"view": "dtl"}, 200)
	if out["swing"].(map[string]any)["view"] != "dtl" || c.an.cp.measured[len(c.an.cp.measured)-1].View != "dtl" {
		t.Fatalf("向きを直したあと: %v", out["swing"])
	}
	c.do("PATCH", "/v1/swings/"+jsonNum(id), map[string]any{"view": "side"}, 400)
}

// 球が無く動画だけの記録: ホームが「ようこそ」のままにならないよう、動画のある最新の記録を返す（測れたスイングだけ数える）
func Testホームは動画だけの記録も返す(t *testing.T) {
	c := newCPEnv(t, "R")
	pid := int64(c.do("GET", "/v1/sessions/"+jsonNum(c.sid), nil, 200)["player_id"].(float64))
	c.swing("dtl") // コマを送っていない（送り直しの途中で残ったもの）は数えない
	h := c.do("GET", "/v1/players/"+jsonNum(pid)+"/home", nil, 200)
	if h["latest_video"] != nil {
		t.Fatalf("測れていないスイングを数えた: %v", h["latest_video"])
	}
	id := c.swing("dtl")
	c.do("PUT", "/v1/swings/"+jsonNum(id)+"/frames", map[string]any{"frames": []map[string]any{{"checkpoint": "P1", "landmarks": lm33()}}}, 200)
	h = c.do("GET", "/v1/players/"+jsonNum(pid)+"/home", nil, 200)
	v, _ := h["latest_video"].(map[string]any)
	if v == nil || int64(v["session_id"].(float64)) != c.sid || v["n_swings"].(float64) != 1 || h["sessions_with_shots"].(float64) != 0 {
		t.Fatalf("動画だけの記録: %v", h)
	}
}
