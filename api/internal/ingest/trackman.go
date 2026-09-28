package ingest

import (
	"bufio"
	"bytes"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"io"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/kajirita2002/golf-diagnosis/api/internal/model"
	"github.com/kajirita2002/golf-diagnosis/api/internal/units"
)

// TrackMan は TrackMan の CSV 書き出しを読む。
//
// 実物の CSV を見るまでは、公開されている列名と、書き出しの版ごとの
// 揺れ（単位の行・列名の中の単位・区切り文字・小数のカンマ・R/L 付きの値）を
// 広めに受ける作りにしてある。実物が届いたら testdata に置いて固定する。
type TrackMan struct{}

func (TrackMan) Name() string    { return "trackman" }
func (TrackMan) Version() string { return "trackman-csv/0.1" }

type fieldSpec struct {
	key     string
	kind    units.Kind
	aliases []string // 正規化した列名（小文字の英数字だけ）。前にあるほど優先
	set     func(*model.Metrics, float64)
	// metricUnit は、単位が書いていない列をメートル法で読むときの単位。
	// 空なら種類ごとの既定（units.DefaultUnit）。TrackMan の画面では
	// 打点が mm・最下点が cm で、種類の既定（cm）と揃っていない。
	metricUnit string
}

func setter(f func(*model.Metrics) **float64) func(*model.Metrics, float64) {
	return func(m *model.Metrics, v float64) { *f(m) = &v }
}

var trackmanFields = []fieldSpec{
	{"club_speed", units.Speed, []string{"clubspeed"}, setter(func(m *model.Metrics) **float64 { return &m.ClubSpeed }), ""},
	{"ball_speed", units.Speed, []string{"ballspeed"}, setter(func(m *model.Metrics) **float64 { return &m.BallSpeed }), ""},
	{"smash_factor", units.Ratio, []string{"smashfactor", "smashfac", "smash"}, setter(func(m *model.Metrics) **float64 { return &m.SmashFactor }), ""},
	{"attack_angle", units.Angle, []string{"attackangle", "attackang"}, setter(func(m *model.Metrics) **float64 { return &m.AttackAngle }), ""},
	{"club_path", units.Angle, []string{"clubpath"}, setter(func(m *model.Metrics) **float64 { return &m.ClubPath }), ""},
	{"face_angle", units.Angle, []string{"faceangle", "faceang"}, setter(func(m *model.Metrics) **float64 { return &m.FaceAngle }), ""},
	{"face_to_path", units.Angle, []string{"facetopath"}, setter(func(m *model.Metrics) **float64 { return &m.FaceToPath }), ""},
	{"dynamic_loft", units.Angle, []string{"dynamicloft", "dynloft"}, setter(func(m *model.Metrics) **float64 { return &m.DynamicLoft }), ""},
	{"spin_loft", units.Angle, []string{"spinloft"}, setter(func(m *model.Metrics) **float64 { return &m.SpinLoft }), ""},
	{"swing_plane", units.Angle, []string{"swingplane", "swingpl"}, setter(func(m *model.Metrics) **float64 { return &m.SwingPlane }), ""},
	{"swing_direction", units.Angle, []string{"swingdirection", "swingdir"}, setter(func(m *model.Metrics) **float64 { return &m.SwingDirection }), ""},
	{"low_point", units.Small, []string{"lowpoint", "lowpointdistance"}, setter(func(m *model.Metrics) **float64 { return &m.LowPoint }), "cm"},
	{"impact_offset", units.Small, []string{"impactoffset", "impoffset"}, setter(func(m *model.Metrics) **float64 { return &m.ImpactOffset }), "mm"},
	{"impact_height", units.Small, []string{"impactheight", "impheight"}, setter(func(m *model.Metrics) **float64 { return &m.ImpactHeight }), "mm"},
	{"launch_angle", units.Angle, []string{"launchangle", "launchang", "launchv", "verticallaunch"}, setter(func(m *model.Metrics) **float64 { return &m.LaunchAngle }), ""},
	{"launch_direction", units.Angle, []string{"launchdirection", "launchdir", "launchh", "horizontallaunch"}, setter(func(m *model.Metrics) **float64 { return &m.LaunchDirection }), ""},
	{"spin_rate", units.Spin, []string{"spinrate", "totalspin"}, setter(func(m *model.Metrics) **float64 { return &m.SpinRate }), ""},
	{"spin_axis", units.Angle, []string{"spinaxis"}, setter(func(m *model.Metrics) **float64 { return &m.SpinAxis }), ""},
	{"carry", units.Distance, []string{"carry", "carrydistance", "carryflat"}, setter(func(m *model.Metrics) **float64 { return &m.Carry }), ""},
	{"total", units.Distance, []string{"total", "totaldistance", "totalflat"}, setter(func(m *model.Metrics) **float64 { return &m.Total }), ""},
	{"side", units.Distance, []string{"side", "carryside", "sidecarry"}, setter(func(m *model.Metrics) **float64 { return &m.Side }), ""},
	{"height", units.Distance, []string{"height", "maxheight"}, setter(func(m *model.Metrics) **float64 { return &m.Height }), ""},
	{"curve", units.Distance, []string{"curve"}, setter(func(m *model.Metrics) **float64 { return &m.Curve }), ""},
	{"landing_angle", units.Angle, []string{"landingangle", "landang"}, setter(func(m *model.Metrics) **float64 { return &m.LandingAngle }), ""},
	{"hang_time", units.Time, []string{"hangtime"}, setter(func(m *model.Metrics) **float64 { return &m.HangTime }), ""},
	{"dynamic_lie", units.Angle, []string{"dynamiclie", "dynlie"}, setter(func(m *model.Metrics) **float64 { return &m.DynamicLie }), ""},
}

