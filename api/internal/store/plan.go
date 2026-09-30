package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
)

// プランと練習の日ごとの run（docs/DESIGN_coaching.md §8・§10.3）。

// ErrConflict は「同時には1つしか持てないもの」がすでにある（動いているプラン・そのセッションの run）。
// HTTP では 409 にする。
var ErrConflict = errors.New("conflict")

// ConflictError は理由つきの ErrConflict。Msg は利用者に見せてよい文。
type ConflictError struct {
	Msg        string
	ExistingID int64 // ぶつかった相手の id（動いているプラン・そのセッションの run）
}

func (e *ConflictError) Error() string { return e.Msg }
func (e *ConflictError) Unwrap() error { return ErrConflict }

// isUniqueViolation は一意の索引に当たったか（SQLite と PostgreSQL の両方）。
// 先に SELECT で確かめてから入れるが、同時に2本来たときの最後の守りは索引なので、ここでも 409 にする。
func isUniqueViolation(err error) bool {
	if err == nil {
		return false
	}
	s := err.Error()
	return strings.Contains(s, "UNIQUE constraint failed") || strings.Contains(s, "SQLSTATE 23505") || strings.Contains(s, "duplicate key value")
}

func rawOrEmpty(r json.RawMessage) string {
	if len(r) == 0 || string(r) == "null" {
		return "{}"
	}
	return string(r)
}

func nullString(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// activePlanID は選手の動いているプランの id（無ければ 0）。
func activePlanID(ctx context.Context, q interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
}, playerID int64) (int64, error) {
	var id int64
	err := q.QueryRowContext(ctx, `SELECT id FROM {s}plans WHERE player_id=? AND status='active' ORDER BY id LIMIT 1`, playerID).Scan(&id)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, nil
	}
	return id, err
}

func conflictActive(id int64) error {
	return &ConflictError{Msg: fmt.Sprintf("動いているプラン（id %d）があります。動かせるプランは1つだけです（?replace=1 で前のプランを「切り替えた」にして作れます）", id), ExistingID: id}
}

