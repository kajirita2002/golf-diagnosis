package httpapi

import (
	"context"
	"encoding/json"
	"strings"
	"sync"
	"testing"

	"github.com/kajirita2002/golf-diagnosis/api/internal/analysis"
)

// 偽の分析サービス（理想との比較の口）。受けた中身は fakeAnalyzer ごとに覚える
var (
	idealMu    sync.Mutex
	idealCalls = map[*fakeAnalyzer][]struct {
		sw   analysis.CheckpointSwing
		item string
	}{}
)

func (f *fakeAnalyzer) CheckpointsIdeal(_ context.Context, sw analysis.CheckpointSwing, item string) (json.RawMessage, error) {
	if f.down {
		return nil, analysis.ErrUnavailable
	}
	idealMu.Lock()
	idealCalls[f] = append(idealCalls[f], struct {
		sw   analysis.CheckpointSwing
		item string
	}{sw, item})
	idealMu.Unlock()
	return json.RawMessage(`{"item_id":"` + item + `","p":"P2","state":"out_range","zone":{"kind":"circle","c":[690,371],"r_lo":0,"r_hi":16},"fixed":{"p":"P2","moved":["head"]}}`), nil
}

func (c *cpEnv) swingWithFrames(t *testing.T, view string) int64 {
	id := c.swing(view)
	frames := []map[string]any{}
	for _, p := range []string{"P1", "P2", "P3", "P4", "P5", "P6", "P7"} {
		frames = append(frames, map[string]any{"checkpoint": p, "t": 0.5, "frame": 120, "landmarks": lm33(), "thumb": jpegB64(t, 360, 202),
			"taps": map[string]any{"grip": []float64{690, 370}, "head": []float64{640, 360}}})
	}
	c.do("PUT", "/v1/swings/"+jsonNum(id)+"/frames", map[string]any{"frames": frames, "missing": []string{}}, 200)
	return id
}

func Test理想の線は分析サービスに測る入力と項目を渡して中継する(t *testing.T) {
	c := newCPEnv(t, "L")
	id := c.swingWithFrames(t, "dtl")
	out := c.do("GET", "/v1/swings/"+jsonNum(id)+"/ideal?item=iron.p2.dtl.head_vs_hands", nil, 200)
	calls := idealCalls[c.an]
	if len(calls) != 1 || calls[0].item != "iron.p2.dtl.head_vs_hands" || len(calls[0].sw.Frames) != 7 || calls[0].sw.Handedness != "L" {
		t.Fatalf("渡した中身: %+v", calls)
	}
	if !strings.Contains(string(calls[0].sw.Frames["P2"]), `"grip":[690,370]`) || strings.Contains(string(calls[0].sw.Frames["P2"]), "thumb") {
		t.Fatalf("タップが渡っていない・サムネイルを渡している: %s", calls[0].sw.Frames["P2"])
	}
	if out["thumb"] != "/v1/swings/"+jsonNum(id)+"/thumbs/P2" || out["width"] != float64(1280) || out["handedness"] != "L" {
		t.Fatalf("描く下地: %v", out)
	}
	if len(out["thumbs"].([]any)) != 7 {
		t.Fatalf("サムネイルのある P: %v", out["thumbs"])
	}
	c.do("GET", "/v1/swings/"+jsonNum(id)+"/ideal", nil, 400)
	c.do("GET", "/v1/swings/"+jsonNum(id)+"/ideal?item=../x", nil, 400)
	c.do("GET", "/v1/swings/99999/ideal?item=a.b", nil, 404)
}

func Test自分のベストは同じ項目が範囲の中だったスイングを新しい順に3本まで(t *testing.T) {
	c := newCPEnv(t, "R")
	pid := int64(c.do("GET", "/v1/sessions/"+jsonNum(c.sid), nil, 200)["player_id"].(float64))
	ids := []int64{}
	for i, d := range []string{"2026-09-20", "2026-09-25", "2026-09-27", "2026-09-28"} {
		se := c.do("POST", "/v1/sessions", map[string]any{"player_id": pid, "date": d}, 201)
		c.sid = int64(se["id"].(float64))
		ids = append(ids, c.swingWithFrames(t, "dtl"))
		_ = i
	}
	out := c.do("GET", "/v1/players/"+jsonNum(pid)+"/best?item=iron.p1.dtl.hands&p=P1", nil, 200)
	best := out["best"].([]any)
	if len(best) != 3 {
		t.Fatalf("3本まで: %v", best)
	}
	first := best[0].(map[string]any)
	if first["swing_id"] != float64(ids[3]) || first["date"] != "2026-09-28" || first["thumb"] != "/v1/swings/"+jsonNum(ids[3])+"/thumbs/P1" {
		t.Fatalf("新しい順: %v", best)
	}
	// 範囲の外だった項目にはベストが無い
	if b := c.do("GET", "/v1/players/"+jsonNum(pid)+"/best?item=iron.p2.dtl.head_vs_hands", nil, 200)["best"].([]any); len(b) != 0 {
		t.Fatalf("範囲の外の項目: %v", b)
	}
	// 消したスイングは出さない
	c.do("DELETE", "/v1/swings/"+jsonNum(ids[3]), nil, 204)
	best = c.do("GET", "/v1/players/"+jsonNum(pid)+"/best?item=iron.p1.dtl.hands&p=P1", nil, 200)["best"].([]any)
	if best[0].(map[string]any)["swing_id"] != float64(ids[2]) {
		t.Fatalf("消したスイング: %v", best)
	}
	// サムネイルの無い P を指すと出さない
	if b := c.do("GET", "/v1/players/"+jsonNum(pid)+"/best?item=iron.p1.dtl.hands&p=P9", nil, 200)["best"].([]any); len(b) != 0 {
		t.Fatalf("サムネイルの無い P: %v", b)
	}
	c.do("GET", "/v1/players/"+jsonNum(pid)+"/best?item=iron.p1.dtl.hands&p=X", nil, 400)
	c.do("GET", "/v1/players/99999/best?item=a.b", nil, 404)
}
