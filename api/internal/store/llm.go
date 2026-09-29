package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
)

// Claude の呼び出しの記録と上限（docs/DESIGN_coaching.md §5.8・§10.3）。
//
//	llm_jobs  … 1回の依頼（つなぎの文なら1範囲）。キャッシュの鍵は (kind, input_hash, model)
//	llm_usage … 1日（UTC）・1種類ごとの回数とトークンと料金。上限はここで数える

const llmJobCols = `id, kind, session_id, scope_id, input_hash, status, result_json, validation_json, usage_json, cost_usd, error, model, prompt_version, created_at, updated_at`

func scanLLMJob(sc interface{ Scan(...any) error }) (*model.LLMJob, error) {
	var j model.LLMJob
	var sid sql.NullInt64
	var scope, result, validation, usage, errText sql.NullString
	var cost sql.NullFloat64
	var created, updated string
	if err := sc.Scan(&j.ID, &j.Kind, &sid, &scope, &j.InputHash, &j.Status, &result, &validation, &usage, &cost, &errText, &j.Model, &j.PromptVersion, &created, &updated); err != nil {
		return nil, err
	}
	if sid.Valid {
		v := sid.Int64
		j.SessionID = &v
	}
	j.ScopeID = scope.String
	j.Result = rawOrNull(result)
	j.Validation = rawOrNull(validation)
	j.Usage = rawOrNull(usage)
	if cost.Valid {
		v := cost.Float64
		j.CostUSD = &v
	}
	j.Error = errText.String
	j.CreatedAt, j.UpdatedAt = parseTS(created), parseTS(updated)
	return &j, nil
}

func rawOrNull(s sql.NullString) json.RawMessage {
	if !s.Valid || s.String == "" {
		return json.RawMessage("null")
	}
	return json.RawMessage(s.String)
}

func rawOrNil(r json.RawMessage) any {
	if len(r) == 0 || string(r) == "null" {
		return nil
	}
	return string(r)
}

// CreateLLMJob はジョブを queued で作る。Model は頼んだモデル（答えが返ったら FinishLLMJob で書き換える）。
func (s *Store) CreateLLMJob(ctx context.Context, j *model.LLMJob) error {
	ts := now()
	var sid any
	if j.SessionID != nil {
		sid = *j.SessionID
	}
	j.Status = model.LLMQueued
	if err := s.db.QueryRowContext(ctx, `INSERT INTO {s}llm_jobs(kind, session_id, scope_id, input_hash, status, model, prompt_version, created_at, updated_at)
		VALUES(?,?,?,?,?,?,?,?,?) RETURNING id`, j.Kind, sid, nullString(j.ScopeID), j.InputHash, j.Status, j.Model, j.PromptVersion, ts, ts).Scan(&j.ID); err != nil {
		return err
	}
	j.CreatedAt, j.UpdatedAt = parseTS(ts), parseTS(ts)
	j.Result, j.Validation, j.Usage = json.RawMessage("null"), json.RawMessage("null"), json.RawMessage("null")
	return nil
}

// GetLLMJob はジョブを読む。
func (s *Store) GetLLMJob(ctx context.Context, id int64) (*model.LLMJob, error) {
	j, err := scanLLMJob(s.db.QueryRowContext(ctx, `SELECT `+llmJobCols+` FROM {s}llm_jobs WHERE id=?`, id))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	return j, err
}

// FindDoneLLMJob はキャッシュを引く: 同じ種類・同じ入力の鍵・同じモデル（実際に答えたモデル）で、
// 使い回してよい（done）いちばん新しいジョブ。無ければ nil。mdl が空ならモデルを問わない（表示だけに使う）。
func (s *Store) FindDoneLLMJob(ctx context.Context, kind model.LLMJobKind, inputHash, mdl string) (*model.LLMJob, error) {
	j, err := scanLLMJob(s.db.QueryRowContext(ctx, `SELECT `+llmJobCols+` FROM {s}llm_jobs
		WHERE kind=? AND input_hash=? AND (?='' OR model=?) AND status='done' ORDER BY id DESC LIMIT 1`, kind, inputHash, mdl, mdl))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return j, err
}

// FindActiveLLMJob は同じ入力でまだ走っている（queued / running）ジョブ。二度押しで2回払わないため。
func (s *Store) FindActiveLLMJob(ctx context.Context, kind model.LLMJobKind, inputHash string) (*model.LLMJob, error) {
	j, err := scanLLMJob(s.db.QueryRowContext(ctx, `SELECT `+llmJobCols+` FROM {s}llm_jobs
		WHERE kind=? AND input_hash=? AND status IN ('queued','running') ORDER BY id DESC LIMIT 1`, kind, inputHash))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return j, err
}

// SetLLMJobRunning は queued → running。
func (s *Store) SetLLMJobRunning(ctx context.Context, id int64) error {
	_, err := s.db.ExecContext(ctx, `UPDATE {s}llm_jobs SET status='running', updated_at=? WHERE id=? AND status='queued'`, now(), id)
	return err
}

