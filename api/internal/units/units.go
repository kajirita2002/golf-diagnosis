// Package units は計測器の単位を SI（m/s・m・度・rpm）に揃える。
//
// 保存するのは必ず SI。表示の単位はクライアントが決める。
// 単位の取り違えは「数字は出ているのに全部おかしい」になって気づきにくいので、
// 分からない単位は推測せずにエラーにする。
package units

import (
	"fmt"
	"strings"
)

// Kind は物理量の種類。
type Kind int

const (
	Speed    Kind = iota // → m/s
	Distance             // → m（飛距離・左右）
	Small                // → m（打点・最下点のような小さい長さ）
	Angle                // → 度
	Spin                 // → rpm
	Ratio                // 単位なし
)

// System は単位の指定が無いときに使う既定の単位系。
type System string

const (
	Imperial System = "imperial" // mph・yd・in
	Metric   System = "metric"   // m/s・m・cm
)

// DefaultUnit は単位系ごとの既定の単位。
func DefaultUnit(k Kind, s System) string {
	switch k {
	case Speed:
		if s == Metric {
			return "m/s"
		}
		return "mph"
	case Distance:
		if s == Metric {
			return "m"
		}
		return "yds"
	case Small:
		if s == Metric {
			return "cm"
		}
		return "in"
	case Angle:
		return "deg"
	case Spin:
		return "rpm"
	}
	return ""
}

// Normalize は表記ゆれを吸収する（"[mph]" "(yds)" "Yards" → mph / yds）。
func Normalize(u string) string {
	u = strings.ToLower(strings.TrimSpace(u))
	u = strings.Trim(u, "[]()")
	u = strings.TrimSpace(u)
	switch u {
	case "mph", "mi/h":
		return "mph"
	case "km/h", "kmh", "kph":
		return "km/h"
	case "m/s", "mps":
		return "m/s"
	case "yds", "yd", "yards", "yard":
		return "yds"
	case "m", "meter", "meters", "metre", "metres":
		return "m"
	case "ft", "feet":
		return "ft"
	case "in", "inch", "inches", "\"":
		return "in"
	case "cm":
		return "cm"
	case "mm":
		return "mm"
	case "deg", "°", "degree", "degrees":
		return "deg"
	case "rpm":
		return "rpm"
	case "", "-":
		return ""
	}
	return u
}

// Known は単位として読めるか（単位の行を見分けるのに使う）。
func Known(u string) bool {
	switch Normalize(u) {
	case "mph", "km/h", "m/s", "yds", "m", "ft", "in", "cm", "mm", "deg", "rpm":
		return true
	}
	return false
}

// ToSI は値を SI に変換する。単位が種類に合わなければエラー。
func ToSI(v float64, unit string, k Kind) (float64, error) {
	u := Normalize(unit)
	switch k {
	case Speed:
		switch u {
		case "m/s":
			return v, nil
		case "mph":
			return v * 0.44704, nil
		case "km/h":
			return v / 3.6, nil
		}
	case Distance, Small:
		switch u {
		case "m":
			return v, nil
		case "yds":
			return v * 0.9144, nil
		case "ft":
			return v * 0.3048, nil
		case "in":
			return v * 0.0254, nil
		case "cm":
			return v / 100, nil
		case "mm":
			return v / 1000, nil
		}
	case Angle:
		if u == "deg" || u == "" {
			return v, nil
		}
	case Spin:
		if u == "rpm" || u == "" {
			return v, nil
		}
	case Ratio:
		return v, nil
	}
	return 0, fmt.Errorf("単位 %q はこの項目（種類 %d）に使えません", unit, k)
}
