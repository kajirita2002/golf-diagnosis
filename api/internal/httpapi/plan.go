package httpapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/kajirita2002/golf-diagnosis/api/internal/analysis"
	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
	"github.com/kajirita2002/golf-diagnosis/api/internal/physics"
	"github.com/kajirita2002/golf-diagnosis/api/internal/store"
)

// アクションプラン（docs/DESIGN_coaching.md §8・§10.2）。
//
//	候補から1つ選ぶ → Plan（動かせるのは選手ごとに1つ。2つ目は 409）
//	練習の日ごと → PlanRun（その日のセッションに既存の Experiment を作って紐づける。1セッション1つ）
//	取り込み → ブロックの割り当て（予定の球数で区切る。境目は PUT で直す）→ 評価（分析サービス）
//
// 判定・状態の移り方・次の手は分析サービス（POST /v1/plan/evaluate）が決める。Go は球を集めて渡し、
// 返ってきた評価を入力の指紋と一緒に plan_runs.evaluation_json に保存するだけ（数字を作らない）。
// プランの状態（plans.status）を評価から勝手に変えない。「定着した」「詰まった」「止める」は
// 評価が言い、変えるのは本人（PATCH）―― 「続けるか本人に選ばせる」（§8.6）。

// BlockNMax は1ブロックの上限（§8.3 の BLOCK_N_MAX）。型の各段はこれを超えられない。
const BlockNMax = 12

// countMax は境目を動かしたあとの1段の球数の上限（打ち直しを足しても、ここまでで足りる）。
const countMax = 60

// ---- 409 ----

func conflictBody(err error) map[string]any {
	out := map[string]any{"error": err.Error()}
	var ce *store.ConflictError
	if errors.As(err, &ce) && ce.ExistingID != 0 {
		out["existing_id"] = ce.ExistingID
	}
	return out
}

func conflict(msg string) error { return &store.ConflictError{Msg: msg} }

// ---- ドリル集 ----

// checkedDrills はドリル集から、人が向きと安全を確かめた（checked_by がある）ドリルだけを残す。
// 分析サービスも checked_by の無いドリルを出さない約束（§10.1）だが、置く側が逆だと逆の動きを
// 練習させるので、Go でももう一度絞る（二重に守る）。
// 形は2通りを受ける: [{…}, …] か {"drills": [{…}, …], …}（ほかのキーはそのまま残す）。
func checkedDrills(raw json.RawMessage) (json.RawMessage, []map[string]any, string, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, nil, "", err
	}
	version := ""
	var list []any
	obj, isObj := v.(map[string]any)
	switch x := v.(type) {
	case []any:
		list = x
	case map[string]any:
		list, _ = x["drills"].([]any)
		for _, k := range []string{"catalog_version", "version"} {
			if s, ok := x[k].(string); ok && s != "" {
				version = s
				break
			}
		}
	}
	kept := []map[string]any{}
	keptAny := []any{}
	for _, d := range list {
		m, ok := d.(map[string]any)
		if !ok {
			continue
		}
		if cb, ok := m["checked_by"].(string); !ok || strings.TrimSpace(cb) == "" {
			continue
		}
		kept = append(kept, m)
		keptAny = append(keptAny, m)
	}
	var out any = keptAny
	if isObj {
		obj["drills"] = keptAny
		out = obj
	}
	b, err := json.Marshal(out)
	return b, kept, version, err
}

// listDrills は GET /v1/drills?handedness=R|L。文の向きの語と図は利き手で入れ替わる（R9）。
func (s *Server) listDrills(w http.ResponseWriter, r *http.Request) {
	hand := model.Handedness(r.URL.Query().Get("handedness"))
	switch hand {
	case "":
		hand = model.RightHanded
	case model.RightHanded, model.LeftHanded:
	default:
		s.fail(w, bad("handedness は R か L です"))
		return
	}
	raw, err := s.Analyzer.Drills(r.Context(), hand)
	if err != nil {
		s.fail(w, err)
		return
	}
	out, _, _, err := checkedDrills(raw)
	if err != nil {
		s.fail(w, errors.New("分析サービスのドリル集を読めません"))
		return
	}
	writeRaw(w, http.StatusOK, out)
}

// ---- プラン ----

type planIn struct {
	Issue          string            `json:"issue"`
	Lever          string            `json:"lever"`
	DrillID        string            `json:"drill_id"`
	CatalogVersion string            `json:"catalog_version"`
	Club           string            `json:"club"`
	TargetMetric   string            `json:"target_metric"`
	Goal           model.Goal        `json:"goal"`
	Hypothesis     string            `json:"hypothesis"`
	Cue            string            `json:"cue"`
	Template       []model.PlanBlock `json:"template"`
	Params         json.RawMessage   `json:"params"`
	Trigger        json.RawMessage   `json:"trigger"`
	Rationale      json.RawMessage   `json:"rationale"`
	FromSession    int64             `json:"from_session"`
	EngineVersion  string            `json:"engine_version"`
	// 候補から作る（本線）: scope_id と candidate_id を渡すと、from_session の球で分析サービスの
	// /v1/plan/build を呼び、返った型・params・trigger・rationale をそのまま保存する（画面で写さない）。
	ScopeID     string         `json:"scope_id"`
	CandidateID string         `json:"candidate_id"`
	Window      map[string]any `json:"window"` // 解説の band_shape.window（{face_min, face_max, path}・右打ちの座標）
	// Variant は型（standard / alternate）。alternate は「移せていない」の次の型（ドリルと本番を1球ずつ交互。§8.6）
	Variant string `json:"variant"`
	// 動きのプラン（kind=motion。docs/DESIGN_v2.md §8.3）: カタログの項目・10球テストで取り出す向きと P・外れの向き・見出し
	Kind       string `json:"kind"`
	CPItemID   string `json:"cp_item_id"`
	View       string `json:"view"`
	Checkpoint string `json:"checkpoint"`
	Fault      string `json:"fault"`
	Title      string `json:"title"`
}

// buildOut は分析サービスの /v1/plan/build の応答のうち、Go が読むところ。
type buildOut struct {
	Plan struct {
		Issue          string            `json:"issue"`
		Title          string            `json:"title"`
		Lever          *string           `json:"lever"`
		DrillID        string            `json:"drill_id"`
		CatalogVersion string            `json:"catalog_version"`
		Club           string            `json:"club"`
		TargetMetric   string            `json:"target_metric"`
		Goal           model.Goal        `json:"goal"`
		Template       []model.PlanBlock `json:"template"`
		Params         json.RawMessage   `json:"params"`
		Trigger        json.RawMessage   `json:"trigger"`
		Rationale      json.RawMessage   `json:"rationale"`
		EngineVersion  string            `json:"engine_version"`
	} `json:"plan"`
	Intervention string `json:"intervention"`
	Claims       []struct {
		ID   string `json:"id"`
		Text string `json:"text"`
	} `json:"claims"`
	Notes []string        `json:"notes"`
	Drill json.RawMessage `json:"drill"`
}

