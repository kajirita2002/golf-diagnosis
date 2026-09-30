package httpapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"io"
	"mime/multipart"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/kajirita2002/golf-diagnosis/api/internal/analysis"
	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
	"github.com/kajirita2002/golf-diagnosis/api/internal/store"
)

// 見た目の評価（Claude）と、TrackMan の球との対応づけ（docs/DESIGN_v2.md §6.7・§9.1・§13.1・§15 段2c）。
//
//   - POST /v1/swings/checks → 202。複数スイングのコマの JPEG（multipart・長辺 1024px・1枚 1.5MB・32枚まで）を受け、
//     **保存しない**で分析サービスへ渡す。答え（選択肢・見た目の短い文）だけを swing_vision に残し、測り直して
//     swing_checks に書く。結果は既存の GET /v1/jobs/{id} で取りに行く。
//   - 本人が料金を見て押したときだけ呼ぶ（REPORT_LLM とは別。キーが無ければ分析サービスが呼ばずに理由を返す）。
//   - キャッシュ（鍵は JPEG の sha256・向き・番手・利き手・fps・カタログの指紋・PROMPT_VERSION・モデル）・二度押し・
//     上限（LLM_DAILY_LIMIT_VIDEO）は、つなぎの文と同じ仕組み（llm_jobs / llm_usage）。
//   - 球との対応づけ: 動画のスイングを、同じクラブの種類の球へ順番に当てる案を出し（match-suggest）、
//     本人が確かめてから結ぶ（PUT /v1/swings/{id}/match）。確かめていない対応づけは印に使わない。

const (
	// VisionMaxImageBytes は画像1枚の上限（§6.7）
	VisionMaxImageBytes = 1536 << 10
	// VisionMaxImages は1回の画像の枚数の上限（既定の2スイング × P1〜P7＋中間＋拡大で最大28枚。§13.2）
	VisionMaxImages = 32
	// VisionMaxEdge は画像の長辺の上限（端末は長辺 1024px で送る）
	VisionMaxEdge = 1024
	// VisionMaxSwings は1回に聞くスイングの数の上限
	VisionMaxSwings = 4
	maxVisionBody   = VisionMaxImages*VisionMaxImageBytes + (1 << 20)
)

type visionMeta struct {
	SessionID int64 `json:"session_id"`
	Swings    []struct {
		SwingID int64 `json:"swing_id"`
	} `json:"swings"`
}

type visionStart struct {
	JobID   int64              `json:"job_id,omitempty"`
	Status  model.LLMJobStatus `json:"status,omitempty"`
	Cached  bool               `json:"cached,omitempty"`
	Limit   bool               `json:"limit,omitempty"`
	Reason  string             `json:"reason,omitempty"`
	Used    int                `json:"used"`
	LimitN  int                `json:"limit_n"`
	CostUSD float64            `json:"cost_usd_today"`
}

