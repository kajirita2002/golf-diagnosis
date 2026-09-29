// Package store は選手・セッション・ショット・実験を保存する。
//
// 保存先は2つ: SQLite（手元・テスト。純 Go の modernc.org/sqlite）と PostgreSQL（公開先）。
// **SQL は1か所にだけ書く。** 違いはプレースホルダ（? と $1）と ID の型だけなので、
// ? で書いて PostgreSQL のときに $n へ置き換える（rebind）。両方に手で書くと片方だけ直す事故になる。
//
// PostgreSQL では **golf スキーマ** に作る。english-tts と同じデータベースを使うので、
// 同じ名前のテーブル（sessions）とぶつけない。
//
// 計測値は JSON の列（metrics_json）に持つ。項目は計測器の対応で増えるので、
// 列を足すたびに移行を書くより、値の形を model.Metrics で1か所に固定するほうが安全。
package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
	_ "modernc.org/sqlite"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
)

// ErrNotFound は対象が無い。
var ErrNotFound = errors.New("not found")

// Store は保存先。
type Store struct {
	db *conn
}

// conn は *sql.DB に、PostgreSQL のときだけプレースホルダを置き換える層をかぶせたもの。
type conn struct {
	*sql.DB
	pg bool
}

func (c *conn) ExecContext(ctx context.Context, q string, args ...any) (sql.Result, error) {
	return c.DB.ExecContext(ctx, rebind(c.pg, q), args...)
}

func (c *conn) QueryContext(ctx context.Context, q string, args ...any) (*sql.Rows, error) {
	return c.DB.QueryContext(ctx, rebind(c.pg, q), args...)
}

func (c *conn) QueryRowContext(ctx context.Context, q string, args ...any) *sql.Row {
	return c.DB.QueryRowContext(ctx, rebind(c.pg, q), args...)
}

func (c *conn) BeginTx(ctx context.Context, opts *sql.TxOptions) (*txn, error) {
	t, err := c.DB.BeginTx(ctx, opts)
	if err != nil {
		return nil, err
	}
	return &txn{t, c.pg}, nil
}

type txn struct {
	*sql.Tx
	pg bool
}

func (t *txn) QueryRowContext(ctx context.Context, q string, args ...any) *sql.Row {
	return t.Tx.QueryRowContext(ctx, rebind(t.pg, q), args...)
}

func (t *txn) ExecContext(ctx context.Context, q string, args ...any) (sql.Result, error) {
	return t.Tx.ExecContext(ctx, rebind(t.pg, q), args...)
}

func (t *txn) QueryContext(ctx context.Context, q string, args ...any) (*sql.Rows, error) {
	return t.Tx.QueryContext(ctx, rebind(t.pg, q), args...)
}

// rebind は SQL を保存先に合わせる。
//   - {s}（テーブル名の前に付ける印）→ PostgreSQL では "golf."、SQLite では消す
//   - ? → $1, $2, ...（PostgreSQL のときだけ）
//
// **スキーマは SQL の中で名指しする。** 接続の設定（search_path）で切り替えていたら、
// 公開先の PostgreSQL の接続プーラーがそれを無視し、english-tts の public.sessions に
// ぶつかって起動できなかった（2026-09-29。SQLSTATE 42804）。
// SQL の中に文字としての ? と {s} は書かない約束（書くと置き換わる）。
func rebind(pg bool, q string) string {
	if !pg {
		return strings.ReplaceAll(q, "{s}", "")
	}
	q = strings.ReplaceAll(q, "{s}", PGSchema+".")
	var b strings.Builder
	n := 0
	for _, r := range q {
		if r == '?' {
			n++
			b.WriteString("$" + strconv.Itoa(n))
			continue
		}
		b.WriteRune(r)
	}
	return b.String()
}

