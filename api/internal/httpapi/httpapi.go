// Package httpapi は HTTP の入口。
package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/kajirita2002/golf-diagnosis/api/internal/analysis"
	"github.com/kajirita2002/golf-diagnosis/api/internal/ingest"
	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
	"github.com/kajirita2002/golf-diagnosis/api/internal/physics"
	"github.com/kajirita2002/golf-diagnosis/api/internal/store"
	"github.com/kajirita2002/golf-diagnosis/api/internal/units"
)

// MaxUploadBytes は CSV の上限。1セッション数百球でも数百KB。
const MaxUploadBytes = 10 << 20

// Analyzer は分析サービス。テストでは偽物に差し替える。
type Analyzer interface {
	Session(ctx context.Context, shots []analysis.ShotPayload) (json.RawMessage, error)
	Report(ctx context.Context, in analysis.ReportInput) (json.RawMessage, error)
	Experiment(ctx context.Context, e *model.Experiment, blocks []analysis.BlockPayload) (json.RawMessage, error)
	Compare(ctx context.Context, a, b []analysis.ShotPayload) (json.RawMessage, error)
	Screenshot(ctx context.Context, mediaType string, image []byte) (json.RawMessage, error)
	VerifyTSV(ctx context.Context, tsv string) (json.RawMessage, error)
	PlanEvaluate(ctx context.Context, in analysis.PlanEvalInput) (json.RawMessage, error)
	PlanBuild(ctx context.Context, in analysis.PlanBuildInput) (json.RawMessage, error)
	Drills(ctx context.Context, hand model.Handedness) (json.RawMessage, error)
	Narrative(ctx context.Context, in analysis.NarrativeInput) (json.RawMessage, error)
	PlanCandidates(ctx context.Context, in analysis.PlanCandidatesInput) (json.RawMessage, error)
	Versions(ctx context.Context) (string, error)
	// 動画のチェックポイント（checkpoint.go）
	Checkpoints(ctx context.Context, hand model.Handedness) (json.RawMessage, error)
	CheckpointsMeasure(ctx context.Context, sw analysis.CheckpointSwing) (json.RawMessage, error)
	CheckpointsFocus(ctx context.Context, in analysis.CheckpointFocusInput) (json.RawMessage, error)
	CheckpointsStamp(ctx context.Context) (string, error)
	VideoCheckpoints(ctx context.Context, body json.RawMessage) (json.RawMessage, error)
}

// Server は API のハンドラをまとめる。
type Server struct {
	Store    *store.Store
	Analyzer Analyzer
	Adapters map[string]ingest.Adapter
	Static   http.Handler // 画面（nil なら配らない）
	Password string       // 空でなければ全部に Basic 認証を掛ける（auth.go）
	Status   Status       // /healthz に出す動作の状態
	Log      *slog.Logger
	LLM      LLMConfig // Claude のつなぎの文（narrative.go）。既定は off
	llm      llmRuntime
	ver      versionCache // 分析サービスの版（評価の指紋に入れる。plan.go）
}

// Status は動いているものの状態。/healthz（パスワード不要）で外から見られる。
// 「設定したのに効いていない」を URL を開くだけで確かめるため。値そのもの（URL・キー）は出さない。
type Status struct {
	DB           string // sqlite / postgres
	DBPersistent bool   // false なら DB_PATH が未設定で、再起動で消える
	AnthropicKey bool   // Claude の API キーがあるか（中身は出さない）
	Commit       string // 動いているコミット（Render の RENDER_GIT_COMMIT）
	AnalysisURL  string // 分析サービス。/healthz で生きているかを見る
	ReportLLM    bool   // REPORT_LLM=on か（解説のつなぎの文に Claude を使うか）
}

// HealthJSON は /healthz の中身。起動中（データベースにつながる前）も同じ形で返す。
// state: starting / ready。dbError: つながらない理由（接続文字列は伏せてある）。
func HealthJSON(st Status, state, dbError string) []byte {
	out := map[string]any{
		"ok":            true,
		"state":         state,
		"physics":       physics.EngineVersion,
		"commit":        st.Commit,
		"db":            st.DB,
		"db_persistent": st.DBPersistent,
		"anthropic_key": st.AnthropicKey,
		"report_llm":    map[bool]string{true: "on", false: "off"}[st.ReportLLM],
		"analysis":      analysisAlive(st.AnalysisURL),
	}
	if dbError != "" {
		out["db_error"] = dbError
	}
	b, _ := json.Marshal(out)
	return b
}