var (
	clubAliases = []string{"club", "clubname", "clubtype"}
	dateAliases = []string{"date", "datetime", "timestamp", "time", "shottime"}
	// 平均・標準偏差などの集計行。TrackMan のレポートは表の下に付くことがある。
	summaryRow = regexp.MustCompile(`(?i)^(average|avg|mean|std\.?\s*dev\.?|stdev|standard deviation|min|max|median|consistency)$`)
	unitInName = regexp.MustCompile(`^(.*?)[\s]*[\[(]([^\])]*)[\])]\s*$`)
	// "4.0 A" / "2.1 B"（最下点のボールの先／手前）、"5.2 R" / "3.1 L"（左右）
	suffixed = regexp.MustCompile(`^\s*(-?[0-9]+(?:[.,][0-9]+)?)\s*([ABRLabrl])\s*$`)
	// 列名の正規化
	nonAlnum = regexp.MustCompile(`[^a-z0-9]+`)
)

func normName(s string) string {
	return nonAlnum.ReplaceAllString(strings.ToLower(s), "")
}

type column struct {
	index  int
	header string
	unit   string // CSV に書いてあった単位（無ければ空）
	spec   *fieldSpec
}

// Parse は CSV を読んで1球ずつにする。
func (t TrackMan) Parse(r io.Reader, opt Options) (*Result, error) {
	if opt.Units == "" {
		opt.Units = units.Imperial
	}
	data, err := io.ReadAll(r)
	if err != nil {
		return nil, err
	}
	data = bytes.TrimPrefix(data, []byte("\xef\xbb\xbf")) // BOM

	delim, body := detectDelimiter(data)
	cr := csv.NewReader(bytes.NewReader(body))
	cr.Comma = delim
	cr.FieldsPerRecord = -1
	cr.LazyQuotes = true
	cr.TrimLeadingSpace = true
	rows, err := cr.ReadAll()
	if err != nil {
		return nil, fmt.Errorf("CSV を読めません: %w", err)
	}

	res := &Result{Source: t.Name(), Version: t.Version(), Columns: map[string]string{}}

	hi := findHeader(rows)
	if hi < 0 {
		return nil, fmt.Errorf("見出しの行が見つかりません（Club Speed / Ball Speed などの列が要ります）")
	}
	header := rows[hi]
	var unitRow []string
	start := hi + 1
	if start < len(rows) && isUnitRow(rows[start]) {
		unitRow = rows[start]
		start++
	}

	cols, clubIdx, dateIdx := mapColumns(header, unitRow, res)
	if clubIdx < 0 && opt.Club != "" {
		res.Warnings = res.Warnings[:0] // クラブは指定で補うので、列が無い警告は出さない
	}

	for _, row := range rows[start:] {
		if blankRow(row) {
			res.Skipped++
			continue
		}
		if isSummary(row, clubIdx) {
			res.Skipped++
			continue
		}
		in := ShotInput{Raw: map[string]string{}}
		for i, h := range header {
			if i < len(row) && strings.TrimSpace(h) != "" {
				in.Raw[h] = row[i]
			}
		}
		if clubIdx >= 0 && clubIdx < len(row) {
			in.Club = strings.TrimSpace(row[clubIdx])
		}
		if in.Club == "" {
			in.Club = opt.Club
		}
		if dateIdx >= 0 && dateIdx < len(row) {
			if ts, ok := parseTime(row[dateIdx]); ok {
				in.HitAt = &ts
			}
		}
		n := 0
		for _, c := range cols {
			if c.index >= len(row) {
				continue
			}
			v, ok, err := parseNumber(row[c.index], delim)
			if err != nil {
				res.Warnings = append(res.Warnings, fmt.Sprintf("%s: %q を数値として読めません", c.header, row[c.index]))
				continue
			}
			if !ok {
				continue
			}
			unit := c.unit
			if unit == "" && opt.Units == units.Metric && c.spec.metricUnit != "" {
				unit = c.spec.metricUnit
			}
			if unit == "" {
				unit = units.DefaultUnit(c.spec.kind, opt.Units)
			}
			si, err := units.ToSI(v, unit, c.spec.kind)
			if err != nil {
				return nil, fmt.Errorf("%s: %w", c.header, err)
			}
			c.spec.set(&in.Metrics, si)
			n++
		}
		if n == 0 {
			res.Skipped++
			continue
		}
		if opt.ImpactOffsetToeNegative && in.Metrics.ImpactOffset != nil {
			v := -*in.Metrics.ImpactOffset
			in.Metrics.ImpactOffset = &v
		}
		Canonicalize(&in.Metrics, opt.Handedness)
		res.Shots = append(res.Shots, in)
	}
	if len(res.Shots) == 0 {
		return nil, fmt.Errorf("1球も読めませんでした")
	}
	return res, nil
}

