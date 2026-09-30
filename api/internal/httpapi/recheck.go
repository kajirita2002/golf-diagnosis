package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
	"github.com/kajirita2002/golf-diagnosis/api/internal/store"
)

// 別の日の再確認（docs/DESIGN_v2.md §8・§15 段4）:
//   - 動きのプラン（plans ＋ motion_plans。KPI はカタログの項目、合格の条件は10球テスト 8/10）
//   - 10球テスト（swing_checks の判定を数える。Claude を呼ばない）
//   - 別の日の動画の比較（版・向きが違えば比べない）
//   - TrackMan の2日の再確認（判定を2つ作らない。項目は作った時点で固定）

// motionTemplate は動きのプランの1回の練習の組み方（⓪準備 → ①いつも通り → ドリル／本番の交互 → ⑥10球テスト）。
// ⑥は本番の段として持つ（ブロックの種類を増やさない）。params.test_block がその段の番号。
var motionTemplate = []model.PlanBlock{
	{Kind: model.BlockWarmup, N: 5}, {Kind: model.BlockBaseline, N: 10}, {Kind: model.BlockDrill, N: 5},
	{Kind: model.BlockIntervention, N: 5}, {Kind: model.BlockDrill, N: 5}, {Kind: model.BlockIntervention, N: 5},
	{Kind: model.BlockIntervention, N: 10},
}

const (
	motionTestBlock = 6
	focusMaxSwings  = 10
)

// createMotionPlan は POST /v1/players/{id}/plans の kind=motion。
func (s *Server) createMotionPlan(w http.ResponseWriter, r *http.Request, pid int64, in *planIn) {
	ctx := r.Context()
	in.CPItemID, in.View, in.Checkpoint = strings.TrimSpace(in.CPItemID), strings.TrimSpace(in.View), strings.TrimSpace(in.Checkpoint)
	switch {
	case in.CPItemID == "" || len(in.CPItemID) > 60 || strings.ContainsAny(in.CPItemID, " :"):
		s.fail(w, bad("cp_item_id（チェックポイントの項目）が要ります"))
		return
	case in.View != "dtl" && in.View != "fo":
		s.fail(w, bad("view は dtl か fo です"))
		return
	case !model.IsCheckpoint(in.Checkpoint):
		s.fail(w, bad("checkpoint %q は P の名前ではありません", in.Checkpoint))
		return
	case in.Club == "":
		s.fail(w, bad("club（10球テストで打つクラブ1本）が要ります"))
		return
	case in.DrillID == "" && in.Cue == "":
		s.fail(w, bad("ドリルを選ばないときは、意識する1点（cue）を書いてください"))
		return
	case len(in.Cue) > 400 || len(in.Fault) > 40 || len(in.Title) > 200:
		s.fail(w, bad("cue・fault・title が長すぎます"))
		return
	}
	pl, err := s.Store.GetPlayer(ctx, pid)
	if err != nil {
		s.fail(w, err)
		return
	}
	raw, err := s.Analyzer.Checkpoints(ctx, pl.Handedness)
	if err != nil {
		s.fail(w, err)
		return
	}
	var cat struct {
		Version string `json:"version"`
	}
	if json.Unmarshal(raw, &cat) != nil || cat.Version == "" {
		s.fail(w, errors.New("分析サービスのカタログの版を読めません"))
		return
	}
	if in.FromSession != 0 {
		se, err := s.Store.GetSession(ctx, in.FromSession)
		if err != nil || se.PlayerID != pid {
			s.fail(w, bad("from_session はこの選手のセッションではありません"))
			return
		}
		// 見た目（AI）の判定だけの項目は、十球テスト（判定できた8本が要る）で合格まで行けない。
		// 見た目の評価は1回に数本しか見ないので、プランを組ませない
		if s.itemBasis(ctx, se, pl, in.CPItemID) == "visual" {
			s.fail(w, bad("この項目は見た目（AI）の判定だけなので、十球テストで数えられません。測れる項目で組んでください"))
			return
		}
	}
	trig := map[string]any{"title": in.Title, "session_id": in.FromSession, "cp_item_id": in.CPItemID}
	params := map[string]any{"kind": "motion", "fault": in.Fault, "test_block": motionTestBlock,
		"pass": map[string]int{"in_range": 8, "judged": 8, "of": 10}, "drill_checked": false}
	cv := in.CatalogVersion
	if cv == "" {
		cv = "cp_drills"
	}
	hyp := "仮説: 「" + in.Cue + "」を意識すると、10球テストで範囲の中が10回中8回以上になる"
	p := model.Plan{PlayerID: pid, Issue: "cp:" + in.CPItemID, DrillID: in.DrillID, CatalogVersion: cv, Club: in.Club,
		TargetMetric: "cp:" + in.CPItemID, Goal: model.Goal("in_range"), Hypothesis: hyp, Cue: in.Cue, Template: motionTemplate,
		Motion: &model.MotionPlan{ItemID: in.CPItemID, CatalogVersion: cat.Version, View: in.View, Checkpoint: in.Checkpoint}}
	p.Params, _ = json.Marshal(params)
	p.Trigger, _ = json.Marshal(trig)
	p.Rationale = json.RawMessage(`{}`)
	if err := s.Store.CreatePlan(ctx, &p, r.URL.Query().Get("replace") == "1"); err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, p)
}