// ErrorJSON は {"error": msg}。
func ErrorJSON(msg string) []byte {
	b, _ := json.Marshal(apiError{msg})
	return b
}

func analysisAlive(base string) string {
	if base == "" {
		return "unknown"
	}
	c := http.Client{Timeout: 1500 * time.Millisecond}
	resp, err := c.Get(base + "/healthz")
	if err != nil {
		return "down"
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "down"
	}
	return "up"
}

// New はサーバーを作る。
func New(st *store.Store, an Analyzer) *Server {
	return &Server{
		Status:   Status{DB: "sqlite", DBPersistent: true},
		Store:    st,
		Analyzer: an,
		Adapters: map[string]ingest.Adapter{"trackman": ingest.TrackMan{}},
		Log:      slog.Default(),
		LLM:      DefaultLLMConfig(),
	}
}

// Handler はルーティング済みのハンドラを返す。
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		_, _ = w.Write(HealthJSON(s.Status, "ready", ""))
	})
	mux.HandleFunc("GET /v1/players", s.listPlayers)
	mux.HandleFunc("POST /v1/players", s.createPlayer)
	mux.HandleFunc("GET /v1/players/{id}", s.getPlayer)
	// 新しい導線（docs/DESIGN_v2.md §15 段1。home.go）
	mux.HandleFunc("POST /v1/me", s.me)
	mux.HandleFunc("PATCH /v1/players/{id}", s.patchPlayer)
	mux.HandleFunc("GET /v1/players/{id}/home", s.home)
	mux.HandleFunc("GET /v1/players/{id}/sessions", s.listSessions)
	mux.HandleFunc("POST /v1/sessions", s.createSession)
	mux.HandleFunc("GET /v1/sessions/{id}", s.getSession)
	mux.HandleFunc("POST /v1/sessions/{id}/import", s.importCSV)
	mux.HandleFunc("GET /v1/sessions/{id}/shots", s.listShots)
	mux.HandleFunc("GET /v1/sessions/{id}/analysis", s.sessionAnalysis)
	mux.HandleFunc("GET /v1/sessions/{id}/report", s.sessionReport)
	mux.HandleFunc("POST /v1/sessions/{id}/report/narrative", s.startNarrative)
	mux.HandleFunc("GET /v1/jobs/{id}", s.getJob)
	mux.HandleFunc("GET /v1/sessions/{id}/compare", s.compareSessions)
	mux.HandleFunc("POST /v1/sessions/{id}/experiments", s.createExperiment)
	mux.HandleFunc("GET /v1/sessions/{id}/experiments", s.listExperiments)
	mux.HandleFunc("PATCH /v1/shots/{id}", s.patchShot)
	mux.HandleFunc("GET /v1/trackman/report-link", s.reportLink)
	mux.HandleFunc("POST /v1/screenshot", s.readScreenshot)
	mux.HandleFunc("POST /v1/screenshot/verify", s.verifyScreenshot)
	mux.HandleFunc("GET /v1/experiments/{id}", s.getExperiment)
	mux.HandleFunc("POST /v1/experiments/{id}/blocks", s.addBlock)
	mux.HandleFunc("GET /v1/experiments/{id}/evaluation", s.evaluateExperiment)
	mux.HandleFunc("PUT /v1/experiments/{id}/blocks", s.replaceExperimentBlocks)
	// プラン（docs/DESIGN_coaching.md §8・§10.2。plan.go）
	mux.HandleFunc("GET /v1/drills", s.listDrills)
	mux.HandleFunc("POST /v1/players/{id}/plans", s.createPlan)
	mux.HandleFunc("GET /v1/players/{id}/plans", s.listPlans)
	mux.HandleFunc("GET /v1/players/{id}/today", s.today)
	mux.HandleFunc("GET /v1/players/{id}/record", s.playerRecord)
	mux.HandleFunc("GET /v1/players/{id}/plan-candidates", s.planCandidates)
	mux.HandleFunc("POST /v1/plans/{id}/continue", s.continuePlan)
	mux.HandleFunc("GET /v1/plans/{id}", s.getPlan)
	mux.HandleFunc("PATCH /v1/plans/{id}", s.patchPlan)
	mux.HandleFunc("POST /v1/plans/{id}/runs", s.createPlanRun)
	mux.HandleFunc("GET /v1/plans/{id}/progress", s.planProgress)
	mux.HandleFunc("GET /v1/sessions/{id}/plan-run", s.sessionPlanRun)
	mux.HandleFunc("GET /v1/plan-runs/{id}", s.getPlanRun)
	mux.HandleFunc("PUT /v1/plan-runs/{id}/blocks", s.replaceRunBlocks)
	mux.HandleFunc("GET /v1/plan-runs/{id}/evaluation", s.evaluatePlanRun)
	// 動画のチェックポイント（docs/DESIGN_v2.md §13.1・段2a。checkpoint.go）
	mux.HandleFunc("GET /v1/checkpoints", s.getCheckpoints)
	mux.HandleFunc("POST /v1/video/checkpoints", s.videoCheckpoints)
	mux.HandleFunc("GET /v1/sessions/{id}/swings", s.listSwings)
	mux.HandleFunc("POST /v1/sessions/{id}/swings", s.createSwing)
	mux.HandleFunc("GET /v1/swings/{id}", s.getSwing)
	mux.HandleFunc("PATCH /v1/swings/{id}", s.patchSwing)
	mux.HandleFunc("DELETE /v1/swings/{id}", s.deleteSwing)
	mux.HandleFunc("PUT /v1/swings/{id}/frames", s.putSwingFrames)
	mux.HandleFunc("PUT /v1/swings/{id}/taps", s.putSwingTaps)
	mux.HandleFunc("GET /v1/swings/{id}/thumbs/{p}", s.getSwingThumb)
	mux.HandleFunc("GET /v1/sessions/{id}/checks", s.sessionChecks)
	if s.Static != nil {
		mux.Handle("GET /", vendorCache(s.Static))
	}
	return securityHeaders(s.requireAuth(mux))
}