const schema = `
CREATE TABLE IF NOT EXISTS {s}players (
	id          {{ID}},
	name        TEXT NOT NULL,
	handedness  TEXT NOT NULL CHECK (handedness IN ('R','L')),
	created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS {s}sessions (
	id          {{ID}},
	player_id   INTEGER NOT NULL REFERENCES {s}players(id),
	date        TEXT NOT NULL,
	location    TEXT NOT NULL DEFAULT '',
	source      TEXT NOT NULL DEFAULT '',
	created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS {s}shots (
	id              {{ID}},
	session_id      INTEGER NOT NULL REFERENCES {s}sessions(id),
	seq             INTEGER NOT NULL,
	club            TEXT NOT NULL DEFAULT '',
	club_category   TEXT NOT NULL DEFAULT 'unknown',
	hit_at          TEXT,
	metrics_json    TEXT NOT NULL,
	estimated_json  TEXT NOT NULL DEFAULT '[]',
	derived_json    TEXT NOT NULL DEFAULT '[]',
	manual_json     TEXT NOT NULL DEFAULT '[]',
	excluded        INTEGER NOT NULL DEFAULT 0,
	good_override   INTEGER,
	raw_json        TEXT NOT NULL DEFAULT '{}',
	adapter_version TEXT NOT NULL DEFAULT '',
	UNIQUE (session_id, seq)
);
CREATE TABLE IF NOT EXISTS {s}experiments (
	id             {{ID}},
	session_id     INTEGER NOT NULL REFERENCES {s}sessions(id),
	hypothesis     TEXT NOT NULL,
	intervention   TEXT NOT NULL DEFAULT '',
	target_metric  TEXT NOT NULL,
	goal           TEXT NOT NULL,
	club           TEXT NOT NULL DEFAULT '',
	created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS {s}blocks (
	id             {{ID}},
	experiment_id  INTEGER NOT NULL REFERENCES {s}experiments(id),
	kind           TEXT NOT NULL,
	seq_from       INTEGER NOT NULL,
	seq_to         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS blocks_experiment ON {s}blocks(experiment_id);
-- プラン（docs/DESIGN_coaching.md §10.3）。experiments には列を足さない（移行の仕組みが無い）。
-- hypothesis / cue は設計書の表に無い列: 実験（experiments.hypothesis / intervention）を
-- run ごとに作るときの文を、プランを作った時点で固定するため。
CREATE TABLE IF NOT EXISTS {s}plans (
	id               {{ID}},
	player_id        INTEGER NOT NULL REFERENCES {s}players(id),
	issue            TEXT NOT NULL,
	lever            TEXT,
	drill_id         TEXT NOT NULL,
	catalog_version  TEXT NOT NULL,
	club             TEXT NOT NULL,
	target_metric    TEXT NOT NULL,
	goal             TEXT NOT NULL,
	hypothesis       TEXT NOT NULL DEFAULT '',
	cue              TEXT NOT NULL DEFAULT '',
	template_json    TEXT NOT NULL,
	params_json      TEXT NOT NULL,
	trigger_json     TEXT NOT NULL,
	rationale_json   TEXT NOT NULL,
	status           TEXT NOT NULL,
	engine_version   TEXT NOT NULL,
	created_at       TEXT NOT NULL,
	closed_at        TEXT,
	close_reason     TEXT
);
-- 動かせるプランは選手ごとに1つ（2つ目は 409）。部分索引は SQLite と PostgreSQL の両方にある。
CREATE UNIQUE INDEX IF NOT EXISTS plans_one_active ON {s}plans(player_id) WHERE status = 'active';
CREATE TABLE IF NOT EXISTS {s}plan_runs (
	id               {{ID}},
	plan_id          INTEGER NOT NULL REFERENCES {s}plans(id),
	session_id       INTEGER NOT NULL REFERENCES {s}sessions(id),
	experiment_id    INTEGER NOT NULL REFERENCES {s}experiments(id),
	counts_json      TEXT NOT NULL DEFAULT '[]',
	evaluation_json  TEXT,
	evaluation_key   TEXT,
	created_at       TEXT NOT NULL
);
-- 1セッションに run は1つまで。診断から外すドリルの球（block_kind）は、この実験のブロックからだけ取る。
CREATE UNIQUE INDEX IF NOT EXISTS plan_runs_one_per_session ON {s}plan_runs(session_id);
CREATE INDEX IF NOT EXISTS plan_runs_plan ON {s}plan_runs(plan_id);
-- Claude の呼び出し（docs/DESIGN_coaching.md §5.8・§10.3。llm.go）。model は実際に答えたモデル。
CREATE TABLE IF NOT EXISTS {s}llm_jobs (
	id               {{ID}},
	kind             TEXT NOT NULL,
	session_id       INTEGER REFERENCES {s}sessions(id),
	scope_id         TEXT,
	input_hash       TEXT NOT NULL,
	status           TEXT NOT NULL,
	result_json      TEXT,
	validation_json  TEXT,
	usage_json       TEXT,
	cost_usd         DOUBLE PRECISION,
	error            TEXT,
	model            TEXT NOT NULL,
	prompt_version   TEXT NOT NULL,
	created_at       TEXT NOT NULL,
	updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS llm_jobs_hash ON {s}llm_jobs(kind, input_hash);
-- 1日（UTC）・1種類ごとの使った量。上限（LLM_DAILY_LIMIT_*）はここの calls で数える。
CREATE TABLE IF NOT EXISTS {s}llm_usage (
	day            TEXT NOT NULL,
	kind           TEXT NOT NULL,
	calls          INTEGER NOT NULL DEFAULT 0,
	input_tokens   INTEGER NOT NULL DEFAULT 0,
	output_tokens  INTEGER NOT NULL DEFAULT 0,
	cost_usd       DOUBLE PRECISION NOT NULL DEFAULT 0,
	PRIMARY KEY (day, kind)
);
`

