package httpapi

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"image"
	_ "image/jpeg" // サムネイルの大きさを確かめる（中身は描かない）
	"io"
	"math"
	"net/http"
	"strings"

	"github.com/kajirita2002/golf-diagnosis/api/internal/analysis"
	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
	"github.com/kajirita2002/golf-diagnosis/api/internal/store"
)

// 動画のチェックポイント（docs/DESIGN_v2.md §5・§6・§13.1。段2a: 手で選ぶ P1〜P7 と測れる項目）。
//
// **動画はサーバーに来ない。** 端末がデコード・姿勢推定をして、ここに送るのは P のコマの時刻・姿勢の点・
// 本人のタップ・長辺 360px のサムネイルだけ。全解像度のコマ（JPEG）は受けない（大きさで断る）。
// 判定は分析サービス（checkpoints/measure.py・judge.py）が持ち、Go は保存と中継だけ。

const (
	// ThumbMaxEdge はサムネイルの長辺の上限（§6.3・§8.6）
	ThumbMaxEdge = 360
	// ThumbMaxBytes はサムネイル1枚の上限（長辺 360px の JPEG なら数十KB）
	ThumbMaxBytes = 96 << 10
	// maxFramesBody は P のコマの送信（12枚 × サムネイル＋点＋時系列）の上限
	maxFramesBody = 6 << 20
	// maxVideoBody は姿勢の時系列（POST /v1/video/checkpoints）の上限。数値だけで、画像は来ない
	// （1万コマ × 33点 × [x,y,v] でおよそ 10MB。粗い走査と細かい走査を合わせてもこれに収まる）
	maxVideoBody = 24 << 20
	// maxSeriesFrames は残す時系列のコマ数の上限（スイングの区間だけ。240fps で約25秒）
	maxSeriesFrames = 6000
)

var clubClasses = map[string]bool{"iron": true, "driver": true, "wood": true, "hybrid": true, "wedge": true}
var tapKeys = map[string]bool{"grip": true, "head": true, "heel": true, "toe": true}

// getCheckpoints はカタログを中継する（ETag つき。画面は写しを localStorage に持つ）。
func (s *Server) getCheckpoints(w http.ResponseWriter, r *http.Request) {
	hand := model.Handedness(r.URL.Query().Get("handedness"))
	raw, err := s.Analyzer.Checkpoints(r.Context(), hand)
	if err != nil {
		s.fail(w, err)
		return
	}
	sum := sha256.Sum256(raw)
	etag := `"cp-` + hex.EncodeToString(sum[:8]) + `"`
	w.Header().Set("ETag", etag)
	w.Header().Set("Cache-Control", "no-cache")
	if r.Header.Get("If-None-Match") == etag {
		w.WriteHeader(http.StatusNotModified)
		return
	}
	writeRaw(w, http.StatusOK, raw)
}

// videoCheckpoints は姿勢の時系列を分析サービスへ中継する（保存しない。§6.3・§13.1）。
// 動画そのもの・コマの画像は受けない（大きさで断り、数値でない中身は分析サービスが断る）。
func (s *Server) videoCheckpoints(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxVideoBody)
	raw, err := io.ReadAll(r.Body)
	if err != nil {
		s.fail(w, bad("姿勢の時系列が大きすぎます（%dMB まで。動画や画像は送らない約束）", maxVideoBody>>20))
		return
	}
	var head struct {
		View   string            `json:"view"`
		Frames []json.RawMessage `json:"frames"`
	}
	if err := json.Unmarshal(raw, &head); err != nil {
		s.fail(w, bad("JSON を読めません: %v", err))
		return
	}
	if head.View != "dtl" && head.View != "fo" {
		s.fail(w, bad("view は dtl（後ろから）か fo（正面から）"))
		return
	}
	if bytes.Contains(raw, []byte("/9j/")) || bytes.Contains(raw, []byte("data:image")) {
		s.fail(w, bad("画像は送らない約束です（姿勢の点の数値だけ）"))
		return
	}
	out, err := s.Analyzer.VideoCheckpoints(r.Context(), raw)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeRaw(w, http.StatusOK, out)
}