// windowOf は画面から来た窓を、数だけ残して分析サービスへ渡す形にする（ok でない窓は渡さない）。
func windowOf(w map[string]any) map[string]any {
	if w == nil {
		return nil
	}
	if ok, has := w["ok"].(bool); has && !ok {
		return nil
	}
	out := map[string]any{}
	for _, k := range []string{"face_min", "face_max", "path"} {
		v, ok := w[k].(float64)
		if !ok || v != v || v > 90 || v < -90 {
			return nil
		}
		out[k] = v
	}
	return out
}

// fromBuild は候補から作るとき、分析サービスの /v1/plan/build を呼んで in を埋める。
// 返り値は画面に添える材料（ドリル・注記・定型文）。
func (s *Server) fromBuild(ctx context.Context, pid int64, in *planIn) (map[string]any, error) {
	if in.FromSession == 0 {
		return nil, bad("候補から作るときは from_session（診断したセッション）が要ります")
	}
	if in.CandidateID == "" {
		return nil, bad("candidate_id が要ります")
	}
	custom := in.DrillID == "" || in.DrillID == "custom"
	if custom && in.Cue == "" {
		return nil, bad("確かめ済みのドリルを選ばないときは、本番で意識する1点（cue）を書いてください")
	}
	se, err := s.Store.GetSession(ctx, in.FromSession)
	if errors.Is(err, store.ErrNotFound) {
		return nil, bad("from_session %d が見つかりません", in.FromSession)
	}
	if err != nil {
		return nil, err
	}
	if se.PlayerID != pid {
		return nil, bad("from_session は別の選手のセッションです")
	}
	pl, err := s.Store.GetPlayer(ctx, pid)
	if err != nil {
		return nil, err
	}
	shots, err := s.Store.ListShots(ctx, in.FromSession)
	if err != nil {
		return nil, err
	}
	ps, err := s.sessionPayloads(ctx, in.FromSession, shots)
	if err != nil {
		return nil, err
	}
	switch in.Variant {
	case "", "standard", "alternate":
	default:
		return nil, bad("variant は standard か alternate です")
	}
	bi := analysis.PlanBuildInput{Shots: ps, Handedness: pl.Handedness, ScopeID: in.ScopeID, CandidateID: in.CandidateID,
		Club: in.Club, Window: windowOf(in.Window), Cue: in.Cue, Variant: in.Variant}
	if !custom {
		bi.DrillID = in.DrillID
	}
	raw, err := s.Analyzer.PlanBuild(ctx, bi)
	if err != nil {
		return nil, err
	}
	var b buildOut
	if err := json.Unmarshal(raw, &b); err != nil || len(b.Plan.Template) == 0 {
		return nil, errors.New("分析サービスのプランの形を読めません")
	}
	bp := b.Plan
	in.Issue, in.Club, in.TargetMetric, in.Goal, in.Template = bp.Issue, bp.Club, bp.TargetMetric, bp.Goal, bp.Template
	in.CatalogVersion, in.EngineVersion, in.Params, in.Rationale = bp.CatalogVersion, bp.EngineVersion, bp.Params, bp.Rationale
	in.Lever = ""
	if bp.Lever != nil {
		in.Lever = *bp.Lever
	}
	in.DrillID = strings.TrimSpace(bp.DrillID)
	if in.DrillID == "custom" {
		in.DrillID = ""
	}
	if strings.TrimSpace(b.Intervention) != "" {
		in.Cue = strings.TrimSpace(b.Intervention)
	}
	// 画面の見出し（「打点をヒールから真ん中へ」）は trigger に置く（表の列を増やさない）
	trig, err := jsonObject("trigger", bp.Trigger)
	if err != nil {
		return nil, err
	}
	if bp.Title != "" {
		trig["title"] = bp.Title
	}
	in.Trigger, _ = json.Marshal(trig)
	// 候補の定型文（先にする理由・次に進む条件・動かさないもの）は、作った時点の文を rationale に写しておく。
	// 今日の練習の画面が、診断を開き直さずに（圏外でも）同じ文を出せるように。
	if len(b.Claims) > 0 {
		rat, err := jsonObject("rationale", bp.Rationale)
		if err != nil {
			return nil, err
		}
		rat["claims"] = b.Claims
		in.Rationale, _ = json.Marshal(rat)
	}
	for _, c := range b.Claims {
		if strings.HasSuffix(c.ID, ".hypothesis") && c.Text != "" {
			in.Hypothesis = c.Text
			break
		}
	}
	extra := map[string]any{"notes": b.Notes, "claims": b.Claims}
	if len(b.Drill) > 0 && string(b.Drill) != "null" {
		extra["drill"] = b.Drill
	}
	return extra, nil
}

func checkTemplate(tpl []model.PlanBlock) error {
	if len(tpl) == 0 {
		return bad("template（ブロックの型 [{kind, n}]）が要ります。候補の design.template を渡してください")
	}
	if len(tpl) > 20 {
		return bad("template が長すぎます（20段まで）")
	}
	var a, b bool
	for _, t := range tpl {
		if !t.Kind.Valid() {
			return bad("template の kind %q は使えません（warmup / baseline / drill / intervention / retention）", t.Kind)
		}
		if t.N < 1 || t.N > BlockNMax {
			return bad("template の球数は1段 1〜%d 球です（%s %d球）", BlockNMax, t.Kind, t.N)
		}
		a = a || t.Kind == model.BlockBaseline
		b = b || t.Kind == model.BlockIntervention
	}
	if !a || !b {
		return bad("template には baseline（いつも通り）と intervention（本番）が1つ以上要ります")
	}
	return nil
}

