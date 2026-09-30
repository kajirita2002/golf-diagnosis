package httpapi

// 理想との比較（docs/DESIGN_v2.md §7・§15 段3）。
//   - GET /v1/swings/{id}/ideal?item=…  課題の項目1つの範囲の形と、範囲の外の部位だけを範囲に入れた線
//     （分析サービスの checkpoints/ideal.py が計算する。Go は保存しない・中継するだけ）。
//   - GET /v1/players/{id}/best?item=…&p=…  自分のベスト（同じ項目が範囲の中だった過去のスイング。新しい順に3本まで）。
//   - 端末の参照動画（③）はサーバーに来ない。受ける口を作らない（checkpoint_test.go の Test参照動画を受ける保存の口は無い）。

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
)

const bestLimit = 3

func validItemID(id string) bool {
	if id == "" || len(id) > 80 {
		return false
	}
	for _, r := range id {
		if !(r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || r == '.' || r == '_') {
			return false
		}
	}
	return true
}

func (s *Server) swingIdeal(w http.ResponseWriter, r *http.Request) {
	sw, err := s.swingOf(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	item := r.URL.Query().Get("item")
	if !validItemID(item) {
		s.fail(w, bad("item が要ります"))
		return
	}
	ctx := r.Context()
	se, err := s.Store.GetSession(ctx, sw.SessionID)
	if err != nil {
		s.fail(w, err)
		return
	}
	pl, err := s.Store.GetPlayer(ctx, se.PlayerID)
	if err != nil {
		s.fail(w, err)
		return
	}
	fs, err := s.Store.ListSwingFrames(ctx, sw.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	series, err := s.Store.GetSwingSeries(ctx, sw.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	vis, err := s.swingVisionOf(ctx, sw.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	raw, err := s.Analyzer.CheckpointsIdeal(ctx, swingInput(sw, pl.Handedness, fs, series, vis), item)
	if err != nil {
		s.fail(w, err)
		return
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		s.fail(w, fmt.Errorf("分析サービスの答えを読めません: %v", err))
		return
	}
	// 描く下地（そのコマのサムネイル）と、座標の大きさ。サムネイルは長辺 360px なので、画面が縮めて重ねる
	p, _ := out["p"].(string)
	out["swing_id"], out["width"], out["height"], out["view"], out["handedness"] = sw.ID, sw.Width, sw.Height, sw.View, pl.Handedness
	out["thumb"] = ""
	for _, f := range fs {
		if f.Checkpoint == p && f.HasThumb {
			out["thumb"] = fmt.Sprintf("/v1/swings/%d/thumbs/%s", sw.ID, p)
		}
	}
	ps := []string{}
	for _, f := range fs {
		if f.HasThumb {
			ps = append(ps, f.Checkpoint)
		}
	}
	out["thumbs"] = ps
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) playerBest(w http.ResponseWriter, r *http.Request) {
	pid, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	if _, err := s.Store.GetPlayer(r.Context(), pid); err != nil {
		s.fail(w, err)
		return
	}
	q := r.URL.Query()
	item, p := q.Get("item"), q.Get("p")
	if !validItemID(item) {
		s.fail(w, bad("item が要ります"))
		return
	}
	if p != "" && !model.IsCheckpoint(p) {
		s.fail(w, bad("P の名前が違います"))
		return
	}
	bs, err := s.Store.BestSwings(r.Context(), pid, item, p, bestLimit)
	if err != nil {
		s.fail(w, err)
		return
	}
	out := make([]map[string]any, 0, len(bs))
	for _, b := range bs {
		m := map[string]any{"swing_id": b.SwingID, "session_id": b.SessionID, "date": b.Date, "view": b.View, "club": b.Club}
		if p != "" {
			m["thumb"] = fmt.Sprintf("/v1/swings/%d/thumbs/%s", b.SwingID, p)
		}
		out = append(out, m)
	}
	writeJSON(w, http.StatusOK, map[string]any{"item": item, "p": strings.TrimSpace(p), "best": out})
}