// readVisionForm は multipart を1部ずつ読む（一時ファイルを作らない＝ディスクにも画像を置かない）。
// 部の名前: meta（JSON）と f.<swing_id>.<P>（JPEG）。
func readVisionForm(w http.ResponseWriter, r *http.Request) (*visionMeta, map[int64]map[string][]byte, error) {
	r.Body = http.MaxBytesReader(w, r.Body, maxVisionBody)
	mr, err := r.MultipartReader()
	if err != nil {
		return nil, nil, bad("multipart/form-data で送ってください（meta と f.<スイング>.<P> の JPEG）")
	}
	var meta *visionMeta
	imgs := map[int64]map[string][]byte{}
	n := 0
	for {
		part, err := mr.NextPart()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, nil, bad("画像が大きすぎるか、送り方が違います（1枚 %dKB・%d枚まで）", VisionMaxImageBytes>>10, VisionMaxImages)
		}
		name := part.FormName()
		switch {
		case name == "meta":
			b, err := io.ReadAll(io.LimitReader(part, 64<<10))
			if err != nil {
				return nil, nil, bad("meta を読めません")
			}
			meta = &visionMeta{}
			if err := json.Unmarshal(b, meta); err != nil {
				return nil, nil, bad("meta の JSON を読めません: %v", err)
			}
		case strings.HasPrefix(name, "f."):
			b, err := readImagePart(part)
			if err != nil {
				return nil, nil, err
			}
			bits := strings.SplitN(name, ".", 3)
			if len(bits) != 3 {
				return nil, nil, bad("画像の名前は f.<スイング>.<P> です")
			}
			sid, err := strconv.ParseInt(bits[1], 10, 64)
			if err != nil || !model.IsCheckpoint(bits[2]) {
				return nil, nil, bad("画像の名前は f.<スイング>.<P> です: %q", name)
			}
			if imgs[sid] == nil {
				imgs[sid] = map[string][]byte{}
			}
			if _, dup := imgs[sid][bits[2]]; dup {
				return nil, nil, bad("%s が2回あります", name)
			}
			imgs[sid][bits[2]] = b
			n++
			if n > VisionMaxImages {
				return nil, nil, bad("画像は%d枚までです", VisionMaxImages)
			}
		default:
			return nil, nil, bad("知らない部 %q", name)
		}
	}
	if meta == nil {
		return nil, nil, bad("meta が要ります")
	}
	return meta, imgs, nil
}

func readImagePart(part *multipart.Part) ([]byte, error) {
	b, err := io.ReadAll(io.LimitReader(part, VisionMaxImageBytes+1))
	if err != nil {
		return nil, bad("画像を読めません")
	}
	if len(b) > VisionMaxImageBytes {
		return nil, bad("画像が大きすぎます（1枚 %dKB まで）", VisionMaxImageBytes>>10)
	}
	cfg, format, err := image.DecodeConfig(bytes.NewReader(b))
	if err != nil || format != "jpeg" {
		return nil, bad("画像は JPEG だけです")
	}
	if cfg.Width > VisionMaxEdge || cfg.Height > VisionMaxEdge {
		return nil, bad("画像は長辺 %dpx までです（%d×%d）", VisionMaxEdge, cfg.Width, cfg.Height)
	}
	return b, nil
}