// Open はデータベースを開いてテーブルを用意する。
//
//	postgres://... / postgresql://...  → PostgreSQL（golf スキーマ）
//	それ以外                          → SQLite のファイル（":memory:" でテスト用）
func Open(dsn string) (*Store, error) {
	if strings.HasPrefix(dsn, "postgres://") || strings.HasPrefix(dsn, "postgresql://") {
		return openPostgres(dsn)
	}
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	// SQLite の書き込みは1本ずつ。":memory:" は接続ごとに別のDBになるので、
	// 1本に固定して閉じさせない（寿命の既定は無期限）ことで1つのDBを保つ。
	db.SetMaxOpenConns(1)
	if _, err := db.Exec("PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL;"); err != nil {
		db.Close()
		return nil, err
	}
	if _, err := db.Exec(rebind(false, strings.ReplaceAll(schema, "{{ID}}", "INTEGER PRIMARY KEY AUTOINCREMENT"))); err != nil {
		db.Close()
		return nil, fmt.Errorf("テーブルを作れません: %w", err)
	}
	return &Store{db: &conn{DB: db}}, nil
}

// PGSchema は PostgreSQL で使うスキーマ。english-tts のテーブルと混ぜない。
const PGSchema = "golf"

func openPostgres(dsn string) (*Store, error) {
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(5) // 無料の PostgreSQL は同時接続が少ない。english-tts と分け合う
	db.SetConnMaxIdleTime(5 * time.Minute)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if _, err := db.ExecContext(ctx, "CREATE SCHEMA IF NOT EXISTS "+PGSchema); err != nil {
		db.Close()
		return nil, fmt.Errorf("スキーマを作れません: %w", err)
	}
	if _, err := db.ExecContext(ctx, rebind(true, strings.ReplaceAll(schema, "{{ID}}", "BIGSERIAL PRIMARY KEY"))); err != nil {
		db.Close()
		return nil, fmt.Errorf("テーブルを作れません: %w", err)
	}
	return &Store{db: &conn{DB: db, pg: true}}, nil
}

// Close は閉じる。
func (s *Store) Close() error { return s.db.Close() }

func now() string { return time.Now().UTC().Format(time.RFC3339Nano) }

func parseTS(s string) time.Time {
	t, _ := time.Parse(time.RFC3339Nano, s)
	return t
}

// ---- players ----

