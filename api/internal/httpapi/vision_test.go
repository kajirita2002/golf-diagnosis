package httpapi

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
)

// 見た目の評価（段2c）と球との対応づけ。Claude は偽物（分析サービスの口ごと差し替える）。

type visEnv struct {
	*cpEnv
	srv *Server
}

func newVisEnv(t *testing.T, limit int) *visEnv {
	st := openStore(t)
	t.Cleanup(func() { st.Close() })
	an := &fakeAnalyzer{}
	srv := New(st, an)
	srv.LLM.DailyLimitVideo = limit
	ts := httptest.NewServer(srv.Handler())
	t.Cleanup(ts.Close)
	t.Cleanup(srv.WaitLLMJobs)
	e := &env{t: t, ts: ts, an: an, st: st}
	p := e.do("POST", "/v1/players", map[string]any{"name": "me", "handedness": "R"}, 201)
	se := e.do("POST", "/v1/sessions", map[string]any{"player_id": p["id"], "date": "2026-09-30"}, 201)
	return &visEnv{cpEnv: &cpEnv{env: e, sid: int64(se["id"].(float64))}, srv: srv}
}

// withFrames はスイングを作って P1〜P7 のコマを入れる。
func (v *visEnv) withFrames(t *testing.T, view string) int64 {
	id := v.swing(view)
	frames := []map[string]any{}
	for _, p := range []string{"P1", "P2", "P3", "P4", "P5", "P6", "P7"} {
		frames = append(frames, map[string]any{"checkpoint": p, "t": 0.5, "frame": 120, "landmarks": lm33(), "thumb": jpegB64(t, 360, 202)})
	}
	v.do("PUT", "/v1/swings/"+jsonNum(id)+"/frames", map[string]any{"frames": frames}, 200)
	return id
}

type part struct {
	name string
	body []byte
}

func (v *visEnv) post(t *testing.T, meta any, parts []part, want int) map[string]any {
	t.Helper()
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	mb, _ := json.Marshal(meta)
	_ = mw.WriteField("meta", string(mb))
	for _, p := range parts {
		fw, _ := mw.CreateFormFile(p.name, p.name+".jpg")
		_, _ = fw.Write(p.body)
	}
	_ = mw.Close()
	req, _ := http.NewRequest("POST", v.ts.URL+"/v1/swings/checks", &buf)
	req.Header.Set("Content-Type", mw.FormDataContentType())
	return v.send(req, want)
}

func jpegBytes(t *testing.T, w, h int) []byte {
	b, _ := base64.StdEncoding.DecodeString(jpegB64(t, w, h))
	return b
}

func framesOf(t *testing.T, id int64, ps ...string) []part {
	var out []part
	for _, p := range ps {
		out = append(out, part{fmt.Sprintf("f.%d.%s", id, p), jpegBytes(t, 1024, 576)})
	}
	return out
}

func meta(sid int64, ids ...int64) map[string]any {
	sw := []map[string]any{}
	for _, id := range ids {
		sw = append(sw, map[string]any{"swing_id": id})
	}
	return map[string]any{"session_id": sid, "swings": sw}
}

func Test見た目の評価はジョブで答えだけを残し画像を残さない(t *testing.T) {
	v := newVisEnv(t, -1)
	a, b := v.withFrames(t, "dtl"), v.withFrames(t, "dtl")
	parts := append(framesOf(t, a, "P1", "P2", "P3"), framesOf(t, b, "P1", "P2")...)
	out := v.post(t, meta(v.sid, a, b), parts, 202)
	if out["job_id"] == nil {
		t.Fatalf("ジョブが無い: %v", out)
	}
	v.srv.WaitLLMJobs()
	job := v.do("GET", "/v1/jobs/"+jsonNum(int64(out["job_id"].(float64))), nil, 200)
	if job["status"] != "done" || job["kind"] != "cp_review" {
		t.Fatalf("ジョブ: %v", job)
	}
	// 分析サービスへ渡したのは2本・順番どおり・画像の中身（保存はしない）
	calls := v.an.cp.visionCalls
	if len(calls) != 1 || len(calls[0]) != 2 || calls[0][0].SwingID != a || len(calls[0][0].Frames) != 3 || len(calls[0][1].Frames) != 2 {
		t.Fatalf("渡した中身: %+v", calls)
	}
	if calls[0][0].Handedness != model.RightHanded || calls[0][0].View != "dtl" {
		t.Fatalf("向き・利き手: %+v", calls[0][0])
	}
	// 答えはスイングごとに残り、測り直しで measure に渡る
	sv, err := v.st.GetSwingVision(context.Background(), a)
	if err != nil || sv == nil || !strings.Contains(string(sv.Answers), "丸まっている") {
		t.Fatalf("答え: %v %v", err, sv)
	}
	last := v.an.cp.measured[len(v.an.cp.measured)-1]
	if !strings.Contains(string(last.Vision), "setup.spine") {
		t.Fatalf("測り直しに答えが渡っていない: %s", last.Vision)
	}
	// 評価のあと DB に JPEG が無い（サムネイルの列を除く）
	n, err := v.st.TextColumnsWithJPEG(context.Background())
	if err != nil || n != 0 {
		t.Fatalf("JPEG が JSON の列に入った: %v %d", err, n)
	}
	sizes, _ := v.st.SwingBlobSizes(context.Background())
	for _, s := range sizes {
		if s > ThumbMaxBytes {
			t.Fatalf("大きな画像が残った: %d", s)
		}
	}
	if raw, _ := json.Marshal(job); bytes.Contains(raw, []byte("/9j/")) {
		t.Fatal("ジョブの結果に画像がある")
	}
	// 一覧には見た目の評価の状態（料金の見積もり・最後の結果）が出る
	got := v.do("GET", "/v1/sessions/"+jsonNum(v.sid)+"/checks", nil, 200)
	vi := got["vision"].(map[string]any)
	if vi["ready"] != true || vi["n_target"].(float64) != 2 || vi["cost_usd_hi"].(float64) < 0.27 {
		t.Fatalf("見た目の評価の状態: %v", vi)
	}
	if l := vi["last"].(map[string]any); l["status"] != "done" || l["n_dropped"].(float64) != 0 || l["tokens"].(float64) != 16500 {
		t.Fatalf("最後の結果: %v", l)
	}
	sws := got["swings"].([]any)
	if sws[0].(map[string]any)["has_vision"] != true {
		t.Fatal("has_vision が立たない")
	}
	// 同じ画像はキャッシュ（Claude を呼ばない・上限にも数えない）
	again := v.post(t, meta(v.sid, a, b), parts, 200)
	if again["cached"] != true || len(v.an.cp.visionCalls) != 1 {
		t.Fatalf("キャッシュ: %v %d", again, len(v.an.cp.visionCalls))
	}
	u, _ := v.st.GetLLMUsage(context.Background(), llmDay(), "cp_review")
	if u.Calls != 1 || u.InputTokens != 14000 {
		t.Fatalf("使った量: %+v", u)
	}
}