// startSwingChecks は POST /v1/swings/checks。
func (s *Server) startSwingChecks(w http.ResponseWriter, r *http.Request) {
	meta, imgs, err := readVisionForm(w, r)
	if err != nil {
		s.fail(w, err)
		return
	}
	if len(meta.Swings) == 0 || len(meta.Swings) > VisionMaxSwings {
		s.fail(w, bad("スイングは1〜%d本です", VisionMaxSwings))
		return
	}
	se, err := s.Store.GetSession(r.Context(), meta.SessionID)
	if err != nil {
		s.fail(w, err)
		return
	}
	pl, err := s.Store.GetPlayer(r.Context(), se.PlayerID)
	if err != nil {
		s.fail(w, err)
		return
	}
	var in []analysis.VisionSwing
	var sws []*model.Swing
	seen := map[int64]bool{}
	for _, m := range meta.Swings {
		if seen[m.SwingID] {
			s.fail(w, bad("同じスイングが2回あります"))
			return
		}
		seen[m.SwingID] = true
		sw, err := s.Store.GetSwing(r.Context(), m.SwingID)
		if err != nil {
			s.fail(w, err)
			return
		}
		if sw.SessionID != se.ID {
			s.fail(w, bad("スイング %d はこの記録のものではありません", sw.ID))
			return
		}
		fs, err := s.Store.ListSwingFrames(r.Context(), sw.ID)
		if err != nil {
			s.fail(w, err)
			return
		}
		have := map[string]bool{}
		for _, f := range fs {
			have[f.Checkpoint] = true
		}
		frames := imgs[sw.ID]
		if len(frames) == 0 {
			s.fail(w, bad("スイング %d のコマの画像がありません", sw.ID))
			return
		}
		for p := range frames {
			if !have[p] {
				s.fail(w, bad("スイング %d の %s はまだ選んでいないコマです", sw.ID, p))
				return
			}
		}
		in = append(in, analysis.VisionSwing{SwingID: sw.ID, View: sw.View, Club: sw.Club, ClubClass: sw.ClubClass, Handedness: pl.Handedness, FPS: sw.FPS, Frames: frames})
		sws = append(sws, sw)
	}
	for sid := range imgs {
		if !seen[sid] {
			s.fail(w, bad("meta に無いスイング %d の画像があります", sid))
			return
		}
	}
	st, err := s.Analyzer.CheckpointsVisionStatus(r.Context())
	if err != nil {
		s.fail(w, err)
		return
	}
	day := llmDay()
	kind := string(model.LLMCPReview)
	out := visionStart{LimitN: s.LLM.DailyLimitVideo}
	if !st.Ready {
		out.Reason = st.Reason
		writeJSON(w, http.StatusOK, out)
		return
	}
	hash := visionHash(in, st)
	// キャッシュ: 同じ画像・同じ条件の答えがあれば呼ばない（上限にも数えない）。答えをこのスイングに当て直す
	j, err := s.Store.FindDoneLLMJob(r.Context(), model.LLMCPReview, hash, st.Model)
	if err == nil && j == nil {
		j, err = s.Store.FindDoneLLMJob(r.Context(), model.LLMCPReview, hash, "")
	}
	if err != nil {
		s.fail(w, err)
		return
	}
	if j != nil {
		if err := s.applyVision(r.Context(), j.ID, j.Result, sws); err != nil {
			s.fail(w, err)
			return
		}
		out.JobID, out.Status, out.Cached = j.ID, j.Status, true
		writeJSON(w, http.StatusOK, out)
		return
	}
	if j, err := s.Store.FindActiveLLMJob(r.Context(), model.LLMCPReview, hash); err != nil {
		s.fail(w, err)
		return
	} else if j != nil {
		out.JobID, out.Status = j.ID, j.Status
		writeJSON(w, http.StatusAccepted, out)
		return
	}
	got, err := s.Store.ReserveLLMCall(r.Context(), day, kind, s.LLM.DailyLimitVideo)
	if err != nil {
		s.fail(w, err)
		return
	}
	if !got {
		out.Limit = true
		out.Reason = fmt.Sprintf("今日の上限（%d回）に達したので、見た目の評価は明日また頼めます", s.LLM.DailyLimitVideo)
		writeJSON(w, http.StatusOK, out)
		return
	}
	sid := se.ID
	job := &model.LLMJob{Kind: model.LLMCPReview, SessionID: &sid, ScopeID: swingScope(sws), InputHash: hash, Model: st.Model, PromptVersion: st.PromptVersion}
	if err := s.Store.CreateLLMJob(r.Context(), job); err != nil {
		_ = s.Store.ReleaseLLMCall(r.Context(), day, kind)
		s.fail(w, err)
		return
	}
	s.runVision(job.ID, in, sws, day)
	if u, err := s.Store.GetLLMUsage(r.Context(), day, kind); err == nil {
		out.Used, out.CostUSD = u.Calls, u.CostUSD
	}
	out.JobID, out.Status = job.ID, job.Status
	writeJSON(w, http.StatusAccepted, out)
}

func swingScope(sws []*model.Swing) string {
	ids := make([]string, len(sws))
	for i, sw := range sws {
		ids[i] = strconv.FormatInt(sw.ID, 10)
	}
	return "swings:" + strings.Join(ids, ",")
}