// ---- 応答 ----

type apiError struct {
	Error string `json:"error"`
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func writeRaw(w http.ResponseWriter, code int, raw json.RawMessage) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	_, _ = w.Write(raw)
}

func (s *Server) fail(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, store.ErrNotFound):
		writeJSON(w, http.StatusNotFound, apiError{"見つかりません"})
	case errors.Is(err, store.ErrConflict):
		writeJSON(w, http.StatusConflict, conflictBody(err))
	case errors.Is(err, analysis.ErrUnavailable):
		writeJSON(w, http.StatusServiceUnavailable, apiError{err.Error()})
	case errors.As(err, new(*analysis.ServiceError)):
		var se *analysis.ServiceError
		errors.As(err, &se)
		code := se.Status
		if code < 400 || code > 599 {
			code = http.StatusBadGateway
		}
		writeJSON(w, code, apiError{se.Message})
	default:
		var be badRequest
		if errors.As(err, &be) {
			writeJSON(w, http.StatusBadRequest, apiError{be.Error()})
			return
		}
		s.Log.Error("request failed", "err", err)
		writeJSON(w, http.StatusInternalServerError, apiError{"サーバーの内部で失敗しました"})
	}
}

type badRequest struct{ msg string }

func (b badRequest) Error() string { return b.msg }

func bad(format string, a ...any) error { return badRequest{fmt.Sprintf(format, a...)} }

func pathID(r *http.Request) (int64, error) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil || id <= 0 {
		return 0, bad("id が不正です")
	}
	return id, nil
}

func decode(r *http.Request, v any) error {
	dec := json.NewDecoder(io.LimitReader(r.Body, 1<<20))
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		return bad("JSON を読めません: %v", err)
	}
	return nil
}

// ---- players / sessions ----