// CreatePlayer は選手を作る。
func (s *Store) CreatePlayer(ctx context.Context, p *model.Player) error {
	if p.Handedness != model.RightHanded && p.Handedness != model.LeftHanded {
		return fmt.Errorf("handedness は R か L")
	}
	ts := now()
	if err := s.db.QueryRowContext(ctx, `INSERT INTO {s}players(name, handedness, created_at) VALUES(?,?,?) RETURNING id`, p.Name, p.Handedness, ts).Scan(&p.ID); err != nil {
		return err
	}
	p.CreatedAt = parseTS(ts)
	return nil
}

// GetPlayer は選手を読む。
func (s *Store) GetPlayer(ctx context.Context, id int64) (*model.Player, error) {
	var p model.Player
	var ts string
	err := s.db.QueryRowContext(ctx, `SELECT id, name, handedness, created_at FROM {s}players WHERE id=?`, id).
		Scan(&p.ID, &p.Name, &p.Handedness, &ts)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	p.CreatedAt = parseTS(ts)
	return &p, nil
}

// ListPlayers は選手を作った順に返す。
func (s *Store) ListPlayers(ctx context.Context) ([]model.Player, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT id, name, handedness, created_at FROM {s}players ORDER BY id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []model.Player{}
	for rows.Next() {
		var p model.Player
		var ts string
		if err := rows.Scan(&p.ID, &p.Name, &p.Handedness, &ts); err != nil {
			return nil, err
		}
		p.CreatedAt = parseTS(ts)
		out = append(out, p)
	}
	return out, rows.Err()
}

// ---- sessions ----

// CreateSession はセッションを作る。
func (s *Store) CreateSession(ctx context.Context, se *model.Session) error {
	if _, err := s.GetPlayer(ctx, se.PlayerID); err != nil {
		return err
	}
	ts := now()
	if err := s.db.QueryRowContext(ctx, `INSERT INTO {s}sessions(player_id, date, location, source, created_at) VALUES(?,?,?,?,?) RETURNING id`,
		se.PlayerID, se.Date, se.Location, se.Source, ts).Scan(&se.ID); err != nil {
		return err
	}
	se.CreatedAt = parseTS(ts)
	return nil
}

// GetSession はセッションを読む。
func (s *Store) GetSession(ctx context.Context, id int64) (*model.Session, error) {
	var se model.Session
	var ts string
	err := s.db.QueryRowContext(ctx, `SELECT id, player_id, date, location, source, created_at FROM {s}sessions WHERE id=?`, id).
		Scan(&se.ID, &se.PlayerID, &se.Date, &se.Location, &se.Source, &ts)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	se.CreatedAt = parseTS(ts)
	return &se, nil
}

