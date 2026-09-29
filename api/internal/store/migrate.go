package store

import (
	"context"
	"fmt"
)

// 列を足す小さな仕組み（docs/DESIGN_v2.md §14・§15 段1）。
//
// schema の CREATE TABLE IF NOT EXISTS は、もう在る表に列を足さない。移行の仕組みが無いまま
// 列を足すと、公開先（前からある PostgreSQL）では列が無いまま動き、書いた瞬間に落ちる。
// そこで「足りない列だけを ALTER TABLE ... ADD COLUMN で足す」を起動のたびに回す。
//
//   - **足すだけ。** 列の型の変更・名前の変更・削除はしない（それは表を足して移す）。
//   - 足す列には必ず DEFAULT を付ける（前からある行に値が要る。NOT NULL なら DEFAULT が無いと足せない）。
//   - 在るかどうかは、SQLite は pragma_table_info、PostgreSQL は information_schema で見る。
//     SQLite には ADD COLUMN IF NOT EXISTS が無いので、見てから足す。
//   - 並べる順は変えない（下に足していく）。
type addedColumn struct {
	Table string // {s} を付けない表の名前
	Name  string
	DDL   string // 型と既定値（例: TEXT NOT NULL DEFAULT '{}'）
}

// addedColumns は schema のあとに足した列。
var addedColumns = []addedColumn{
	// 設定（距離の単位など。docs/DESIGN_v2.md §10 S）。中身は httpapi が形を決めて検査する
	{Table: "players", Name: "prefs_json", DDL: "TEXT NOT NULL DEFAULT '{}'"},
}

// ensureColumns は足りない列を足す。
func ensureColumns(ctx context.Context, c *conn, cols []addedColumn) error {
	for _, col := range cols {
		ok, err := hasColumn(ctx, c, col.Table, col.Name)
		if err != nil {
			return fmt.Errorf("%s.%s を確かめられません: %w", col.Table, col.Name, err)
		}
		if ok {
			continue
		}
		if _, err := c.ExecContext(ctx, "ALTER TABLE {s}"+col.Table+" ADD COLUMN "+col.Name+" "+col.DDL); err != nil {
			return fmt.Errorf("%s.%s を足せません: %w", col.Table, col.Name, err)
		}
	}
	return nil
}

func hasColumn(ctx context.Context, c *conn, table, name string) (bool, error) {
	var n int
	var err error
	if c.pg {
		err = c.QueryRowContext(ctx, `SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=? AND table_name=? AND column_name=?`,
			PGSchema, table, name).Scan(&n)
	} else {
		err = c.QueryRowContext(ctx, `SELECT COUNT(*) FROM pragma_table_info(?) WHERE name=?`, table, name).Scan(&n)
	}
	return n > 0, err
}
