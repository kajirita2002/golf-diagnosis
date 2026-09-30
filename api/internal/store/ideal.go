package store

import (
	"context"
)

// BestSwing は「自分のベスト」（docs/DESIGN_v2.md §7.1 ②）: 同じ項目が範囲の中だった過去のスイング。
type BestSwing struct {
	SwingID   int64  `json:"swing_id"`
	SessionID int64  `json:"session_id"`
	Date      string `json:"date"`
	View      string `json:"view"`
	Club      string `json:"club"`
}

// BestSwings は選手のスイングのうち、項目が範囲の中だったもの（いまの版の判定だけ）を新しい順に limit 本まで返す。
// p を渡すと、その P のサムネイルがあるスイングだけ（並べて見せられないものは返さない）。
func (s *Store) BestSwings(ctx context.Context, playerID int64, itemID, p string, limit int) ([]BestSwing, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT sw.id, se.id, se.date, sw.view, sw.club
		FROM {s}swing_checks c
		JOIN {s}swings sw ON sw.id = c.swing_id
		JOIN {s}sessions se ON se.id = sw.session_id
		WHERE se.player_id = ? AND c.item_id = ? AND c.state = 'in_range' AND c.catalog_version = sw.cp_catalog_version
		  AND sw.deleted_at IS NULL
		  AND (? = '' OR EXISTS (SELECT 1 FROM {s}swing_frames f WHERE f.swing_id = sw.id AND f.checkpoint = ? AND f.thumb IS NOT NULL))
		ORDER BY se.date DESC, sw.id DESC
		LIMIT ?`, playerID, itemID, p, p, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []BestSwing{}
	for rows.Next() {
		var b BestSwing
		if err := rows.Scan(&b.SwingID, &b.SessionID, &b.Date, &b.View, &b.Club); err != nil {
			return nil, err
		}
		out = append(out, b)
	}
	return out, rows.Err()
}
