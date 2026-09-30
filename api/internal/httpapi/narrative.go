package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/kajirita2002/golf-diagnosis/api/internal/analysis"
	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
	"github.com/kajirita2002/golf-diagnosis/api/internal/store"
)

// 解説のつなぎの文（Claude・段階A。docs/DESIGN_coaching.md §5.8・§10.2・§11 Phase 1c）。
//
//   - **REPORT_LLM が on でなければ Claude を一切呼ばない**（既定 off）。解説は定型文だけで完結する（R10）。
//   - **ジョブにする。** POST は llm_jobs に行を入れて 202 を返し、要求とは切り離した context で分析サービスを
//     呼んで結果を保存する。スマホの画面が消えて fetch が切れても、払った生成を捨てない。画面は /v1/jobs/{id} を
//     数秒おきに見る。起動時に走っていたはずのジョブは failed にする（main が FailStaleLLMJobs を呼ぶ）。
//   - **キャッシュ。** 鍵は (kind, input_hash, 実際に答えたモデル)。input_hash は分析サービスが入力・facts・版・
//     PROMPT_VERSION・MODEL から作る（球の除外で変わる）。同じ入力なら Claude を呼ばない。頼んだモデルの答えが
//     無くても、フォールバックで別のモデルが答えたものがあればそれを返す（答えたモデルの名前と料金は画面に出す）。
//     同じ入力が今日断られていれば（refusal）、その日は呼び直さない。
//   - **上限。** LLM_DAILY_LIMIT_NARRATIVE（既定 20/日・UTC の日付・範囲ごとに1回）。超えたら定型文のまま。
//     キャッシュに当たったぶんは数えない。結局 Claude を呼ばなかったとき（キーが無いなど）は枠を返す。

// LLMConfig は Claude を使うかと上限。
type LLMConfig struct {
	Enabled bool // REPORT_LLM=on
	// DailyLimitNarrative は1日に Claude を呼ぶ範囲の数。負なら上限なし。
	DailyLimitNarrative int
	// DailyLimitVideo は1日に見た目の評価（動画のコマ）で Claude を呼ぶ回数。負なら上限なし（既定）。
	// 見た目の評価は REPORT_LLM に関係なく、本人が料金を見て押したときだけ呼ぶ（§6.7）。
	DailyLimitVideo int
	// Timeout は1範囲ぶんの生成を待つ長さ（要求とは切り離して数える）。
	Timeout time.Duration
	// Concurrency は同時に走らせる生成の数（CPU 0.1・512MB の無料プランで詰まらせない）。
	Concurrency int
}

// DefaultNarrativeLimit は LLM_DAILY_LIMIT_NARRATIVE の既定（§5.8）。
const DefaultNarrativeLimit = 20

// DefaultLLMConfig は既定（off・20/日）。
func DefaultLLMConfig() LLMConfig {
	return LLMConfig{Enabled: false, DailyLimitNarrative: DefaultNarrativeLimit, DailyLimitVideo: -1, Timeout: 5 * time.Minute, Concurrency: 2}
}

// LLMConfigFromEnv は環境変数から読む。読めない値は既定に戻し、その理由を warnings に返す。
//
//	REPORT_LLM=on|off                既定 off（on 以外は全部 off）
//	LLM_DAILY_LIMIT_NARRATIVE=<整数>  既定 20。負なら上限なし、0 なら呼ばない
func LLMConfigFromEnv(get func(string) string) (LLMConfig, []string) {
	c := DefaultLLMConfig()
	var warns []string
	switch v := strings.ToLower(strings.TrimSpace(get("REPORT_LLM"))); v {
	case "on":
		c.Enabled = true
	case "", "off":
	default:
		warns = append(warns, fmt.Sprintf("REPORT_LLM=%q は on / off ではないので off にしました", v))
	}
	if v := strings.TrimSpace(get("LLM_DAILY_LIMIT_NARRATIVE")); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			warns = append(warns, fmt.Sprintf("LLM_DAILY_LIMIT_NARRATIVE=%q は整数ではないので %d にしました", v, DefaultNarrativeLimit))
		} else {
			c.DailyLimitNarrative = n
		}
	}
	if v := strings.TrimSpace(get("LLM_DAILY_LIMIT_VIDEO")); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			warns = append(warns, fmt.Sprintf("LLM_DAILY_LIMIT_VIDEO=%q は整数ではないので上限なしにしました", v))
		} else {
			c.DailyLimitVideo = n
		}
	}
	return c, warns
}