// detectDelimiter は区切り文字を決める。Excel の "sep=;" の行があればそれに従う。
func detectDelimiter(data []byte) (rune, []byte) {
	sc := bufio.NewScanner(bytes.NewReader(data))
	sc.Buffer(make([]byte, 1024*1024), 1024*1024)
	if sc.Scan() {
		first := strings.TrimSpace(sc.Text())
		if strings.HasPrefix(strings.ToLower(first), "sep=") && len(first) >= 5 {
			rest := data[len(sc.Bytes()):]
			rest = bytes.TrimLeft(rest, "\r\n")
			return rune(first[4]), rest
		}
		counts := map[rune]int{',': strings.Count(first, ","), ';': strings.Count(first, ";"), '\t': strings.Count(first, "\t")}
		best, bestN := ',', -1
		for _, d := range []rune{',', ';', '\t'} {
			if counts[d] > bestN {
				best, bestN = d, counts[d]
			}
		}
		return best, data
	}
	return ',', data
}

func findHeader(rows [][]string) int {
	for i, row := range rows {
		if i > 20 {
			break
		}
		// 知っている計測項目の列名が2つ以上ある行を見出しとみなす
		hits := 0
		for _, cell := range row {
			name, _ := splitUnit(cell)
			n := normName(name)
			for _, spec := range trackmanFields {
				if indexOf(spec.aliases, n) >= 0 {
					hits++
					break
				}
			}
		}
		if hits >= 2 {
			return i
		}
	}
	return -1
}

func splitUnit(cell string) (name, unit string) {
	cell = strings.TrimSpace(cell)
	if m := unitInName.FindStringSubmatch(cell); m != nil {
		return strings.TrimSpace(m[1]), strings.TrimSpace(m[2])
	}
	return cell, ""
}

func isUnitRow(row []string) bool {
	nonEmpty, unitLike := 0, 0
	for _, c := range row {
		c = strings.TrimSpace(c)
		if c == "" {
			continue
		}
		nonEmpty++
		if units.Known(c) || (strings.HasPrefix(c, "[") && strings.HasSuffix(c, "]")) {
			unitLike++
		}
	}
	return nonEmpty > 0 && unitLike*2 >= nonEmpty
}

