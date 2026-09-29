"""閾値と既定値。どれも「初期値・要較正」。

実データで較正したら ENGINE_VERSION を上げる（診断には必ず版を付けて返す）。
"""

ENGINE_VERSION = "analysis/0.3"
# 解説レポートとプランの候補の版（docs/DESIGN_coaching.md §10.3）。
REPORT_VERSION = "report/0.1"
PLAN_VERSION = "plan/0.1"

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

# ---- 解説レポート（docs/DESIGN_coaching.md）。どれも初期値・要較正 ----

# 芯と極端な打点の境目（m）。Go の physics.CenterStrikeM / ExtremeStrikeM と同じ値。
# 打点の分類そのもの（contact）は Go が1球ごとに出すので、ここは文と図の目盛りにだけ使う。
CENTER_STRIKE_M = 0.010
EXTREME_STRIKE_M = 0.030
# ヒール側に「ずれた」と数える打点（m）。芯の外側。
HEEL_SIDE_M = 0.010

# l1 の spread: SD ÷ MMD がこれ以下なら stable、これ以上なら wide、間は some。
SPREAD_STABLE = 1.5
SPREAD_WIDE = 3.0
# 図と profile を作る最小の球数（これ未満の範囲は①と⑧だけ）。
MIN_SCOPE_N = 5
# cross_club で比べる単位の最小の球数（候補を除いて）。
MIN_CROSS_N = 5

# 帯（l1_in_band）。§5.2
BAND_MIN_OWN_K_N = 10  # これ未満の種類は「まとめた k」を使う
BAND_RESID_RATIO = 0.5  # 予測だけの残差 SD が帯の半幅のこの割合を超えたら式を使わない
LAUNCH_RESID_INFO_DEG = 0.5  # 打ち出しの予測との差の SD がこれ未満なら「計測器の計算かも」（R8）
BAND_PATH_STEP = 0.5  # 帯の形を Go に頼むときのパスの刻み（度）

# issue とゲート。§5.4
ISSUE_EXTREME_SHARE = 0.10
ISSUE_EXTREME_MIN = 2
ISSUE_STRIKE_MEAN_M = 0.015
ISSUE_STRIKE_SD_M = 0.012
ISSUE_DIR_DEG = 2.0
ISSUE_DIR_SD_RATIO = 0.5
ISSUE_VAR_SD_DEG = 3.0
ISSUE_THIN_SHARE = 0.15
ISSUE_THIN_MIN = 2
LEVER_SD_RATIO = 2.0
BORDERLINE = 0.10  # 比べる量の差が閾値のこの割合以内なら「ぎりぎり」
G0_MEASURED_SHARE = 0.60
G2_MIN_N = 10
G2_BORDER_R = 0.10  # 相関の区間の0に近い側の端がこれ以内なら「ぎりぎり」
VAR_SIDE_RATIO = 2.0  # 多い側が少ない側のこの倍に届かなければ *_var にまとめる
# 1つの候補を実験するときの球数。1.96 × SD × √(2/n) ≤ DETECT_MMD_MULT × MMD
DETECT_MMD_MULT = 2.0
BLOCK_N_MAX = 12  # 1ブロックの上限。超えるなら「1回の練習では判定できない」

# 理想。§7.3
IDEAL_SHARE_GOAL = 0.75
IDEAL_STEP = 0.2
IDEAL_TYPE_MIN_N = 10  # 型の文を出す最小の球数（帯の判定ができた球）
IDEAL_SAME_SIDE = 0.75
IDEAL_IN_SHARE = 0.25
GOOD_REF_MIN_N = 10  # 本人の良い球の分布を重ねる最小の球数

# ---- 語の一覧（§5.5）。検証・ドリル集の検査・動画の文の検査が全部これを読む ----
LAYER_TERMS = {
    "L3": [
        "体", "身体", "腰", "骨盤", "肩", "胸", "腕", "肘", "手首", "右手", "左手", "手のひら", "甲",
        "膝", "頭", "体重", "前傾", "起き上が", "下半身", "上半身", "回転", "開きが早", "グリップ",
        "握り", "構え", "手元",
    ],
    "L2": ["シャフト", "切り返し", "トップの位置", "ダウンスイング", "テークバック", "寝", "振り遅れ", "開いて", "閉じて", "返", "こね", "すくい"],
}
# 一覧の語を含むが体・クラブの動きの意味ではない語（検査の前に取り除く）。「全体」の「体」など。
LAYER_TERM_ALLOW = ["全体", "大体", "具体", "物体", "体系", "繰り返", "見返"]


def layer_terms_in(text: str, layers: tuple[str, ...] = ("L2", "L3")) -> list[str]:
    """文に含まれる L2/L3 の語（検証・ドリル集の検査・動画の文の検査が共通で使う）。"""
    for w in LAYER_TERM_ALLOW:
        text = text.replace(w, "")
    return [t for layer in layers for t in LAYER_TERMS[layer] if t in text]


DIRECTION_TERMS = ["右", "左", "インサイド", "アウトサイド", "プッシュ", "プル", "スライス", "フック", "フェード", "ドロー"]
QUANTITY_TERMS = ["全部", "すべて", "ほとんど", "大半", "半分", "倍", "多く", "少な", "一番", "いつも"]
CAUSAL_TERMS = ["ので", "から", "ため", "原因", "せい", "結果", "によって"]
PROBABILITY_TERMS = ["確率", "可能性", "たぶん", "おそらく", "偶然", "きっと", "%"]
