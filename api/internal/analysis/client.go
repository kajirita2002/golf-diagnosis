// Package analysis は Python の分析サービス（深い診断）を呼ぶ。
//
// 分析サービスは状態を持たない。必要な球は毎回ここから渡す。
// 分析サービスが落ちていても、1球ごとの物理分解（速い診断）は Go だけで返せる
// ―― 2段の速さに分けた理由（docs/DESIGN.md）。
package analysis

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
	"github.com/kajirita2002/golf-diagnosis/api/internal/physics"
)

// ErrUnavailable は分析サービスに届かない。
var ErrUnavailable = errors.New("分析サービスに接続できません")

// ShotPayload は分析サービスへ渡す1球。分解は Go で計算したものを渡す
// （同じ式を2か所に書かない）。
type ShotPayload struct {
	ID            int64                 `json:"id"`
	Seq           int                   `json:"seq"`
	Club          string                `json:"club"`
	ClubCategory  string                `json:"club_category"`
	Metrics       model.Metrics         `json:"metrics"`
	Estimated     []string              `json:"estimated"`
	Manual        []string              `json:"manual"`
	Excluded      bool                  `json:"excluded"`
	GoodOverride  *bool                 `json:"good_override"`
	Decomposition physics.Decomposition `json:"decomposition"`
}

// BlockPayload は実験の1ブロック。
type BlockPayload struct {
	Kind    model.BlockKind `json:"kind"`
	SeqFrom int             `json:"seq_from"`
	SeqTo   int             `json:"seq_to"`
	Shots   []ShotPayload   `json:"shots"`
}

// ServiceError は分析サービスが理由つきで断ったとき（画像の形式が違う・API キーが無いなど）。
// 理由は利用者に見せてよい文なので、そのまま返す。
type ServiceError struct {
	Status  int
	Message string
}

func (e *ServiceError) Error() string { return e.Message }

// Client は分析サービスのクライアント。
type Client struct {
	BaseURL string
	HTTP    *http.Client
}

// New はクライアントを作る。
func New(baseURL string) *Client {
	// スクリーンショットの読み取りは Claude の応答を待つので長めにとる
	return &Client{BaseURL: baseURL, HTTP: &http.Client{Timeout: 3 * time.Minute}}
}

// Session はセッション全体の分析を頼む。返り値は分析サービスの JSON をそのまま返す。
func (c *Client) Session(ctx context.Context, shots []ShotPayload) (json.RawMessage, error) {
	return c.post(ctx, "/v1/session", map[string]any{"shots": shots})
}

// Compare は2つのセッションの比較を頼む。a が比べる元（昨日）、b が今回。
func (c *Client) Compare(ctx context.Context, a, b []ShotPayload) (json.RawMessage, error) {
	return c.post(ctx, "/v1/compare", map[string]any{"a": a, "b": b})
}

// Screenshot はスクリーンショットの表を読ませる（取り込みはしない）。
// Claude の API を呼ぶので時間がかかる。
func (c *Client) Screenshot(ctx context.Context, mediaType string, image []byte) (json.RawMessage, error) {
	return c.post(ctx, "/v1/screenshot", map[string]any{
		"media_type": mediaType,
		"data":       base64.StdEncoding.EncodeToString(image),
	})
}

// VerifyTSV は人が直した表をもう一度検算させる。
func (c *Client) VerifyTSV(ctx context.Context, tsv string) (json.RawMessage, error) {
	return c.post(ctx, "/v1/screenshot/verify", map[string]any{"tsv": tsv})
}

// Experiment は実験の評価を頼む。
func (c *Client) Experiment(ctx context.Context, e *model.Experiment, blocks []BlockPayload) (json.RawMessage, error) {
	return c.post(ctx, "/v1/experiment", map[string]any{
		"target_metric": e.TargetMetric,
		"goal":          e.Goal,
		"blocks":        blocks,
	})
}

func (c *Client) post(ctx context.Context, path string, body any) (json.RawMessage, error) {
	b, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+path, bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrUnavailable, err)
	}
	defer resp.Body.Close()
	out, err := io.ReadAll(io.LimitReader(resp.Body, 16<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		var d struct {
			Detail any `json:"detail"`
		}
		if json.Unmarshal(out, &d) == nil {
			if msg, ok := d.Detail.(string); ok && msg != "" {
				return nil, &ServiceError{Status: resp.StatusCode, Message: msg}
			}
		}
		return nil, fmt.Errorf("分析サービスが %d を返しました: %s", resp.StatusCode, truncate(string(out), 500))
	}
	if !json.Valid(out) {
		return nil, fmt.Errorf("分析サービスの応答が JSON ではありません")
	}
	return out, nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