// seriesIn は残す時系列の形（video.py の series_for）。知らない欄は断り、書き直した形で残す（画像を紛れ込ませない）。
type seriesIn struct {
	V            int          `json:"v"`
	VideoVersion string       `json:"video_version"`
	T0           float64      `json:"t0"`
	FPS          float64      `json:"fps"`
	T            []float64    `json:"t"`
	Hand         [][]*float64 `json:"hand"`
	Hip          [][]*float64 `json:"hip"`
	Chest        [][]*float64 `json:"chest"`
}

func validSeries(raw json.RawMessage) (json.RawMessage, error) {
	if len(raw) == 0 || string(raw) == "null" {
		return nil, nil
	}
	var se seriesIn
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&se); err != nil {
		return nil, bad("series の形が違います: %v", err)
	}
	n := len(se.T)
	if n < 3 || n > maxSeriesFrames {
		return nil, bad("series のコマ数は 3〜%d", maxSeriesFrames)
	}
	if len(se.Hand) != n || len(se.Hip) != n || len(se.Chest) != n {
		return nil, bad("series の t と点の数が合いません")
	}
	if len(se.VideoVersion) > 32 || math.IsNaN(se.T0) || math.IsInf(se.T0, 0) || se.FPS < 0 || se.FPS > 2000 {
		return nil, bad("series の t0 / fps / 版が不正です")
	}
	prev := math.Inf(-1)
	for i, t := range se.T {
		if math.IsNaN(t) || math.IsInf(t, 0) || t < prev {
			return nil, bad("series の t は古い順の数です")
		}
		prev = t
		for _, arr := range [][][]*float64{se.Hand, se.Hip, se.Chest} {
			q := arr[i]
			if q == nil {
				continue
			}
			if len(q) != 3 {
				return nil, bad("series の点は [x, y, visibility]")
			}
			for _, v := range q {
				if v == nil || math.IsNaN(*v) || math.IsInf(*v, 0) || *v < -5 || *v > 5 {
					return nil, bad("series の点の値が不正です")
				}
			}
		}
	}
	b, err := json.Marshal(se)
	return b, err
}

// vendorCache は /vendor/（MediaPipe などの版入りのファイル）に immutable を付ける（§6.3）。
func vendorCache(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/vendor/") {
			w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
		}
		next.ServeHTTP(w, r)
	})
}

// validBall はボールの2点（[[x,y],[x,y]]）か空。
func validBall(raw json.RawMessage, wd, ht int) error {
	if len(raw) == 0 || string(raw) == "null" || string(raw) == "[]" {
		return nil
	}
	var pts [][]float64
	if err := json.Unmarshal(raw, &pts); err != nil || len(pts) != 2 {
		return bad("ball は [[x,y],[x,y]]（ボールの両端の2点）")
	}
	for _, p := range pts {
		if len(p) != 2 || !inFrame(p[0], wd) || !inFrame(p[1], ht) {
			return bad("ball の点がコマの外です")
		}
	}
	return nil
}

func inFrame(v float64, n int) bool {
	return !math.IsNaN(v) && !math.IsInf(v, 0) && v >= -1 && v <= float64(n)+1
}

func (s *Server) sessionOf(r *http.Request) (*model.Session, *model.Player, error) {
	id, err := pathID(r)
	if err != nil {
		return nil, nil, err
	}
	se, err := s.Store.GetSession(r.Context(), id)
	if err != nil {
		return nil, nil, err
	}
	pl, err := s.Store.GetPlayer(r.Context(), se.PlayerID)
	if err != nil {
		return nil, nil, err
	}
	return se, pl, nil
}

