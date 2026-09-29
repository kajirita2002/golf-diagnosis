// server は Golf Swing Diagnosis Engine の API。
//
//	DB_PATH       保存先。SQLite のファイルか PostgreSQL の URL（postgres://...。golf スキーマに作る）
//	              未設定なら DEFAULT_DB_PATH（コンテナでは /app/data/golf.db）、それも無ければ golf.db。
//	              **未設定のまま公開すると再起動で消える**ので、/healthz と画面にそう出す（落とさない）。
//	ANALYSIS_URL  Python の分析サービス（既定 http://127.0.0.1:8001）
//	WEB_DIR       画面の静的ファイル（既定 ../web。無ければ配らない）
//	PORT          待ち受け（既定 8080）
//	APP_PASSWORD  空でなければ全部に Basic 認証を掛ける（公開するときは必ず入れる）
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/kajirita2002/golf-diagnosis/api/internal/analysis"
	"github.com/kajirita2002/golf-diagnosis/api/internal/httpapi"
	"github.com/kajirita2002/golf-diagnosis/api/internal/store"
)

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func main() {
	log := slog.New(slog.NewTextHandler(os.Stderr, nil))
	dbPath, persistent := os.Getenv("DB_PATH"), true
	if dbPath == "" {
		// 落として再起動を繰り返すと、原因が外から見えない（Render の画面は起動中のまま回り続ける）。
		// 一時的な保存先で動かして、/healthz と画面に「再起動で消える」と出す。
		dbPath, persistent = env("DEFAULT_DB_PATH", "golf.db"), false
		log.Warn("DB_PATH が未設定です。一時的な保存先で動きます（再起動で消えます）", "path", dbPath)
	}
	st, err := openWithRetry(log, dbPath)
	if err != nil {
		log.Error("DB を開けません", "kind", dbKind(dbPath), "err", err)
		os.Exit(1)
	}
	defer st.Close()

	srv := httpapi.New(st, analysis.New(env("ANALYSIS_URL", "http://127.0.0.1:8001")))
	srv.Log = log
	srv.Password = os.Getenv("APP_PASSWORD")
	srv.Status = httpapi.Status{
		DB:           dbKind(dbPath),
		DBPersistent: persistent,
		AnthropicKey: os.Getenv("ANTHROPIC_API_KEY") != "",
		Commit:       short(os.Getenv("RENDER_GIT_COMMIT")),
	}
	if srv.Password == "" {
		log.Warn("APP_PASSWORD が未設定です。パスワード無しで動きます（手元で使うときだけにしてください）")
	}
	if dir := env("WEB_DIR", "../web"); dir != "" {
		if fi, err := os.Stat(dir); err == nil && fi.IsDir() {
			srv.Static = http.FileServer(http.Dir(dir))
		}
	}

	hs := &http.Server{
		Addr:              ":" + env("PORT", "8080"),
		Handler:           srv.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
	}
	go func() {
		log.Info("listening", "addr", hs.Addr)
		if err := hs.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("server", "err", err)
			os.Exit(1)
		}
	}()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	<-ctx.Done()
	shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = hs.Shutdown(shutdown)
}

func dbKind(dsn string) string {
	if strings.HasPrefix(dsn, "postgres://") || strings.HasPrefix(dsn, "postgresql://") {
		return "postgres"
	}
	return "sqlite"
}

func short(commit string) string {
	if len(commit) > 7 {
		return commit[:7]
	}
	return commit
}

// openWithRetry は PostgreSQL が起きるのを待つ（無料の PostgreSQL は寝ていると最初の接続に数秒かかる）。
// エラーに接続文字列（パスワードを含む）を載せない。
func openWithRetry(log *slog.Logger, dsn string) (*store.Store, error) {
	var err error
	for i, wait := 0, 2*time.Second; i < 6; i, wait = i+1, wait*2 {
		var st *store.Store
		if st, err = store.Open(dsn); err == nil {
			return st, nil
		}
		if dbKind(dsn) != "postgres" {
			return nil, err
		}
		log.Warn("PostgreSQL に接続できません。待ってやり直します", "try", i+1, "wait", wait.String(), "err", err)
		time.Sleep(wait)
	}
	return nil, err
}