// itemBasis はそのセッションの判定で、項目の根拠（measured / visual …）。読めなければ空。
func (s *Server) itemBasis(ctx context.Context, se *model.Session, pl *model.Player, itemID string) string {
	d, err := s.checksData(ctx, se, pl, false)
	if err != nil {
		return ""
	}
	raw, ok := d["checks"].(json.RawMessage)
	if !ok {
		return ""
	}
	var c struct {
		Items []struct {
			ID    string `json:"id"`
			Basis string `json:"basis"`
		} `json:"items"`
	}
	if json.Unmarshal(raw, &c) != nil {
		return ""
	}
	for _, it := range c.Items {
		if it.ID == itemID {
			return it.Basis
		}
	}
	return ""
}

// ---- 10球テスト ----

type swingState struct {
	SwingID int64  `json:"swing_id"`
	State   string `json:"state"`
	Fault   string `json:"fault,omitempty"`
	Basis   string `json:"basis,omitempty"`
}

func planFault(p *model.Plan) string {
	var pr struct {
		Fault string `json:"fault"`
	}
	_ = json.Unmarshal(p.Params, &pr)
	return pr.Fault
}

func (s *Server) createFocusTest(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var in struct {
		PlanID     int64           `json:"plan_id"`
		SessionID  int64           `json:"session_id"`
		ItemID     string          `json:"item_id"`
		View       string          `json:"view"`
		Block      string          `json:"block"`
		SwingIDs   []int64         `json:"swing_ids"`
		SelfRating json.RawMessage `json:"self_rating"`
	}
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	if in.Block == "" {
		in.Block = "test"
	}
	if in.Block != "test" && in.Block != "baseline" {
		s.fail(w, bad("block は test（⑥）か baseline（①）です"))
		return
	}
	se, err := s.Store.GetSession(ctx, in.SessionID)
	if errors.Is(err, store.ErrNotFound) {
		s.fail(w, bad("session_id %d が見つかりません", in.SessionID))
		return
	}
	if err != nil {
		s.fail(w, err)
		return
	}
	item, version, view, fault := in.ItemID, "", in.View, ""
	var planID *int64
	var plan *model.Plan
	if in.PlanID != 0 {
		p, err := s.Store.GetPlan(ctx, in.PlanID)
		if err != nil {
			s.fail(w, err)
			return
		}
		if p.PlayerID != se.PlayerID || p.Motion == nil {
			s.fail(w, bad("plan_id はこの選手の動きのプランではありません"))
			return
		}
		plan, planID = p, &p.ID
		item, version, view, fault = p.Motion.ItemID, p.Motion.CatalogVersion, p.Motion.View, planFault(p)
	}
	if item == "" {
		s.fail(w, bad("item_id（数える項目）か plan_id が要ります"))
		return
	}
	all, err := s.Store.ListSwings(ctx, se.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	var picked []model.Swing
	if len(in.SwingIDs) > 0 {
		if len(in.SwingIDs) > focusMaxSwings {
			s.fail(w, bad("10球テストのスイングは %d 本までです", focusMaxSwings))
			return
		}
		by := map[int64]model.Swing{}
		for _, sw := range all {
			by[sw.ID] = sw
		}
		for _, id := range in.SwingIDs {
			sw, ok := by[id]
			if !ok {
				s.fail(w, bad("スイング %d はこのセッションにありません", id))
				return
			}
			picked = append(picked, sw)
		}
	} else {
		for _, sw := range all {
			if view == "" || sw.View == view {
				picked = append(picked, sw)
			}
		}
		if len(picked) > focusMaxSwings {
			picked = picked[len(picked)-focusMaxSwings:]
		}
	}
	if len(picked) == 0 {
		s.fail(w, bad("数えるスイングがありません（動画を入れてから数えます）"))
		return
	}
	// 版・向きが違えば比べない（§8.5）。測っていないスイングは「判定できない」に数える
	if view == "" {
		view = picked[0].View
	}
	if version == "" {
		version = picked[0].CPCatalogVersion
	}
	states := make([]swingState, 0, len(picked))
	ids := make([]int64, 0, len(picked))
	for _, sw := range picked {
		if sw.View != view {
			s.fail(w, bad("向きが違うスイングが混ざっています（%s と %s）。同じ向きのスイングだけで数えます", view, sw.View))
			return
		}
		if sw.CPCatalogVersion != "" && sw.CPCatalogVersion != version {
			s.fail(w, bad("基準が新しくなったので比べません（プランの版 %s・スイングの版 %s）", version, sw.CPCatalogVersion))
			return
		}
		st := swingState{SwingID: sw.ID, State: "unknown"}
		if sw.CPCatalogVersion != "" {
			cs, err := s.Store.ListSwingChecks(ctx, sw.ID, version)
			if err != nil {
				s.fail(w, err)
				return
			}
			for _, c := range cs {
				if c.ItemID == item {
					if c.State == "in_range" || c.State == "out_range" {
						st.State = c.State
					}
					st.Fault, st.Basis = c.FaultID, c.Basis
				}
			}
		}
		states = append(states, st)
		ids = append(ids, sw.ID)
	}
	var sr any
	if len(in.SelfRating) > 0 && string(in.SelfRating) != "null" {
		if _, err := jsonObject("self_rating", in.SelfRating); err != nil {
			s.fail(w, err)
			return
		}
		sr = in.SelfRating
	}
	body := map[string]any{"swings": states, "self_rating": sr}
	if fault != "" {
		body["target_fault"] = fault
	}
	raw, err := s.Analyzer.Call(ctx, "/v1/focus-test/judge", body)
	if err != nil {
		s.fail(w, err)
		return
	}
	var jr struct {
		InRange int   `json:"in_range"`
		Judged  int   `json:"judged"`
		Passed  *bool `json:"passed"`
	}
	if err := json.Unmarshal(raw, &jr); err != nil {
		s.fail(w, errors.New("分析サービスの10球テストの形を読めません"))
		return
	}
	// 合格に要る数を黙って下げない: 判定できたのが8に満たなければ、分析サービスが何と言っても判定できない
	if jr.Judged < 8 {
		jr.Passed = nil
	}
	sid := se.ID
	f := model.FocusTest{PlayerID: se.PlayerID, PlanID: planID, SessionID: &sid, ItemID: item, CatalogVersion: version, View: view,
		Block: in.Block, SwingIDs: ids, SelfRating: in.SelfRating, InRange: jr.InRange, Judged: jr.Judged, Passed: jr.Passed, Result: raw}
	if sr == nil {
		f.SelfRating = json.RawMessage(`{}`)
	}
	if err := s.Store.CreateFocusTest(ctx, &f); err != nil {
		s.fail(w, err)
		return
	}
	f.Date = se.Date
	out, err := s.focusTestView(ctx, &f, plan)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, out)
}

