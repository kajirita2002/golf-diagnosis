// server は Golf Swing Diagnosis Engine の API。
//
//	DB_PATH       保存先。SQLite のファイル（既定 golf.db）か PostgreSQL の URL（postgres://...。golf スキーマに作る）
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
	st, err := store.Open(env("DB_PATH", "golf.db"))
	if err != nil {
		log.Error("DB を開けません", "err", err)
		os.Exit(1)
	}
	defer st.Close()

	srv := httpapi.New(st, analysis.New(env("ANALYSIS_URL", "http://127.0.0.1:8001")))
	srv.Log = log
	srv.Password = os.Getenv("APP_PASSWORD")
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
