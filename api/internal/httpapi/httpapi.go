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
	Experiment(ctx context.Context, e *model.Experiment, blocks []analysis.BlockPayload) (json.RawMessage, error)
}

// Server は API のハンドラをまとめる。
type Server struct {
	Store    *store.Store
	Analyzer Analyzer
	Adapters map[string]ingest.Adapter
	Static   http.Handler // 画面（nil なら配らない）
	Log      *slog.Logger
}

// New はサーバーを作る。
func New(st *store.Store, an Analyzer) *Server {
	return &Server{
		Store:    st,
		Analyzer: an,
		Adapters: map[string]ingest.Adapter{"trackman": ingest.TrackMan{}},
		Log:      slog.Default(),
	}
}

// Handler はルーティング済みのハンドラを返す。
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "physics": physics.EngineVersion})
	})
	mux.HandleFunc("POST /v1/players", s.createPlayer)
	mux.HandleFunc("GET /v1/players/{id}", s.getPlayer)
	mux.HandleFunc("GET /v1/players/{id}/sessions", s.listSessions)
	mux.HandleFunc("POST /v1/sessions", s.createSession)
	mux.HandleFunc("GET /v1/sessions/{id}", s.getSession)
	mux.HandleFunc("POST /v1/sessions/{id}/import", s.importCSV)
	mux.HandleFunc("GET /v1/sessions/{id}/shots", s.listShots)
	mux.HandleFunc("GET /v1/sessions/{id}/analysis", s.sessionAnalysis)
	mux.HandleFunc("POST /v1/sessions/{id}/experiments", s.createExperiment)
	mux.HandleFunc("GET /v1/sessions/{id}/experiments", s.listExperiments)
	mux.HandleFunc("PATCH /v1/shots/{id}", s.patchShot)
	mux.HandleFunc("GET /v1/experiments/{id}", s.getExperiment)
	mux.HandleFunc("POST /v1/experiments/{id}/blocks", s.addBlock)
	mux.HandleFunc("GET /v1/experiments/{id}/evaluation", s.evaluateExperiment)
	if s.Static != nil {
		mux.Handle("GET /", s.Static)
	}
	return mux
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
	case errors.Is(err, analysis.ErrUnavailable):
		writeJSON(w, http.StatusServiceUnavailable, apiError{err.Error()})
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
	shots, err := s.Store.ListShots(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	ps := make([]analysis.ShotPayload, 0, len(shots))
	for _, sh := range shots {
		ps = append(ps, payload(sh))
	}
	out, err := s.Analyzer.Session(r.Context(), ps)
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
		s.fail(w, bad("kind は baseline / intervention / retention"))
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
	blocks := make([]analysis.BlockPayload, 0, len(e.Blocks))
	for _, b := range e.Blocks {
		bp := analysis.BlockPayload{Kind: b.Kind, SeqFrom: b.SeqFrom, SeqTo: b.SeqTo, Shots: []analysis.ShotPayload{}}
		for _, sh := range shots {
			if sh.Seq < b.SeqFrom || sh.Seq > b.SeqTo || sh.Excluded {
				continue
			}
			if e.Club != "" && !strings.EqualFold(sh.Club, e.Club) {
				continue
			}
			bp.Shots = append(bp.Shots, payload(sh))
		}
		blocks = append(blocks, bp)
	}
	out, err := s.Analyzer.Experiment(r.Context(), e, blocks)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeRaw(w, http.StatusOK, out)
}