// focusTestView は10球テスト1つの画面ぶん: 本体・前回（同じ項目・版・向きだけ）・前と後の同じ P・プランの状態。
func (s *Server) focusTestView(ctx context.Context, f *model.FocusTest, plan *model.Plan) (map[string]any, error) {
	out := map[string]any{"test": f}
	tests, err := s.Store.ListFocusTests(ctx, f.PlayerID, f.ItemID, 0)
	if err != nil {
		return nil, err
	}
	var prev *model.FocusTest
	for i := range tests {
		x := &tests[i]
		if x.ID == f.ID {
			break
		}
		if x.CatalogVersion == f.CatalogVersion && x.View == f.View {
			prev = x
		}
	}
	out["previous"] = prev
	if plan == nil && f.PlanID != nil {
		if plan, err = s.Store.GetPlan(ctx, *f.PlanID); err != nil {
			return nil, err
		}
	}
	if plan != nil && plan.Motion != nil {
		out["plan"] = plan
		if prog, err := s.motionProgress(ctx, plan); err == nil {
			out["progress"] = prog
		}
		out["thumbs"] = s.beforeAfter(ctx, plan, f)
	}
	return out, nil
}

// beforeAfter は前（プランを作った日のスイング）と後（この10球テストで範囲の中だったスイング）の同じ P のサムネイル。
func (s *Server) beforeAfter(ctx context.Context, p *model.Plan, f *model.FocusTest) map[string]any {
	cp := p.Motion.Checkpoint
	has := func(swID int64) bool {
		fs, err := s.Store.ListSwingFrames(ctx, swID)
		if err != nil {
			return false
		}
		for _, x := range fs {
			if x.Checkpoint == cp && x.HasThumb {
				return true
			}
		}
		return false
	}
	var trig struct {
		SessionID int64 `json:"session_id"`
	}
	_ = json.Unmarshal(p.Trigger, &trig)
	out := map[string]any{"p": cp}
	if trig.SessionID != 0 {
		if ss, err := s.Store.ListSwings(ctx, trig.SessionID); err == nil {
			for _, sw := range ss {
				if sw.View == p.Motion.View && has(sw.ID) {
					out["before"] = fmt.Sprintf("/v1/swings/%d/thumbs/%s", sw.ID, cp)
					break
				}
			}
		}
	}
	var res struct {
		Marks []struct {
			State string `json:"state"`
		} `json:"marks"`
	}
	_ = json.Unmarshal(f.Result, &res)
	for i, id := range f.SwingIDs {
		if i < len(res.Marks) && res.Marks[i].State == "in_range" && has(id) {
			out["after"] = fmt.Sprintf("/v1/swings/%d/thumbs/%s", id, cp)
			break
		}
	}
	return out
}

