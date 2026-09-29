package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
)

// 動画のスイング・P のコマ・判定（docs/DESIGN_v2.md §13.2）。
//
// **全解像度のコマも動画もここには入らない。** 入るのは長辺 360px のサムネイル（thumb）と、
// 姿勢の点・タップ・判定だけ。サムネイルの大きさは httpapi が確かめてから渡す。

func rawOr(r json.RawMessage, def string) string {
	if len(r) == 0 || string(r) == "null" {
		return def
	}
	return string(r)
}

func nullFloat(v float64) any {
	if v == 0 {
		return nil
	}
	return v
}

// CreateSwing はスイングの行を作る。
func (s *Store) CreateSwing(ctx context.Context, sw *model.Swing) error {
	if sw.View != "dtl" && sw.View != "fo" {
		return fmt.Errorf("view は dtl か fo")
	}
	missing, _ := json.Marshal(nonNil(sw.Missing))
	ts := now()
	err := s.db.QueryRowContext(ctx, `INSERT INTO {s}swings(session_id, view, club, club_class, fps_measured, fps_source, width, height, duration_s,
		taps_json, capture_json, missing_json, cp_catalog_version, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id`,
		sw.SessionID, sw.View, sw.Club, sw.ClubClass, nullFloat(sw.FPS), sw.FPSSource, sw.Width, sw.Height, nullFloat(sw.Duration),
		rawOr(sw.Ball, "[]"), rawOr(sw.Capture, "{}"), string(missing), sw.CPCatalogVersion, ts).Scan(&sw.ID)
	if err != nil {
		return err
	}
	sw.CreatedAt = parseTS(ts)
	sw.Missing = nonNil(sw.Missing)
	return nil
}

const swingCols = `id, session_id, view, club, club_class, fps_measured, fps_source, width, height, duration_s, taps_json, capture_json, missing_json, cp_catalog_version, measure_json, created_at`

func scanSwing(sc interface{ Scan(...any) error }) (*model.Swing, error) {
	var sw model.Swing
	var fps, dur sql.NullFloat64
	var ball, capture, missing, ts string
	var measure sql.NullString
	if err := sc.Scan(&sw.ID, &sw.SessionID, &sw.View, &sw.Club, &sw.ClubClass, &fps, &sw.FPSSource, &sw.Width, &sw.Height, &dur,
		&ball, &capture, &missing, &sw.CPCatalogVersion, &measure, &ts); err != nil {
		return nil, err
	}
	sw.FPS, sw.Duration = fps.Float64, dur.Float64
	sw.Ball, sw.Capture = json.RawMessage(ball), json.RawMessage(capture)
	if measure.Valid {
		sw.Measure = json.RawMessage(measure.String)
	}
	_ = json.Unmarshal([]byte(missing), &sw.Missing)
	sw.Missing = nonNil(sw.Missing)
	sw.CreatedAt = parseTS(ts)
	return &sw, nil
}

// GetSwing は消していないスイングを読む。
func (s *Store) GetSwing(ctx context.Context, id int64) (*model.Swing, error) {
	sw, err := scanSwing(s.db.QueryRowContext(ctx, `SELECT `+swingCols+` FROM {s}swings WHERE id=? AND deleted_at IS NULL`, id))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	return sw, err
}

