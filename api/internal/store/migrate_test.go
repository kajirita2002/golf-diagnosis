package store

import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"testing"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
)

// 前からある DB（prefs_json の無い players）を開き直すと、列が足され、前の行は既定値で読める。
// 2回開いても落ちない（足すのは無いときだけ）。
func Test前からあるDBに列を足す(t *testing.T) {
	path := filepath.Join(t.TempDir(), "old.db")
	old, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := old.Exec(`CREATE TABLE players (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
		handedness TEXT NOT NULL CHECK (handedness IN ('R','L')), created_at TEXT NOT NULL);
		INSERT INTO players(name, handedness, created_at) VALUES('前からいる人','L','2026-09-01T00:00:00Z');`); err != nil {
		t.Fatal(err)
	}
	old.Close()
	for i := 0; i < 2; i++ {
		st, err := Open(path)
		if err != nil {
			t.Fatalf("%d回目: %v", i+1, err)
		}
		ps, err := st.ListPlayers(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		if len(ps) != 1 || ps[0].Name != "前からいる人" || string(ps[0].Prefs) != "{}" {
			t.Fatalf("%d回目: %+v", i+1, ps)
		}
		st.Close()
	}
}

// PostgreSQL でも同じ（STORE_PG_TEST=1 のときだけ。openTest と同じ理由）。
func Test前からあるDBに列を足すPostgreSQL(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" || os.Getenv("STORE_PG_TEST") != "1" {
		t.Skip("TEST_DATABASE_URL と STORE_PG_TEST=1 のときだけ")
	}
	if err := DropForTest(dsn); err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`CREATE SCHEMA golf; CREATE TABLE golf.players (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL,
		handedness TEXT NOT NULL, created_at TEXT NOT NULL);
		INSERT INTO golf.players(name, handedness, created_at) VALUES('前からいる人','R','2026-09-01T00:00:00Z');`); err != nil {
		t.Fatal(err)
	}
	db.Close()
	for i := 0; i < 2; i++ {
		st, err := Open(dsn)
		if err != nil {
			t.Fatalf("%d回目: %v", i+1, err)
		}
		ps, err := st.ListPlayers(context.Background())
		st.Close()
		if err != nil || len(ps) != 1 || string(ps[0].Prefs) != "{}" {
			t.Fatalf("%d回目: %+v %v", i+1, ps, err)
		}
	}
}

// 使う人は1人に決まる: いなければ作り、いれば一番古い人を返す（2人目を作らない）。
func Test使う人は一番古い人(t *testing.T) {
	st := openTest(t)
	ctx := context.Background()
	p, created, err := st.EnsurePlayer(ctx, "わたし", model.LeftHanded)
	if err != nil || !created || p.Handedness != model.LeftHanded {
		t.Fatalf("%+v %v %v", p, created, err)
	}
	q, created, err := st.EnsurePlayer(ctx, "別の人", model.RightHanded)
	if err != nil || created || q.ID != p.ID {
		t.Fatalf("2回目で別の人を作った: %+v %v %v", q, created, err)
	}
	hand := model.RightHanded
	u, err := st.UpdatePlayer(ctx, p.ID, &hand, []byte(`{"dist_unit":"m"}`))
	if err != nil || u.Handedness != model.RightHanded || string(u.Prefs) != `{"dist_unit":"m"}` {
		t.Fatalf("%+v %v", u, err)
	}
	bad := model.Handedness("X")
	if _, err := st.UpdatePlayer(ctx, p.ID, &bad, nil); err == nil {
		t.Fatal("知らない利き手を通した")
	}
}