func (s *Server) createSwing(w http.ResponseWriter, r *http.Request) {
	se, _, err := s.sessionOf(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	var in struct {
		View      string          `json:"view"`
		Club      string          `json:"club"`
		ClubClass string          `json:"club_class"`
		FPS       float64         `json:"fps"`
		FPSSource string          `json:"fps_source"`
		Width     int             `json:"width"`
		Height    int             `json:"height"`
		Duration  float64         `json:"duration"`
		Ball      json.RawMessage `json:"ball"`
		Capture   json.RawMessage `json:"capture"`
	}
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	if in.View != "dtl" && in.View != "fo" {
		s.fail(w, bad("view は dtl（後ろから）か fo（正面から）"))
		return
	}
	if in.ClubClass == "" {
		in.ClubClass = "iron"
	}
	if !clubClasses[in.ClubClass] {
		s.fail(w, bad("club_class は iron / driver / wood / hybrid / wedge"))
		return
	}
	if in.Width < 16 || in.Height < 16 || in.Width > 10000 || in.Height > 10000 {
		s.fail(w, bad("width / height（コマの大きさ）が不正です"))
		return
	}
	if in.FPS < 0 || in.FPS > 2000 || math.IsNaN(in.FPS) {
		s.fail(w, bad("fps が不正です"))
		return
	}
	if in.FPSSource != "" && in.FPSSource != "container" && in.FPSSource != "playback" {
		s.fail(w, bad("fps_source は container か playback"))
		return
	}
	if err := validBall(in.Ball, in.Width, in.Height); err != nil {
		s.fail(w, err)
		return
	}
	if len(in.Capture) > 4096 {
		s.fail(w, bad("capture が大きすぎます（画像は送らない約束）"))
		return
	}
	sw := model.Swing{SessionID: se.ID, View: in.View, Club: strings.TrimSpace(in.Club), ClubClass: in.ClubClass, FPS: in.FPS, FPSSource: in.FPSSource,
		Width: in.Width, Height: in.Height, Duration: in.Duration, Ball: in.Ball, Capture: in.Capture}
	if err := s.Store.CreateSwing(r.Context(), &sw); err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, sw)
}