func Test見た目の評価の受け口は画像を確かめる(t *testing.T) {
	v := newVisEnv(t, -1)
	a := v.withFrames(t, "dtl")
	// 長辺 1024px を超える・JPEG でない・選んでいないコマ・ほかの記録のスイング・知らないスイング
	v.post(t, meta(v.sid, a), []part{{fmt.Sprintf("f.%d.P1", a), jpegBytes(t, 1280, 720)}}, 400)
	v.post(t, meta(v.sid, a), []part{{fmt.Sprintf("f.%d.P1", a), []byte("\x89PNG\r\n\x1a\nxxxx")}}, 400)
	v.post(t, meta(v.sid, a), framesOf(t, a, "P8"), 400)
	v.post(t, meta(v.sid, a), framesOf(t, a+99, "P1"), 400)
	v.post(t, map[string]any{"session_id": v.sid, "swings": []any{}}, nil, 400)
	// 33枚は断る
	var many []part
	for i := 0; i < 33; i++ {
		many = append(many, part{fmt.Sprintf("f.%d.P%d", a, i%7+1), jpegBytes(t, 32, 32)})
	}
	v.post(t, meta(v.sid, a), many, 400)
	if len(v.an.cp.visionCalls) != 0 {
		t.Fatal("断ったのに呼んだ")
	}
	// 別の記録のスイング
	se2 := v.do("POST", "/v1/sessions", map[string]any{"player_id": 1, "date": "2026-09-29"}, 201)
	v.post(t, meta(int64(se2["id"].(float64)), a), framesOf(t, a, "P1"), 400)
}

func Test鍵が無ければ呼ばずに理由を返す(t *testing.T) {
	v := newVisEnv(t, -1)
	v.an.cp.visionOff = true
	a := v.withFrames(t, "dtl")
	out := v.post(t, meta(v.sid, a), framesOf(t, a, "P1"), 200)
	if out["job_id"] != nil || !strings.Contains(out["reason"].(string), "まだ使えません") {
		t.Fatalf("鍵が無いとき: %v", out)
	}
	got := v.do("GET", "/v1/sessions/"+jsonNum(v.sid)+"/checks", nil, 200)
	if vi := got["vision"].(map[string]any); vi["ready"] != false || vi["reason"] == "" {
		t.Fatalf("一覧の状態: %v", vi)
	}
}

func Test見た目の評価の上限(t *testing.T) {
	v := newVisEnv(t, 0)
	a := v.withFrames(t, "dtl")
	out := v.post(t, meta(v.sid, a), framesOf(t, a, "P1"), 200)
	if out["limit"] != true || len(v.an.cp.visionCalls) != 0 {
		t.Fatalf("上限: %v", out)
	}
}

