package ingest

import (
	"fmt"
	"net/url"
	"regexp"
	"strings"
)

// TrackMan のレポート（web-dynamic-reports）を、診断に要る10項目と Club data の表で開くリンクを作る。
//
// ユーザーは押して開き、スクリーンショットを撮って貼るだけにする（表示の項目を毎回選ばせない）。
// パラメータは 2026-09-29 に本人が送ってくれた URL（10項目を選んだ状態）から取った。
// sgos[]（どの打球群を選ぶか）の意味は確かめていない。表が空で開くときはここを見直す。

const trackmanReportHost = "web-dynamic-reports.trackmangolf.com"

// ReportMetrics は表に出す列。診断に要る10項目（docs/DESIGN.md）。
var ReportMetrics = []string{
	"ClubSpeed", "AttackAngle", "Carry", "Side", "LaunchDirection",
	"ClubPath", "DynamicLoft", "FaceAngle", "SpinAxis", "ImpactOffset",
}

var reportID = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)

// ReportID はレポートの URL（か ID そのもの）からレポートの ID を取り出す。
// TrackMan 以外の URL は受けない（任意の URL を開かせる入口にしない）。
func ReportID(input string) (string, error) {
	s := strings.TrimSpace(input)
	if reportID.MatchString(s) {
		return strings.ToLower(s), nil
	}
	u, err := url.Parse(s)
	if err != nil || u.Scheme != "https" || u.Host != trackmanReportHost {
		return "", fmt.Errorf("TrackMan のレポートの URL（https://%s/?a=...）を入れてください", trackmanReportHost)
	}
	id := u.Query().Get("a")
	if !reportID.MatchString(id) {
		return "", fmt.Errorf("URL にレポートの ID（a=...）がありません")
	}
	return strings.ToLower(id), nil
}

// ReportLink は10項目・Club data・メートル法で開くリンクを返す。
func ReportLink(id string) string {
	q := url.Values{}
	q.Set("a", id)
	q.Set("dm", "c")
	q.Set("nd", "true")
	q.Set("nd_ballType", "Premium")
	q.Set("nd_altitude", "0")
	q.Set("nd_temperature", "25")
	q.Set("nd_altitudeUnit", "Meters")
	q.Set("nd_temperatureUnit", "Celsius")
	q.Set("op", "false")
	q.Set("sro", "false")
	q.Set("do", "true")
	q.Set("to", "true")
	q.Set("vo", "true")
	q.Set("cdo", "true")
	q.Set("ot", "c")
	q.Set("ov", "d")
	for _, m := range ReportMetrics {
		q.Add("mp[]", m)
	}
	q.Set("u", "m")
	q.Set("v", "clubData")
	q.Add("sgos[]", id)
	return "https://" + trackmanReportHost + "/?" + q.Encode()
}