// ListSessions は選手のセッションを新しい順に返す。
func (s *Store) ListSessions(ctx context.Context, playerID int64) ([]model.Session, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT id, player_id, date, location, source, created_at FROM {s}sessions WHERE player_id=? ORDER BY date DESC, id DESC`, playerID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []model.Session{}
	for rows.Next() {
		var se model.Session
		var ts string
		if err := rows.Scan(&se.ID, &se.PlayerID, &se.Date, &se.Location, &se.Source, &ts); err != nil {
			return nil, err
		}
		se.CreatedAt = parseTS(ts)
		out = append(out, se)
	}
	return out, rows.Err()
}

// ---- shots ----

// AppendShots はセッションの末尾に球を足す。Seq は続きから振る。
// 1回の取り込みは全部入るか全部入らないか（途中で失敗しても半端に残さない）。
func (s *Store) AppendShots(ctx context.Context, sessionID int64, shots []model.Shot) ([]model.Shot, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	var maxSeq int
	if err := tx.QueryRowContext(ctx, `SELECT COALESCE(MAX(seq),0) FROM {s}shots WHERE session_id=?`, sessionID).Scan(&maxSeq); err != nil {
		return nil, err
	}
	out := make([]model.Shot, 0, len(shots))
	for i, sh := range shots {
		sh.SessionID = sessionID
		sh.Seq = maxSeq + i + 1
		mj, _ := json.Marshal(sh.Metrics)
		ej, _ := json.Marshal(nonNil(sh.Estimated))
		dj, _ := json.Marshal(nonNil(sh.Derived))
		manj, _ := json.Marshal(nonNil(sh.Manual))
		raw := sh.Raw
		if len(raw) == 0 {
			raw = json.RawMessage("{}")
		}
		var hit any
		if sh.HitAt != nil {
			hit = sh.HitAt.UTC().Format(time.RFC3339)
		}
		err := tx.QueryRowContext(ctx, `INSERT INTO {s}shots(session_id, seq, club, club_category, hit_at, metrics_json, estimated_json, derived_json, manual_json, excluded, good_override, raw_json, adapter_version)
			VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id`,
			sh.SessionID, sh.Seq, sh.Club, sh.ClubCategory, hit, string(mj), string(ej), string(dj), string(manj), boolInt(sh.Excluded), nullBool(sh.GoodOverride), string(raw), sh.AdapterVersion).Scan(&sh.ID)
		if err != nil {
			return nil, err
		}
		out = append(out, sh)
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	return out, nil
}

const shotCols = `id, session_id, seq, club, club_category, hit_at, metrics_json, estimated_json, derived_json, manual_json, excluded, good_override, raw_json, adapter_version`

func scanShot(sc interface{ Scan(...any) error }) (model.Shot, error) {
	var sh model.Shot
	var hit sql.NullString
	var mj, ej, dj, manj, raw string
	var excl int
	var good sql.NullInt64
	if err := sc.Scan(&sh.ID, &sh.SessionID, &sh.Seq, &sh.Club, &sh.ClubCategory, &hit, &mj, &ej, &dj, &manj, &excl, &good, &raw, &sh.AdapterVersion); err != nil {
		return sh, err
	}
	if hit.Valid {
		if t, err := time.Parse(time.RFC3339, hit.String); err == nil {
			sh.HitAt = &t
		}
	}
	if err := json.Unmarshal([]byte(mj), &sh.Metrics); err != nil {
		return sh, fmt.Errorf("shot %d の metrics_json が壊れています: %w", sh.ID, err)
	}
	_ = json.Unmarshal([]byte(ej), &sh.Estimated)
	_ = json.Unmarshal([]byte(dj), &sh.Derived)
	_ = json.Unmarshal([]byte(manj), &sh.Manual)
	sh.Excluded = excl != 0
	if good.Valid {
		b := good.Int64 != 0
		sh.GoodOverride = &b
	}
	sh.Raw = json.RawMessage(raw)
	return sh, nil
}

// ListShots はセッションの球を打った順に返す。
func (s *Store) ListShots(ctx context.Context, sessionID int64) ([]model.Shot, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT `+shotCols+` FROM {s}shots WHERE session_id=? ORDER BY seq`, sessionID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []model.Shot{}
	for rows.Next() {
		sh, err := scanShot(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, sh)
	}
	return out, rows.Err()
}

// GetShot は1球を読む。
func (s *Store) GetShot(ctx context.Context, id int64) (*model.Shot, error) {
	sh, err := scanShot(s.db.QueryRowContext(ctx, `SELECT `+shotCols+` FROM {s}shots WHERE id=?`, id))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return &sh, nil
}

// UpdateShot は人が直せる項目（クラブ・除外・Good の上書き・手入力の値）を書く。
func (s *Store) UpdateShot(ctx context.Context, sh *model.Shot) error {
	mj, _ := json.Marshal(sh.Metrics)
	manj, _ := json.Marshal(nonNil(sh.Manual))
	res, err := s.db.ExecContext(ctx, `UPDATE {s}shots SET club=?, club_category=?, metrics_json=?, manual_json=?, excluded=?, good_override=? WHERE id=?`,
		sh.Club, sh.ClubCategory, string(mj), string(manj), boolInt(sh.Excluded), nullBool(sh.GoodOverride), sh.ID)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	return nil
}

// ---- experiments ----