// motionProgress は動きのプランの状態（分析サービスの /v1/plan/motion-progress）。
func (s *Server) motionProgress(ctx context.Context, p *model.Plan) (json.RawMessage, error) {
	tests, err := s.Store.ListFocusTests(ctx, p.PlayerID, p.Motion.ItemID, p.ID)
	if err != nil {
		return nil, err
	}
	in := make([]map[string]any, 0, len(tests))
	for _, t := range tests {
		in = append(in, map[string]any{"date": t.Date, "block": t.Block, "passed": t.Passed})
	}
	return s.Analyzer.Call(ctx, "/v1/plan/motion-progress", map[string]any{"tests": in})
}

func (s *Server) getFocusTest(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	f, err := s.Store.GetFocusTest(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	out, err := s.focusTestView(r.Context(), f, nil)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, out)
}

// listFocusTests は経過（T）の「◯/10」の推移。条件（向き・版）ごとに線を分けるのは画面。
func (s *Server) listFocusTests(w http.ResponseWriter, r *http.Request) {
	pid, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	if _, err := s.Store.GetPlayer(r.Context(), pid); err != nil {
		s.fail(w, err)
		return
	}
	ts, err := s.Store.ListFocusTests(r.Context(), pid, r.URL.Query().Get("item"), 0)
	if err != nil {
		s.fail(w, err)
		return
	}
	for i := range ts {
		ts[i].Result = nil // 一覧には要らない
	}
	writeJSON(w, http.StatusOK, map[string]any{"tests": ts, "pass_line": 8})
}

// ---- 別の日の動画の比較（§8.5） ----

