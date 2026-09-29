package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/kajirita2002/golf-diagnosis/api/internal/analysis"
	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
	"github.com/kajirita2002/golf-diagnosis/api/internal/store"
)

// 新しい導線（docs/DESIGN_v2.md §4・§10 H・§15 段1）のための口。
//
//   - POST /v1/me … 画面の「使う人」。画面から選手を外したので、最初の起動で1人を作る（§2.2）。
//   - PATCH /v1/players/{id} … 設定（利き手・距離の単位など。§10 S）。
//   - GET /v1/players/{id}/home … ホームに要るものを1往復で（§13.1）。状態の見出しと主ボタンは
//     画面の homeState が決める（ここは材料だけ返す）。文は全部分析サービスの定型文（gist）から取り、
//     ここで事実の文を作らない。

// homeReportTimeout は、ホームのために解説を作るのを待つ長さ。届かなければ課題なしで返す
// （分析サービスが寝ていてもホームは出す。プランと記録はこの API だけで分かる）。
const homeReportTimeout = 8 * time.Second

func (s *Server) me(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Handedness model.Handedness `json:"handedness"`
	}
	if r.ContentLength != 0 {
		if err := decode(r, &in); err != nil {
			s.fail(w, err)
			return
		}
	}
	if in.Handedness == "" {
		in.Handedness = model.RightHanded
	}
	if in.Handedness != model.RightHanded && in.Handedness != model.LeftHanded {
		s.fail(w, bad("handedness は R か L"))
		return
	}
	p, created, err := s.Store.EnsurePlayer(r.Context(), "わたし", in.Handedness)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"player": p, "created": created})
}

// prefsAllowed は設定に置いてよい値。知らない鍵・値は断る（画面の打ち間違いで壊れた値を残さない）。
var prefsAllowed = map[string][]string{
	"dist_unit": {"yd", "m"},
	// 課題の選び方で「飛距離を優先」を選ぶと、飛ぶ力の項目を候補の先頭に寄せる（docs/DESIGN_v2.md §4.4 の 6）
	"priority": {"accuracy", "distance"},
}

func (s *Server) patchPlayer(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	var in struct {
		Handedness *model.Handedness `json:"handedness"`
		Prefs      map[string]string `json:"prefs"`
	}
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	if in.Handedness != nil && *in.Handedness != model.RightHanded && *in.Handedness != model.LeftHanded {
		s.fail(w, bad("handedness は R か L"))
		return
	}
	var prefs json.RawMessage
	if in.Prefs != nil {
		cur, err := s.Store.GetPlayer(r.Context(), id)
		if err != nil {
			s.fail(w, err)
			return
		}
		merged := map[string]string{}
		_ = json.Unmarshal(cur.Prefs, &merged)
		for k, v := range in.Prefs {
			vals, ok := prefsAllowed[k]
			if !ok {
				s.fail(w, bad("設定 %q は知りません", k))
				return
			}
			okv := false
			for _, a := range vals {
				okv = okv || a == v
			}
			if !okv {
				s.fail(w, bad("設定 %s の値 %q は使えません", k, v))
				return
			}
			merged[k] = v
		}
		prefs, _ = json.Marshal(merged)
	}
	p, err := s.Store.UpdatePlayer(r.Context(), id, in.Handedness, prefs)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, p)
}

// homeFocus は課題（いま一番に直すもの）。段1では球の解説の要点（gist）から取る。
type homeFocus struct {
	SessionID   int64           `json:"session_id"`
	SessionDate string          `json:"session_date"`
	ScopeID     string          `json:"scope_id"`
	Label       string          `json:"label"`
	CandidateID string          `json:"candidate_id,omitempty"`
	Title       string          `json:"title"`
	Cue         string          `json:"cue,omitempty"`
	NextTitle   string          `json:"next_title,omitempty"`
	Gap         json.RawMessage `json:"gap,omitempty"`    // 要点の「理想との差」の塊（言葉の行と比べる行）
	Figure      json.RawMessage `json:"figure,omitempty"` // 比べる図 C1 の中身
	BandShape   json.RawMessage `json:"band_shape,omitempty"`
	Startable   bool            `json:"startable"` // この課題でプランを組めるか（測るだけの候補は組めない）
}

type homePlan struct {
	ID         int64           `json:"id"`
	Title      string          `json:"title"`
	Cue        string          `json:"cue"`
	Club       string          `json:"club"`
	NextIndex  int             `json:"next_index"`
	Total      int             `json:"total"` // 1回の球数（型の合計）
	CreatedAt  time.Time       `json:"created_at"`
	Last       json.RawMessage `json:"last"`      // 最後に判定した回の {date, plain, progress}（無ければ null）
	LastDate   string          `json:"last_date"` // 最後に判定した回の日付
	TriggerSID int64           `json:"trigger_session_id,omitempty"`
	Issue      string          `json:"issue"`             // 直す候補の id（要点の候補と突き合わせる）
	Advance    string          `json:"advance,omitempty"` // 次に進む条件（定型文。数字入りなので画面は「なぜ？」の層で出す）
}

