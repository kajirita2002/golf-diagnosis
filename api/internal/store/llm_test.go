package store

import (
	"context"
	"encoding/json"
	"os"
	"regexp"
	"sync"
	"testing"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
)

// llm.go の SQL のテーブル名にも {s} が付いている（PostgreSQL では english-tts と同じ DB の golf スキーマ）。
func TestLLMのテーブル名はスキーマの印付き(t *testing.T) {
	re := regexp.MustCompile(`(?i)\b(FROM|INTO|UPDATE|EXISTS|REFERENCES|JOIN|ON)\s+(llm_jobs|llm_usage|sessions)\b`)
	for _, f := range []string{"store.go", "llm.go"} {
		src, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		if m := re.FindAllString(string(src), -1); len(m) > 0 {
			t.Fatalf("%s: {s} の付いていないテーブル名: %v", f, m)
		}
	}
}

// キャッシュの鍵は (kind, input_hash, 実際に答えたモデル)。フォールバックで別のモデルが答えたものは、
// 頼んだモデルの答えとして引かない。done でないもの（failed）は使い回さない。
func TestLLMジョブのキャッシュは実際に答えたモデルで引く(t *testing.T) {
	st := openTest(t)
	ctx := context.Background()
	mk := func(hash string) *model.LLMJob {
		j := &model.LLMJob{Kind: model.LLMNarrative, ScopeID: "group:iron", InputHash: hash, Model: "claude-opus-5-5", PromptVersion: "narrative/0.1"}
		if err := st.CreateLLMJob(ctx, j); err != nil {
			t.Fatal(err)
		}
		return j
	}
	a := mk("h1")
	if j, _ := st.FindActiveLLMJob(ctx, model.LLMNarrative, "h1"); j == nil || j.ID != a.ID {
		t.Fatalf("走っているジョブが引けない: %+v", j)
	}
	if err := st.SetLLMJobRunning(ctx, a.ID); err != nil {
		t.Fatal(err)
	}
	cost := 0.0108
	if err := st.FinishLLMJob(ctx, a.ID, LLMJobFinish{Status: model.LLMDone, Result: json.RawMessage(`{"x":1}`), Usage: json.RawMessage(`{"input_tokens":1}`), CostUSD: &cost, Model: "claude-sonnet-5-5"}); err != nil {
		t.Fatal(err)
	}
	if j, _ := st.FindDoneLLMJob(ctx, model.LLMNarrative, "h1", "claude-opus-5-5"); j != nil {
		t.Fatal("別のモデルの答えを、頼んだモデルの答えとして引いた")
	}
	j, err := st.FindDoneLLMJob(ctx, model.LLMNarrative, "h1", "claude-sonnet-5-5")
	if err != nil || j == nil || j.Model != "claude-sonnet-5-5" || string(j.Result) != `{"x":1}` || j.CostUSD == nil {
		t.Fatalf("%+v %v", j, err)
	}
	b := mk("h2")
	if err := st.FinishLLMJob(ctx, b.ID, LLMJobFinish{Status: model.LLMFailed, Error: "キーが無い"}); err != nil {
		t.Fatal(err)
	}
	if j, _ := st.FindDoneLLMJob(ctx, model.LLMNarrative, "h2", "claude-opus-5-5"); j != nil {
		t.Fatal("failed を使い回した")
	}
	got, _ := st.GetLLMJob(ctx, b.ID)
	if got.Model != "claude-opus-5-5" || got.Error != "キーが無い" || string(got.Result) != "null" {
		t.Fatalf("%+v", got)
	}
	// 起動時に、走っていたはずのジョブは failed
	c := mk("h3")
	if n, err := st.FailStaleLLMJobs(ctx); err != nil || n != 1 {
		t.Fatalf("%d %v", n, err)
	}
	if got, _ := st.GetLLMJob(ctx, c.ID); got.Status != model.LLMFailed {
		t.Fatalf("%+v", got)
	}
}

// 上限は1文の UPDATE の条件で数える。同時に来ても上限を超えない。返した枠はまた使える。
func TestLLMの上限(t *testing.T) {
	st := openTest(t)
	ctx := context.Background()
	var wg sync.WaitGroup
	var mu sync.Mutex
	ok := 0
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			got, err := st.ReserveLLMCall(ctx, "2026-09-29", "narrative", 3)
			if err != nil {
				t.Error(err)
			}
			if got {
				mu.Lock()
				ok++
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	if ok != 3 {
		t.Fatalf("上限3で %d 回取れた", ok)
	}
	if err := st.ReleaseLLMCall(ctx, "2026-09-29", "narrative"); err != nil {
		t.Fatal(err)
	}
	if got, _ := st.ReserveLLMCall(ctx, "2026-09-29", "narrative", 3); !got {
		t.Fatal("返した枠が使えない")
	}
	if got, _ := st.ReserveLLMCall(ctx, "2026-09-30", "narrative", 3); !got {
		t.Fatal("日が変われば数え直す")
	}
	if got, _ := st.ReserveLLMCall(ctx, "2026-09-29", "video_review", -1); !got {
		t.Fatal("上限なし")
	}
	if err := st.AddLLMUsage(ctx, "2026-09-29", "narrative", 1200, 300, 0.0108); err != nil {
		t.Fatal(err)
	}
	u, err := st.GetLLMUsage(ctx, "2026-09-29", "narrative")
	if err != nil || u.Calls != 3 || u.InputTokens != 1200 || u.OutputTokens != 300 || u.CostUSD < 0.0107 || u.CostUSD > 0.0109 {
		t.Fatalf("%+v %v", u, err)
	}
}
