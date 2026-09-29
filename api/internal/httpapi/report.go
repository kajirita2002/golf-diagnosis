package httpapi

import (
	"bytes"
	"compress/gzip"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net/http"
	"strings"

	"github.com/kajirita2002/golf-diagnosis/api/internal/analysis"
	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
	"github.com/kajirita2002/golf-diagnosis/api/internal/physics"
)

// 解説レポート（docs/DESIGN_coaching.md §6・§10.2）。
//
// 分析サービスの POST /v1/report へ中継し、応答の中の「帯の依頼」（band_request）ごとに
// physics.Band で帯の形を計算して足す（帯の形は D-plane の式が要るので Go だけが作る）。
// 解説は「深い診断」なので、分析サービスが落ちていれば出さない。そのときも1球ごとの分解
// （速い診断）は返す ―― /analysis と違って 503 にしないのは、解説の画面が分解だけで
// 「1球ずつ」を出し続けられるようにするため。

// reportResponse は /v1/sessions/{id}/report の応答。
type reportResponse struct {
	SessionID      int64            `json:"session_id"`
	Handedness     model.Handedness `json:"handedness"`
	PhysicsVersion string           `json:"physics_version"`
	// Available は解説があるか。false のとき Report は null で、理由が Reason。
	Available bool            `json:"available"`
	Reason    string          `json:"reason,omitempty"`
	Report    json.RawMessage `json:"report"`
	Shots     []shotView      `json:"shots"`
}