func (s *Server) compareChecks(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	q := r.URL.Query()
	a, errA := strconv.ParseInt(q.Get("a"), 10, 64)
	b, errB := strconv.ParseInt(q.Get("b"), 10, 64)
	if errA != nil || errB != nil || a <= 0 || b <= 0 {
		s.fail(w, bad("a と b（セッションの id）が要ります"))
		return
	}
	item := q.Get("item")
	sa, err := s.Store.GetSession(ctx, a)
	if err != nil {
		s.fail(w, err)
		return
	}
	sb, err := s.Store.GetSession(ctx, b)
	if err != nil {
		s.fail(w, err)
		return
	}
	if sa.PlayerID != sb.PlayerID {
		s.fail(w, bad("別の選手のセッションとは比べません"))
		return
	}
	type side struct {
		views, versions map[string]bool
		counts          map[string][2]int // item → [範囲の中, 範囲の外]
	}
	load := func(sid int64) (*side, error) {
		ss, err := s.Store.ListSwings(ctx, sid)
		if err != nil {
			return nil, err
		}
		x := &side{views: map[string]bool{}, versions: map[string]bool{}, counts: map[string][2]int{}}
		for _, sw := range ss {
			if sw.CPCatalogVersion == "" {
				continue
			}
			x.views[sw.View], x.versions[sw.CPCatalogVersion] = true, true
			cs, err := s.Store.ListSwingChecks(ctx, sw.ID, sw.CPCatalogVersion)
			if err != nil {
				return nil, err
			}
			for _, c := range cs {
				if item != "" && c.ItemID != item {
					continue
				}
				n := x.counts[c.ItemID]
				switch c.State {
				case "in_range":
					n[0]++
				case "out_range":
					n[1]++
				}
				x.counts[c.ItemID] = n
			}
		}
		return x, nil
	}
	A, err := load(a)
	if err != nil {
		s.fail(w, err)
		return
	}
	B, err := load(b)
	if err != nil {
		s.fail(w, err)
		return
	}
	one := func(m map[string]bool) string {
		for k := range m {
			return k
		}
		return ""
	}
	out := map[string]any{"a": a, "b": b, "comparable": true, "items": []any{}}
	switch {
	case len(A.views) == 0 || len(B.views) == 0:
		out["comparable"], out["reason"], out["text"] = false, "no_video", "どちらかの日に、測った動画がありません。"
	case len(A.versions) != 1 || len(B.versions) != 1 || one(A.versions) != one(B.versions):
		out["comparable"], out["reason"], out["text"] = false, "version", "基準が新しくなったので比べません。"
	case len(A.views) != 1 || len(B.views) != 1 || one(A.views) != one(B.views):
		out["comparable"], out["reason"], out["text"] = false, "view", "撮った向きが違うので比べません。"
	}
	if out["comparable"] == false {
		writeJSON(w, http.StatusOK, out)
		return
	}
	label := func(c [2]int) string {
		n := c[0] + c[1]
		if c[1] > c[0] {
			return fmt.Sprintf("範囲の外（%d本中%d本）", n, c[1])
		}
		return fmt.Sprintf("範囲の中（%d本中%d本）", n, c[0])
	}
	items := []any{}
	for id, ca := range A.counts {
		cb, ok := B.counts[id]
		if !ok || ca[0]+ca[1] == 0 || cb[0]+cb[1] == 0 {
			continue
		}
		items = append(items, map[string]any{"item_id": id, "a": map[string]int{"in_range": ca[0], "out_range": ca[1]},
			"b": map[string]int{"in_range": cb[0], "out_range": cb[1]}, "label": label(ca) + " → " + label(cb)})
	}
	out["items"], out["view"], out["catalog_version"] = items, one(A.views), one(A.versions)
	writeJSON(w, http.StatusOK, out)
}

// ---- TrackMan の2日の再確認（§8.4） ----