// visionHash はキャッシュの鍵（Go の側）。スイングの順番・画像の中身・条件・版・モデル。
func visionHash(in []analysis.VisionSwing, st *analysis.VisionStatus) string {
	h := sha256.New()
	fmt.Fprintf(h, "%s|%s|%s\n", st.PromptVersion, st.CatalogStamp, st.Model)
	for _, sw := range in {
		fmt.Fprintf(h, "%s|%s|%s|%s|%.3f\n", sw.View, sw.Club, sw.ClubClass, sw.Handedness, sw.FPS)
		ps := make([]string, 0, len(sw.Frames))
		for p := range sw.Frames {
			ps = append(ps, p)
		}
		sort.Strings(ps)
		for _, p := range ps {
			sum := sha256.Sum256(sw.Frames[p])
			fmt.Fprintf(h, "%s:%s\n", p, hex.EncodeToString(sum[:]))
		}
	}
	return hex.EncodeToString(h.Sum(nil))
}

// visionResult は分析サービスの応答のうち、保存と数えるのに要るところ。
type visionResult struct {
	Called    int     `json:"called"`
	Model     *string `json:"model"`
	Cacheable bool    `json:"cacheable"`
	Reason    *string `json:"reason"`
	Message   string  `json:"message"`
	Swings    []struct {
		Swing   int             `json:"swing"`
		Answers json.RawMessage `json:"answers"`
	} `json:"swings"`
	Validation json.RawMessage `json:"validation"`
	Usage      struct {
		InputTokens  int64    `json:"input_tokens"`
		OutputTokens int64    `json:"output_tokens"`
		CostUSD      *float64 `json:"cost_usd"`
	} `json:"usage"`
}

// applyVision は答えをスイングに当て（並びの順）、測り直す。
func (s *Server) applyVision(ctx context.Context, jobID int64, raw json.RawMessage, sws []*model.Swing) error {
	var res visionResult
	if err := json.Unmarshal(raw, &res); err != nil {
		return fmt.Errorf("見た目の評価の結果を読めません: %w", err)
	}
	for i, sw := range sws {
		if i >= len(res.Swings) {
			break
		}
		if err := s.Store.PutSwingVision(ctx, sw.ID, jobID, "", res.Swings[i].Answers); err != nil {
			return err
		}
		cur, err := s.Store.GetSwing(ctx, sw.ID)
		if err != nil {
			return err
		}
		if _, err := s.measureSwingCtx(ctx, cur); err != nil {
			return err
		}
	}
	return nil
}

// runVision は要求と切り離して聞き、答えを保存する。画像はこの関数が終われば手放す（どこにも書かない）。
func (s *Server) runVision(jobID int64, in []analysis.VisionSwing, sws []*model.Swing, day string) {
	s.llm.wg.Add(1)
	go func() {
		defer s.llm.wg.Done()
		sem := s.llmSem()
		sem <- struct{}{}
		defer func() { <-sem }()
		timeout := s.LLM.Timeout
		if timeout <= 0 {
			timeout = 5 * time.Minute
		}
		ctx, cancel := context.WithTimeout(context.Background(), timeout)
		defer cancel()
		kind := string(model.LLMCPReview)
		_ = s.Store.SetLLMJobRunning(ctx, jobID)
		raw, err := s.Analyzer.CheckpointsVision(ctx, in)
		in = nil // 画像を手放す
		sctx, scancel := context.WithTimeout(context.Background(), 60*time.Second)
		defer scancel()
		fail := func(msg string) {
			_ = s.Store.FinishLLMJob(sctx, jobID, store.LLMJobFinish{Status: model.LLMFailed, Error: msg})
		}
		if err != nil {
			_ = s.Store.ReleaseLLMCall(sctx, day, kind)
			s.Log.Error("vision failed", "job", jobID, "err", err)
			fail("分析サービスで見た目の評価ができませんでした: " + err.Error())
			return
		}
		var res visionResult
		if err := json.Unmarshal(raw, &res); err != nil {
			_ = s.Store.ReleaseLLMCall(sctx, day, kind)
			fail("分析サービスの応答を読めません")
			return
		}
		if res.Called == 0 {
			_ = s.Store.ReleaseLLMCall(sctx, day, kind)
		} else {
			cost := 0.0
			if res.Usage.CostUSD != nil {
				cost = *res.Usage.CostUSD
			}
			_ = s.Store.AddLLMUsage(sctx, day, kind, res.Usage.InputTokens, res.Usage.OutputTokens, cost)
		}
		usage, _ := json.Marshal(res.Usage)
		f := store.LLMJobFinish{Status: model.LLMDone, Result: raw, Validation: res.Validation, Usage: usage, CostUSD: res.Usage.CostUSD}
		if res.Model != nil {
			f.Model = *res.Model
		}
		if !res.Cacheable {
			f.Status = model.LLMFailed
			f.Error = res.Message
			if f.Error == "" && res.Reason != nil {
				f.Error = *res.Reason
			}
			if f.Error == "" {
				f.Error = "見た目の評価ができませんでした"
			}
		} else if err := s.applyVision(sctx, jobID, raw, sws); err != nil {
			s.Log.Error("vision apply failed", "job", jobID, "err", err)
			f.Status, f.Error = model.LLMFailed, "答えを保存できませんでした: "+err.Error()
		}
		if err := s.Store.FinishLLMJob(sctx, jobID, f); err != nil {
			s.Log.Error("vision save failed", "job", jobID, "err", err)
		}
	}()
}