// jsonObject は空なら {}、オブジェクトでなければ 400。
func jsonObject(name string, raw json.RawMessage) (map[string]any, error) {
	if len(bytes.TrimSpace(raw)) == 0 || string(bytes.TrimSpace(raw)) == "null" {
		return map[string]any{}, nil
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var m map[string]any
	if err := dec.Decode(&m); err != nil || m == nil {
		return nil, bad("%s は JSON のオブジェクトです", name)
	}
	return m, nil
}

func (s *Server) createPlan(w http.ResponseWriter, r *http.Request) {
	pid, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	var in planIn
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	if _, err := s.Store.GetPlayer(r.Context(), pid); err != nil {
		s.fail(w, err)
		return
	}
	in.Issue, in.Club, in.DrillID, in.Cue = strings.TrimSpace(in.Issue), strings.TrimSpace(in.Club), strings.TrimSpace(in.DrillID), strings.TrimSpace(in.Cue)
	switch in.Kind {
	case "", "ball":
	case "motion":
		s.createMotionPlan(w, r, pid, &in)
		return
	default:
		s.fail(w, bad("kind は ball か motion です"))
		return
	}
	var extra map[string]any
	if in.ScopeID != "" {
		// 型・params は分析サービスが作る（本線）。確かめていないドリルは下でもう一度断る
		if extra, err = s.fromBuild(r.Context(), pid, &in); err != nil {
			s.fail(w, err)
			return
		}
	}
	switch {
	case in.Issue == "" || len(in.Issue) > 64:
		s.fail(w, bad("issue（何を直すか。例: strike_heel）が要ります"))
		return
	case in.Club == "":
		// 既存の評価はクラブ名の完全一致で球を拾う（§8.1）。空だと全部のクラブが混ざる
		s.fail(w, bad("club（プランで打つクラブ1本）が要ります"))
		return
	case !targetMetrics[in.TargetMetric]:
		s.fail(w, bad("target_metric %q は目標にできません", in.TargetMetric))
		return
	case !in.Goal.Valid():
		s.fail(w, bad("goal は reduce_abs / reduce_sd / increase / decrease"))
		return
	}
	if err := checkTemplate(in.Template); err != nil {
		s.fail(w, err)
		return
	}
	params, err := jsonObject("params", in.Params)
	if err != nil {
		s.fail(w, err)
		return
	}
	trigger, err := jsonObject("trigger", in.Trigger)
	if err != nil {
		s.fail(w, err)
		return
	}
	rationale, err := jsonObject("rationale", in.Rationale)
	if err != nil {
		s.fail(w, err)
		return
	}
	if in.FromSession != 0 && in.ScopeID == "" {
		se, err := s.Store.GetSession(r.Context(), in.FromSession)
		if errors.Is(err, store.ErrNotFound) {
			s.fail(w, bad("from_session %d が見つかりません", in.FromSession))
			return
		}
		if err != nil {
			s.fail(w, err)
			return
		}
		if se.PlayerID != pid {
			s.fail(w, bad("from_session は別の選手のセッションです"))
			return
		}
	}
	if in.FromSession != 0 {
		trigger["session_id"] = in.FromSession
	}

	// ドリルは人が向きと安全を確かめたもの（checked_by）だけ。確かめ済みが無いあいだは、
	// 本人が意識する1点（cue）を書いて始める（§8.2「空のあいだは『意識する1点を自分で書く』欄だけ」）。
	if in.DrillID != "" {
		pl, err := s.Store.GetPlayer(r.Context(), pid)
		if err != nil {
			s.fail(w, err)
			return
		}
		raw, err := s.Analyzer.Drills(r.Context(), pl.Handedness)
		if err != nil {
			s.fail(w, err)
			return
		}
		_, drills, version, err := checkedDrills(raw)
		if err != nil {
			s.fail(w, errors.New("分析サービスのドリル集を読めません"))
			return
		}
		var hit map[string]any
		for _, d := range drills {
			if id, _ := d["id"].(string); id == in.DrillID {
				hit = d
				break
			}
		}
		if hit == nil {
			s.fail(w, bad("ドリル %q は確かめ済みのドリルではありません（向きと安全を人が確かめたものだけ使えます）", in.DrillID))
			return
		}
		if in.CatalogVersion == "" {
			in.CatalogVersion = version
		}
		if in.Cue == "" {
			in.Cue, _ = hit["cue_transfer"].(string)
		}
	} else if in.Cue == "" {
		s.fail(w, bad("確かめ済みのドリルを選ばないときは、本番で意識する1点（cue）を書いてください"))
		return
	}
	if len(in.Cue) > 400 || len(in.Hypothesis) > 1000 {
		s.fail(w, bad("cue か hypothesis が長すぎます"))
		return
	}
	if strings.TrimSpace(in.Hypothesis) == "" {
		in.Hypothesis = "仮説: 「" + in.Cue + "」を意識して打つと、" + in.TargetMetric + " が動く（" + in.Club + "）"
	}
	p := model.Plan{PlayerID: pid, Issue: in.Issue, Lever: in.Lever, DrillID: in.DrillID, CatalogVersion: in.CatalogVersion,
		Club: in.Club, TargetMetric: in.TargetMetric, Goal: in.Goal, Hypothesis: in.Hypothesis, Cue: in.Cue, Template: in.Template,
		EngineVersion: in.EngineVersion}
	p.Params, _ = json.Marshal(params)
	p.Trigger, _ = json.Marshal(trigger)
	p.Rationale, _ = json.Marshal(rationale)
	if err := s.Store.CreatePlan(r.Context(), &p, r.URL.Query().Get("replace") == "1"); err != nil {
		s.fail(w, err)
		return
	}
	if extra == nil {
		writeJSON(w, http.StatusCreated, p)
		return
	}
	writeJSON(w, http.StatusCreated, struct {
		model.Plan
		Build map[string]any `json:"build"`
	}{p, extra})
}

func (s *Server) listPlans(w http.ResponseWriter, r *http.Request) {
	pid, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	if _, err := s.Store.GetPlayer(r.Context(), pid); err != nil {
		s.fail(w, err)
		return
	}
	ps, err := s.Store.ListPlans(r.Context(), pid)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, ps)
}

type planWithRuns struct {
	model.Plan
	Runs []model.PlanRun `json:"runs"`
}

func (s *Server) getPlan(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	p, err := s.Store.GetPlan(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	runs, err := s.Store.ListPlanRuns(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, planWithRuns{Plan: *p, Runs: runs})
}

func (s *Server) patchPlan(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	var in struct {
		Status      model.PlanStatus `json:"status"`
		CloseReason string           `json:"close_reason"`
	}
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	if !in.Status.Valid() {
		s.fail(w, bad("status は active / done / switched / blocked / abandoned"))
		return
	}
	if len(in.CloseReason) > 400 {
		s.fail(w, bad("close_reason が長すぎます"))
		return
	}
	p, err := s.Store.SetPlanStatus(r.Context(), id, in.Status, strings.TrimSpace(in.CloseReason))
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, p)
}

// ---- run ----

func templateCounts(tpl []model.PlanBlock) []int {
	out := make([]int, len(tpl))
	for i, t := range tpl {
		out[i] = t.N
	}
	return out
}

func checkCounts(tpl []model.PlanBlock, counts []int) error {
	if len(counts) != len(tpl) {
		return bad("counts は型と同じ %d 段です（%d 段が来ました）", len(tpl), len(counts))
	}
	for i, n := range counts {
		if n < 0 || n > countMax {
			return bad("counts[%d] は 0〜%d 球です", i, countMax)
		}
	}
	return nil
}