func (s *Server) createCheckup(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	pid, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	var in struct {
		BaselineSessionID int64 `json:"baseline_session_id"`
		RecheckSessionID  int64 `json:"recheck_session_id"`
		PlanID            int64 `json:"plan_id"`
	}
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	if in.BaselineSessionID == in.RecheckSessionID {
		s.fail(w, bad("別の日のセッションを2つ選んでください"))
		return
	}
	for _, sid := range []int64{in.BaselineSessionID, in.RecheckSessionID} {
		se, err := s.Store.GetSession(ctx, sid)
		if errors.Is(err, store.ErrNotFound) {
			s.fail(w, bad("セッション %d が見つかりません", sid))
			return
		}
		if err != nil {
			s.fail(w, err)
			return
		}
		if se.PlayerID != pid {
			s.fail(w, bad("セッション %d は別の選手のものです", sid))
			return
		}
	}
	c := model.Checkup{PlayerID: pid, BaselineSessionID: in.BaselineSessionID, RecheckSessionID: in.RecheckSessionID}
	if in.PlanID != 0 {
		p, err := s.Store.GetPlan(ctx, in.PlanID)
		if err != nil {
			s.fail(w, err)
			return
		}
		if p.PlayerID != pid {
			s.fail(w, bad("plan_id は別の選手のプランです"))
			return
		}
		c.PlanID = &p.ID
	}
	res, err := s.runCheckup(ctx, &c, nil)
	if err != nil {
		s.fail(w, err)
		return
	}
	var got struct {
		Items json.RawMessage `json:"items"`
	}
	if json.Unmarshal(res, &got) != nil || len(got.Items) == 0 || string(got.Items) == "null" {
		s.fail(w, errors.New("分析サービスの再確認の形を読めません"))
		return
	}
	var items []map[string]any
	_ = json.Unmarshal(got.Items, &items)
	scope := map[string]any{}
	if len(items) > 0 {
		scope["club"] = items[0]["club"]
	}
	c.Scope, _ = json.Marshal(scope)
	c.Items, c.Result, c.ResultKey = got.Items, res, s.analysisVersion(ctx)
	if err := s.Store.CreateCheckup(ctx, &c); err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, c)
}

// runCheckup は分析サービスに2日の球を渡す。items が nil なら既定の項目を作らせる（作るときだけ）。
func (s *Server) runCheckup(ctx context.Context, c *model.Checkup, items json.RawMessage) (json.RawMessage, error) {
	payload := func(sid int64) (any, error) {
		shots, err := s.Store.ListShots(ctx, sid)
		if err != nil {
			return nil, err
		}
		return s.sessionPayloads(ctx, sid, shots)
	}
	a, err := payload(c.BaselineSessionID)
	if err != nil {
		return nil, err
	}
	b, err := payload(c.RecheckSessionID)
	if err != nil {
		return nil, err
	}
	body := map[string]any{"a": a, "b": b, "items": nil}
	if items != nil {
		body["items"] = items
	}
	if c.PlanID != nil {
		p, err := s.Store.GetPlan(ctx, *c.PlanID)
		if err != nil {
			return nil, err
		}
		if p.Motion == nil {
			body["plan"] = map[string]any{"target_metric": p.TargetMetric, "goal": p.Goal, "club": p.Club}
			// プランの評価（保存したもの）の状態をそのまま出す。判定を2つ作らない
			runs, err := s.Store.ListPlanRuns(ctx, p.ID)
			if err != nil {
				return nil, err
			}
			for i := len(runs) - 1; i >= 0; i-- {
				if ev := runs[i].Evaluation; len(ev) > 0 && string(ev) != "null" {
					body["plan_eval"] = ev
					break
				}
			}
			var trig struct {
				SessionID int64 `json:"session_id"`
			}
			_ = json.Unmarshal(p.Trigger, &trig)
			body["baseline_is_diagnosis"] = trig.SessionID != 0 && trig.SessionID == c.BaselineSessionID
		}
	}
	return s.Analyzer.Call(ctx, "/v1/checkup", body)
}

func (s *Server) getCheckup(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	c, err := s.Store.GetCheckup(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	// 開くたびに計算し直す（球の除外・クラブの直し・版の変更を拾う）。項目は作った時点のまま（あとから増やさない）。
	// 分析サービスに届かなければ、前に残した結果を出す
	if res, err := s.runCheckup(r.Context(), c, c.Items); err == nil {
		c.Result = res
		_ = s.Store.SaveCheckupResult(r.Context(), c.ID, s.analysisVersion(r.Context()), res)
	} else if len(c.Result) == 0 {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, c)
}
