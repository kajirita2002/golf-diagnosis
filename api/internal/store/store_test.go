package store

import (
	"os"
	"regexp"
	"testing"
)

func TestRebindはPostgreSQLのときだけ置き換える(t *testing.T) {
	q := "SELECT a FROM {s}shots WHERE x=? AND y IN (?,?)"
	if got := rebind(false, q); got != "SELECT a FROM shots WHERE x=? AND y IN (?,?)" {
		t.Fatalf("%q", got)
	}
	if got := rebind(true, q); got != "SELECT a FROM golf.shots WHERE x=$1 AND y IN ($2,$3)" {
		t.Fatalf("%q", got)
	}
}

// SQL の中のテーブル名には全部 {s} が付いている（付け忘れると PostgreSQL で public を見に行く）。
func Testテーブル名は全部スキーマの印付き(t *testing.T) {
	re := regexp.MustCompile(`(?i)\b(FROM|INTO|UPDATE|EXISTS|REFERENCES|JOIN|ON)\s+(players|sessions|shots|experiments|blocks|plans|plan_runs)\b`)
	for _, f := range []string{"store.go", "plan.go"} {
		src, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		if m := re.FindAllString(string(src), -1); len(m) > 0 {
			t.Fatalf("%s: {s} の付いていないテーブル名: %v", f, m)
		}
	}
}