// CreatePlan はプランを作る（状態は active）。動いているプランがあれば ConflictError。
// replace なら、前のプランを switched にしてから作る（1トランザクション）。
func (s *Store) CreatePlan(ctx context.Context, p *model.Plan, replace bool) error {
	if _, err := s.GetPlayer(ctx, p.PlayerID); err != nil {
		return err
	}
	tpl, err := json.Marshal(p.Template)
	if err != nil {
		return err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	cur, err := activePlanID(ctx, tx, p.PlayerID)
	if err != nil {
		return err
	}
	ts := now()
	if cur != 0 {
		if !replace {
			return conflictActive(cur)
		}
		if _, err := tx.ExecContext(ctx, `UPDATE {s}plans SET status='switched', closed_at=?, close_reason=? WHERE id=?`, ts, "別のプランに切り替えた", cur); err != nil {
			return err
		}
	}
	p.Status = model.PlanActive
	err = tx.QueryRowContext(ctx, `INSERT INTO {s}plans(player_id, issue, lever, drill_id, catalog_version, club, target_metric, goal, hypothesis, cue,
		template_json, params_json, trigger_json, rationale_json, status, engine_version, created_at)
		VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id`,
		p.PlayerID, p.Issue, nullString(p.Lever), p.DrillID, p.CatalogVersion, p.Club, p.TargetMetric, string(p.Goal), p.Hypothesis, p.Cue,
		string(tpl), rawOrEmpty(p.Params), rawOrEmpty(p.Trigger), rawOrEmpty(p.Rationale), string(p.Status), p.EngineVersion, ts).Scan(&p.ID)
	if err == nil && p.Motion != nil {
		m := p.Motion
		_, err = tx.ExecContext(ctx, `INSERT INTO {s}motion_plans(plan_id, cp_item_id, cp_catalog_version, view, checkpoint, created_at) VALUES(?,?,?,?,?,?)`,
			p.ID, m.ItemID, m.CatalogVersion, m.View, m.Checkpoint, ts)
	}
	if err == nil {
		err = tx.Commit()
	}
	if isUniqueViolation(err) {
		_ = tx.Rollback() // SQLite は接続1本なので、閉じてから読む
		id, _ := activePlanID(ctx, s.db, p.PlayerID)
		return conflictActive(id)
	}
	if err != nil {
		return err
	}
	p.CreatedAt = parseTS(ts)
	p.Kind = "ball"
	if p.Motion != nil {
		p.Kind = "motion"
	}
	p.Params, p.Trigger, p.Rationale = json.RawMessage(rawOrEmpty(p.Params)), json.RawMessage(rawOrEmpty(p.Trigger)), json.RawMessage(rawOrEmpty(p.Rationale))
	return nil
}

const planCols = `id, player_id, issue, lever, drill_id, catalog_version, club, target_metric, goal, hypothesis, cue,
	template_json, params_json, trigger_json, rationale_json, status, engine_version, created_at, closed_at, close_reason,
	(SELECT cp_item_id FROM {s}motion_plans m WHERE m.plan_id = {s}plans.id), (SELECT cp_catalog_version FROM {s}motion_plans m WHERE m.plan_id = {s}plans.id),
	(SELECT view FROM {s}motion_plans m WHERE m.plan_id = {s}plans.id), (SELECT checkpoint FROM {s}motion_plans m WHERE m.plan_id = {s}plans.id)`

func scanPlan(sc interface{ Scan(...any) error }) (model.Plan, error) {
	var p model.Plan
	var lever, closed, reason, mItem, mVer, mView, mP sql.NullString
	var tpl, params, trig, rat, ts string
	if err := sc.Scan(&p.ID, &p.PlayerID, &p.Issue, &lever, &p.DrillID, &p.CatalogVersion, &p.Club, &p.TargetMetric, &p.Goal, &p.Hypothesis, &p.Cue,
		&tpl, &params, &trig, &rat, &p.Status, &p.EngineVersion, &ts, &closed, &reason, &mItem, &mVer, &mView, &mP); err != nil {
		return p, err
	}
	p.Kind = "ball"
	if mItem.Valid {
		p.Kind = "motion"
		p.Motion = &model.MotionPlan{ItemID: mItem.String, CatalogVersion: mVer.String, View: mView.String, Checkpoint: mP.String}
	}
	p.Lever, p.CloseReason = lever.String, reason.String
	if err := json.Unmarshal([]byte(tpl), &p.Template); err != nil {
		return p, fmt.Errorf("plan %d の template_json が壊れています: %w", p.ID, err)
	}
	p.Params, p.Trigger, p.Rationale = json.RawMessage(params), json.RawMessage(trig), json.RawMessage(rat)
	p.CreatedAt = parseTS(ts)
	if closed.Valid {
		t := parseTS(closed.String)
		p.ClosedAt = &t
	}
	return p, nil
}

// GetPlan はプランを読む。
func (s *Store) GetPlan(ctx context.Context, id int64) (*model.Plan, error) {
	p, err := scanPlan(s.db.QueryRowContext(ctx, `SELECT `+planCols+` FROM {s}plans WHERE id=?`, id))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return &p, nil
}

// ListPlans は選手のプランを新しい順に返す。
func (s *Store) ListPlans(ctx context.Context, playerID int64) ([]model.Plan, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT `+planCols+` FROM {s}plans WHERE player_id=? ORDER BY id DESC`, playerID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []model.Plan{}
	for rows.Next() {
		p, err := scanPlan(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, rows.Err()
}

// ActivePlan は選手の動いているプラン。無ければ ErrNotFound。
func (s *Store) ActivePlan(ctx context.Context, playerID int64) (*model.Plan, error) {
	id, err := activePlanID(ctx, s.db, playerID)
	if err != nil {
		return nil, err
	}
	if id == 0 {
		return nil, ErrNotFound
	}
	return s.GetPlan(ctx, id)
}

// SetPlanStatus はプランの状態を変える。active 以外にするときは closed_at を入れ、
// active に戻すときは closed_at と理由を消す（ほかに動いているプランがあれば ConflictError）。
func (s *Store) SetPlanStatus(ctx context.Context, id int64, st model.PlanStatus, reason string) (*model.Plan, error) {
	if !st.Valid() {
		return nil, fmt.Errorf("status %q は使えません", st)
	}
	p, err := s.GetPlan(ctx, id)
	if err != nil {
		return nil, err
	}
	var res sql.Result
	if st == model.PlanActive {
		if p.Status != model.PlanActive {
			cur, err := activePlanID(ctx, s.db, p.PlayerID)
			if err != nil {
				return nil, err
			}
			if cur != 0 && cur != id {
				return nil, conflictActive(cur)
			}
		}
		res, err = s.db.ExecContext(ctx, `UPDATE {s}plans SET status='active', closed_at=NULL, close_reason=NULL WHERE id=?`, id)
	} else {
		res, err = s.db.ExecContext(ctx, `UPDATE {s}plans SET status=?, closed_at=?, close_reason=? WHERE id=?`, string(st), now(), nullString(reason), id)
	}
	if isUniqueViolation(err) {
		cur, _ := activePlanID(ctx, s.db, p.PlayerID)
		return nil, conflictActive(cur)
	}
	if err != nil {
		return nil, err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return nil, ErrNotFound
	}
	return s.GetPlan(ctx, id)
}

// ---- plan_runs ----

func conflictRun(id int64) error {
	return &ConflictError{Msg: fmt.Sprintf("このセッションにはもうプランの練習（run %d）があります。1つのセッションに1つまでです", id), ExistingID: id}
}

func runIDBySession(ctx context.Context, q interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
}, sessionID int64) (int64, error) {
	var id int64
	err := q.QueryRowContext(ctx, `SELECT id FROM {s}plan_runs WHERE session_id=?`, sessionID).Scan(&id)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, nil
	}
	return id, err
}

// CreatePlanRun はその日の run を作る。既存の Experiment を1つ作って紐づける（1トランザクション）。
// e は SessionID・Hypothesis・Intervention・TargetMetric・Goal・Club を入れて渡す。
// ブロックはここでは作らない（取り込んだあと、予定の球数で区切るか PUT で入れる）。
func (s *Store) CreatePlanRun(ctx context.Context, planID int64, e *model.Experiment, counts []int) (*model.PlanRun, error) {
	if !e.Goal.Valid() {
		return nil, fmt.Errorf("goal %q は使えません", e.Goal)
	}
	se, err := s.GetSession(ctx, e.SessionID)
	if err != nil {
		return nil, err
	}
	if counts == nil {
		counts = []int{}
	}
	cj, _ := json.Marshal(counts)
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	if id, err := runIDBySession(ctx, tx, e.SessionID); err != nil {
		return nil, err
	} else if id != 0 {
		return nil, conflictRun(id)
	}
	ts := now()
	if err := tx.QueryRowContext(ctx, `INSERT INTO {s}experiments(session_id, hypothesis, intervention, target_metric, goal, club, created_at) VALUES(?,?,?,?,?,?,?) RETURNING id`,
		e.SessionID, e.Hypothesis, e.Intervention, e.TargetMetric, string(e.Goal), e.Club, ts).Scan(&e.ID); err != nil {
		return nil, err
	}
	e.CreatedAt, e.Blocks = parseTS(ts), []model.Block{}
	run := &model.PlanRun{PlanID: planID, SessionID: e.SessionID, SessionDate: se.Date, ExperimentID: e.ID, Counts: counts, Evaluation: json.RawMessage("null")}
	err = tx.QueryRowContext(ctx, `INSERT INTO {s}plan_runs(plan_id, session_id, experiment_id, counts_json, created_at) VALUES(?,?,?,?,?) RETURNING id`,
		planID, e.SessionID, e.ID, string(cj), ts).Scan(&run.ID)
	if err == nil {
		err = tx.Commit()
	}
	if isUniqueViolation(err) {
		_ = tx.Rollback() // SQLite は接続1本なので、閉じてから読む
		id, _ := runIDBySession(ctx, s.db, e.SessionID)
		return nil, conflictRun(id)
	}
	if err != nil {
		return nil, err
	}
	run.CreatedAt = parseTS(ts)
	return run, nil
}

const runCols = `r.id, r.plan_id, r.session_id, se.date, r.experiment_id, r.counts_json, r.evaluation_json, r.evaluation_key, r.created_at`

func scanRun(sc interface{ Scan(...any) error }) (model.PlanRun, error) {
	var r model.PlanRun
	var cj, ts string
	var ev, key sql.NullString
	if err := sc.Scan(&r.ID, &r.PlanID, &r.SessionID, &r.SessionDate, &r.ExperimentID, &cj, &ev, &key, &ts); err != nil {
		return r, err
	}
	if err := json.Unmarshal([]byte(cj), &r.Counts); err != nil {
		return r, fmt.Errorf("plan_run %d の counts_json が壊れています: %w", r.ID, err)
	}
	if r.Counts == nil {
		r.Counts = []int{}
	}
	r.Evaluation = json.RawMessage("null")
	if ev.Valid && ev.String != "" {
		r.Evaluation = json.RawMessage(ev.String)
	}
	r.EvaluationKey = key.String
	r.CreatedAt = parseTS(ts)
	return r, nil
}

// GetPlanRun は run を読む。
func (s *Store) GetPlanRun(ctx context.Context, id int64) (*model.PlanRun, error) {
	r, err := scanRun(s.db.QueryRowContext(ctx, `SELECT `+runCols+` FROM {s}plan_runs r JOIN {s}sessions se ON se.id = r.session_id WHERE r.id=?`, id))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return &r, nil
}

// PlanRunBySession はセッションの run（無ければ ErrNotFound）。
func (s *Store) PlanRunBySession(ctx context.Context, sessionID int64) (*model.PlanRun, error) {
	r, err := scanRun(s.db.QueryRowContext(ctx, `SELECT `+runCols+` FROM {s}plan_runs r JOIN {s}sessions se ON se.id = r.session_id WHERE r.session_id=?`, sessionID))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return &r, nil
}

// ListPlanRuns はプランの run を練習の順（セッションの日付 → 作った順）に返す。
// 1件目の評価の A がプランの物差し（§8.6）。
func (s *Store) ListPlanRuns(ctx context.Context, planID int64) ([]model.PlanRun, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT `+runCols+` FROM {s}plan_runs r JOIN {s}sessions se ON se.id = r.session_id WHERE r.plan_id=? ORDER BY se.date, r.id`, planID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []model.PlanRun{}
	for rows.Next() {
		r, err := scanRun(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// SetPlanRunCounts は予定の球数を書き換える。保存した評価は捨てる（入力が変わるので）。
func (s *Store) SetPlanRunCounts(ctx context.Context, id int64, counts []int) error {
	cj, _ := json.Marshal(counts)
	res, err := s.db.ExecContext(ctx, `UPDATE {s}plan_runs SET counts_json=?, evaluation_json=NULL, evaluation_key=NULL WHERE id=?`, string(cj), id)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	return nil
}

// SavePlanRunEvaluation は評価を入力の指紋（key）と一緒に保存する。
// 同じ指紋なら次からは分析サービスを呼ばない（CPU 0.1 では評価に数秒かかる。§8.5）。
func (s *Store) SavePlanRunEvaluation(ctx context.Context, id int64, key string, evaluation json.RawMessage) error {
	res, err := s.db.ExecContext(ctx, `UPDATE {s}plan_runs SET evaluation_json=?, evaluation_key=? WHERE id=?`, string(evaluation), key, id)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	return nil
}

// ---- ブロックの置き換え ----

// ReplaceBlocks は実験のブロックを丸ごと差し替える（1トランザクション。§8.4 の境目の直し）。
// 範囲の重なりもここで見る（途中まで消えて半端に残らないように）。
func (s *Store) ReplaceBlocks(ctx context.Context, experimentID int64, blocks []model.Block) ([]model.Block, error) {
	if err := CheckBlocks(blocks); err != nil {
		return nil, err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	var one int
	if err := tx.QueryRowContext(ctx, `SELECT 1 FROM {s}experiments WHERE id=?`, experimentID).Scan(&one); errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	} else if err != nil {
		return nil, err
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM {s}blocks WHERE experiment_id=?`, experimentID); err != nil {
		return nil, err
	}
	out := make([]model.Block, 0, len(blocks))
	for _, b := range blocks {
		b.ExperimentID = experimentID
		if err := tx.QueryRowContext(ctx, `INSERT INTO {s}blocks(experiment_id, kind, seq_from, seq_to) VALUES(?,?,?,?) RETURNING id`, experimentID, string(b.Kind), b.SeqFrom, b.SeqTo).Scan(&b.ID); err != nil {
			return nil, err
		}
		out = append(out, b)
	}
	// 境目が動けば評価の入力が変わる。保存した評価は捨てる（指紋でも見分けるが、古い評価を残さない）
	if _, err := tx.ExecContext(ctx, `UPDATE {s}plan_runs SET evaluation_json=NULL, evaluation_key=NULL WHERE experiment_id=?`, experimentID); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	return out, nil
}

// CheckBlocks はブロックの種類と範囲（1以上・重ならない）を見る。
func CheckBlocks(blocks []model.Block) error {
	for i, b := range blocks {
		if !b.Kind.Valid() {
			return fmt.Errorf("kind %q は使えません（warmup / baseline / drill / intervention / retention）", b.Kind)
		}
		if b.SeqFrom < 1 || b.SeqTo < b.SeqFrom {
			return fmt.Errorf("seq の範囲が不正です（%d〜%d）", b.SeqFrom, b.SeqTo)
		}
		for _, o := range blocks[:i] {
			if b.SeqFrom <= o.SeqTo && o.SeqFrom <= b.SeqTo {
				return fmt.Errorf("ブロックの範囲が重なっています（%s %d〜%d と %s %d〜%d）", o.Kind, o.SeqFrom, o.SeqTo, b.Kind, b.SeqFrom, b.SeqTo)
			}
		}
	}
	return nil
}

// SetPlanRationale はプランの rationale_json を書き換える（「止める」のあと本人が「続ける」を選んだ run の id など）。
// params は作った時点で固定する約束なので触らない。rationale は判定の材料ではなく、本人の選択と先にする理由の記録。
func (s *Store) SetPlanRationale(ctx context.Context, id int64, rationale json.RawMessage) (*model.Plan, error) {
	res, err := s.db.ExecContext(ctx, `UPDATE {s}plans SET rationale_json=? WHERE id=?`, rawOrEmpty(rationale), id)
	if err != nil {
		return nil, err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return nil, ErrNotFound
	}
	// 評価の入力（continue_after_run）が変わるので、保存した評価の指紋は次に読むときに作り直される
	return s.GetPlan(ctx, id)
}

// ReplaceRunBlocks は run の予定の球数と実験のブロックを1トランザクションで差し替える（§10.2）。
// counts が nil なら球数は変えない。保存した評価は、この run とそれより後の run の分を捨てる
// （後の run は、この run の評価を物差し・前の回として読むので）。
func (s *Store) ReplaceRunBlocks(ctx context.Context, runID int64, counts []int, blocks []model.Block) error {
	if err := CheckBlocks(blocks); err != nil {
		return err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var planID, expID int64
	if err := tx.QueryRowContext(ctx, `SELECT plan_id, experiment_id FROM {s}plan_runs WHERE id=?`, runID).Scan(&planID, &expID); errors.Is(err, sql.ErrNoRows) {
		return ErrNotFound
	} else if err != nil {
		return err
	}
	if counts != nil {
		cj, _ := json.Marshal(counts)
		if _, err := tx.ExecContext(ctx, `UPDATE {s}plan_runs SET counts_json=? WHERE id=?`, string(cj), runID); err != nil {
			return err
		}
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM {s}blocks WHERE experiment_id=?`, expID); err != nil {
		return err
	}
	for _, b := range blocks {
		if _, err := tx.ExecContext(ctx, `INSERT INTO {s}blocks(experiment_id, kind, seq_from, seq_to) VALUES(?,?,?,?)`, expID, string(b.Kind), b.SeqFrom, b.SeqTo); err != nil {
			return err
		}
	}
	if _, err := tx.ExecContext(ctx, `UPDATE {s}plan_runs SET evaluation_json=NULL, evaluation_key=NULL WHERE plan_id=? AND id>=?`, planID, runID); err != nil {
		return err
	}
	return tx.Commit()
}
