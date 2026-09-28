"""ダミーの TrackMan 風 CSV を作る（実物の CSV が届くまでの代わり）。

**本物の書き出しではない。** 列名・単位の行・集計行は公開情報からの推測。
実物が届いたら testdata/ に置き、取り込みのテストを実物で固定し直す。

作る球（7番アイアン・右打ち・30球）:
  1〜10  基準       フェース・トゥ・パスが +3° 前後（プッシュフェード気味）
  11〜20 介入       ドリル後。+0.8° 前後に寄る
  21〜25 定着       意識を外す。+1.5° 前後
  26〜30 ヒール打ち  フェース・トゥ・パスは小さいのにスライス回転（打点が原因）

使い方: python3 testdata/gen_dummy.py > testdata/trackman_dummy_session.csv
"""

import math
import random
import sys

random.seed(20260928)

HEADER = [
    "Date", "Player", "Club", "Club Speed", "Attack Angle", "Club Path", "Swing Plane",
    "Swing Direction", "Dyn. Loft", "Face Angle", "Face To Path", "Ball Speed",
    "Smash Factor", "Launch Angle", "Launch Direction", "Spin Rate", "Spin Axis",
    "Height", "Carry", "Side", "Total", "Low Point", "Impact Offset", "Impact Height",
]
UNITS = [
    "", "", "", "[mph]", "[deg]", "[deg]", "[deg]", "[deg]", "[deg]", "[deg]", "[deg]",
    "[mph]", "", "[deg]", "[deg]", "[rpm]", "[deg]", "[ft]", "[yds]", "[yds]", "[yds]",
    "[in]", "[in]", "[in]",
]


def axis_from(ftp, spin_loft):
    return math.degrees(math.atan(math.sin(math.radians(ftp)) / math.tan(math.radians(spin_loft))))


def shot(i, ftp_mu, ftp_sd, offset_in=0.0, gear=0.0):
    path = random.gauss(2.5, 1.0)
    ftp = random.gauss(ftp_mu, ftp_sd)
    face = path + ftp
    aoa = random.gauss(-4.3, 0.6)
    dl = random.gauss(21.0, 1.0)
    sl = dl - aoa
    axis = axis_from(ftp, sl) + gear + random.gauss(0, 0.8)
    ld = 0.75 * face + 0.25 * path + random.gauss(0, 0.4)
    cs = random.gauss(82.0, 1.2)
    bs = cs * random.gauss(1.33, 0.01)
    carry = random.gauss(160.0, 3.0) - abs(axis) * 0.3
    # 着地の左右: 打ち出し方向 + 曲がり（スピン軸にほぼ比例）の粗い近似
    side = carry * math.tan(math.radians(ld)) + axis * 0.9
    return [
        f"2026-09-27 10:{i // 2:02d}:{(i % 2) * 30:02d}", "Rita", "7 Iron",
        f"{cs:.1f}", f"{aoa:.1f}", f"{path:.1f}", f"{random.gauss(62, 1):.1f}",
        f"{path + random.gauss(0.5, 0.5):.1f}", f"{dl:.1f}", f"{face:.1f}", f"{ftp:.1f}",
        f"{bs:.1f}", f"{bs / cs:.2f}", f"{random.gauss(17.0, 1.0):.1f}", f"{ld:.1f}",
        f"{random.gauss(6200, 250):.0f}", f"{axis:.1f}", f"{random.gauss(95, 5):.0f}",
        f"{carry:.1f}", f"{side:.1f}", f"{carry + random.gauss(8, 2):.1f}",
        f"{random.gauss(3.5, 0.8):.1f}", f"{offset_in:.2f}", f"{random.gauss(0, 0.1):.2f}",
    ]


def main(out=sys.stdout):
    rows = [HEADER, UNITS]
    i = 0
    for _ in range(10):
        i += 1
        rows.append(shot(i, 3.0, 1.2, random.gauss(0, 0.12)))
    for _ in range(10):
        i += 1
        rows.append(shot(i, 0.8, 1.0, random.gauss(0, 0.12)))
    for _ in range(5):
        i += 1
        rows.append(shot(i, 1.5, 1.0, random.gauss(0, 0.12)))
    for _ in range(5):
        i += 1
        # ヒール（-0.6in ≒ -15mm）。ギア効果でスライス回転が +6° ほど乗る
        rows.append(shot(i, 0.5, 0.6, random.gauss(-0.6, 0.08), gear=6.0))
    rows.append(["", "", "Average"] + [""] * (len(HEADER) - 3))
    for r in rows:
        out.write(",".join(r) + "\n")


if __name__ == "__main__":
    main()