func (s *Server) sessionReport(w http.ResponseWriter, r *http.Request) {
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
	shots, err := s.Store.ListShots(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	exps, err := s.Store.ListExperiments(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	out := reportResponse{
		SessionID:      id,
		Handedness:     pl.Handedness,
		PhysicsVersion: physics.EngineVersion,
		Report:         json.RawMessage("null"),
		Shots:          make([]shotView, 0, len(shots)),
	}
	ps := make([]analysis.ShotPayload, 0, len(shots))
	for _, sh := range shots {
		out.Shots = append(out.Shots, view(sh))
		ps = append(ps, payload(sh))
	}

	raw, err := s.Analyzer.Report(r.Context(), analysis.ReportInput{Shots: ps, Handedness: pl.Handedness, Experiments: exps})
	if err == nil {
		raw, err = addBandShapes(raw)
	}
	var se2 *analysis.ServiceError
	switch {
	case err == nil:
		out.Available, out.Report = true, raw
	case errors.Is(err, analysis.ErrUnavailable):
		out.Reason = "分析サービスに接続できないので、解説は出せません。1球ごとの分解は出せます"
	case errors.As(err, &se2):
		out.Reason = se2.Message
	default:
		s.Log.Error("report failed", "session", id, "err", err)
		out.Reason = "分析サービスで解説を作れませんでした。1球ごとの分解は出せます"
	}
	writeJSONMaybeGzip(w, r, http.StatusOK, out)
}

// writeJSONMaybeGzip は、受け手が gzip を受け付けるなら gzip で返す（§10.2。解説は図の中身を含めて
// 数百KBになるので、スマホの回線でそのまま送らない）。
func writeJSONMaybeGzip(w http.ResponseWriter, r *http.Request, code int, v any) {
	if !acceptsGzip(r) {
		writeJSON(w, code, v)
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Content-Encoding", "gzip")
	w.Header().Add("Vary", "Accept-Encoding")
	w.WriteHeader(code)
	gz := gzip.NewWriter(w)
	_ = json.NewEncoder(gz).Encode(v)
	_ = gz.Close()
}

func acceptsGzip(r *http.Request) bool {
	for _, part := range strings.Split(r.Header.Get("Accept-Encoding"), ",") {
		enc, q, _ := strings.Cut(strings.TrimSpace(part), ";")
		if strings.EqualFold(strings.TrimSpace(enc), "gzip") && strings.ReplaceAll(strings.TrimSpace(q), " ", "") != "q=0" {
			return true
		}
	}
	return false
}

// bandRequest は分析サービスが図の中身に置く「帯の依頼」。k と帯の幅は分析サービスが決める
// （GOOD_TARGETS を Go に書き写さない）。
//
//	carry / spin_loft … 描く範囲の代表値（中央値）
//	lim               … 帯の半幅（m）。無ければ max(side_min_m, side_pct × carry)
//	k / category      … 係数と種類（打ち出しに効くフェースの重みが種類で変わる）
//	paths             … 帯の多角形を描くパスの値。無ければ path_min〜path_max を path_step（既定 0.25°）刻み
//	window_path       … 窓（帯をパスの中央値で切った断面）を出すパス
type bandRequest struct {
	Carry      *float64  `json:"carry"`
	SpinLoft   *float64  `json:"spin_loft"`
	Lim        *float64  `json:"lim"`
	SidePct    *float64  `json:"side_pct"`
	SideMinM   *float64  `json:"side_min_m"`
	K          *float64  `json:"k"`
	Category   string    `json:"category"`
	Paths      []float64 `json:"paths"`
	PathMin    *float64  `json:"path_min"`
	PathMax    *float64  `json:"path_max"`
	PathStep   *float64  `json:"path_step"`
	WindowPath *float64  `json:"window_path"`
}

// bandShape は band_request の隣に足す帯の形。ok=false のときは reason だけ。
type bandShape struct {
	EngineVersion string              `json:"engine_version"`
	OK            bool                `json:"ok"`
	Reason        string              `json:"reason,omitempty"`
	Lim           float64             `json:"lim,omitempty"`
	Ranges        []physics.FaceRange `json:"ranges,omitempty"`
	Window        *physics.FaceRange  `json:"window,omitempty"`
}

// addBandShapes は応答の JSON をたどり、band_request を持つオブジェクトすべてに band_shape を足す。
// どこに置くか（範囲・図 F3 など）は分析サービスが決めるので、場所を決め打ちしない。
func addBandShapes(raw json.RawMessage) (json.RawMessage, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber() // 分析サービスの数字をそのまま返す（float64 を通すと桁が変わる）
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, fmt.Errorf("分析サービスの応答を読めません: %w", err)
	}
	walkBand(v)
	return json.Marshal(v)
}

func walkBand(v any) {
	switch x := v.(type) {
	case map[string]any:
		for k, c := range x {
			if k != "band_request" {
				walkBand(c)
			}
		}
		if req, ok := x["band_request"]; ok && req != nil {
			x["band_shape"] = computeBand(req)
		}
	case []any:
		for _, c := range x {
			walkBand(c)
		}
	}
}

func computeBand(req any) bandShape {
	out := bandShape{EngineVersion: physics.EngineVersion}
	b, _ := json.Marshal(req)
	var in bandRequest
	if err := json.Unmarshal(b, &in); err != nil {
		out.Reason = "帯の依頼の形が不正です"
		return out
	}
	if in.Carry == nil || in.SpinLoft == nil || in.K == nil {
		out.Reason = "帯の依頼に carry / spin_loft / k がありません"
		return out
	}
	lim := 0.0
	switch {
	case in.Lim != nil:
		lim = *in.Lim
	case in.SidePct != nil && in.SideMinM != nil:
		lim = math.Max(*in.SideMinM, *in.SidePct**in.Carry)
	default:
		out.Reason = "帯の依頼に帯の幅（lim か side_pct と side_min_m）がありません"
		return out
	}
	paths, err := bandPaths(in)
	if err != nil {
		out.Reason = err.Error()
		return out
	}
	if in.WindowPath != nil {
		paths = append(paths, *in.WindowPath)
	}
	rs, err := physics.Band(*in.Carry, *in.SpinLoft, lim, *in.K, in.Category, paths)
	if err != nil {
		out.Reason = err.Error()
		return out
	}
	if in.WindowPath != nil {
		wnd := rs[len(rs)-1]
		out.Window = &wnd
		rs = rs[:len(rs)-1]
	}
	out.OK, out.Lim, out.Ranges = true, lim, rs
	if out.Ranges == nil {
		out.Ranges = []physics.FaceRange{}
	}
	return out
}

// bandPaths は多角形を描くパスの値。明示が無ければ path_min〜path_max を刻む。
func bandPaths(in bandRequest) ([]float64, error) {
	if len(in.Paths) > 0 {
		return append([]float64(nil), in.Paths...), nil
	}
	if in.PathMin == nil || in.PathMax == nil {
		if in.WindowPath != nil {
			return []float64{}, nil // 窓だけ
		}
		return nil, errors.New("帯の依頼にパスの値（paths か path_min と path_max）がありません")
	}
	step := 0.25
	if in.PathStep != nil {
		step = *in.PathStep
	}
	lo, hi := *in.PathMin, *in.PathMax
	if !(step > 0) || !(hi >= lo) || math.IsInf(hi-lo, 0) || (hi-lo)/step > 399 {
		return nil, errors.New("帯の依頼のパスの範囲が不正です")
	}
	var ps []float64
	for i := 0; ; i++ {
		p := lo + float64(i)*step
		if p > hi+1e-9 {
			break
		}
		ps = append(ps, math.Round(p*1e6)/1e6)
	}
	return ps, nil
}