func (s *Server) createPlanRun(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	var in struct {
		SessionID int64 `json:"session_id"`
		Counts    []int `json:"counts"`
	}
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	p, err := s.Store.GetPlan(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	if p.Status != model.PlanActive {
		s.fail(w, conflict("このプランは動いていません（状態 "+string(p.Status)+"）。練習を足せるのは動いているプランだけです"))
		return
	}
	if p.Motion != nil {
		// 動きのプランは Python の evaluate が cp: の指標を知らないので、練習は10球テストで記録する
		s.fail(w, bad("動きのプランの練習は10球テスト（POST /v1/focus-tests）で記録します"))
		return
	}
	se, err := s.Store.GetSession(r.Context(), in.SessionID)
	if errors.Is(err, store.ErrNotFound) {
		s.fail(w, bad("session_id %d が見つかりません", in.SessionID))
		return
	}
	if err != nil {
		s.fail(w, err)
		return
	}
	if se.PlayerID != p.PlayerID {
		s.fail(w, bad("別の選手のセッションです"))
		return
	}
	if err := s.checkRunSession(r.Context(), p, se); err != nil {
		s.fail(w, err)
		return
	}
	counts := in.Counts
	if counts == nil {
		counts = templateCounts(p.Template)
	}
	if err := checkCounts(p.Template, counts); err != nil {
		s.fail(w, err)
		return
	}
	e := model.Experiment{SessionID: se.ID, Hypothesis: p.Hypothesis, Intervention: p.Cue, TargetMetric: p.TargetMetric, Goal: p.Goal, Club: p.Club}
	run, err := s.Store.CreatePlanRun(r.Context(), p.ID, &e, counts)
	if err != nil {
		s.fail(w, err)
		return
	}
	v, err := s.runView(r.Context(), run)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, v)
}

// checkRunSession は練習（run）にしてよいセッションか。きっかけの診断のセッションと、プランを作る前の日付で
// もう球が入っているセッションは断る（その球に「準備」「ドリル」が付いて診断から消え、解説と候補が変わる。
// しかも日付順で練習の1回目＝物差しの候補に入ってしまう）。日付の比べは時差のぶん1日の余裕を見る。
func (s *Server) checkRunSession(ctx context.Context, p *model.Plan, se *model.Session) error {
	trig, _ := jsonObject("trigger", p.Trigger)
	if v, ok := trig["session_id"]; ok {
		if n, ok := v.(json.Number); ok {
			if id, err := n.Int64(); err == nil && id == se.ID {
				return bad("このセッションはプランのきっかけにした診断のセッションです。練習は別の日のセッションに記録してください")
			}
		}
	}
	if d, err := time.Parse("2006-01-02", se.Date); err == nil && d.Before(p.CreatedAt.UTC().Truncate(24*time.Hour).AddDate(0, 0, -1)) {
		shots, err := s.Store.ListShots(ctx, se.ID)
		if err != nil {
			return err
		}
		if len(shots) > 0 {
			return bad("このセッション（%s）はプランを作る前の球です。練習は、プランを作ったあとのセッションに記録してください", se.Date)
		}
	}
	return nil
}

// assignment は run のブロックの境目。保存したものが無ければ、予定の球数で区切った仮の境目（planned）。
type assignment struct {
	Blocks []model.Block `json:"blocks"`
	// Source は saved（人が確かめて保存した）か planned（予定の球数で区切った仮のもの。保存していない）。
	Source string `json:"blocks_source"`
	// ClubSeqs は実験のクラブの球の番号（打った順。除外した球も打った球なので入れる）。
	ClubSeqs []int `json:"club_seqs"`
	// Planned は予定の球数の合計。取り込んだ球数（len(ClubSeqs)）と違えば Mismatch（画面は黄色）。
	Planned    int   `json:"planned"`
	Unassigned []int `json:"unassigned_seqs"`
	Mismatch   bool  `json:"mismatch"`
}

// planBlocks は、そのクラブの球を打った順に並べ、型の球数どおりに区切る（§8.4 の本線）。
// 球が足りなければ後ろの段は短くなるか無くなり、余った球は unassigned に残す。
func planBlocks(tpl []model.PlanBlock, counts []int, club string, shots []model.Shot) ([]model.Block, []int, []int) {
	var seqs []int
	for _, sh := range shots {
		if strings.EqualFold(sh.Club, club) {
			seqs = append(seqs, sh.Seq)
		}
	}
	sort.Ints(seqs)
	blocks := []model.Block{}
	i := 0
	for j, t := range tpl {
		n := 0
		if j < len(counts) {
			n = counts[j]
		}
		if n <= 0 || i >= len(seqs) {
			continue
		}
		end := min(i+n, len(seqs))
		blocks = append(blocks, model.Block{Kind: t.Kind, SeqFrom: seqs[i], SeqTo: seqs[end-1]})
		i = end
	}
	unassigned := append([]int{}, seqs[i:]...)
	if seqs == nil {
		seqs = []int{}
	}
	return blocks, seqs, unassigned
}

func sum(xs []int) int {
	t := 0
	for _, x := range xs {
		t += x
	}
	return t
}

// assign は run の境目を決める（保存があればそれ、無ければ予定の球数で区切る）。
func assign(p *model.Plan, run *model.PlanRun, e *model.Experiment, shots []model.Shot) assignment {
	planned, seqs, unassigned := planBlocks(p.Template, run.Counts, e.Club, shots)
	a := assignment{Blocks: planned, Source: "planned", ClubSeqs: seqs, Planned: sum(run.Counts), Unassigned: unassigned}
	if len(e.Blocks) > 0 {
		a.Blocks, a.Source = e.Blocks, "saved"
		in := map[int]bool{}
		for _, b := range e.Blocks {
			for _, q := range seqs {
				if q >= b.SeqFrom && q <= b.SeqTo {
					in[q] = true
				}
			}
		}
		a.Unassigned = []int{}
		for _, q := range seqs {
			if !in[q] {
				a.Unassigned = append(a.Unassigned, q)
			}
		}
	}
	a.Mismatch = a.Planned != len(seqs)
	return a
}

// runCtx は run の評価・表示に要るものをまとめて読む。
type runCtx struct {
	plan  *model.Plan
	run   *model.PlanRun
	exp   *model.Experiment
	shots []model.Shot
	asg   assignment
}

func (s *Server) loadRun(ctx context.Context, run *model.PlanRun, plan *model.Plan) (*runCtx, error) {
	if plan == nil {
		p, err := s.Store.GetPlan(ctx, run.PlanID)
		if err != nil {
			return nil, err
		}
		plan = p
	}
	e, err := s.Store.GetExperiment(ctx, run.ExperimentID)
	if err != nil {
		return nil, err
	}
	shots, err := s.Store.ListShots(ctx, run.SessionID)
	if err != nil {
		return nil, err
	}
	return &runCtx{plan: plan, run: run, exp: e, shots: shots, asg: assign(plan, run, e, shots)}, nil
}

// blockKinds はそのセッションの球の block_kind（§10.2）。そのセッションの plan_runs の実験の
// ブロックからだけ取り、実験のクラブに合う球にだけ付ける。プランの外で作った実験のブロックは使わない
// （診断から球が消えない）。run が無ければ空。
func (s *Server) blockKinds(ctx context.Context, sessionID int64, shots []model.Shot) (map[int64]model.BlockKind, error) {
	run, err := s.Store.PlanRunBySession(ctx, sessionID)
	if errors.Is(err, store.ErrNotFound) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	p, err := s.Store.GetPlan(ctx, run.PlanID)
	if err != nil {
		return nil, err
	}
	e, err := s.Store.GetExperiment(ctx, run.ExperimentID)
	if err != nil {
		return nil, err
	}
	a := assign(p, run, e, shots)
	out := map[int64]model.BlockKind{}
	for _, b := range a.Blocks {
		for _, sh := range shots {
			if sh.Seq >= b.SeqFrom && sh.Seq <= b.SeqTo && strings.EqualFold(sh.Club, e.Club) {
				out[sh.ID] = b.Kind
			}
		}
	}
	return out, nil
}

