"""閾値と既定値。どれも「初期値・要較正」。

実データで較正したら ENGINE_VERSION を上げる（診断には必ず版を付けて返す）。
"""

ENGINE_VERSION = "analysis/0.2"

# 比べる群に最低これだけの球が無ければ「データ不足」と言う。
# 何も言わないことも正しい出力の1つ（docs/DESIGN.md ❹）。
MIN_BLOCK_N = 5
# ばらつきの要因分析（回帰）に要る球数。
MIN_DRIVER_N = 10

# ミスヒットの除外候補。キャリーがそのクラブの中央値のこの割合に届かない球と、
# スピン軸がこの角度を超える球（トップ・大きな引っかけなど）。
MISHIT_CARRY_RATIO = 0.6
MISHIT_AXIS_DEG = 45.0
# 極端な打点（ネック・先端寄り）の群として出す最小の球数。
MIN_EXTREME_STRIKE_N = 2

CATEGORY_LABEL = {"driver": "ドライバー", "wood": "ウッド", "hybrid": "ユーティリティ", "iron": "アイアン", "wedge": "ウェッジ"}

# Good 判定の目標の範囲（クラブの種類ごと）。
#   side_pct   … 着地の左右のずれがキャリーの何%以内か
#   side_min_m … 短いクラブで厳しすぎないための下限
#   carry_pct  … キャリーがそのクラブの中央値から何%以内か
#   start_deg  … 打ち出し方向が何度以内か
GOOD_TARGETS = {
    "driver": {"side_pct": 0.07, "side_min_m": 8.0, "carry_pct": 0.08, "start_deg": 4.0},
    "wood": {"side_pct": 0.06, "side_min_m": 7.0, "carry_pct": 0.08, "start_deg": 4.0},
    "hybrid": {"side_pct": 0.06, "side_min_m": 6.0, "carry_pct": 0.07, "start_deg": 3.5},
    "iron": {"side_pct": 0.05, "side_min_m": 5.0, "carry_pct": 0.07, "start_deg": 3.0},
    "wedge": {"side_pct": 0.05, "side_min_m": 3.0, "carry_pct": 0.06, "start_deg": 3.0},
}
DEFAULT_TARGET = GOOD_TARGETS["iron"]

# 実験で「意味のある変化」とみなす最小の差（Minimal Meaningful Difference）。
# 計測器の再現性と、球筋に効く大きさの両方から決める。いまは仮の値。
MMD = {
    "face_to_path": 1.0,
    "face_angle": 1.0,
    "club_path": 1.0,
    "attack_angle": 1.0,
    "dynamic_loft": 1.5,
    "spin_loft": 1.5,
    "launch_direction": 1.0,
    "launch_angle": 1.0,
    "spin_axis": 2.0,
    "side": 3.0,  # m
    "carry": 3.0,  # m
    "low_point": 0.02,  # m
    "impact_offset": 0.004,  # m
    "impact_height": 0.004,  # m
    "club_speed": 0.5,  # m/s
    "ball_speed": 0.7,  # m/s
    "smash_factor": 0.02,
    "spin_rate": 250.0,  # rpm
}

# セッションのばらつきとして見るインパクト（L1）の指標。
L1_METRICS = [
    "face_angle",
    "club_path",
    "face_to_path",
    "attack_angle",
    "dynamic_loft",
    "spin_loft",
    "low_point",
    "impact_offset",
    "impact_height",
]

BOOTSTRAP_N = 5000
PERMUTATION_N = 5000
SEED = 0  # 同じ入力には同じ答えを返す