func (s *Server) home(w http.ResponseWriter, r *http.Request) {
	pid, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	pl, err := s.Store.GetPlayer(r.Context(), pid)
	if err != nil {
		s.fail(w, err)
		return
	}
	ss, err := s.Store.ListSessions(r.Context(), pid)
	if err == nil {
		err = s.Store.CountShots(r.Context(), pid, ss)
	}
	if err != nil {
		s.fail(w, err)
		return
	}
	out := map[string]any{"player": pl, "sessions": len(ss), "latest": nil, "plan": nil, "focus": nil, "focus_state": "no_shots"}
	var latest *model.Session
	withShots := 0
	for i := range ss {
		if ss[i].NShots > 0 {
			withShots++
			if latest == nil {
				latest = &ss[i]
			}
		}
	}
	out["sessions_with_shots"] = withShots
	// 動画のスイングがある最新の記録（球が無く動画だけの日でも、ホームを「ようこそ」のままにしない）。
	// 数えるのは測れたスイングだけ（送り直しの途中で残った、コマの無いスイングは数えない）
	for i := range ss {
		sws, err := s.Store.ListSwings(r.Context(), ss[i].ID)
		if err != nil {
			s.fail(w, err)
			return
		}
		n, views := 0, map[string]int{}
		for _, sw := range sws {
			if len(sw.Measure) > 0 {
				n++
				views[sw.View]++
			}
		}
		if n > 0 {
			most := 0
			for _, c := range views {
				most = max(most, c)
			}
			out["latest_video"] = map[string]any{"session_id": ss[i].ID, "date": ss[i].Date, "n_swings": n, "n_same_view": most}
			break
		}
	}
	if latest != nil {
		out["latest"] = map[string]any{"session_id": latest.ID, "date": latest.Date, "location": latest.Location, "n_shots": latest.NShots}
	}

	// プラン（動いているもの）。課題はプランのきっかけの診断から取る（今日の1点がプランと食い違わないように）
	focusSID, focusScope, planIssue := int64(0), "", ""
	p, err := s.Store.ActivePlan(r.Context(), pid)
	switch {
	case err == nil:
		hp, trig, scope, err := s.homePlan(r.Context(), p)
		if err != nil {
			s.fail(w, err)
			return
		}
		out["plan"] = hp
		focusSID, focusScope, planIssue = trig, scope, p.Issue
	case !errors.Is(err, store.ErrNotFound):
		s.fail(w, err)
		return
	}
	if focusSID == 0 && latest != nil {
		focusSID = latest.ID
	}
	if focusSID != 0 {
		ctx, cancel := context.WithTimeout(r.Context(), homeReportTimeout)
		f, state := s.homeFocusOf(ctx, focusSID, focusScope, planIssue)
		cancel()
		out["focus_state"] = state
		if f != nil {
			out["focus"] = f
		}
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) homePlan(ctx context.Context, p *model.Plan) (*homePlan, int64, string, error) {
	runs, err := s.Store.ListPlanRuns(ctx, p.ID)
	if err != nil {
		return nil, 0, "", err
	}
	var trig struct {
		SessionID int64  `json:"session_id"`
		ScopeID   string `json:"scope_id"`
		Title     string `json:"title"`
		Plain     struct {
			Title string `json:"title"`
		} `json:"plain"`
	}
	_ = json.Unmarshal(p.Trigger, &trig)
	title := trig.Plain.Title
	if title == "" {
		title = trig.Title
	}
	if title == "" {
		title = p.Issue
	}
	var rat struct {
		Claims []struct {
			ID   string `json:"id"`
			Text string `json:"text"`
		} `json:"claims"`
	}
	_ = json.Unmarshal(p.Rationale, &rat)
	advance := ""
	for _, c := range rat.Claims {
		if strings.HasSuffix(c.ID, ".advance") {
			advance = c.Text
		}
	}
	total := 0
	for _, b := range p.Template {
		total += b.N
	}
	hp := &homePlan{ID: p.ID, Title: title, Cue: p.Cue, Club: p.Club, NextIndex: len(runs), Total: total, CreatedAt: p.CreatedAt,
		Last: json.RawMessage("null"), TriggerSID: trig.SessionID, Issue: p.Issue, Advance: advance}
	for i := len(runs) - 1; i >= 0; i-- {
		if string(runs[i].Evaluation) == "null" || len(runs[i].Evaluation) == 0 {
			continue
		}
		var ev struct {
			Plain    json.RawMessage `json:"plain"`
			Progress json.RawMessage `json:"progress"`
		}
		if json.Unmarshal(runs[i].Evaluation, &ev) == nil {
			hp.Last, _ = json.Marshal(map[string]json.RawMessage{"plain": nz(ev.Plain), "progress": nz(ev.Progress)})
			hp.LastDate = runs[i].SessionDate
		}
		break
	}
	return hp, trig.SessionID, trig.ScopeID, nil
}

func nz(r json.RawMessage) json.RawMessage {
	if len(r) == 0 {
		return json.RawMessage("null")
	}
	return r
}

// homeFocusOf は解説を作り、課題の範囲（scope が空なら、課題のある最初の本体の範囲）の要点を取り出す。
// 状態: found（課題あり）/ no_focus（言えることがまだ無い）/ unavailable（分析サービスに届かない）。
//
// planIssue はプランの候補の id（プランが無ければ空）。要点の「まずここ」は解説が選んだ候補なので、
// 本人が「次」の候補でプランを始めたときは食い違う。そのときは課題をプランの候補にし、
// 要点の「まずここ」のための材料（理想との差の図と文・意識する一点・「次に見る」）は返さない
// （別の候補の図や文をプランの課題の横に並べない。docs/DESIGN_v2.md §3 R4）。
func (s *Server) homeFocusOf(ctx context.Context, sid int64, scope, planIssue string) (*homeFocus, string) {
	se, err := s.Store.GetSession(ctx, sid)
	if err != nil {
		return nil, "unavailable"
	}
	pl, err := s.Store.GetPlayer(ctx, se.PlayerID)
	if err != nil {
		return nil, "unavailable"
	}
	shots, err := s.Store.ListShots(ctx, sid)
	if err != nil {
		return nil, "unavailable"
	}
	exps, err := s.Store.ListExperiments(ctx, sid)
	if err != nil {
		return nil, "unavailable"
	}
	ps, err := s.sessionPayloads(ctx, sid, shots)
	if err != nil {
		return nil, "unavailable"
	}
	raw, err := s.Analyzer.Report(ctx, analysis.ReportInput{Shots: ps, Handedness: pl.Handedness, Experiments: exps})
	if err == nil {
		raw, err = addBandShapes(raw)
	}
	if err != nil {
		if !errors.Is(err, analysis.ErrUnavailable) && !errors.Is(err, context.DeadlineExceeded) {
			s.Log.Warn("home report failed", "session", sid, "err", err)
		}
		return nil, "unavailable"
	}
	var rep struct {
		Scopes []struct {
			ScopeID   string                     `json:"scope_id"`
			Kind      string                     `json:"kind"`
			Label     string                     `json:"label"`
			Figures   map[string]json.RawMessage `json:"figures"`
			BandShape json.RawMessage            `json:"band_shape"`
			Gist      *struct {
				Focus *struct {
					CandidateID string `json:"candidate_id"`
					Title       string `json:"title"`
				} `json:"focus"`
				Steps []struct {
					Text  string `json:"text"`
					Which string `json:"which"`
				} `json:"steps"`
				Candidates map[string]struct {
					Title string `json:"title"`
				} `json:"candidates"`
				Blocks []json.RawMessage `json:"blocks"`
			} `json:"gist"`
		} `json:"scopes"`
	}
	if json.Unmarshal(raw, &rep) != nil {
		return nil, "unavailable"
	}
	for _, sc := range rep.Scopes {
		if sc.Kind != "main" || sc.Gist == nil || sc.Gist.Focus == nil || sc.Gist.Focus.Title == "" {
			continue
		}
		if scope != "" && sc.ScopeID != scope {
			continue
		}
		f := &homeFocus{SessionID: sid, SessionDate: se.Date, ScopeID: sc.ScopeID, Label: sc.Label,
			CandidateID: sc.Gist.Focus.CandidateID, Title: sc.Gist.Focus.Title, BandShape: sc.BandShape}
		if c1, ok := sc.Figures["C1"]; ok {
			f.Figure = c1
		}
		for _, braw := range sc.Gist.Blocks {
			var b struct {
				ID    string `json:"id"`
				Start string `json:"start"`
				Lines []struct {
					Text string `json:"text"`
				} `json:"lines"`
			}
			if json.Unmarshal(braw, &b) != nil {
				continue
			}
			switch b.ID {
			case "gap":
				f.Gap = braw
			case "action":
				f.Startable = b.Start != ""
				for _, ln := range b.Lines {
					if strings.HasPrefix(ln.Text, "意識する一点: ") {
						c := strings.TrimPrefix(ln.Text, "意識する一点: ")
						c = strings.TrimSuffix(strings.TrimPrefix(c, "「"), "。")
						f.Cue = strings.TrimSuffix(c, "」")
					}
				}
			}
		}
		// 次に見る: 要点の手順の「次」（その候補の言葉だけ。前置きの「できたら」は画面が付けない）
		for _, st := range sc.Gist.Steps {
			if st.Which != "next" {
				continue
			}
			if i := strings.Index(st.Text, "「"); i >= 0 {
				if j := strings.LastIndex(st.Text, "」"); j > i {
					f.NextTitle = st.Text[i+len("「") : j]
				}
			}
		}
		if f.NextTitle == f.Title {
			f.NextTitle = ""
		}
		if planIssue != "" && planIssue != f.CandidateID {
			title := ""
			if c, ok := sc.Gist.Candidates[planIssue]; ok {
				title = c.Title
			}
			// 「次に見る」は解説の順（まずここ → 次）なので、プランの候補の次は分からない。出さない
			*f = homeFocus{SessionID: f.SessionID, SessionDate: f.SessionDate, ScopeID: f.ScopeID, Label: f.Label,
				CandidateID: planIssue, Title: title, Startable: true}
		}
		return f, "found"
	}
	return nil, "no_focus"
}