func mapColumns(header, unitRow []string, res *Result) (cols []column, clubIdx, dateIdx int) {
	clubIdx, dateIdx = -1, -1
	type pick struct {
		col  column
		rank int
	}
	chosen := map[string]pick{}
	for i, h := range header {
		name, unit := splitUnit(h)
		if unit == "" && unitRow != nil && i < len(unitRow) {
			unit = strings.TrimSpace(unitRow[i])
		}
		unit = units.Normalize(unit)
		n := normName(name)
		if n == "" {
			continue
		}
		if clubIdx < 0 && indexOf(clubAliases, n) >= 0 {
			clubIdx = i
			continue
		}
		if dateIdx < 0 && indexOf(dateAliases, n) >= 0 {
			dateIdx = i
			continue
		}
		matched := false
		for fi := range trackmanFields {
			spec := &trackmanFields[fi]
			rank := indexOf(spec.aliases, n)
			if rank < 0 {
				continue
			}
			matched = true
			if p, ok := chosen[spec.key]; !ok || rank < p.rank {
				if ok {
					res.Ignored = append(res.Ignored, p.col.header)
				}
				chosen[spec.key] = pick{column{index: i, header: h, unit: unit, spec: spec}, rank}
			} else {
				res.Ignored = append(res.Ignored, h)
			}
			break
		}
		if !matched {
			res.Ignored = append(res.Ignored, h)
		}
	}
	for _, spec := range trackmanFields {
		if p, ok := chosen[spec.key]; ok {
			cols = append(cols, p.col)
			res.Columns[spec.key] = p.col.header
		}
	}
	if clubIdx < 0 {
		res.Warnings = append(res.Warnings, "クラブの列がありません。クラブ別の判定ができないので、取り込み後に指定してください")
	}
	return cols, clubIdx, dateIdx
}

func indexOf(xs []string, s string) int {
	for i, x := range xs {
		if x == s {
			return i
		}
	}
	return -1
}

func blankRow(row []string) bool {
	for _, c := range row {
		if strings.TrimSpace(c) != "" {
			return false
		}
	}
	return true
}

func isSummary(row []string, clubIdx int) bool {
	for i, c := range row {
		c = strings.TrimSpace(c)
		if c == "" {
			continue
		}
		// 先頭の空でないセルかクラブ列が集計の名前なら集計行
		if summaryRow.MatchString(c) {
			return true
		}
		if i != clubIdx {
			return false
		}
	}
	return false
}

// parseNumber は数値を読む。空なら ok=false。
// "4.0 A" / "2.1 B" / "5.2 R" / "3.1 L" の向き付きも受ける（B と L はマイナス）。
func parseNumber(s string, delim rune) (float64, bool, error) {
	s = strings.TrimSpace(s)
	if s == "" || s == "-" || s == "--" || strings.EqualFold(s, "n/a") {
		return 0, false, nil
	}
	sign := 1.0
	if m := suffixed.FindStringSubmatch(s); m != nil {
		s = m[1]
		switch strings.ToUpper(m[2]) {
		case "B", "L":
			sign = -1
		}
	}
	if delim != ',' && strings.Count(s, ",") == 1 && !strings.Contains(s, ".") {
		s = strings.Replace(s, ",", ".", 1) // 小数のカンマ
	}
	v, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return 0, false, err
	}
	return v * sign, true, nil
}

var timeLayouts = []string{
	time.RFC3339,
	"2006-01-02 15:04:05",
	"2006-01-02 15:04",
	"2006/01/02 15:04:05",
	"01/02/2006 15:04:05",
	"01/02/2006 3:04:05 PM",
	"1/2/2006 3:04:05 PM",
	"02.01.2006 15:04:05",
}

func parseTime(s string) (time.Time, bool) {
	s = strings.TrimSpace(s)
	for _, l := range timeLayouts {
		if t, err := time.Parse(l, s); err == nil {
			return t, true
		}
	}
	return time.Time{}, false
}

// RawJSON は取り込んだ行をそのまま保存するための JSON。
func RawJSON(raw map[string]string) json.RawMessage {
	b, _ := json.Marshal(raw)
	return b
}