// llmRuntime は走っている生成（テストで待つため）と、同時に走らせる数の枠。
type llmRuntime struct {
	once sync.Once
	sem  chan struct{}
	wg   sync.WaitGroup
}

func (s *Server) llmSem() chan struct{} {
	s.llm.once.Do(func() {
		n := s.LLM.Concurrency
		if n <= 0 {
			n = 1
		}
		s.llm.sem = make(chan struct{}, n)
	})
	return s.llm.sem
}

// WaitLLMJobs は走っている生成が終わるのを待つ（テストと終了のとき）。
func (s *Server) WaitLLMJobs() { s.llm.wg.Wait() }

func llmDay() string { return time.Now().UTC().Format("2006-01-02") }

// reportNarrativeMeta は /v1/report の応答のうち、つなぎの文に要るところだけ。
type reportNarrativeMeta struct {
	Scopes []struct {
		ScopeID   string `json:"scope_id"`
		Kind      string `json:"kind"`
		Label     string `json:"label"`
		Narrative *struct {
			InputHash     string `json:"input_hash"`
			PromptVersion string `json:"prompt_version"`
		} `json:"narrative"`
	} `json:"scopes"`
	Inputs        map[string]json.RawMessage `json:"narrative_inputs"`
	Model         string                     `json:"narrative_model"`
	PromptVersion string                     `json:"narrative_prompt_version"`
}

// narrativeJobView は POST の応答の1範囲ぶん。
type narrativeJobView struct {
	ScopeID string             `json:"scope_id"`
	Label   string             `json:"label"`
	JobID   int64              `json:"job_id,omitempty"`
	Status  model.LLMJobStatus `json:"status,omitempty"`
	Cached  bool               `json:"cached,omitempty"`
	// Limit は上限に当たって Claude を呼ばなかった（定型文だけ）。
	Limit  bool   `json:"limit,omitempty"`
	Reason string `json:"reason,omitempty"`
}

type narrativeStart struct {
	Enabled bool               `json:"enabled"`
	Reason  string             `json:"reason,omitempty"`
	Jobs    []narrativeJobView `json:"jobs"`
	Day     string             `json:"day,omitempty"`
	Limit   int                `json:"limit"`
	Used    int                `json:"used"`
	CostUSD float64            `json:"cost_usd_today"`
}