// CreateExperiment は実験を作る。
func (s *Store) CreateExperiment(ctx context.Context, e *model.Experiment) error {
	if _, err := s.GetSession(ctx, e.SessionID); err != nil {
		return err
	}
	if !e.Goal.Valid() {
		return fmt.Errorf("goal %q は使えません", e.Goal)
	}
	ts := now()
	if err := s.db.QueryRowContext(ctx, `INSERT INTO {s}experiments(session_id, hypothesis, intervention, target_metric, goal, club, created_at) VALUES(?,?,?,?,?,?,?) RETURNING id`,
		e.SessionID, e.Hypothesis, e.Intervention, e.TargetMetric, e.Goal, e.Club, ts).Scan(&e.ID); err != nil {
		return err
	}
	e.CreatedAt = parseTS(ts)
	e.Blocks = []model.Block{}
	return nil
}

// AddBlock は実験にブロックを足す。
func (s *Store) AddBlock(ctx context.Context, b *model.Block) error {
	if !b.Kind.Valid() {
		return fmt.Errorf("kind %q は使えません", b.Kind)
	}
	if b.SeqFrom < 1 || b.SeqTo < b.SeqFrom {
		return fmt.Errorf("seq の範囲が不正です（%d〜%d）", b.SeqFrom, b.SeqTo)
	}
	if err := s.db.QueryRowContext(ctx, `INSERT INTO {s}blocks(experiment_id, kind, seq_from, seq_to) VALUES(?,?,?,?) RETURNING id`, b.ExperimentID, b.Kind, b.SeqFrom, b.SeqTo).Scan(&b.ID); err != nil {
		return err
	}
	return nil
}

// GetExperiment は実験をブロックごと読む。
func (s *Store) GetExperiment(ctx context.Context, id int64) (*model.Experiment, error) {
	var e model.Experiment
	var ts string
	err := s.db.QueryRowContext(ctx, `SELECT id, session_id, hypothesis, intervention, target_metric, goal, club, created_at FROM {s}experiments WHERE id=?`, id).
		Scan(&e.ID, &e.SessionID, &e.Hypothesis, &e.Intervention, &e.TargetMetric, &e.Goal, &e.Club, &ts)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	e.CreatedAt = parseTS(ts)
	rows, err := s.db.QueryContext(ctx, `SELECT id, experiment_id, kind, seq_from, seq_to FROM {s}blocks WHERE experiment_id=? ORDER BY seq_from, id`, id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	e.Blocks = []model.Block{}
	for rows.Next() {
		var b model.Block
		if err := rows.Scan(&b.ID, &b.ExperimentID, &b.Kind, &b.SeqFrom, &b.SeqTo); err != nil {
			return nil, err
		}
		e.Blocks = append(e.Blocks, b)
	}
	return &e, rows.Err()
}

// ListExperiments はセッションの実験を返す。
func (s *Store) ListExperiments(ctx context.Context, sessionID int64) ([]model.Experiment, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT id FROM {s}experiments WHERE session_id=? ORDER BY id`, sessionID)
	if err != nil {
		return nil, err
	}
	var ids []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			rows.Close()
			return nil, err
		}
		ids = append(ids, id)
	}
	rows.Close()
	out := []model.Experiment{}
	for _, id := range ids {
		e, err := s.GetExperiment(ctx, id)
		if err != nil {
			return nil, err
		}
		out = append(out, *e)
	}
	return out, nil
}

func nonNil(xs []string) []string {
	if xs == nil {
		return []string{}
	}
	return xs
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

func nullBool(b *bool) any {
	if b == nil {
		return nil
	}
	return boolInt(*b)
}

// DropForTest は PostgreSQL の golf スキーマを消す。**テスト専用**（TEST_DATABASE_URL にだけ使う）。
func DropForTest(dsn string) error {
	if !strings.HasPrefix(dsn, "postgres://") && !strings.HasPrefix(dsn, "postgresql://") {
		return fmt.Errorf("PostgreSQL の URL ではありません")
	}
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		return err
	}
	defer db.Close()
	_, err = db.Exec("DROP SCHEMA IF EXISTS " + PGSchema + " CASCADE")
	return err
}
