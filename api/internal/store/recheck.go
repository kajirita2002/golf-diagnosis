package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
)

// 別の日の再確認（docs/DESIGN_v2.md §8・段4）: 10球テストと TrackMan の2日の再確認。

// CreateFocusTest は10球テストを1つ残し、使ったスイングに10球テストの印を付ける。
func (s *Store) CreateFocusTest(ctx context.Context, f *model.FocusTest) error {
	ids, _ := json.Marshal(f.SwingIDs)
	var passed any
	if f.Passed != nil {
		if *f.Passed {
			passed = 1
		} else {
			passed = 0
		}
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	ts := now()
	if err := tx.QueryRowContext(ctx, `INSERT INTO {s}focus_tests(player_id, plan_id, session_id, item_id, catalog_version, view, block, swing_ids_json,
		self_rating_json, in_range, judged, passed, result_json, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id`,
		f.PlayerID, f.PlanID, f.SessionID, f.ItemID, f.CatalogVersion, f.View, f.Block, string(ids), rawOr(f.SelfRating, "{}"),
		f.InRange, f.Judged, passed, rawOr(f.Result, "{}"), ts).Scan(&f.ID); err != nil {
		return err
	}
	for _, id := range f.SwingIDs {
		if _, err := tx.ExecContext(ctx, `UPDATE {s}swings SET is_focus_test=1 WHERE id=?`, id); err != nil {
			return err
		}
	}
	if err := tx.Commit(); err != nil {
		return err
	}
	f.CreatedAt = parseTS(ts)
	return nil
}

const focusCols = `f.id, f.player_id, f.plan_id, f.session_id, f.item_id, f.catalog_version, f.view, f.block, f.swing_ids_json, f.self_rating_json,
	f.in_range, f.judged, f.passed, f.result_json, f.created_at, COALESCE(se.date, '')`

func scanFocus(sc interface{ Scan(...any) error }) (model.FocusTest, error) {
	var f model.FocusTest
	var plan, sess, passed sql.NullInt64
	var ids, sr, res, ts string
	if err := sc.Scan(&f.ID, &f.PlayerID, &plan, &sess, &f.ItemID, &f.CatalogVersion, &f.View, &f.Block, &ids, &sr, &f.InRange, &f.Judged, &passed, &res, &ts, &f.Date); err != nil {
		return f, err
	}
	if plan.Valid {
		f.PlanID = &plan.Int64
	}
	if sess.Valid {
		f.SessionID = &sess.Int64
	}
	if passed.Valid {
		b := passed.Int64 == 1
		f.Passed = &b
	}
	_ = json.Unmarshal([]byte(ids), &f.SwingIDs)
	f.SelfRating, f.Result, f.CreatedAt = json.RawMessage(sr), json.RawMessage(res), parseTS(ts)
	return f, nil
}

// GetFocusTest は10球テストを1つ読む。
func (s *Store) GetFocusTest(ctx context.Context, id int64) (*model.FocusTest, error) {
	f, err := scanFocus(s.db.QueryRowContext(ctx, `SELECT `+focusCols+` FROM {s}focus_tests f LEFT JOIN {s}sessions se ON se.id = f.session_id WHERE f.id=?`, id))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return &f, nil
}

// ListFocusTests は選手の10球テスト（古い順）。itemID が空でなければその項目だけ、planID が 0 でなければそのプランだけ。
func (s *Store) ListFocusTests(ctx context.Context, playerID int64, itemID string, planID int64) ([]model.FocusTest, error) {
	q := `SELECT ` + focusCols + ` FROM {s}focus_tests f LEFT JOIN {s}sessions se ON se.id = f.session_id WHERE f.player_id=?`
	args := []any{playerID}
	if itemID != "" {
		q += ` AND f.item_id=?`
		args = append(args, itemID)
	}
	if planID != 0 {
		q += ` AND f.plan_id=?`
		args = append(args, planID)
	}
	rows, err := s.db.QueryContext(ctx, q+` ORDER BY COALESCE(se.date, ''), f.id`, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []model.FocusTest{}
	for rows.Next() {
		f, err := scanFocus(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, f)
	}
	return out, rows.Err()
}

// CreateCheckup は2日の再確認を作る（items は作った時点で固定する）。
func (s *Store) CreateCheckup(ctx context.Context, c *model.Checkup) error {
	ts := now()
	if err := s.db.QueryRowContext(ctx, `INSERT INTO {s}checkups(player_id, baseline_session_id, recheck_session_id, plan_id, scope_json, items_json, result_json, result_key, created_at)
		VALUES(?,?,?,?,?,?,?,?,?) RETURNING id`, c.PlayerID, c.BaselineSessionID, c.RecheckSessionID, c.PlanID, rawOr(c.Scope, "{}"), rawOr(c.Items, "[]"),
		nullString(string(c.Result)), nullString(c.ResultKey), ts).Scan(&c.ID); err != nil {
		return err
	}
	c.CreatedAt = parseTS(ts)
	return nil
}

// GetCheckup は2日の再確認を読む。
func (s *Store) GetCheckup(ctx context.Context, id int64) (*model.Checkup, error) {
	var c model.Checkup
	var plan sql.NullInt64
	var res, key sql.NullString
	var scope, items, ts string
	err := s.db.QueryRowContext(ctx, `SELECT id, player_id, baseline_session_id, recheck_session_id, plan_id, scope_json, items_json, result_json, result_key, created_at FROM {s}checkups WHERE id=?`, id).
		Scan(&c.ID, &c.PlayerID, &c.BaselineSessionID, &c.RecheckSessionID, &plan, &scope, &items, &res, &key, &ts)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	if plan.Valid {
		c.PlanID = &plan.Int64
	}
	c.Scope, c.Items, c.ResultKey, c.CreatedAt = json.RawMessage(scope), json.RawMessage(items), key.String, parseTS(ts)
	if res.Valid {
		c.Result = json.RawMessage(res.String)
	}
	return &c, nil
}

// SaveCheckupResult は計算し直した結果を残す（items は変えない）。
func (s *Store) SaveCheckupResult(ctx context.Context, id int64, key string, result json.RawMessage) error {
	_, err := s.db.ExecContext(ctx, `UPDATE {s}checkups SET result_json=?, result_key=? WHERE id=?`, string(result), key, id)
	return err
}