type runViewT struct {
	model.PlanRun
	Index      int               `json:"index"` // 0 始まりの練習の回（0 がプランの1回目）
	Plan       *model.Plan       `json:"plan"`
	Experiment *model.Experiment `json:"experiment"`
	Template   []model.PlanBlock `json:"template"`
	assignment
}

func (s *Server) runView(ctx context.Context, run *model.PlanRun) (*runViewT, error) {
	rc, err := s.loadRun(ctx, run, nil)
	if err != nil {
		return nil, err
	}
	runs, err := s.Store.ListPlanRuns(ctx, run.PlanID)
	if err != nil {
		return nil, err
	}
	idx := 0
	for i, x := range runs {
		if x.ID == run.ID {
			idx = i
		}
	}
	return &runViewT{PlanRun: *run, Index: idx, Plan: rc.plan, Experiment: rc.exp, Template: rc.plan.Template, assignment: rc.asg}, nil
}

func (s *Server) getPlanRun(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	run, err := s.Store.GetPlanRun(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	v, err := s.runView(r.Context(), run)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, v)
}

func (s *Server) sessionPlanRun(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	if _, err := s.Store.GetSession(r.Context(), id); err != nil {
		s.fail(w, err)
		return
	}
	run, err := s.Store.PlanRunBySession(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	v, err := s.runView(r.Context(), run)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, v)
}

type blockIn struct {
	Kind    model.BlockKind `json:"kind"`
	SeqFrom int             `json:"seq_from"`
	SeqTo   int             `json:"seq_to"`
}

func toBlocks(in []blockIn) ([]model.Block, error) {
	out := make([]model.Block, 0, len(in))
	for _, b := range in {
		out = append(out, model.Block{Kind: b.Kind, SeqFrom: b.SeqFrom, SeqTo: b.SeqTo})
	}
	if err := store.CheckBlocks(out); err != nil {
		return nil, bad("%v", err)
	}
	return out, nil
}