func (s *Server) createPlayer(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Name       string           `json:"name"`
		Handedness model.Handedness `json:"handedness"`
	}
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	if strings.TrimSpace(in.Name) == "" {
		s.fail(w, bad("name が要ります"))
		return
	}
	if in.Handedness == "" {
		in.Handedness = model.RightHanded
	}
	if in.Handedness != model.RightHanded && in.Handedness != model.LeftHanded {
		s.fail(w, bad("handedness は R か L"))
		return
	}
	p := model.Player{Name: in.Name, Handedness: in.Handedness}
	if err := s.Store.CreatePlayer(r.Context(), &p); err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, p)
}

func (s *Server) listPlayers(w http.ResponseWriter, r *http.Request) {
	ps, err := s.Store.ListPlayers(r.Context())
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, ps)
}

func (s *Server) getPlayer(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	p, err := s.Store.GetPlayer(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, p)
}

func (s *Server) listSessions(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	if _, err := s.Store.GetPlayer(r.Context(), id); err != nil {
		s.fail(w, err)
		return
	}
	ss, err := s.Store.ListSessions(r.Context(), id)
	if err == nil {
		err = s.Store.CountShots(r.Context(), id, ss)
	}
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, ss)
}

func (s *Server) createSession(w http.ResponseWriter, r *http.Request) {
	var in struct {
		PlayerID int64  `json:"player_id"`
		Date     string `json:"date"`
		Location string `json:"location"`
	}
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	if len(in.Date) != 10 {
		s.fail(w, bad("date は YYYY-MM-DD"))
		return
	}
	se := model.Session{PlayerID: in.PlayerID, Date: in.Date, Location: in.Location}
	if err := s.Store.CreateSession(r.Context(), &se); err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, se)
}

func (s *Server) getSession(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	se, err := s.Store.GetSession(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, se)
}

// ---- 取り込み ----

// importCSV は CSV を取り込む。multipart の file か、本文そのままの CSV を受ける。
//
//	?source=trackman（既定） ?units=imperial|metric（CSV に単位が無い列に使う）
//	?impact_offset_toe_negative=1（計測器の打点がトゥ = マイナスのとき）
//	?club=6 Iron（クラブの列が無いときに全部の球へ付ける。画面の表の貼り付け用）
func (s *Server) importCSV(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	se, err := s.Store.GetSession(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	pl, err := s.Store.GetPlayer(r.Context(), se.PlayerID)
	if err != nil {
		s.fail(w, err)
		return
	}
	q := r.URL.Query()
	source := q.Get("source")
	if source == "" {
		source = "trackman"
	}
	ad, ok := s.Adapters[source]
	if !ok {
		s.fail(w, bad("source %q には対応していません", source))
		return
	}
	sys := units.System(q.Get("units"))
	if sys == "" {
		sys = units.Imperial
	}
	if sys != units.Imperial && sys != units.Metric {
		s.fail(w, bad("units は imperial か metric"))
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, MaxUploadBytes)
	var body io.Reader
	if strings.HasPrefix(r.Header.Get("Content-Type"), "multipart/") {
		f, _, err := r.FormFile("file")
		if err != nil {
			s.fail(w, bad("file が要ります: %v", err))
			return
		}
		defer f.Close()
		body = f
	} else {
		b, err := io.ReadAll(r.Body)
		if err != nil {
			s.fail(w, bad("本文を読めません: %v", err))
			return
		}
		body = bytes.NewReader(b)
	}

	res, err := ad.Parse(body, ingest.Options{
		Units:                   sys,
		Handedness:              pl.Handedness,
		Club:                    strings.TrimSpace(q.Get("club")),
		ImpactOffsetToeNegative: q.Get("impact_offset_toe_negative") == "1",
	})
	if err != nil {
		s.fail(w, bad("%v", err))
		return
	}
	shots := make([]model.Shot, 0, len(res.Shots))
	for _, in := range res.Shots {
		m := in.Metrics
		derived := ingest.Fill(&m)
		shots = append(shots, model.Shot{
			Club:           in.Club,
			ClubCategory:   physics.ClubCategory(in.Club),
			HitAt:          in.HitAt,
			Metrics:        m,
			Estimated:      in.Estimated,
			Derived:        derived,
			Raw:            ingest.RawJSON(in.Raw),
			AdapterVersion: res.Version,
		})
	}
	saved, err := s.Store.AppendShots(r.Context(), id, shots)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{
		"imported": len(saved),
		"skipped":  res.Skipped,
		"columns":  res.Columns,
		"ignored":  nonNil(res.Ignored),
		"warnings": nonNil(res.Warnings),
		"seq_from": saved[0].Seq,
		"seq_to":   saved[len(saved)-1].Seq,
	})
}

