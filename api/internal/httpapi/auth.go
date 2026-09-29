package httpapi

import (
	"crypto/sha256"
	"crypto/subtle"
	"net/http"
)

// 公開するときのパスワード（Basic 認証）。
//
// 1人で使うアプリなので、ログイン画面やアカウントは作らない。ブラウザが1回聞いて覚える。
// パスワードが無いまま公開すると、URL を知った人があなたの Claude の API キーで
// スクショ読み取りを動かせる（料金がかかる）ので、公開の設定（render.yaml）では必ず入れる。
// /healthz だけは通す（デプロイ先の死活監視が使う。中身は版の番号だけ）。

func (s *Server) requireAuth(next http.Handler) http.Handler {
	if s.Password == "" {
		return next
	}
	want := sha256.Sum256([]byte(s.Password))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/healthz" {
			next.ServeHTTP(w, r)
			return
		}
		_, pass, ok := r.BasicAuth()
		got := sha256.Sum256([]byte(pass))
		// 長さで答えが漏れないように、ハッシュどうしを一定時間で比べる
		if !ok || subtle.ConstantTimeCompare(got[:], want[:]) != 1 {
			w.Header().Set("WWW-Authenticate", `Basic realm="Swing Lab", charset="UTF-8"`)
			http.Error(w, "パスワードが要ります", http.StatusUnauthorized)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// securityHeaders は全部の応答に付ける。
// Referrer-Policy: TrackMan のレポートの ID を URL に持つので、よそへ送らない。
func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("Referrer-Policy", "no-referrer")
		h.Set("X-Frame-Options", "DENY")
		next.ServeHTTP(w, r)
	})
}