func Test答えが使えなければ失敗にして枠は残す(t *testing.T) {
	v := newVisEnv(t, -1)
	v.an.cp.visionOut = `{"called":2,"model":"claude-opus-5-5","cacheable":false,"reason":"validation","message":"Claude の答えが検証を通りませんでした","usage":{"input_tokens":10,"output_tokens":5,"cost_usd":0.01},"swings":[]}`
	a := v.withFrames(t, "dtl")
	out := v.post(t, meta(v.sid, a), framesOf(t, a, "P1"), 202)
	v.srv.WaitLLMJobs()
	job := v.do("GET", "/v1/jobs/"+jsonNum(int64(out["job_id"].(float64))), nil, 200)
	if job["status"] != "failed" || !strings.Contains(job["error"].(string), "検証") {
		t.Fatalf("ジョブ: %v", job)
	}
	if sv, _ := v.st.GetSwingVision(context.Background(), a); sv != nil {
		t.Fatal("使えない答えを残した")
	}
	// 失敗はキャッシュしない（もう一度押せば頼み直せる）
	v.post(t, meta(v.sid, a), framesOf(t, a, "P1"), 202)
	v.srv.WaitLLMJobs()
	if len(v.an.cp.visionCalls) != 2 {
		t.Fatalf("頼み直せない: %d", len(v.an.cp.visionCalls))
	}
}

func Test球との対応づけは案を出して本人が結ぶ(t *testing.T) {
	v := newVisEnv(t, -1)
	a, b := v.withFrames(t, "dtl"), v.withFrames(t, "dtl")
	v.importCSV(int(v.sid), "Club,Club Speed [mph],Face Angle [deg],Club Path [deg],Carry [yds]\n7i,80,-1,3,150\n7i,81,0,4,152\n", "", 201)
	s := v.do("POST", "/v1/sessions/"+jsonNum(v.sid)+"/swings/match-suggest", nil, 200)
	if s["mismatch"] != false || len(s["pairs"].([]any)) != 2 {
		t.Fatalf("案: %v", s)
	}
	p0 := s["pairs"].([]any)[0].(map[string]any)
	if int64(p0["swing_id"].(float64)) != a || p0["seq"].(float64) != 1 {
		t.Fatalf("順番に当てていない: %v", p0)
	}
	v.do("PUT", "/v1/swings/"+jsonNum(a)+"/match", map[string]any{"seq": 1}, 200)
	v.do("PUT", "/v1/swings/"+jsonNum(b)+"/match", map[string]any{"seq": 9}, 400)
	got := v.do("GET", "/v1/sessions/"+jsonNum(v.sid)+"/checks", nil, 200)
	if got["match"].(map[string]any)["matched"].(float64) != 1 {
		t.Fatalf("結んだ数: %v", got["match"])
	}
	// 症状は結んだ球と一緒に探し、課題の印に渡す
	sy := v.an.cp.symptoms[len(v.an.cp.symptoms)-1]
	if len(sy.MatchedSeqs) != 1 || sy.MatchedSeqs[0] != 1 || sy.Clubs[0] != "iron" || len(sy.Shots) != 2 {
		t.Fatalf("症状の入力: %+v", sy)
	}
	f := v.an.cp.focus[len(v.an.cp.focus)-1]
	if len(f.Symptoms) != 1 || f.Symptoms[0] != "S5" {
		t.Fatalf("課題の印の症状: %v", f.Symptoms)
	}
	// 外す
	v.do("PUT", "/v1/swings/"+jsonNum(a)+"/match", map[string]any{"seq": nil}, 200)
	// 本数が合わなければ案を出さない
	v.withFrames(t, "dtl")
	s = v.do("POST", "/v1/sessions/"+jsonNum(v.sid)+"/swings/match-suggest", nil, 200)
	if s["mismatch"] != true || len(s["pairs"].([]any)) != 0 || s["n_swings"].(float64) != 3 || s["n_shots"].(float64) != 2 {
		t.Fatalf("本数が合わないとき: %v", s)
	}
}

func Testホームの今日の一点が動きの課題になる(t *testing.T) {
	v := newVisEnv(t, -1)
	v.an.cp.focusOut = `{"focus":"iron.p2.dtl.head_vs_hands","rationale":"前ほど響く","items":[{"id":"iron.p2.dtl.head_vs_hands","title":"上げ始めのクラブの先の位置","p":"P2",
		"fault_label":"クラブが体の内側に引かれています","basis":"measured_tap","linked":true,"linked_text":"球の課題とつながる候補です（まだ確かめていません）",
		"frames":[{"swing_id":1,"p":"P2"}],"drills":[{"id":"cp.p2_head_inside","title":"上げ始めで手首を返しすぎない","cue":"手のひらは返さず"}]}]}`
	v.withFrames(t, "dtl")
	h := v.do("GET", "/v1/players/1/home", nil, 200)
	mf, ok := h["motion_focus"].(map[string]any)
	if !ok || mf["item_id"] != "iron.p2.dtl.head_vs_hands" || mf["linked"] != true || mf["drill"].(map[string]any)["id"] != "cp.p2_head_inside" {
		t.Fatalf("ホームの動きの課題: %v", h["motion_focus"])
	}
	// 課題が無ければ出さない
	v.an.cp.focusOut = `{"focus":null,"items":[]}`
	h = v.do("GET", "/v1/players/1/home", nil, 200)
	if h["motion_focus"] != nil {
		t.Fatalf("課題が無いのに出した: %v", h["motion_focus"])
	}
}
