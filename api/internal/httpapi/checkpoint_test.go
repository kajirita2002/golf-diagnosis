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
	video    []json.RawMessage
	measured []analysis.CheckpointSwing
	focus    []analysis.CheckpointFocusInput
	stamp    string // 分析サービスの指紋（空なら stamp-1）
	failNext bool   // 次の測るを 503 にする（保存のあとに測るのが失敗した道）
	// 見た目の評価（段2c）
	visionOff   bool
	focusOut    string
	visionOut   string
	visionCalls [][]analysis.VisionSwing
	symptoms    []analysis.SymptomsInput
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

func (f *fakeAnalyzer) VideoCheckpoints(_ context.Context, body json.RawMessage) (json.RawMessage, error) {
	if f.down {
		return nil, analysis.ErrUnavailable
	}
	f.cp.video = append(f.cp.video, body)
	return json.RawMessage(`{"video_version":"video/0.2","swings":[{"index":1,"t0":1.05,"ps":[]}],"excluded":[]}`), nil
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
	if f.cp.focusOut != "" {
		return json.RawMessage(f.cp.focusOut), nil
	}
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

func seriesOf(n int) map[string]any {
	t := make([]float64, n)
	pts := make([][]float64, n)
	for i := range t {
		t[i] = 1 + float64(i)/60
		pts[i] = []float64{0.5, 0.5, 0.9}
	}
	return map[string]any{"v": 1, "video_version": "video/0.2", "t0": 1.05, "fps": 60, "t": t, "hand": pts, "hip": pts, "chest": pts}
}

func Test姿勢の時系列は中継するだけで保存しない(t *testing.T) {
	c := newCPEnv(t, "R")
	frames := []map[string]any{}
	for i := 0; i < 5; i++ {
		frames = append(frames, map[string]any{"t": float64(i) / 60, "lm": nil})
	}
	out := c.do("POST", "/v1/video/checkpoints", map[string]any{"view": "dtl", "handedness": "R", "fps": 60, "width": 1280, "height": 720, "frames": frames}, 200)
	if out["video_version"] != "video/0.2" || len(c.an.cp.video) != 1 {
		t.Fatalf("中継: %v", out)
	}
	c.do("POST", "/v1/video/checkpoints", map[string]any{"view": "side", "frames": frames}, 400)
	// 画像は送らない約束（base64 の JPEG が紛れていたら断る）
	c.do("POST", "/v1/video/checkpoints", map[string]any{"view": "dtl", "frames": frames, "thumb": "/9j/4AAQ"}, 400)
	if len(c.an.cp.video) != 1 {
		t.Fatal("断った本文を分析サービスへ渡した")
	}
	// 上限を超える本文は読まずに断る
	big := bytes.Repeat([]byte("0,"), maxVideoBody/2+10)
	req, _ := http.NewRequest("POST", c.ts.URL+"/v1/video/checkpoints", bytes.NewReader(append(append([]byte(`{"view":"dtl","frames":[],"x":[`), big...), []byte(`0]}`)...)))
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 400 {
		t.Fatalf("大きすぎる本文: %d", resp.StatusCode)
	}
	// 何も保存していない（スイングの行ができていない）
	if ss, _ := c.env.st.ListSwings(context.Background(), c.sid); len(ss) != 0 {
		t.Fatalf("中継でスイングができた: %d", len(ss))
	}
}

func Test自動で取り出した時系列をスイングに残して測る(t *testing.T) {
	c := newCPEnv(t, "R")
	id := c.swing("dtl")
	frames := []map[string]any{{"checkpoint": "P1", "t": 1.0, "frame": 60, "source": "auto", "method": "rule", "status": "estimated", "landmarks": lm33(), "thumb": jpegB64(t, 360, 202)},
		{"checkpoint": "P4", "t": 1.8, "frame": 108, "source": "manual", "method": "rule", "status": "estimated", "landmarks": lm33()}}
	out := c.do("PUT", "/v1/swings/"+jsonNum(id)+"/frames", map[string]any{"frames": frames, "series": seriesOf(120)}, 200)
	sw := out["swing"].(map[string]any)
	if sw["has_series"] != true {
		t.Fatalf("時系列の印: %v", sw)
	}
	m := c.an.cp.measured[len(c.an.cp.measured)-1]
	if !strings.Contains(string(m.Series), `"t0":1.05`) {
		t.Fatalf("測る入力に時系列が無い: %s", m.Series)
	}
	// 自動と手で直したコマは source で分かれて残る（手で直したコマの割合を出すため）
	fs := out["frames"].([]any)
	if fs[0].(map[string]any)["source"] != "auto" || fs[1].(map[string]any)["source"] != "manual" {
		t.Fatalf("source: %v", fs)
	}
	// 自動のコマは決め方と状態（目安）を残す。手で選んだコマは持たない（手で選んだものを「目安」と出さない）
	if f0 := fs[0].(map[string]any); f0["status"] != "estimated" || f0["method"] != "rule" {
		t.Fatalf("自動のコマの状態: %v", f0)
	}
	if f1 := fs[1].(map[string]any); f1["status"] != "" || f1["method"] != "" {
		t.Fatalf("手で選んだコマに状態が残った: %v", f1)
	}
	for _, bad := range []map[string]any{{"status": "failed"}, {"method": "claude"}} {
		f := map[string]any{"checkpoint": "P1", "t": 1.0, "frame": 60, "source": "auto", "landmarks": lm33()}
		for k, v := range bad {
			f[k] = v
		}
		c.do("PUT", "/v1/swings/"+jsonNum(id)+"/frames", map[string]any{"frames": []map[string]any{f}}, 400)
	}
	// 時系列を渡さずにコマだけ直しても、時系列は残る
	c.do("PUT", "/v1/swings/"+jsonNum(id)+"/frames", map[string]any{"frames": frames[:1]}, 200)
	m = c.an.cp.measured[len(c.an.cp.measured)-1]
	if len(m.Series) == 0 {
		t.Fatal("コマだけ直したら時系列が消えた")
	}
	// 形の違う時系列・知らない欄・画像は断る
	bad := seriesOf(10)
	bad["thumb"] = "/9j/4AAQ"
	c.do("PUT", "/v1/swings/"+jsonNum(id)+"/frames", map[string]any{"frames": frames, "series": bad}, 400)
	short := seriesOf(10)
	short["hand"] = [][]float64{{0.5, 0.5, 0.9}}
	c.do("PUT", "/v1/swings/"+jsonNum(id)+"/frames", map[string]any{"frames": frames, "series": short}, 400)
	back := seriesOf(10)
	back["t"] = []float64{3, 2, 1, 4, 5, 6, 7, 8, 9, 10}
	c.do("PUT", "/v1/swings/"+jsonNum(id)+"/frames", map[string]any{"frames": frames, "series": back}, 400)
	c.do("PUT", "/v1/swings/"+jsonNum(id)+"/frames", map[string]any{"frames": frames, "series": seriesOf(maxSeriesFrames + 1)}, 400)
	// 時系列が変わると指紋が変わり、次に一覧を開いたときに測り直す
	n := len(c.an.cp.measured)
	c.do("GET", "/v1/sessions/"+jsonNum(c.sid)+"/checks", nil, 200)
	if len(c.an.cp.measured) != n {
		t.Fatal("変わっていないのに測り直した")
	}
	if err := c.env.st.PutSwingSeries(context.Background(), id, json.RawMessage(`{"v":1,"t0":2,"fps":60,"t":[1,2,3],"hand":[null,null,null],"hip":[null,null,null],"chest":[null,null,null]}`)); err != nil {
		t.Fatal(err)
	}
	c.do("GET", "/v1/sessions/"+jsonNum(c.sid)+"/checks", nil, 200)
	if len(c.an.cp.measured) != n+1 {
		t.Fatal("時系列が変わったのに測り直していない")
	}
	// JSON の列に画像は無い
	if k, err := c.env.st.TextColumnsWithJPEG(context.Background()); err != nil || k != 0 {
		t.Fatalf("画像: %v %d", err, k)
	}
}

func (f *fakeAnalyzer) CheckpointsVisionStatus(_ context.Context) (*analysis.VisionStatus, error) {
	if f.down {
		return nil, analysis.ErrUnavailable
	}
	if f.cp.visionOff {
		return &analysis.VisionStatus{Ready: false, Reason: "AI の評価はまだ使えません", Model: "claude-opus-5-5", PromptVersion: "checkpoints_q/1.0", CostPerSwingUSD: []float64{0.08, 0.14}, DefaultSwings: 2}, nil
	}
	return &analysis.VisionStatus{Ready: true, Model: "claude-opus-5-5", PromptVersion: "checkpoints_q/1.0", CatalogStamp: "st", CostPerSwingUSD: []float64{0.08, 0.14}, DefaultSwings: 2, MaxSwings: 4, MaxImages: 32}, nil
}

func (f *fakeAnalyzer) CheckpointsVision(_ context.Context, swings []analysis.VisionSwing) (json.RawMessage, error) {
	f.cp.visionCalls = append(f.cp.visionCalls, swings)
	if f.cp.visionOut != "" {
		return json.RawMessage(f.cp.visionOut), nil
	}
	var sw []string
	for i := range swings {
		sw = append(sw, `{"swing":`+jsonNum(int64(i+1))+`,"swing_id":`+jsonNum(swings[i].SwingID)+`,"answers":{"setup.spine":{"option":"丸まっている","visibility":"clear","visual":"背中が丸く見えます"}},"reselect":[]}`)
	}
	return json.RawMessage(`{"called":1,"model":"claude-opus-5-5","cacheable":true,"reason":null,"usage":{"input_tokens":14000,"output_tokens":2500,"cost_usd":0.106},
		"validation":[{"attempt":1,"problems":[]}],"dropped":[],"extra":["手元が低く見えます"],"n_asked":20,"swings":[` + strings.Join(sw, ",") + `]}`), nil
}

func (f *fakeAnalyzer) CheckpointsSymptoms(_ context.Context, in analysis.SymptomsInput) (json.RawMessage, error) {
	f.cp.symptoms = append(f.cp.symptoms, in)
	return json.RawMessage(`{"found":[{"id":"S5"}],"for_focus":["S5"]}`), nil
}