// visionInfo は一覧（C）に出す見た目の評価の状態: 呼べるか・料金の見積もり・いちばん新しいジョブの要約。
func (s *Server) visionInfo(ctx context.Context, sessionID int64, nUsable int) map[string]any {
	out := map[string]any{"ready": false, "reason": "", "n_target": min(nUsable, 2), "limit": s.LLM.DailyLimitVideo}
	st, err := s.Analyzer.CheckpointsVisionStatus(ctx)
	if err != nil {
		out["reason"] = "分析サービスに届かないので、見た目の評価はいまは頼めません"
	} else {
		out["ready"], out["reason"] = st.Ready, st.Reason
		n := min(nUsable, max(st.DefaultSwings, 1))
		out["n_target"] = n
		if len(st.CostPerSwingUSD) == 2 {
			out["cost_usd_hi"] = st.CostPerSwingUSD[1] * float64(n)
			out["cost_usd_lo"] = st.CostPerSwingUSD[0] * float64(n)
		}
		out["max_images"] = st.MaxImages
	}
	if u, err := s.Store.GetLLMUsage(ctx, llmDay(), string(model.LLMCPReview)); err == nil {
		out["used_today"] = u.Calls
	}
	j, err := s.Store.LatestLLMJobForSession(ctx, model.LLMCPReview, sessionID)
	if err != nil || j == nil {
		return out
	}
	last := map[string]any{"job_id": j.ID, "status": j.Status, "error": j.Error, "model": j.Model, "cost_usd": j.CostUSD, "scope": j.ScopeID}
	var res struct {
		Dropped []json.RawMessage `json:"dropped"`
		Extra   []string          `json:"extra"`
		Swings  []struct {
			SwingID  int64    `json:"swing_id"`
			Reselect []string `json:"reselect"`
		} `json:"swings"`
		NAsked int `json:"n_asked"`
		Usage  struct {
			InputTokens  int64 `json:"input_tokens"`
			OutputTokens int64 `json:"output_tokens"`
		} `json:"usage"`
	}
	if json.Unmarshal(j.Result, &res) == nil {
		last["n_dropped"] = len(res.Dropped)
		last["n_asked"] = res.NAsked
		last["extra"] = res.Extra
		last["tokens"] = res.Usage.InputTokens + res.Usage.OutputTokens
		var rs []map[string]any
		for _, x := range res.Swings {
			if len(x.Reselect) > 0 {
				rs = append(rs, map[string]any{"swing_id": x.SwingID, "ps": x.Reselect})
			}
		}
		last["reselect"] = rs
	}
	out["last"] = last
	return out
}

// ---- 球との対応づけ（§9.1） ----

