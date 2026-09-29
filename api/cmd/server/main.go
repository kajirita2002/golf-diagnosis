// server は Golf Swing Diagnosis Engine の API。
//
//	DB_PATH       保存先。SQLite のファイルか PostgreSQL の URL（postgres://...。golf スキーマに作る）
//	              未設定なら DEFAULT_DB_PATH（コンテナでは /app/data/golf.db）、それも無ければ golf.db。
//	              **未設定のまま公開すると再起動で消える**ので、/healthz と画面にそう出す。
//	ANALYSIS_URL  Python の分析サービス（既定 http://127.0.0.1:8001）
//	WEB_DIR       画面の静的ファイル（既定 ../web。無ければ配らない）
//	PORT          待ち受け（既定 8080）
//	APP_PASSWORD  空でなければ全部に Basic 認証を掛ける（公開するときは必ず入れる）
//	REPORT_LLM    on なら解説のつなぎの文に Claude を使う（既定 off。off なら Claude を一切呼ばない）
//	LLM_DAILY_LIMIT_NARRATIVE  つなぎの文で Claude を呼ぶ範囲の数の1日の上限（既定 20。負なら上限なし）
//
// **起動の約束: 待ち受けは最初に開き、何があっても落とさない。**
// Render は待ち受けが開かないと「起動中」の画面のまま再起動を繰り返し、原因が外から見えない
// （2026-09-29 に実際に踏んだ）。データベースは裏でつなぎ、つながるまでは
// /healthz が理由を返し、ほかは 503 を返す。
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
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

// gate はデータベースがつながるまでの受付。つながったら本物のハンドラへ切り替える。
type gate struct {
	mu      sync.RWMutex
	ready   http.Handler
	status  httpapi.Status
	lastErr string
}

func (g *gate) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	g.mu.RLock()
	h, st, lastErr := g.ready, g.status, g.lastErr
	g.mu.RUnlock()
	if h != nil {
		h.ServeHTTP(w, r)
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	if r.URL.Path == "/healthz" {
		// 起動中でも 200 で返す（Render の死活監視を通して、再起動の繰り返しにしない）
		_, _ = w.Write(httpapi.HealthJSON(st, "starting", lastErr))
		return
	}
	w.Header().Set("Retry-After", "10")
	w.WriteHeader(http.StatusServiceUnavailable)
	msg := "起動中です。データベースに接続しています"
	if lastErr != "" {
		msg = "データベースに接続できません: " + lastErr
	}
	_, _ = w.Write(httpapi.ErrorJSON(msg))
}

func main() {
	log := slog.New(slog.NewTextHandler(os.Stderr, nil))
	dbPath, persistent := os.Getenv("DB_PATH"), true
	if dbPath == "" {
		dbPath, persistent = env("DEFAULT_DB_PATH", "golf.db"), false
		log.Warn("DB_PATH が未設定です。一時的な保存先で動きます（再起動で消えます）", "path", dbPath)
	}
	llmCfg, llmWarns := httpapi.LLMConfigFromEnv(os.Getenv)
	for _, w := range llmWarns {
		log.Warn(w)
	}
	status := httpapi.Status{
		ReportLLM:    llmCfg.Enabled,
		DB:           dbKind(dbPath),
		DBPersistent: persistent,
		AnthropicKey: os.Getenv("ANTHROPIC_API_KEY") != "",
		Commit:       short(os.Getenv("RENDER_GIT_COMMIT")),
		AnalysisURL:  env("ANALYSIS_URL", "http://127.0.0.1:8001"),
	}
	g := &gate{status: status}

	hs := &http.Server{
		Addr:              ":" + env("PORT", "8080"),
		Handler:           g,
		ReadHeaderTimeout: 10 * time.Second,
	}
	go func() {
		log.Info("listening", "addr", hs.Addr)
		if err := hs.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("server", "err", err)
			os.Exit(1)
		}
	}()

	// データベースは裏でつなぐ。つながるまで諦めない（落とすと原因が見えなくなる）。
	var (
		stMu sync.Mutex
		st   *store.Store
	)
	go func() {
		wait := 2 * time.Second
		for try := 1; ; try++ {
			s, err := store.Open(dbPath)
			if err == nil {
				stMu.Lock()
				st = s
				stMu.Unlock()
				// 走っていたはずの Claude のジョブは、再起動で続きが消えているので failed にする（§5.8）
				if n, err := s.FailStaleLLMJobs(context.Background()); err != nil {
					log.Error("途中のジョブを片付けられません", "err", err)
				} else if n > 0 {
					log.Warn("再起動で止まったジョブを failed にしました", "n", n)
				}
				srv := httpapi.New(s, analysis.New(status.AnalysisURL))
				srv.Log = log
				srv.LLM = llmCfg
				srv.Password = os.Getenv("APP_PASSWORD")
				srv.Status = status
				if srv.Password == "" {
					log.Warn("APP_PASSWORD が未設定です。パスワード無しで動きます（手元で使うときだけにしてください）")
				}
				if dir := env("WEB_DIR", "../web"); dir != "" {
					if fi, err := os.Stat(dir); err == nil && fi.IsDir() {
						srv.Static = http.FileServer(http.Dir(dir))
					}
				}
				g.mu.Lock()
				g.ready, g.lastErr = srv.Handler(), ""
				g.mu.Unlock()
				log.Info("ready", "db", status.DB, "persistent", status.DBPersistent)
				return
			}
			msg := sanitize(err.Error(), dbPath)
			g.mu.Lock()
			g.lastErr = msg
			g.mu.Unlock()
			log.Error("DB を開けません。待ってやり直します", "kind", status.DB, "try", try, "wait", wait.String(), "err", msg)
			time.Sleep(wait)
			if wait < 30*time.Second {
				wait *= 2
			}
		}
	}()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	<-ctx.Done()
	shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = hs.Shutdown(shutdown)
	stMu.Lock()
	if st != nil {
		st.Close()
	}
	stMu.Unlock()
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

// sanitize は外へ出すエラーから接続文字列（パスワードを含む）を消し、長さを切る。
func sanitize(msg, dsn string) string {
	if dsn != "" {
		msg = strings.ReplaceAll(msg, dsn, "(DB_PATH)")
	}
	if i := strings.Index(msg, "://"); i >= 0 {
		// user:pass@host の形が残っていたら丸ごと伏せる
		if j := strings.Index(msg[i:], "@"); j >= 0 {
			msg = msg[:i+3] + "***" + msg[i+j:]
		}
	}
	if len(msg) > 300 {
		msg = msg[:300] + "…"
	}
	return msg
}
