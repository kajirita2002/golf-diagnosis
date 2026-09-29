package store

import "testing"

func TestRebindはPostgreSQLのときだけ置き換える(t *testing.T) {
	q := "SELECT a FROM t WHERE x=? AND y IN (?,?)"
	if got := rebind(false, q); got != q {
		t.Fatalf("%q", got)
	}
	if got := rebind(true, q); got != "SELECT a FROM t WHERE x=$1 AND y IN ($2,$3)" {
		t.Fatalf("%q", got)
	}
}