// LLMJobFinish は終わったジョブに書くもの。Model は実際に答えたモデル（空なら頼んだモデルのまま）。
type LLMJobFinish struct {
	Status     model.LLMJobStatus
	Result     json.RawMessage
	Validation json.RawMessage
	Usage      json.RawMessage
	CostUSD    *float64
	Model      string
	Error      string
}

// FinishLLMJob はジョブを done / failed にする。
func (s *Store) FinishLLMJob(ctx context.Context, id int64, f LLMJobFinish) error {
	var cost any
	if f.CostUSD != nil {
		cost = *f.CostUSD
	}
	res, err := s.db.ExecContext(ctx, `UPDATE {s}llm_jobs SET status=?, result_json=?, validation_json=?, usage_json=?, cost_usd=?,
		model=CASE WHEN ? = '' THEN model ELSE ? END, error=?, updated_at=? WHERE id=?`,
		f.Status, rawOrNil(f.Result), rawOrNil(f.Validation), rawOrNil(f.Usage), cost, f.Model, f.Model, nullString(f.Error), now(), id)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	return nil
}

// FailStaleLLMJobs は起動時に、走っていたはずのジョブ（queued / running）を failed にする。
// 無料プランは眠ると（再起動すると）メモリの中の続きが消えるので、待ち続ける画面を作らない。
func (s *Store) FailStaleLLMJobs(ctx context.Context) (int64, error) {
	res, err := s.db.ExecContext(ctx, `UPDATE {s}llm_jobs SET status='failed', error=?, updated_at=? WHERE status IN ('queued','running')`,
		"サーバーが再起動したので、途中で止まりました", now())
	if err != nil {
		return 0, err
	}
	return res.RowsAffected()
}

// ReserveLLMCall は上限の枠を1つ取る。limit < 0 なら上限なし。取れなければ false（Claude を呼ばない）。
// 1文の UPDATE の条件で数えるので、同時に2本来ても上限を超えない。
func (s *Store) ReserveLLMCall(ctx context.Context, day, kind string, limit int) (bool, error) {
	if _, err := s.db.ExecContext(ctx, `INSERT INTO {s}llm_usage(day, kind) VALUES(?,?) ON CONFLICT(day, kind) DO NOTHING`, day, kind); err != nil {
		return false, err
	}
	var res sql.Result
	var err error
	if limit < 0 {
		res, err = s.db.ExecContext(ctx, `UPDATE {s}llm_usage SET calls = calls + 1 WHERE day=? AND kind=?`, day, kind)
	} else {
		res, err = s.db.ExecContext(ctx, `UPDATE {s}llm_usage SET calls = calls + 1 WHERE day=? AND kind=? AND calls < ?`, day, kind, limit)
	}
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n == 1, nil
}

// ReleaseLLMCall は取った枠を返す（結局 Claude を呼ばなかったとき。キーが無い・分析サービスに届かないなど）。
func (s *Store) ReleaseLLMCall(ctx context.Context, day, kind string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE {s}llm_usage SET calls = calls - 1 WHERE day=? AND kind=? AND calls > 0`, day, kind)
	return err
}

// AddLLMUsage は実際に使ったトークンと料金を足す（料金の分からないモデルは 0 を足す）。
func (s *Store) AddLLMUsage(ctx context.Context, day, kind string, in, out int64, cost float64) error {
	if _, err := s.db.ExecContext(ctx, `INSERT INTO {s}llm_usage(day, kind) VALUES(?,?) ON CONFLICT(day, kind) DO NOTHING`, day, kind); err != nil {
		return err
	}
	_, err := s.db.ExecContext(ctx, `UPDATE {s}llm_usage SET input_tokens = input_tokens + ?, output_tokens = output_tokens + ?, cost_usd = cost_usd + ? WHERE day=? AND kind=?`,
		in, out, cost, day, kind)
	return err
}

// GetLLMUsage はその日・その種類の使った量（無ければ 0 の行）。
func (s *Store) GetLLMUsage(ctx context.Context, day, kind string) (model.LLMUsage, error) {
	u := model.LLMUsage{Day: day, Kind: kind}
	err := s.db.QueryRowContext(ctx, `SELECT calls, input_tokens, output_tokens, cost_usd FROM {s}llm_usage WHERE day=? AND kind=?`, day, kind).
		Scan(&u.Calls, &u.InputTokens, &u.OutputTokens, &u.CostUSD)
	if errors.Is(err, sql.ErrNoRows) {
		return u, nil
	}
	return u, err
}

// FindRefusedLLMJobSince は同じ入力で since（UTC の時刻の文字列）以降に断られた（refusal）ジョブ。
// 同じ入力は同じように断られるので、その日は呼び直さない（押すたびに払わない）。無ければ nil。
func (s *Store) FindRefusedLLMJobSince(ctx context.Context, kind model.LLMJobKind, inputHash, since string) (*model.LLMJob, error) {
	j, err := scanLLMJob(s.db.QueryRowContext(ctx, `SELECT `+llmJobCols+` FROM {s}llm_jobs
		WHERE kind=? AND input_hash=? AND status='failed' AND error='refusal' AND created_at>=? ORDER BY id DESC LIMIT 1`, kind, inputHash, since))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return j, err
}