// matchSuggest は POST /v1/sessions/{id}/swings/match-suggest。動画のスイング（作った順）を、
// 同じクラブの種類の球（除外していない・番号の順）へ順番に当てる案。本数が合わなければ mismatch を立てる。
func (s *Server) matchSuggest(w http.ResponseWriter, r *http.Request) {
	se, _, err := s.sessionOf(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	out, err := s.matchPlan(r.Context(), se.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) matchPlan(ctx context.Context, sessionID int64) (map[string]any, error) {
	sws, err := s.Store.ListSwings(ctx, sessionID)
	if err != nil {
		return nil, err
	}
	shots, err := s.Store.ListShots(ctx, sessionID)
	if err != nil {
		return nil, err
	}
	var use []model.Swing
	classes := map[string]bool{}
	matched := 0
	for _, sw := range sws {
		if len(sw.Measure) == 0 {
			continue
		}
		use = append(use, sw)
		classes[sw.ClubClass] = true
		if sw.SeqFrom != nil {
			matched++
		}
	}
	var seqs []int
	for _, sh := range shots {
		if sh.Excluded || !classes[sh.ClubCategory] {
			continue
		}
		seqs = append(seqs, sh.Seq)
	}
	pairs := []map[string]any{}
	if len(use) == len(seqs) {
		for i, sw := range use {
			pairs = append(pairs, map[string]any{"swing_id": sw.ID, "seq": seqs[i]})
		}
	}
	return map[string]any{"n_swings": len(use), "n_shots": len(seqs), "mismatch": len(use) != len(seqs) || len(use) == 0, "pairs": pairs, "matched": matched}, nil
}

// putSwingMatch は PUT /v1/swings/{id}/match {seq: N | null}。本人が確かめて結ぶ（外す）。
func (s *Server) putSwingMatch(w http.ResponseWriter, r *http.Request) {
	sw, err := s.swingOf(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	var in struct {
		Seq *int `json:"seq"`
	}
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	if in.Seq != nil {
		shots, err := s.Store.ListShots(r.Context(), sw.SessionID)
		if err != nil {
			s.fail(w, err)
			return
		}
		ok := false
		for _, sh := range shots {
			ok = ok || sh.Seq == *in.Seq
		}
		if !ok {
			s.fail(w, bad("この記録に %d 球目はありません", *in.Seq))
			return
		}
	}
	if err := s.Store.SetSwingMatch(r.Context(), sw.ID, in.Seq); err != nil {
		s.fail(w, err)
		return
	}
	cur, err := s.Store.GetSwing(r.Context(), sw.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, cur)
}

// sessionSymptoms は課題の印に使う症状の id（その日の球から。その球の動画が要るものは結んだときだけ）。
// 球が無い・分析サービスに届かないときは空（印を付けないだけで、一覧は出す）。
func (s *Server) sessionSymptoms(ctx context.Context, se *model.Session, pl *model.Player, sws []model.Swing) (json.RawMessage, []string) {
	shots, err := s.Store.ListShots(ctx, se.ID)
	if err != nil || len(shots) == 0 {
		return nil, nil
	}
	ps, err := s.sessionPayloads(ctx, se.ID, shots)
	if err != nil {
		return nil, nil
	}
	classes := map[string]bool{}
	var matched []int
	for _, sw := range sws {
		classes[sw.ClubClass] = true
		if sw.SeqFrom != nil {
			matched = append(matched, *sw.SeqFrom)
		}
	}
	var cl []string
	for c := range classes {
		cl = append(cl, c)
	}
	sort.Strings(cl)
	raw, err := s.Analyzer.CheckpointsSymptoms(ctx, analysis.SymptomsInput{Shots: ps, Handedness: pl.Handedness, Clubs: cl, MatchedSeqs: matched})
	if err != nil {
		s.Log.Warn("symptoms failed", "session", se.ID, "err", err)
		return nil, nil
	}
	var v struct {
		ForFocus []string `json:"for_focus"`
	}
	_ = json.Unmarshal(raw, &v)
	return raw, v.ForFocus
}