func (s *Server) listSwings(w http.ResponseWriter, r *http.Request) {
	se, _, err := s.sessionOf(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	ss, err := s.Store.ListSwings(r.Context(), se.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, ss)
}

func (s *Server) swingOf(r *http.Request) (*model.Swing, error) {
	id, err := pathID(r)
	if err != nil {
		return nil, err
	}
	return s.Store.GetSwing(r.Context(), id)
}

func (s *Server) getSwing(w http.ResponseWriter, r *http.Request) {
	sw, err := s.swingOf(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	fs, err := s.Store.ListSwingFrames(r.Context(), sw.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"swing": sw, "frames": fs})
}

func (s *Server) patchSwing(w http.ResponseWriter, r *http.Request) {
	sw, err := s.swingOf(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	var in struct {
		Club      *string `json:"club"`
		ClubClass *string `json:"club_class"`
		View      *string `json:"view"`
	}
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	// 向きを直す（構えの形が選んだ向きと食い違ったとき。§6.1）。直したら測り直す
	if in.View != nil && *in.View != "dtl" && *in.View != "fo" {
		s.fail(w, bad("view は dtl（後ろから）か fo（正面から）"))
		return
	}
	if in.ClubClass != nil && !clubClasses[*in.ClubClass] {
		s.fail(w, bad("club_class は iron / driver / wood / hybrid / wedge"))
		return
	}
	if in.Club != nil && in.ClubClass == nil {
		in.ClubClass = &sw.ClubClass
	}
	if in.ClubClass != nil && in.Club == nil {
		in.Club = &sw.Club
	}
	if err := s.Store.UpdateSwing(r.Context(), sw.ID, store.UpdateSwingInput{Club: in.Club, Class: in.ClubClass, View: in.View}); err != nil {
		s.fail(w, err)
		return
	}
	out, err := s.measureAndStore(r, sw.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) deleteSwing(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	if err := s.Store.DeleteSwing(r.Context(), id); err != nil {
		s.fail(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

type frameIn struct {
	Checkpoint string          `json:"checkpoint"`
	T          float64         `json:"t"`
	Frame      int             `json:"frame"`
	Source     string          `json:"source"`
	Method     string          `json:"method"` // 自動で決めたときの決め方（§6.4）
	Status     string          `json:"status"` // 自動で決めたときの状態。estimated は「目安」
	Landmarks  json.RawMessage `json:"landmarks"`
	Taps       json.RawMessage `json:"taps"`
	Thumb      string          `json:"thumb"` // 長辺 360px までの JPEG（base64）。全解像度のコマは受けない
}

// 自動で決めたコマの決め方と状態（video.py の _p。failed のコマは送られない＝コマが無い）
var (
	frameMethods  = map[string]bool{"": true, "rule": true, "ball_roi": true, "club_tap": true, "midpoint": true}
	frameStatuses = map[string]bool{"": true, "ok": true, "estimated": true}
)

// validLandmarks は 33点 [{x, y, visibility}]（0〜1 の割合）か空。
func validLandmarks(raw json.RawMessage) error {
	if len(raw) == 0 || string(raw) == "null" || string(raw) == "[]" {
		return nil
	}
	var pts []map[string]float64
	if err := json.Unmarshal(raw, &pts); err != nil {
		return bad("landmarks は [{x, y, visibility}] の並び")
	}
	if len(pts) != 33 {
		return bad("landmarks は33点（MediaPipe Pose の並び）")
	}
	for _, p := range pts {
		for k, v := range p {
			if k != "x" && k != "y" && k != "z" && k != "visibility" && k != "presence" {
				return bad("landmarks に知らない欄 %q", k)
			}
			if math.IsNaN(v) || math.IsInf(v, 0) || v < -5 || v > 5 {
				return bad("landmarks の値が不正です")
			}
		}
	}
	return nil
}

// validTaps は {grip:[x,y], head:[x,y], heel?, toe?}（コマの画素）か空。
func validTaps(raw json.RawMessage, wd, ht int) error {
	if len(raw) == 0 || string(raw) == "null" || string(raw) == "{}" {
		return nil
	}
	var taps map[string][]float64
	if err := json.Unmarshal(raw, &taps); err != nil {
		return bad("taps は {grip:[x,y], head:[x,y]}")
	}
	for k, p := range taps {
		if !tapKeys[k] {
			return bad("taps に知らない点 %q", k)
		}
		if len(p) != 2 || !inFrame(p[0], wd) || !inFrame(p[1], ht) {
			return bad("taps の点 %q がコマの外です", k)
		}
	}
	return nil
}

// decodeThumb はサムネイル（base64 の JPEG）を確かめる。長辺 360px を超えるものは断る（全解像度のコマを置かない）。
func decodeThumb(b64 string) ([]byte, error) {
	if b64 == "" {
		return nil, nil
	}
	if i := strings.Index(b64, ","); strings.HasPrefix(b64, "data:") && i > 0 {
		b64 = b64[i+1:]
	}
	b, err := base64.StdEncoding.DecodeString(b64)
	if err != nil {
		return nil, bad("thumb が base64 ではありません")
	}
	if len(b) > ThumbMaxBytes {
		return nil, bad("thumb が大きすぎます（長辺 %dpx の JPEG だけを送ります）", ThumbMaxEdge)
	}
	cfg, format, err := image.DecodeConfig(bytes.NewReader(b))
	if err != nil || format != "jpeg" {
		return nil, bad("thumb は JPEG だけです")
	}
	if cfg.Width > ThumbMaxEdge || cfg.Height > ThumbMaxEdge {
		return nil, bad("thumb は長辺 %dpx までです（%d×%d）。全解像度のコマはサーバーに置きません", ThumbMaxEdge, cfg.Width, cfg.Height)
	}
	return b, nil
}

// putSwingFrames は P のコマを入れる（渡さなかった P は消す。サムネイルを渡さなかった P は前のものを残す）→ 測って判定を残す。
func (s *Server) putSwingFrames(w http.ResponseWriter, r *http.Request) {
	sw, err := s.swingOf(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	var in struct {
		Frames  []frameIn       `json:"frames"`
		Missing []string        `json:"missing"`
		Ball    json.RawMessage `json:"ball"`
		Series  json.RawMessage `json:"series"`
	}
	dec := json.NewDecoder(io.LimitReader(r.Body, maxFramesBody))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&in); err != nil {
		s.fail(w, bad("JSON を読めません: %v", err))
		return
	}
	seen := map[string]bool{}
	var frames []model.SwingFrame
	for _, f := range in.Frames {
		if !model.IsCheckpoint(f.Checkpoint) {
			s.fail(w, bad("P の名前が違います: %q", f.Checkpoint))
			return
		}
		if seen[f.Checkpoint] {
			s.fail(w, bad("%s が2回あります", f.Checkpoint))
			return
		}
		seen[f.Checkpoint] = true
		if f.Source == "" {
			f.Source = "manual"
		}
		if f.Source != "manual" && f.Source != "auto" {
			s.fail(w, bad("source は manual か auto"))
			return
		}
		// 決め方と状態は自動のコマだけが持つ（手で選んだ・直したコマは空。手で選んだものを「目安」と出さない）
		if f.Source == "manual" {
			f.Method, f.Status = "", ""
		}
		if !frameMethods[f.Method] || !frameStatuses[f.Status] {
			s.fail(w, bad("method は rule / ball_roi / club_tap / midpoint、status は ok / estimated"))
			return
		}
		if err := validLandmarks(f.Landmarks); err != nil {
			s.fail(w, err)
			return
		}
		if err := validTaps(f.Taps, sw.Width, sw.Height); err != nil {
			s.fail(w, err)
			return
		}
		th, err := decodeThumb(f.Thumb)
		if err != nil {
			s.fail(w, err)
			return
		}
		frames = append(frames, model.SwingFrame{Checkpoint: f.Checkpoint, T: f.T, Frame: f.Frame, Source: f.Source, Method: f.Method, Status: f.Status, Landmarks: f.Landmarks, Taps: f.Taps, Thumb: th})
	}
	for _, p := range in.Missing {
		if !model.IsCheckpoint(p) {
			s.fail(w, bad("missing の P の名前が違います: %q", p))
			return
		}
	}
	if err := validBall(in.Ball, sw.Width, sw.Height); err != nil {
		s.fail(w, err)
		return
	}
	series, err := validSeries(in.Series)
	if err != nil {
		s.fail(w, err)
		return
	}
	if err := s.Store.PutSwingFrames(r.Context(), sw.ID, frames, true); err != nil {
		s.fail(w, err)
		return
	}
	// 時系列（自動の取り出しのとき）は渡されたときだけ入れ替える（手で選び直しても、同じスイングの動きは残す）
	if series != nil {
		if err := s.Store.PutSwingSeries(r.Context(), sw.ID, series); err != nil {
			s.fail(w, err)
			return
		}
	}
	up := store.UpdateSwingInput{Missing: &in.Missing}
	if in.Missing == nil {
		up.Missing = nil
	}
	if len(in.Ball) > 0 {
		up.Ball = in.Ball
	}
	if err := s.Store.UpdateSwing(r.Context(), sw.ID, up); err != nil {
		s.fail(w, err)
		return
	}
	out, err := s.measureAndStore(r, sw.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, out)
}

// putSwingTaps はタップだけを直す（P ごとの {grip, head, …} とボールの2点）→ 測り直す。
func (s *Server) putSwingTaps(w http.ResponseWriter, r *http.Request) {
	sw, err := s.swingOf(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	var in struct {
		Taps map[string]json.RawMessage `json:"taps"`
		Ball json.RawMessage            `json:"ball"`
	}
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	fs, err := s.Store.ListSwingFrames(r.Context(), sw.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	have := map[string]bool{}
	for i := range fs {
		have[fs[i].Checkpoint] = true
		if t, ok := in.Taps[fs[i].Checkpoint]; ok {
			if err := validTaps(t, sw.Width, sw.Height); err != nil {
				s.fail(w, err)
				return
			}
			fs[i].Taps = t
		}
	}
	for p := range in.Taps {
		if !have[p] {
			s.fail(w, bad("%s のコマがまだありません", p))
			return
		}
	}
	if err := validBall(in.Ball, sw.Width, sw.Height); err != nil {
		s.fail(w, err)
		return
	}
	if err := s.Store.PutSwingFrames(r.Context(), sw.ID, fs, true); err != nil {
		s.fail(w, err)
		return
	}
	if len(in.Ball) > 0 {
		if err := s.Store.UpdateSwing(r.Context(), sw.ID, store.UpdateSwingInput{Ball: in.Ball}); err != nil {
			s.fail(w, err)
			return
		}
	}
	out, err := s.measureAndStore(r, sw.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) getSwingThumb(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	p := r.PathValue("p")
	if !model.IsCheckpoint(p) {
		s.fail(w, bad("P の名前が違います"))
		return
	}
	b, err := s.Store.GetSwingThumb(r.Context(), id, p)
	if err != nil {
		s.fail(w, err)
		return
	}
	sum := sha256.Sum256(b)
	etag := `"t-` + hex.EncodeToString(sum[:8]) + `"`
	w.Header().Set("ETag", etag)
	w.Header().Set("Cache-Control", "private, no-cache")
	if r.Header.Get("If-None-Match") == etag {
		w.WriteHeader(http.StatusNotModified)
		return
	}
	w.Header().Set("Content-Type", "image/jpeg")
	_, _ = w.Write(b)
}

// measureAndStore はスイングを分析サービスで測り、判定を swing_checks に残す。返すのはスイング・コマ・測った結果。
func (s *Server) measureAndStore(r *http.Request, swingID int64) (map[string]any, error) {
	sw, err := s.Store.GetSwing(r.Context(), swingID)
	if err != nil {
		return nil, err
	}
	res, err := s.measureSwing(r, sw)
	if err != nil {
		return nil, err
	}
	fs, err := s.Store.ListSwingFrames(r.Context(), swingID)
	if err != nil {
		return nil, err
	}
	sw, err = s.Store.GetSwing(r.Context(), swingID)
	if err != nil {
		return nil, err
	}
	return map[string]any{"swing": sw, "frames": fs, "measure": res}, nil
}

type measureResult struct {
	CatalogVersion string            `json:"catalog_version"`
	Stamp          string            `json:"stamp"`
	Camera         json.RawMessage   `json:"camera"`
	Scale          json.RawMessage   `json:"scale"`
	ViewCheck      json.RawMessage   `json:"view_check"`
	Items          []json.RawMessage `json:"items"`
}

func (s *Server) measureSwing(r *http.Request, sw *model.Swing) (json.RawMessage, error) {
	se, err := s.Store.GetSession(r.Context(), sw.SessionID)
	if err != nil {
		return nil, err
	}
	pl, err := s.Store.GetPlayer(r.Context(), se.PlayerID)
	if err != nil {
		return nil, err
	}
	fs, err := s.Store.ListSwingFrames(r.Context(), sw.ID)
	if err != nil {
		return nil, err
	}
	series, err := s.Store.GetSwingSeries(r.Context(), sw.ID)
	if err != nil {
		return nil, err
	}
	in := swingInput(sw, pl.Handedness, fs, series)
	raw, err := s.Analyzer.CheckpointsMeasure(r.Context(), in)
	if err != nil {
		return nil, err
	}
	var m measureResult
	if err := json.Unmarshal(raw, &m); err != nil || m.CatalogVersion == "" {
		return nil, fmt.Errorf("分析サービスの判定を読めません: %v", err)
	}
	checks := make([]model.SwingCheck, 0, len(m.Items))
	for _, it := range m.Items {
		var h struct {
			ID     string `json:"id"`
			State  string `json:"state"`
			Fault  string `json:"fault"`
			Basis  string `json:"basis"`
			Reason string `json:"reason"`
		}
		if err := json.Unmarshal(it, &h); err != nil || h.ID == "" {
			return nil, fmt.Errorf("分析サービスの判定の形が違います")
		}
		st := h.State
		if st == "same_as" {
			// 束ねた項目は相手の判定を使う（表では「判断できない」の行にし、理由に印を残す）
			st, h.Reason = "unknown", "same_as"
		}
		switch st {
		case "in_range", "out_range", "unknown", "reference":
		default:
			return nil, fmt.Errorf("分析サービスの判定の状態が違います: %q", h.State)
		}
		if h.Basis == "" {
			h.Basis = "none"
		}
		checks = append(checks, model.SwingCheck{SwingID: sw.ID, ItemID: h.ID, CatalogVersion: m.CatalogVersion, State: st, FaultID: h.Fault, Basis: h.Basis, Reason: h.Reason, Evidence: it})
	}
	// fp: 測った条件の指紋（分析サービスの指紋・利き手・向き・番手・コマとタップ）。GET のときに違えば測り直す
	meta, _ := json.Marshal(map[string]any{"camera": m.Camera, "scale": m.Scale, "view_check": m.ViewCheck, "fp": swingFingerprint(m.Stamp, in)})
	if err := s.Store.PutSwingChecks(r.Context(), sw.ID, m.CatalogVersion, meta, checks); err != nil {
		return nil, err
	}
	return raw, nil
}

// swingInput は分析サービスに渡す1スイング（測る入力）。指紋もこの形から作る（渡した中身＝測った条件）。
func swingInput(sw *model.Swing, hand model.Handedness, fs []model.SwingFrame, series json.RawMessage) analysis.CheckpointSwing {
	in := analysis.CheckpointSwing{View: sw.View, Club: sw.Club, ClubClass: sw.ClubClass, Handedness: hand, FPS: sw.FPS,
		Width: sw.Width, Height: sw.Height, Ball: sw.Ball, Frames: map[string]json.RawMessage{}, Missing: sw.Missing, Series: series}
	for _, f := range fs {
		b, _ := json.Marshal(map[string]any{"t": f.T, "landmarks": f.Landmarks, "taps": f.Taps})
		in.Frames[f.Checkpoint] = b
	}
	return in
}

// swingFingerprint は測った条件の指紋。stamp は分析サービスの側（カタログの中身と判定の版）。
// 利き手を変えた・タップを直したのに保存のあとの測り直しが失敗した・カタログを直した、のどれでも指紋が変わり、
// 次に一覧を開いたときに測り直す（古い判定を出し続けない）。
func swingFingerprint(stamp string, in analysis.CheckpointSwing) string {
	if stamp == "" {
		return ""
	}
	b, _ := json.Marshal(in) // map のキーは並べ替えて書き出されるので、同じ中身なら同じ文字列
	h := sha256.New()
	h.Write([]byte(stamp + "|"))
	h.Write(b)
	return hex.EncodeToString(h.Sum(nil)[:12])
}

func storedFingerprint(sw *model.Swing) string {
	var m struct {
		FP string `json:"fp"`
	}
	_ = json.Unmarshal(sw.Measure, &m)
	return m.FP
}

// sessionChecks は記録のスイング全部の判定をまとめ、課題を1つ選んで返す（§4.4）。まだ測っていないスイングはここで測る。
func (s *Server) sessionChecks(w http.ResponseWriter, r *http.Request) {
	se, pl, err := s.sessionOf(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	ss, err := s.Store.ListSwings(r.Context(), se.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	type swingOut struct {
		model.Swing
		Frames []model.SwingFrame `json:"frames"`
	}
	var payload []json.RawMessage
	outs := []swingOut{}
	stamp := ""
	for i := range ss {
		sw := &ss[i]
		fs, err := s.Store.ListSwingFrames(r.Context(), sw.ID)
		if err != nil {
			s.fail(w, err)
			return
		}
		if len(fs) == 0 {
			outs = append(outs, swingOut{*sw, fs})
			continue
		}
		stale := sw.CPCatalogVersion == ""
		if !stale {
			if stamp == "" {
				if stamp, err = s.Analyzer.CheckpointsStamp(r.Context()); err != nil {
					s.fail(w, err)
					return
				}
			}
			series, err := s.Store.GetSwingSeries(r.Context(), sw.ID)
			if err != nil {
				s.fail(w, err)
				return
			}
			stale = storedFingerprint(sw) != swingFingerprint(stamp, swingInput(sw, pl.Handedness, fs, series))
		}
		if stale {
			if _, err := s.measureSwing(r, sw); err != nil {
				s.fail(w, err)
				return
			}
			if sw, err = s.Store.GetSwing(r.Context(), sw.ID); err != nil {
				s.fail(w, err)
				return
			}
		}
		cs, err := s.Store.ListSwingChecks(r.Context(), sw.ID, sw.CPCatalogVersion)
		if err != nil {
			s.fail(w, err)
			return
		}
		items := make([]json.RawMessage, 0, len(cs))
		for _, c := range cs {
			items = append(items, c.Evidence)
		}
		b, _ := json.Marshal(map[string]any{"swing_id": sw.ID, "view": sw.View, "items": items})
		payload = append(payload, b)
		for j := range fs {
			fs[j].Landmarks, fs[j].Taps = nil, nil // 一覧には要らない（コマの有る無しとサムネイルの有無だけ）
		}
		outs = append(outs, swingOut{*sw, fs})
	}
	out := map[string]any{"session_id": se.ID, "swings": outs, "handedness": pl.Handedness}
	if len(payload) == 0 {
		out["checks"] = nil
		writeJSON(w, http.StatusOK, out)
		return
	}
	var prefs map[string]any
	_ = json.Unmarshal(pl.Prefs, &prefs)
	pb, _ := json.Marshal(map[string]any{"priority": prefs["priority"]})
	agg, err := s.Analyzer.CheckpointsFocus(r.Context(), analysis.CheckpointFocusInput{Swings: payload, Handedness: pl.Handedness, Prefs: pb})
	if err != nil {
		s.fail(w, err)
		return
	}
	out["checks"] = agg
	writeJSON(w, http.StatusOK, out)
}