func nonNil(xs []string) []string {
	if xs == nil {
		return []string{}
	}
	return xs
}

// ---- 球 ----

type shotView struct {
	model.Shot
	Decomposition physics.Decomposition `json:"decomposition"`
}

func view(sh model.Shot) shotView {
	return shotView{Shot: sh, Decomposition: physics.Decompose(sh.Metrics, sh.ClubCategory)}
}

func (s *Server) listShots(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	if _, err := s.Store.GetSession(r.Context(), id); err != nil {
		s.fail(w, err)
		return
	}
	shots, err := s.Store.ListShots(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	out := make([]shotView, 0, len(shots))
	for _, sh := range shots {
		out = append(out, view(sh))
	}
	writeJSON(w, http.StatusOK, out)
}

// patchShot は人が直せる項目を直す。
//
//	club / excluded / good_override（null で自動に戻す）
//	impact_offset_mm / impact_height_mm（インパクトテープで見た打点。トゥ = +、上 = +）
func (s *Server) patchShot(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	var in map[string]json.RawMessage
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	sh, err := s.Store.GetShot(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	for k, raw := range in {
		switch k {
		case "club":
			var v string
			if err := json.Unmarshal(raw, &v); err != nil {
				s.fail(w, bad("club は文字列"))
				return
			}
			sh.Club = v
			sh.ClubCategory = physics.ClubCategory(v)
		case "excluded":
			var v bool
			if err := json.Unmarshal(raw, &v); err != nil {
				s.fail(w, bad("excluded は真偽値"))
				return
			}
			sh.Excluded = v
		case "good_override":
			if string(raw) == "null" {
				sh.GoodOverride = nil
				continue
			}
			var v bool
			if err := json.Unmarshal(raw, &v); err != nil {
				s.fail(w, bad("good_override は真偽値か null"))
				return
			}
			sh.GoodOverride = &v
		case "impact_offset_mm", "impact_height_mm":
			var v float64
			if err := json.Unmarshal(raw, &v); err != nil {
				s.fail(w, bad("%s は数値", k))
				return
			}
			if v < -60 || v > 60 {
				s.fail(w, bad("%s が大きすぎます（±60mm まで）", k))
				return
			}
			m := v / 1000
			field := "impact_offset"
			if k == "impact_offset_mm" {
				sh.Metrics.ImpactOffset = &m
			} else {
				sh.Metrics.ImpactHeight = &m
				field = "impact_height"
			}
			sh.Manual = addOnce(sh.Manual, field)
		default:
			s.fail(w, bad("%s は直せません", k))
			return
		}
	}
	if err := s.Store.UpdateShot(r.Context(), sh); err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, view(*sh))
}

func addOnce(xs []string, v string) []string {
	for _, x := range xs {
		if x == v {
			return xs
		}
	}
	return append(xs, v)
}

// ---- 分析 ----

func payload(sh model.Shot) analysis.ShotPayload {
	return analysis.ShotPayload{
		ID:            sh.ID,
		Seq:           sh.Seq,
		Club:          sh.Club,
		ClubCategory:  sh.ClubCategory,
		Metrics:       sh.Metrics,
		Estimated:     nonNil(sh.Estimated),
		Manual:        nonNil(sh.Manual),
		Excluded:      sh.Excluded,
		GoodOverride:  sh.GoodOverride,
		Decomposition: physics.Decompose(sh.Metrics, sh.ClubCategory),
	}
}

func (s *Server) sessionAnalysis(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	if _, err := s.Store.GetSession(r.Context(), id); err != nil {
		s.fail(w, err)
		return
	}
	ps, err := s.payloads(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	out, err := s.Analyzer.Session(r.Context(), ps)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeRaw(w, http.StatusOK, out)
}

func (s *Server) payloads(ctx context.Context, sessionID int64) ([]analysis.ShotPayload, error) {
	shots, err := s.Store.ListShots(ctx, sessionID)
	if err != nil {
		return nil, err
	}
	return s.sessionPayloads(ctx, sessionID, shots)
}

// sessionPayloads は球を分析サービスへ渡す形にし、プランの練習のブロックの種類（block_kind）を付ける。
func (s *Server) sessionPayloads(ctx context.Context, sessionID int64, shots []model.Shot) ([]analysis.ShotPayload, error) {
	kinds, err := s.blockKinds(ctx, sessionID, shots)
	if err != nil {
		return nil, err
	}
	ps := make([]analysis.ShotPayload, 0, len(shots))
	for _, sh := range shots {
		p := payload(sh)
		p.BlockKind = kinds[sh.ID]
		ps = append(ps, p)
	}
	return ps, nil
}

// compareSessions は「今日（{id}）は、?with= のセッションと何が違ったか」を返す。
// 同じ選手のセッションどうしだけ比べる（別の人と比べても原因の話にならない）。
func (s *Server) compareSessions(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	other, err := strconv.ParseInt(r.URL.Query().Get("with"), 10, 64)
	if err != nil || other <= 0 {
		s.fail(w, bad("with に比べるセッションの id が要ります"))
		return
	}
	if other == id {
		s.fail(w, bad("同じセッションどうしは比べられません"))
		return
	}
	cur, err := s.Store.GetSession(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	prev, err := s.Store.GetSession(r.Context(), other)
	if err != nil {
		s.fail(w, err)
		return
	}
	if cur.PlayerID != prev.PlayerID {
		s.fail(w, bad("別の選手のセッションとは比べられません"))
		return
	}
	a, err := s.payloads(r.Context(), prev.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	b, err := s.payloads(r.Context(), cur.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	out, err := s.Analyzer.Compare(r.Context(), a, b)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeRaw(w, http.StatusOK, out)
}

// ---- 実験 ----

// targetMetrics は実験の目標にできる指標。分析サービスと同じ名前を使う。
var targetMetrics = map[string]bool{
	"face_to_path": true, "face_angle": true, "club_path": true, "attack_angle": true,
	"dynamic_loft": true, "spin_loft": true, "low_point": true, "impact_offset": true,
	"impact_height": true, "launch_direction": true, "spin_axis": true, "side": true,
	"carry": true, "club_speed": true, "ball_speed": true, "smash_factor": true,
	"spin_rate": true, "launch_angle": true,
}

func (s *Server) createExperiment(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	var in struct {
		Hypothesis   string     `json:"hypothesis"`
		Intervention string     `json:"intervention"`
		TargetMetric string     `json:"target_metric"`
		Goal         model.Goal `json:"goal"`
		Club         string     `json:"club"`
	}
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	if strings.TrimSpace(in.Hypothesis) == "" {
		s.fail(w, bad("hypothesis が要ります（何を確かめる実験か）"))
		return
	}
	if !targetMetrics[in.TargetMetric] {
		s.fail(w, bad("target_metric %q は目標にできません", in.TargetMetric))
		return
	}
	if !in.Goal.Valid() {
		s.fail(w, bad("goal は reduce_abs / reduce_sd / increase / decrease"))
		return
	}
	e := model.Experiment{SessionID: id, Hypothesis: in.Hypothesis, Intervention: in.Intervention,
		TargetMetric: in.TargetMetric, Goal: in.Goal, Club: in.Club}
	if err := s.Store.CreateExperiment(r.Context(), &e); err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, e)
}

func (s *Server) listExperiments(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	if _, err := s.Store.GetSession(r.Context(), id); err != nil {
		s.fail(w, err)
		return
	}
	es, err := s.Store.ListExperiments(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, es)
}

func (s *Server) getExperiment(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	e, err := s.Store.GetExperiment(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, e)
}

func (s *Server) addBlock(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	var in struct {
		Kind    model.BlockKind `json:"kind"`
		SeqFrom int             `json:"seq_from"`
		SeqTo   int             `json:"seq_to"`
	}
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	e, err := s.Store.GetExperiment(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	if !in.Kind.Valid() {
		s.fail(w, bad("kind は warmup / baseline / drill / intervention / retention"))
		return
	}
	if in.SeqFrom < 1 || in.SeqTo < in.SeqFrom {
		s.fail(w, bad("seq の範囲が不正です"))
		return
	}
	for _, b := range e.Blocks {
		if in.SeqFrom <= b.SeqTo && b.SeqFrom <= in.SeqTo {
			s.fail(w, bad("ほかのブロック（%s %d〜%d）と範囲が重なっています", b.Kind, b.SeqFrom, b.SeqTo))
			return
		}
	}
	b := model.Block{ExperimentID: id, Kind: in.Kind, SeqFrom: in.SeqFrom, SeqTo: in.SeqTo}
	if err := s.Store.AddBlock(r.Context(), &b); err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, b)
}

// blockPayloads はブロックごとに球を拾う（除外した球は入れない。クラブが決まっていれば完全一致の球だけ）。
// 球にはそのブロックの種類を block_kind として付ける。
func blockPayloads(club string, blocks []model.Block, shots []model.Shot) []analysis.BlockPayload {
	out := make([]analysis.BlockPayload, 0, len(blocks))
	for _, b := range blocks {
		bp := analysis.BlockPayload{Kind: b.Kind, SeqFrom: b.SeqFrom, SeqTo: b.SeqTo, Shots: []analysis.ShotPayload{}}
		for _, sh := range shots {
			if sh.Seq < b.SeqFrom || sh.Seq > b.SeqTo || sh.Excluded {
				continue
			}
			if club != "" && !strings.EqualFold(sh.Club, club) {
				continue
			}
			p := payload(sh)
			p.BlockKind = b.Kind
			bp.Shots = append(bp.Shots, p)
		}
		out = append(out, bp)
	}
	return out
}

func (s *Server) evaluateExperiment(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	e, err := s.Store.GetExperiment(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	shots, err := s.Store.ListShots(r.Context(), e.SessionID)
	if err != nil {
		s.fail(w, err)
		return
	}
	out, err := s.Analyzer.Experiment(r.Context(), e, blockPayloads(e.Club, e.Blocks, shots))
	if err != nil {
		s.fail(w, err)
		return
	}
	writeRaw(w, http.StatusOK, out)
}

// ---- スクリーンショット ----

// MaxScreenshotBytes は画像1枚の上限（Claude の API の上限に合わせる）。
const MaxScreenshotBytes = 5 << 20

var screenshotTypes = map[string]bool{"image/png": true, "image/jpeg": true, "image/webp": true, "image/gif": true}

// reportLink は TrackMan のレポートを10項目・Club data で開くリンクを返す。?url= に URL か ID。
func (s *Server) reportLink(w http.ResponseWriter, r *http.Request) {
	id, err := ingest.ReportID(r.URL.Query().Get("url"))
	if err != nil {
		s.fail(w, bad("%v", err))
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"id": id, "url": ingest.ReportLink(id)})
}

// readScreenshot は画像（multipart の image）の表を読んで返す。取り込みはしない。
func (s *Server) readScreenshot(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, MaxScreenshotBytes+1<<20)
	f, _, err := r.FormFile("image")
	if err != nil {
		s.fail(w, bad("image が要ります（5MB まで）: %v", err))
		return
	}
	defer f.Close()
	img, err := io.ReadAll(io.LimitReader(f, MaxScreenshotBytes+1))
	if err != nil {
		s.fail(w, bad("画像を読めません: %v", err))
		return
	}
	if len(img) > MaxScreenshotBytes {
		s.fail(w, bad("画像が大きすぎます（5MB まで）。表の部分だけを切り取ってください"))
		return
	}
	// 申告された形式は信用せず、中身から決める
	mt := http.DetectContentType(img)
	if !screenshotTypes[mt] {
		s.fail(w, bad("画像の形式（%s）には対応していません。PNG / JPEG / WebP / GIF にしてください", mt))
		return
	}
	out, err := s.Analyzer.Screenshot(r.Context(), mt, img)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeRaw(w, http.StatusOK, out)
}

func (s *Server) verifyScreenshot(w http.ResponseWriter, r *http.Request) {
	var in struct {
		TSV string `json:"tsv"`
	}
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	out, err := s.Analyzer.VerifyTSV(r.Context(), in.TSV)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeRaw(w, http.StatusOK, out)
}