// replaceExperimentBlocks は PUT /v1/experiments/{id}/blocks: {blocks: [{kind, seq_from, seq_to}]}。
// ブロックを丸ごと差し替える（1トランザクション。空の配列で全部消す）。
func (s *Server) replaceExperimentBlocks(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	var in struct {
		Blocks []blockIn `json:"blocks"`
	}
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	if in.Blocks == nil {
		s.fail(w, bad("blocks が要ります（全部消すなら []）"))
		return
	}
	bs, err := toBlocks(in.Blocks)
	if err != nil {
		s.fail(w, err)
		return
	}
	if _, err := s.Store.ReplaceBlocks(r.Context(), id, bs); err != nil {
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

// replaceRunBlocks は PUT /v1/plan-runs/{id}/blocks。本文はどちらか1つ:
//
//	{counts: [n, …]}                      … 型の各段の球数（境目を1球ずつ動かす・打ち直しを足す）。
//	                                          そのクラブの球を打った順に区切り直して保存する
//	{blocks: [{kind, seq_from, seq_to}]}  … 境目をそのまま保存する
//
// どちらも前のブロックを消して作り直す（1トランザクション）。保存した評価は捨てる。
func (s *Server) replaceRunBlocks(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	var in struct {
		Counts []int     `json:"counts"`
		Blocks []blockIn `json:"blocks"`
	}
	if err := decode(r, &in); err != nil {
		s.fail(w, err)
		return
	}
	if (in.Counts == nil) == (in.Blocks == nil) {
		s.fail(w, bad("counts か blocks のどちらか1つを渡してください"))
		return
	}
	run, err := s.Store.GetPlanRun(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	p, err := s.Store.GetPlan(r.Context(), run.PlanID)
	if err != nil {
		s.fail(w, err)
		return
	}
	var bs []model.Block
	var counts []int
	if in.Counts != nil {
		if err := checkCounts(p.Template, in.Counts); err != nil {
			s.fail(w, err)
			return
		}
		shots, err := s.Store.ListShots(r.Context(), run.SessionID)
		if err != nil {
			s.fail(w, err)
			return
		}
		bs, _, _ = planBlocks(p.Template, in.Counts, p.Club, shots)
		counts = in.Counts
	} else {
		// 空の配列で「全部消す」は実験の PUT の意味。run では「予定の球数で区切った仮の境目」に黙って戻ってしまうので断る
		if len(in.Blocks) == 0 {
			s.fail(w, bad("blocks が空です。予定の球数で区切り直すなら counts を送ってください"))
			return
		}
		if bs, err = toBlocks(in.Blocks); err != nil {
			s.fail(w, err)
			return
		}
	}
	// 球数とブロックは1トランザクションで差し替える（片方だけ変わって残らないように。§10.2）。
	// この run とそれより後の run の保存した評価も捨てる（後の run はこの run の評価を物差しとして読む）
	if err := s.Store.ReplaceRunBlocks(r.Context(), id, counts, bs); err != nil {
		s.fail(w, err)
		return
	}
	run, err = s.Store.GetPlanRun(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	v, err := s.runView(r.Context(), run)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, v)
}

// ---- 評価 ----

func planPayload(p *model.Plan) analysis.PlanPayload {
	return analysis.PlanPayload{ID: p.ID, Issue: p.Issue, Lever: p.Lever, DrillID: p.DrillID, CatalogVersion: p.CatalogVersion,
		Club: p.Club, TargetMetric: p.TargetMetric, Goal: p.Goal, Template: p.Template, Params: p.Params, Trigger: p.Trigger,
		Rationale: p.Rationale, Status: p.Status, EngineVersion: p.EngineVersion, CreatedAt: p.CreatedAt}
}

// evalKey は評価の保存の鍵。「入力の指紋|分析サービスの版」。入力の指紋は球・境目・前の run の評価・Go の物理の版で
// 決まり、版は分析サービスの engine_version と plan_version（§8.5「入力の指紋と版と一緒に保存する」）。
// 判定の規則を変えて版を上げれば、保存した評価は次に読むときに作り直される（前は版が入らず、古い判定が出続けた）。
func evalKey(in analysis.PlanEvalInput, ver string) string {
	b, _ := json.Marshal(in)
	h := sha256.New()
	h.Write(b)
	h.Write([]byte("\x00" + physics.EngineVersion))
	return hex.EncodeToString(h.Sum(nil)) + "|" + ver
}

// keyMatches は保存した鍵が使えるか。版が分からない（分析サービスに届かない）ときは入力の指紋だけで見る
// （届かないなら作り直せないので、保存した評価を出すほうがよい）。
func keyMatches(saved, key, ver string) bool {
	if ver != "" {
		return saved == key
	}
	in := key[:strings.IndexByte(key, '|')+1]
	return strings.HasPrefix(saved, in)
}

// versionCache は分析サービスの版（/healthz）を数分だけ覚える（評価のたびに聞きに行かない）。
type versionCache struct {
	mu  sync.Mutex
	ver string
	at  time.Time
}

const versionTTL = 2 * time.Minute

// analysisVersion は分析サービスの版。届かなければ最後に分かった版（無ければ空）。
func (s *Server) analysisVersion(ctx context.Context) string {
	s.ver.mu.Lock()
	defer s.ver.mu.Unlock()
	if s.ver.ver != "" && time.Since(s.ver.at) < versionTTL {
		return s.ver.ver
	}
	cctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if v, err := s.Analyzer.Versions(cctx); err == nil && v != "" {
		s.ver.ver, s.ver.at = v, time.Now()
	}
	return s.ver.ver
}

// planBlockPayloads はプランの評価に渡すブロック。本人が除外した球も印（excluded）つきで送り、分析サービスが
// 数えて「除外した◯球を除く」と書けるようにする（R6。前は送る前に落としていて、除外の数がどこにも出なかった）。
func planBlockPayloads(club string, blocks []model.Block, shots []model.Shot) []analysis.BlockPayload {
	out := make([]analysis.BlockPayload, 0, len(blocks))
	for _, b := range blocks {
		bp := analysis.BlockPayload{Kind: b.Kind, SeqFrom: b.SeqFrom, SeqTo: b.SeqTo, Shots: []analysis.ShotPayload{}}
		for _, sh := range shots {
			if sh.Seq < b.SeqFrom || sh.Seq > b.SeqTo || (club != "" && !strings.EqualFold(sh.Club, club)) {
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

// continueAfter は「止める」のあと本人が「続ける」を選んだ run の id（rationale.continue_after_run）。無ければ nil。
func continueAfter(p *model.Plan) *int64 {
	rat, err := jsonObject("rationale", p.Rationale)
	if err != nil {
		return nil
	}
	if n, ok := rat["continue_after_run"].(json.Number); ok {
		if v, err := n.Int64(); err == nil && v > 0 {
			return &v
		}
	}
	return nil
}

type runEval struct {
	RunID      int64           `json:"run_id"`
	Index      int             `json:"index"`
	SessionID  int64           `json:"session_id"`
	Date       string          `json:"date"`
	Cached     bool            `json:"cached"`
	Fresh      bool            `json:"fresh"` // false: 分析サービスに届かず、保存してあった評価（古いかもしれない）
	Source     string          `json:"blocks_source"`
	Evaluation json.RawMessage `json:"evaluation"`
}

// evaluateChain はプランの run を練習の順に upto 番目まで評価する。前の run の評価は次の run の
// 入力（past_runs）になるので、前から順に見て、入力の指紋が変わったものだけ分析サービスを呼ぶ。
// 分析サービスに届かないとき、offline なら保存してある評価で埋めて返す（fresh=false）。
func (s *Server) evaluateChain(ctx context.Context, p *model.Plan, runs []model.PlanRun, upto int, refresh, offline bool) ([]runEval, error) {
	out := make([]runEval, 0, upto+1)
	past := []analysis.PastRunPayload{}
	down := false
	pl, err := s.Store.GetPlayer(ctx, p.PlayerID)
	if err != nil {
		return nil, err
	}
	hist, err := s.planHistory(ctx, p.PlayerID, p.ID)
	if err != nil {
		return nil, err
	}
	ver := s.analysisVersion(ctx)
	cont := continueAfter(p)
	for i := 0; i <= upto && i < len(runs); i++ {
		run := runs[i]
		rc, err := s.loadRun(ctx, &run, p)
		if err != nil {
			return nil, err
		}
		in := analysis.PlanEvalInput{
			Plan: planPayload(p),
			Run: analysis.RunPayload{ID: run.ID, Index: i, SessionID: run.SessionID, Date: run.SessionDate, Counts: run.Counts,
				BlocksSource: rc.asg.Source, ClubShots: len(rc.asg.ClubSeqs), Planned: rc.asg.Planned},
			Experiment: analysis.ExperimentPayload{TargetMetric: rc.exp.TargetMetric, Goal: rc.exp.Goal,
				Blocks: planBlockPayloads(rc.exp.Club, rc.asg.Blocks, rc.shots)},
			PastRuns:         append([]analysis.PastRunPayload{}, past...),
			Handedness:       pl.Handedness,
			History:          hist,
			ContinueAfterRun: cont,
		}
		key := evalKey(in, ver)
		ev := runEval{RunID: run.ID, Index: i, SessionID: run.SessionID, Date: run.SessionDate, Source: rc.asg.Source, Fresh: true}
		switch {
		case !refresh && keyMatches(run.EvaluationKey, key, ver) && string(run.Evaluation) != "null":
			ev.Cached, ev.Evaluation = true, run.Evaluation
		case down:
			ev.Fresh, ev.Evaluation = false, run.Evaluation
		default:
			raw, err := s.Analyzer.PlanEvaluate(ctx, in)
			if err != nil {
				if offline && errors.Is(err, analysis.ErrUnavailable) {
					down = true
					ev.Fresh, ev.Evaluation = false, run.Evaluation
					break
				}
				return nil, err
			}
			// 要求が途中で切れても払った計算を捨てないよう、保存は要求の ctx と切り離す
			if err := s.Store.SavePlanRunEvaluation(context.WithoutCancel(ctx), run.ID, key, raw); err != nil {
				return nil, err
			}
			ev.Evaluation = raw
		}
		out = append(out, ev)
		past = append(past, analysis.PastRunPayload{RunID: run.ID, Index: i, SessionID: run.SessionID, Date: run.SessionDate, Evaluation: ev.Evaluation})
	}
	return out, nil
}

// evalSummary は保存した評価（分析サービスの JSON）のうち、記録と履歴に使うところ。
// Go は数字を作らない。分析サービスが書いた判定と状態を読むだけ。
type evalSummary struct {
	Grade          string `json:"grade"`
	CountsAsWorked bool   `json:"counts_as_worked"`
	Progress       *struct {
		State  string          `json:"state"`
		Title  string          `json:"title"`
		Record json.RawMessage `json:"record"`
	} `json:"progress"`
}

func summarize(raw json.RawMessage) *evalSummary {
	if len(raw) == 0 || string(raw) == "null" {
		return nil
	}
	var e evalSummary
	if json.Unmarshal(raw, &e) != nil {
		return nil
	}
	return &e
}

// planHistory はこの選手のほかのプランの要約（最後に保存した評価の状態）。「詰まった」の判定に使う（§8.6）。
func (s *Server) planHistory(ctx context.Context, playerID, except int64) ([]analysis.HistoryItem, error) {
	ps, err := s.Store.ListPlans(ctx, playerID)
	if err != nil {
		return nil, err
	}
	out := []analysis.HistoryItem{}
	for _, p := range ps {
		if p.ID == except {
			continue
		}
		runs, err := s.Store.ListPlanRuns(ctx, p.ID)
		if err != nil {
			return nil, err
		}
		state := ""
		for i := len(runs) - 1; i >= 0; i-- {
			if e := summarize(runs[i].Evaluation); e != nil && e.Progress != nil {
				state = e.Progress.State
				break
			}
		}
		if state == "" {
			continue
		}
		out = append(out, analysis.HistoryItem{PlanID: p.ID, Issue: p.Issue, DrillID: p.DrillID, State: state})
	}
	return out, nil
}

// playerRecord は GET /v1/players/{id}/record。「あなたの記録」（§8.6）: プランごとに、練習の回と
// 保存した判定を並べる。件数だけで出し、割合は出さない（確率のふりになる）。保存した評価を読むだけで、
// 分析サービスは呼ばない（練習場の電波が弱くても見られる）。
func (s *Server) playerRecord(w http.ResponseWriter, r *http.Request) {
	pid, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	if _, err := s.Store.GetPlayer(r.Context(), pid); err != nil {
		s.fail(w, err)
		return
	}
	ps, err := s.Store.ListPlans(r.Context(), pid)
	if err != nil {
		s.fail(w, err)
		return
	}
	type runRow struct {
		RunID          int64  `json:"run_id"`
		SessionID      int64  `json:"session_id"`
		Date           string `json:"date"`
		Evaluated      bool   `json:"evaluated"`
		Grade          string `json:"grade,omitempty"`
		CountsAsWorked bool   `json:"counts_as_worked"`
	}
	type planRow struct {
		Plan       model.Plan      `json:"plan"`
		Runs       []runRow        `json:"runs"`
		State      string          `json:"state,omitempty"`
		StateTitle string          `json:"state_title,omitempty"`
		Record     json.RawMessage `json:"record,omitempty"`
	}
	out := []planRow{}
	for _, p := range ps {
		runs, err := s.Store.ListPlanRuns(r.Context(), p.ID)
		if err != nil {
			s.fail(w, err)
			return
		}
		row := planRow{Plan: p, Runs: []runRow{}}
		for _, x := range runs {
			rr := runRow{RunID: x.ID, SessionID: x.SessionID, Date: x.SessionDate}
			if e := summarize(x.Evaluation); e != nil {
				rr.Evaluated, rr.Grade, rr.CountsAsWorked = true, e.Grade, e.CountsAsWorked
				if e.Progress != nil {
					row.State, row.StateTitle, row.Record = e.Progress.State, e.Progress.Title, e.Progress.Record
				}
			}
			row.Runs = append(row.Runs, rr)
		}
		out = append(out, row)
	}
	writeJSON(w, http.StatusOK, out)
}

// evaluatePlanRun は GET /v1/plan-runs/{id}/evaluation。?refresh=1 で保存を使わずに作り直す。
func (s *Server) evaluatePlanRun(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	run, err := s.Store.GetPlanRun(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	p, err := s.Store.GetPlan(r.Context(), run.PlanID)
	if err != nil {
		s.fail(w, err)
		return
	}
	runs, err := s.Store.ListPlanRuns(r.Context(), p.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	upto := 0
	for i, x := range runs {
		if x.ID == id {
			upto = i
		}
	}
	evs, err := s.evaluateChain(r.Context(), p, runs, upto, r.URL.Query().Get("refresh") == "1", false)
	if err != nil {
		s.fail(w, err)
		return
	}
	last := evs[len(evs)-1]
	writeJSON(w, http.StatusOK, map[string]any{"plan_id": p.ID, "run_id": id, "index": last.Index, "cached": last.Cached,
		"blocks_source": last.Source, "evaluation": last.Evaluation})
}

// planProgress は GET /v1/plans/{id}/progress。run ごとの評価を練習の順に並べる（推移と状態）。
// 状態（効いた・定着した…）は評価の中身（分析サービス）が持つ。分析サービスに届かなければ、
// 保存してある評価で返す（fresh=false。練習場の電波が弱くても進捗は見られる）。
func (s *Server) planProgress(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	p, err := s.Store.GetPlan(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	runs, err := s.Store.ListPlanRuns(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	if p.Motion != nil {
		prog, err := s.motionProgress(r.Context(), p)
		if err != nil {
			s.fail(w, err)
			return
		}
		tests, err := s.Store.ListFocusTests(r.Context(), p.PlayerID, p.Motion.ItemID, p.ID)
		if err != nil {
			s.fail(w, err)
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"plan": p, "runs": []any{}, "latest": nil, "motion": prog, "tests": tests})
		return
	}
	evs := []runEval{}
	if len(runs) > 0 {
		if evs, err = s.evaluateChain(r.Context(), p, runs, len(runs)-1, r.URL.Query().Get("refresh") == "1", true); err != nil {
			s.fail(w, err)
			return
		}
	}
	var latest json.RawMessage = json.RawMessage("null")
	if len(evs) > 0 {
		latest = evs[len(evs)-1].Evaluation
	}
	writeJSON(w, http.StatusOK, map[string]any{"plan": p, "runs": evs, "latest": latest})
}

// ---- 今日の練習 ----

// today は GET /v1/players/{id}/today。今日の練習の1画面ぶん（圏外用に丸ごと localStorage へ写せる大きさ）。
// 動いているプランが無ければ plan は null（画面は候補を出す）。
func (s *Server) today(w http.ResponseWriter, r *http.Request) {
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
	out := map[string]any{"player_id": pid, "handedness": pl.Handedness, "plan": nil, "runs": []any{}, "last_evaluation": nil}
	p, err := s.Store.ActivePlan(r.Context(), pid)
	if errors.Is(err, store.ErrNotFound) {
		writeJSON(w, http.StatusOK, out)
		return
	}
	if err != nil {
		s.fail(w, err)
		return
	}
	runs, err := s.Store.ListPlanRuns(r.Context(), p.ID)
	if err != nil {
		s.fail(w, err)
		return
	}
	// 分析サービスの版が変わっていれば（判定の規則を直した）、保存した評価を作り直してから出す（§8.5）。
	// 届かなければ保存したまま（圏外・分析サービスが寝ていても今日の練習は出す）
	if ver := s.analysisVersion(r.Context()); ver != "" && staleRuns(runs, ver) {
		if _, err := s.evaluateChain(r.Context(), p, runs, len(runs)-1, false, true); err == nil {
			if again, err := s.Store.ListPlanRuns(r.Context(), p.ID); err == nil {
				runs = again
			}
		}
	}
	type runSummary struct {
		ID        int64  `json:"id"`
		Index     int    `json:"index"`
		SessionID int64  `json:"session_id"`
		Date      string `json:"date"`
		Counts    []int  `json:"counts"`
		Evaluated bool   `json:"evaluated"`
	}
	rs := make([]runSummary, 0, len(runs))
	var last json.RawMessage
	for i, x := range runs {
		ev := string(x.Evaluation) != "null"
		rs = append(rs, runSummary{ID: x.ID, Index: i, SessionID: x.SessionID, Date: x.SessionDate, Counts: x.Counts, Evaluated: ev})
		if ev {
			last = x.Evaluation
		}
	}
	out["plan"], out["runs"], out["next_index"], out["next_counts"] = p, rs, len(runs), templateCounts(p.Template)
	if p.Motion != nil {
		// 動きのプラン: 状態と10球テストの記録（分析サービスが寝ていても、記録だけは出す）
		m := map[string]any{"test_block": motionTestBlock}
		if tests, err := s.Store.ListFocusTests(r.Context(), pid, p.Motion.ItemID, p.ID); err == nil {
			for i := range tests {
				tests[i].Result = nil
			}
			m["tests"] = tests
		}
		if prog, err := s.motionProgress(r.Context(), p); err == nil {
			m["progress"] = prog
		}
		out["motion"] = m
	}
	if last != nil {
		out["last_evaluation"] = last
		// 「足りない」のとき分析サービスが出した次の型の球数（§8.6「次の型の球数を増やす」）。型と同じ段の数のときだけ
		var e struct {
			Progress *struct {
				NextCounts []int `json:"next_counts"`
			} `json:"progress"`
		}
		if json.Unmarshal(last, &e) == nil && e.Progress != nil && len(e.Progress.NextCounts) == len(p.Template) && checkCounts(p.Template, e.Progress.NextCounts) == nil {
			out["next_counts"] = e.Progress.NextCounts
			out["next_counts_reason"] = "more_shots"
		}
	}
	writeJSON(w, http.StatusOK, out)
}

// staleRuns は、保存した評価のうち今の分析サービスの版で作っていないものがあるか。
func staleRuns(runs []model.PlanRun, ver string) bool {
	for _, x := range runs {
		if string(x.Evaluation) != "null" && len(x.Evaluation) > 0 && !strings.HasSuffix(x.EvaluationKey, "|"+ver) {
			return true
		}
	}
	return false
}

// continuePlan は POST /v1/plans/{id}/continue。「止める」と出たあと、本人が「続ける」を選んだ（§8.6）。
// その時点で最後の run の id を rationale.continue_after_run に書き、崩れはそれより後の回だけ数える。
func (s *Server) continuePlan(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	p, err := s.Store.GetPlan(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	if p.Status != model.PlanActive {
		s.fail(w, conflict("このプランは動いていません（状態 "+string(p.Status)+"）"))
		return
	}
	runs, err := s.Store.ListPlanRuns(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	if len(runs) == 0 {
		s.fail(w, bad("まだ練習がありません"))
		return
	}
	rat, err := jsonObject("rationale", p.Rationale)
	if err != nil {
		s.fail(w, err)
		return
	}
	rat["continue_after_run"] = runs[len(runs)-1].ID
	b, _ := json.Marshal(rat)
	np, err := s.Store.SetPlanRationale(r.Context(), id, b)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, np)
}

// planCandidates は GET /v1/players/{id}/plan-candidates?session=。候補（いま・次）と、候補ごとに勧めるドリル
// （確かめ済みだけ）を分析サービスに頼む（§10.2）。この選手のプランと練習の記録を渡すので、
//   - 「詰まった」で閉じた issue は「いま」「次」から外れて後ろへ回る（§8.6「issue を blocked にして次へ」）
//   - 記録のあるドリルが先に来て、前回の判定（効いたに数えたか・日付）が付く（§8.2・§8.6）
func (s *Server) planCandidates(w http.ResponseWriter, r *http.Request) {
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
	var sid int64
	if _, err := fmt.Sscan(r.URL.Query().Get("session"), &sid); err != nil || sid <= 0 {
		s.fail(w, bad("session（候補を作る診断のセッション）が要ります"))
		return
	}
	se, err := s.Store.GetSession(r.Context(), sid)
	if errors.Is(err, store.ErrNotFound) {
		s.fail(w, bad("session %d が見つかりません", sid))
		return
	}
	if err != nil {
		s.fail(w, err)
		return
	}
	if se.PlayerID != pid {
		s.fail(w, bad("別の選手のセッションです"))
		return
	}
	shots, err := s.Store.ListShots(r.Context(), sid)
	if err != nil {
		s.fail(w, err)
		return
	}
	ps, err := s.sessionPayloads(r.Context(), sid, shots)
	if err != nil {
		s.fail(w, err)
		return
	}
	plans, err := s.Store.ListPlans(r.Context(), pid)
	if err != nil {
		s.fail(w, err)
		return
	}
	history, prev := []map[string]any{}, []map[string]any{}
	for _, p := range plans {
		runs, err := s.Store.ListPlanRuns(r.Context(), p.ID)
		if err != nil {
			s.fail(w, err)
			return
		}
		state := ""
		for _, x := range runs {
			e := summarize(x.Evaluation)
			if e == nil {
				continue
			}
			if e.Progress != nil {
				state = e.Progress.State
			}
			if p.DrillID != "" && e.Grade != "" {
				history = append(history, map[string]any{"plan_id": p.ID, "drill_id": p.DrillID, "issue": p.Issue, "grade": e.Grade,
					"counts_as_worked": e.CountsAsWorked, "date": x.SessionDate})
			}
		}
		row := map[string]any{"plan_id": p.ID, "issue": p.Issue, "drill_id": p.DrillID, "status": string(p.Status), "state": state,
			"created_at": p.CreatedAt.Format(time.RFC3339)}
		if p.ClosedAt != nil {
			row["closed_at"] = p.ClosedAt.Format(time.RFC3339)
		}
		prev = append(prev, row)
	}
	raw, err := s.Analyzer.PlanCandidates(r.Context(), analysis.PlanCandidatesInput{Shots: ps, Handedness: pl.Handedness, History: history, Plans: prev})
	if err != nil {
		s.fail(w, err)
		return
	}
	// ドリルは Go でももう一度、人が確かめたものだけに絞る（二重に守る）
	raw, err = checkedCandidateDrills(raw)
	if err != nil {
		s.fail(w, errors.New("分析サービスの候補の形を読めません"))
		return
	}
	writeRaw(w, http.StatusOK, raw)
}

// checkedCandidateDrills は候補の drills.drills から checked_by の無いものを落とす。
func checkedCandidateDrills(raw json.RawMessage) (json.RawMessage, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v map[string]any
	if err := dec.Decode(&v); err != nil || v == nil {
		return nil, errors.New("形が違います")
	}
	scopes, _ := v["scopes"].([]any)
	for _, x := range scopes {
		sc, _ := x.(map[string]any)
		cs, _ := sc["candidates"].(map[string]any)
		list, _ := cs["candidates"].([]any)
		for _, y := range list {
			c, _ := y.(map[string]any)
			dd, _ := c["drills"].(map[string]any)
			ds, _ := dd["drills"].([]any)
			kept := []any{}
			for _, z := range ds {
				d, _ := z.(map[string]any)
				if cb, ok := d["checked_by"].(string); ok && strings.TrimSpace(cb) != "" {
					kept = append(kept, d)
				}
			}
			if dd != nil {
				dd["drills"] = kept
			}
		}
	}
	return json.Marshal(v)
}
