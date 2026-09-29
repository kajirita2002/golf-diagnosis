package ingest

import (
	"net/url"
	"testing"
)

func TestReportIDはURLかIDから取り出す(t *testing.T) {
	const id = "26828541-c2b0-f111-8234-f42679e923bf"
	for _, in := range []string{
		id,
		"  " + id + "\n",
		"https://web-dynamic-reports.trackmangolf.com/?a=" + id,
		"https://web-dynamic-reports.trackmangolf.com/?a=" + id + "&dm=c&v=clubData&mp%5B%5D=ClubSpeed",
	} {
		got, err := ReportID(in)
		if err != nil || got != id {
			t.Errorf("%q: %q %v", in, got, err)
		}
	}
	for _, bad := range []string{
		"",
		"https://example.com/?a=" + id,
		"http://web-dynamic-reports.trackmangolf.com/?a=" + id,
		"https://web-dynamic-reports.trackmangolf.com/?a=not-an-id",
		"javascript:alert(1)",
	} {
		if _, err := ReportID(bad); err == nil {
			t.Errorf("%q を受けてしまった", bad)
		}
	}
}

func TestReportLinkは10項目とClubDataで開く(t *testing.T) {
	const id = "26828541-c2b0-f111-8234-f42679e923bf"
	u, err := url.Parse(ReportLink(id))
	if err != nil {
		t.Fatal(err)
	}
	q := u.Query()
	if u.Host != trackmanReportHost || q.Get("a") != id || q.Get("v") != "clubData" || q.Get("u") != "m" {
		t.Fatalf("%v", u)
	}
	if got := q["mp[]"]; len(got) != 10 || got[0] != "ClubSpeed" || got[9] != "ImpactOffset" {
		t.Fatalf("mp[] = %v", got)
	}
	// 作ったリンクからまた ID が取れる
	if back, err := ReportID(u.String()); err != nil || back != id {
		t.Fatalf("%q %v", back, err)
	}
}