// ListSwings はセッションの消していないスイング（古い順）。
func (s *Store) ListSwings(ctx context.Context, sessionID int64) ([]model.Swing, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT `+swingCols+` FROM {s}swings WHERE session_id=? AND deleted_at IS NULL ORDER BY id`, sessionID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []model.Swing{}
	for rows.Next() {
		sw, err := scanSwing(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, *sw)
	}
	return out, rows.Err()
}

// UpdateSwingInput はあとから直せる欄（ボールのタップ・写っていない P・番手・向き）。nil は変えない。
type UpdateSwingInput struct {
	Ball    json.RawMessage
	Missing *[]string
	Club    *string
	Class   *string
	View    *string
}

// UpdateSwing はボールのタップ・写っていない P・番手・向きを直す。直したら判定の版を空に戻す
// （同じトランザクションで。このあとの測り直しが失敗しても、次に一覧を開いたときに測り直す）。
func (s *Store) UpdateSwing(ctx context.Context, id int64, in UpdateSwingInput) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	changed := false
	if in.Ball != nil {
		if _, err := tx.ExecContext(ctx, `UPDATE {s}swings SET taps_json=? WHERE id=? AND deleted_at IS NULL`, rawOr(in.Ball, "[]"), id); err != nil {
			return err
		}
		changed = true
	}
	if in.Missing != nil {
		b, _ := json.Marshal(nonNil(*in.Missing))
		if _, err := tx.ExecContext(ctx, `UPDATE {s}swings SET missing_json=? WHERE id=? AND deleted_at IS NULL`, string(b), id); err != nil {
			return err
		}
		changed = true
	}
	if in.Club != nil && in.Class != nil {
		if _, err := tx.ExecContext(ctx, `UPDATE {s}swings SET club=?, club_class=? WHERE id=? AND deleted_at IS NULL`, *in.Club, *in.Class, id); err != nil {
			return err
		}
		changed = true
	}
	if in.View != nil {
		if *in.View != "dtl" && *in.View != "fo" {
			return fmt.Errorf("view は dtl か fo")
		}
		if _, err := tx.ExecContext(ctx, `UPDATE {s}swings SET view=? WHERE id=? AND deleted_at IS NULL`, *in.View, id); err != nil {
			return err
		}
		changed = true
	}
	if changed {
		if _, err := tx.ExecContext(ctx, `UPDATE {s}swings SET cp_catalog_version='' WHERE id=?`, id); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// DeleteSwing は論理削除（30日後に消す。いまは印を付けるだけ）。
func (s *Store) DeleteSwing(ctx context.Context, id int64) error {
	res, err := s.db.ExecContext(ctx, `UPDATE {s}swings SET deleted_at=? WHERE id=? AND deleted_at IS NULL`, now(), id)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	return nil
}

// PutSwingFrames はスイングの P のコマを入れ替える（渡さなかった P は消す）。
// keepThumbs が真なら、サムネイルを渡さなかった P は前のサムネイルを残す（タップだけ直すとき）。
func (s *Store) PutSwingFrames(ctx context.Context, swingID int64, frames []model.SwingFrame, keepThumbs bool) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	old := map[string][]byte{}
	if keepThumbs {
		rows, err := tx.QueryContext(ctx, `SELECT checkpoint, thumb FROM {s}swing_frames WHERE swing_id=?`, swingID)
		if err != nil {
			return err
		}
		for rows.Next() {
			var p string
			var b []byte
			if err := rows.Scan(&p, &b); err != nil {
				rows.Close()
				return err
			}
			old[p] = b
		}
		rows.Close()
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM {s}swing_frames WHERE swing_id=?`, swingID); err != nil {
		return err
	}
	for _, f := range frames {
		if !model.IsCheckpoint(f.Checkpoint) {
			return fmt.Errorf("P の名前が違います: %q", f.Checkpoint)
		}
		thumb := f.Thumb
		if len(thumb) == 0 {
			thumb = old[f.Checkpoint]
		}
		var th any
		if len(thumb) > 0 {
			th = thumb
		}
		if _, err := tx.ExecContext(ctx, `INSERT INTO {s}swing_frames(swing_id, checkpoint, t, frame, source, landmarks_json, taps_json, thumb) VALUES(?,?,?,?,?,?,?,?)`,
			swingID, f.Checkpoint, f.T, f.Frame, f.Source, rawOr(f.Landmarks, "[]"), rawOr(f.Taps, "{}"), th); err != nil {
			return err
		}
	}
	// コマかタップが変わったので、前の判定は古い（このあとの測り直しが失敗しても、次に一覧を開いたときに測り直す）
	if _, err := tx.ExecContext(ctx, `UPDATE {s}swings SET cp_catalog_version='' WHERE id=?`, swingID); err != nil {
		return err
	}
	return tx.Commit()
}