// startNarrative は POST /v1/sessions/{id}/report/narrative。本体の範囲ごとにジョブを作って 202 を返す。
func (s *Server) startNarrative(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	if !s.LLM.Enabled {
		writeJSON(w, http.StatusOK, narrativeStart{Enabled: false, Jobs: []narrativeJobView{}, Limit: s.LLM.DailyLimitNarrative,
			Reason: "REPORT_LLM が off なので、Claude は呼びません（文章は定型文だけです）"})
		return
	}
	se, err := s.Store.GetSession(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	pl, err := s.Store.GetPlayer(r.Context(), se.PlayerID)
	if err != nil {
		s.fail(w, err)
		return
	}
	shots, err := s.Store.ListShots(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	exps, err := s.Store.ListExperiments(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	ps, err := s.sessionPayloads(r.Context(), id, shots)
	if err != nil {
		s.fail(w, err)
		return
	}
	// 定型文と入力は /report と同じ口で作り直す（入力は球の除外などで変わるので、画面から受け取らない）
	raw, err := s.Analyzer.Report(r.Context(), analysis.ReportInput{Shots: ps, Handedness: pl.Handedness, Experiments: exps})
	if err != nil {
		s.fail(w, err)
		return
	}
	var meta reportNarrativeMeta
	if err := json.Unmarshal(raw, &meta); err != nil {
		s.fail(w, fmt.Errorf("分析サービスの応答を読めません: %w", err))
		return
	}
	day := llmDay()
	out := narrativeStart{Enabled: true, Jobs: []narrativeJobView{}, Day: day, Limit: s.LLM.DailyLimitNarrative}
	for _, sc := range meta.Scopes {
		inp, ok := meta.Inputs[sc.ScopeID]
		if sc.Kind != "main" || sc.Narrative == nil || sc.Narrative.InputHash == "" || !ok {
			continue
		}
		v := narrativeJobView{ScopeID: sc.ScopeID, Label: sc.Label}
		hash := sc.Narrative.InputHash
		// 同じ入力の答えがあれば呼ばない（上限にも数えない）。頼んだモデルの答えを先に探し、無ければフォールバックで
		// 別のモデルが答えたものを返す（表示では答えたモデルの名前と料金を出す）。前は頼んだモデルだけで引いたので、
		// 別のモデルが答えた範囲は押すたびに呼び直して払っていた（§5.8）
		j, err := s.Store.FindDoneLLMJob(r.Context(), model.LLMNarrative, hash, meta.Model)
		if err == nil && j == nil {
			j, err = s.Store.FindDoneLLMJob(r.Context(), model.LLMNarrative, hash, "")
		}
		if err != nil {
			s.fail(w, err)
			return
		}
		if j != nil {
			v.JobID, v.Status, v.Cached = j.ID, j.Status, true
			out.Jobs = append(out.Jobs, v)
			continue
		}
		// 同じ入力が今日すでに断られていれば呼ばない（同じように断られるので、押すたびに払わない）
		if j, err := s.Store.FindRefusedLLMJobSince(r.Context(), model.LLMNarrative, hash, day); err != nil {
			s.fail(w, err)
			return
		} else if j != nil {
			v.JobID, v.Status = j.ID, j.Status
			v.Reason = "同じ内容は今日 Claude に断られたので、定型文だけです（明日また頼めます）"
			out.Jobs = append(out.Jobs, v)
			continue
		}
		// 二度押し: 同じ入力で走っているジョブがあれば、それを返す
		if j, err := s.Store.FindActiveLLMJob(r.Context(), model.LLMNarrative, hash); err != nil {
			s.fail(w, err)
			return
		} else if j != nil {
			v.JobID, v.Status = j.ID, j.Status
			out.Jobs = append(out.Jobs, v)
			continue
		}
		got, err := s.Store.ReserveLLMCall(r.Context(), day, string(model.LLMNarrative), s.LLM.DailyLimitNarrative)
		if err != nil {
			s.fail(w, err)
			return
		}
		if !got {
			v.Limit = true
			v.Reason = fmt.Sprintf("今日の上限（%d範囲）に達したので、Claude は呼ばずに定型文だけです", s.LLM.DailyLimitNarrative)
			out.Jobs = append(out.Jobs, v)
			continue
		}
		sid := id
		job := &model.LLMJob{Kind: model.LLMNarrative, SessionID: &sid, ScopeID: sc.ScopeID, InputHash: hash, Model: meta.Model, PromptVersion: meta.PromptVersion}
		if err := s.Store.CreateLLMJob(r.Context(), job); err != nil {
			_ = s.Store.ReleaseLLMCall(r.Context(), day, string(model.LLMNarrative))
			s.fail(w, err)
			return
		}
		s.runNarrative(job.ID, analysis.NarrativeInput{Input: inp, InputHash: hash}, day)
		v.JobID, v.Status = job.ID, job.Status
		out.Jobs = append(out.Jobs, v)
	}
	if u, err := s.Store.GetLLMUsage(r.Context(), day, string(model.LLMNarrative)); err == nil {
		out.Used, out.CostUSD = u.Calls, u.CostUSD
	}
	writeJSON(w, http.StatusAccepted, out)
}

// narrativeResult は分析サービスの応答のうち、保存と数えるのに要るところ。
type narrativeResult struct {
	Called     int             `json:"called"`
	Model      *string         `json:"model"`
	Cacheable  bool            `json:"cacheable"`
	Reason     *string         `json:"reason"`
	Validation json.RawMessage `json:"validation"`
	Usage      struct {
		InputTokens  int64    `json:"input_tokens"`
		OutputTokens int64    `json:"output_tokens"`
		CostUSD      *float64 `json:"cost_usd"`
	} `json:"usage"`
}

// runNarrative は要求と切り離して生成し、結果を保存する。
func (s *Server) runNarrative(jobID int64, in analysis.NarrativeInput, day string) {
	s.llm.wg.Add(1)
	go func() {
		defer s.llm.wg.Done()
		sem := s.llmSem()
		sem <- struct{}{}
		defer func() { <-sem }()
		timeout := s.LLM.Timeout
		if timeout <= 0 {
			timeout = 5 * time.Minute
		}
		ctx, cancel := context.WithTimeout(context.Background(), timeout)
		defer cancel()
		kind := string(model.LLMNarrative)
		_ = s.Store.SetLLMJobRunning(ctx, jobID)
		raw, err := s.Analyzer.Narrative(ctx, in)
		// 保存は生成の時間切れと別に数える（切れても理由を書き残す）
		sctx, scancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer scancel()
		if err != nil {
			_ = s.Store.ReleaseLLMCall(sctx, day, kind)
			s.Log.Error("narrative failed", "job", jobID, "err", err)
			_ = s.Store.FinishLLMJob(sctx, jobID, store.LLMJobFinish{Status: model.LLMFailed, Error: "分析サービスでつなぎの文を作れませんでした: " + err.Error()})
			return
		}
		var res narrativeResult
		if err := json.Unmarshal(raw, &res); err != nil {
			_ = s.Store.ReleaseLLMCall(sctx, day, kind)
			_ = s.Store.FinishLLMJob(sctx, jobID, store.LLMJobFinish{Status: model.LLMFailed, Error: "分析サービスの応答を読めません"})
			return
		}
		if res.Called == 0 {
			_ = s.Store.ReleaseLLMCall(sctx, day, kind) // 呼んでいないので上限に数えない
		} else {
			cost := 0.0
			if res.Usage.CostUSD != nil {
				cost = *res.Usage.CostUSD
			}
			_ = s.Store.AddLLMUsage(sctx, day, kind, res.Usage.InputTokens, res.Usage.OutputTokens, cost)
		}
		usage, _ := json.Marshal(res.Usage)
		f := store.LLMJobFinish{Status: model.LLMDone, Result: raw, Validation: res.Validation, Usage: usage, CostUSD: res.Usage.CostUSD}
		if res.Model != nil {
			f.Model = *res.Model // 実際に答えたモデル（フォールバックで変わりうる）
		}
		if !res.Cacheable {
			// キーが無い・API が落ちていた・off など。定型文の結果は残すが、使い回さない
			f.Status = model.LLMFailed
			if res.Reason != nil {
				f.Error = *res.Reason
			}
		}
		if err := s.Store.FinishLLMJob(sctx, jobID, f); err != nil {
			s.Log.Error("narrative save failed", "job", jobID, "err", err)
		}
	}()
}

// getJob は GET /v1/jobs/{id}。ジョブの状態と結果（つなぎの文・動画の review で共通）。
func (s *Server) getJob(w http.ResponseWriter, r *http.Request) {
	id, err := pathID(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	j, err := s.Store.GetLLMJob(r.Context(), id)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, j)
}

// narrativeView は /report の応答から narrative_inputs（Go だけが使う）を外し、Claude が on なら、
// 本体の範囲ごとに保存したつなぎの文（キャッシュ）を scopes[].narrative に足す。
//
// 頼んだモデルの答えを先に探し、無ければほかのモデルの答え（フォールバック）を、答えたモデルの名前と
// 料金つきで出す（POST も同じ順に引くので、別のモデルの答えがある範囲は呼び直さない）。
func (s *Server) narrativeView(ctx context.Context, raw json.RawMessage) (json.RawMessage, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v map[string]any
	if err := dec.Decode(&v); err != nil {
		return nil, fmt.Errorf("分析サービスの応答を読めません: %w", err)
	}
	if v == nil {
		return raw, nil
	}
	delete(v, "narrative_inputs")
	mdl, _ := v["narrative_model"].(string)
	scopes, _ := v["scopes"].([]any)
	if s.LLM.Enabled {
		for _, x := range scopes {
			sc, _ := x.(map[string]any)
			narr, _ := sc["narrative"].(map[string]any)
			hash, _ := narr["input_hash"].(string)
			if hash == "" {
				continue
			}
			j, err := s.Store.FindDoneLLMJob(ctx, model.LLMNarrative, hash, mdl)
			if err == nil && j == nil {
				j, err = s.Store.FindDoneLLMJob(ctx, model.LLMNarrative, hash, "")
			}
			if err != nil {
				return nil, err
			}
			if j == nil {
				continue
			}
			narr["job_id"] = j.ID
			narr["result"] = j.Result
			narr["model"] = j.Model
			narr["cost_usd"] = j.CostUSD
		}
	}
	return json.Marshal(v)
}