// ListSwingFrames はスイングの P のコマ（サムネイルの中身は入れない。有るかだけ）。
func (s *Store) ListSwingFrames(ctx context.Context, swingID int64) ([]model.SwingFrame, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT checkpoint, t, frame, source, landmarks_json, taps_json, thumb IS NOT NULL FROM {s}swing_frames WHERE swing_id=?`, swingID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []model.SwingFrame{}
	for rows.Next() {
		var f model.SwingFrame
		var t sql.NullFloat64
		var fr sql.NullInt64
		var lm, taps string
		if err := rows.Scan(&f.Checkpoint, &t, &fr, &f.Source, &lm, &taps, &f.HasThumb); err != nil {
			return nil, err
		}
		f.T, f.Frame = t.Float64, int(fr.Int64)
		f.Landmarks, f.Taps = json.RawMessage(lm), json.RawMessage(taps)
		out = append(out, f)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	// P の順に並べる
	idx := map[string]int{}
	for i, c := range model.Checkpoints {
		idx[c] = i
	}
	for i := 1; i < len(out); i++ {
		for j := i; j > 0 && idx[out[j].Checkpoint] < idx[out[j-1].Checkpoint]; j-- {
			out[j], out[j-1] = out[j-1], out[j]
		}
	}
	return out, nil
}

// GetSwingThumb は P のサムネイル（JPEG）。
func (s *Store) GetSwingThumb(ctx context.Context, swingID int64, checkpoint string) ([]byte, error) {
	var b []byte
	err := s.db.QueryRowContext(ctx, `SELECT f.thumb FROM {s}swing_frames f JOIN {s}swings w ON w.id=f.swing_id WHERE f.swing_id=? AND f.checkpoint=? AND w.deleted_at IS NULL`,
		swingID, checkpoint).Scan(&b)
	if errors.Is(err, sql.ErrNoRows) || (err == nil && len(b) == 0) {
		return nil, ErrNotFound
	}
	return b, err
}

// PutSwingChecks はスイングの判定を、その版のぶんだけ入れ替える。measure は撮り方の検査・物差しなど（スイングに残す）。
func (s *Store) PutSwingChecks(ctx context.Context, swingID int64, catalogVersion string, measure json.RawMessage, checks []model.SwingCheck) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, `DELETE FROM {s}swing_checks WHERE swing_id=? AND catalog_version=?`, swingID, catalogVersion); err != nil {
		return err
	}
	ts := now()
	for _, c := range checks {
		if _, err := tx.ExecContext(ctx, `INSERT INTO {s}swing_checks(swing_id, item_id, catalog_version, state, fault_id, basis, reason, evidence_json, created_at) VALUES(?,?,?,?,?,?,?,?,?)`,
			swingID, c.ItemID, catalogVersion, c.State, nullString(c.FaultID), c.Basis, c.Reason, rawOr(c.Evidence, "{}"), ts); err != nil {
			return err
		}
	}
	if _, err := tx.ExecContext(ctx, `UPDATE {s}swings SET cp_catalog_version=?, measure_json=? WHERE id=?`, catalogVersion, rawOr(measure, "{}"), swingID); err != nil {
		return err
	}
	return tx.Commit()
}

// ListSwingChecks はスイングの判定（その版だけ）。
func (s *Store) ListSwingChecks(ctx context.Context, swingID int64, catalogVersion string) ([]model.SwingCheck, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT swing_id, item_id, catalog_version, state, fault_id, basis, reason, evidence_json FROM {s}swing_checks WHERE swing_id=? AND catalog_version=? ORDER BY item_id`,
		swingID, catalogVersion)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []model.SwingCheck{}
	for rows.Next() {
		var c model.SwingCheck
		var fault sql.NullString
		var ev string
		if err := rows.Scan(&c.SwingID, &c.ItemID, &c.CatalogVersion, &c.State, &fault, &c.Basis, &c.Reason, &ev); err != nil {
			return nil, err
		}
		c.FaultID, c.Evidence = fault.String, json.RawMessage(ev)
		out = append(out, c)
	}
	return out, rows.Err()
}

// SwingBlobSizes は保存しているサムネイルの大きさ（テストで「全解像度のコマが無い」を確かめる）。
func (s *Store) SwingBlobSizes(ctx context.Context) ([]int, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT thumb FROM {s}swing_frames WHERE thumb IS NOT NULL`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []int
	for rows.Next() {
		var b []byte
		if err := rows.Scan(&b); err != nil {
			return nil, err
		}
		out = append(out, len(b))
	}
	return out, rows.Err()
}

// TextColumnsWithJPEG は、サムネイルの列以外に JPEG らしい中身（base64 の先頭 /9j/ か生の FF D8 FF）が
// 入っている行の数（テスト用。画像を JSON の列へ紛れ込ませていないか）。
func (s *Store) TextColumnsWithJPEG(ctx context.Context) (int, error) {
	var n int
	err := s.db.QueryRowContext(ctx, `SELECT
		(SELECT COUNT(*) FROM {s}swings WHERE taps_json LIKE '%/9j/%' OR capture_json LIKE '%/9j/%' OR COALESCE(measure_json,'') LIKE '%/9j/%')
		+ (SELECT COUNT(*) FROM {s}swing_frames WHERE landmarks_json LIKE '%/9j/%' OR taps_json LIKE '%/9j/%')
		+ (SELECT COUNT(*) FROM {s}swing_checks WHERE evidence_json LIKE '%/9j/%')`).Scan(&n)
	return n, err
}
