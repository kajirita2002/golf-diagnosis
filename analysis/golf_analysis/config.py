"""閾値と既定値。どれも「初期値・要較正」。

実データで較正したら ENGINE_VERSION を上げる（診断には必ず版を付けて返す）。
"""

# 0.4: 実験の評価と比較の bootstrap・並べ替えをベクトル化（乱数の引き方が変わった）。
#      プランの練習の球（warmup / drill）を診断から外す（docs/DESIGN_coaching.md §8.3・§10.2）。
ENGINE_VERSION = "analysis/0.4"
# 解説レポートとプランの候補の版（docs/DESIGN_coaching.md §10.3）。
REPORT_VERSION = "report/0.1"
PLAN_VERSION = "plan/0.2"

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


# つなぎの文（narrative.py）の検証が読む語の一覧。禁止語の一覧は言い換え（平仮名・英語・カタカナ・類語）に
# 弱いので、広めに取る（落ちても定型文に戻るだけで、読める文章は残る。R10）。英字と数字は narrative.py で
# 文字の種類ごと禁じる。レビューで通ってしまった攻撃の文は tests/test_narrative.py に並べて固定してある。
DIRECTION_TERMS = [
    "右", "左", "みぎ", "ひだり", "ライト", "レフト", "インサイド", "アウトサイド", "内側", "外側", "うちがわ", "そとがわ",
    "内", "外", "開", "閉", "ひら", "とじ", "逆", "反対", "側", "向こう", "手前", "オープン", "クローズ",
    "プッシュ", "プル", "スライ", "フック", "フッカー", "フェード", "ドロー", "シャンク", "引っか", "引っ掛", "ひっか",
    "押し出", "捕ま", "つかま", "ネック", "先端", "トゥ", "ヒール",
]
QUANTITY_TERMS = [
    "全部", "全て", "すべて", "全球", "毎回", "常に", "いつも", "ほとんど", "ほぼ", "大半", "大部分", "過半", "大多数", "多数",
    "半分", "倍", "割", "多く", "多い", "少な", "少し", "わずか", "一番", "最も", "もっとも", "大きく", "小さく", "一部",
    "かなり", "とても", "非常", "すごく",
]
CAUSAL_TERMS = [
    "ので", "から", "ため", "原因", "要因", "起因", "影響", "おかげ", "ゆえ", "故に", "せい", "結果", "によって", "により",
    "よって", "だから", "引き起こ", "つなが", "繋が", "生じ", "招",
    # 条件の約束（「〜を直せば減ります」。§7.1 で禁じた形）
    "れば", "なら", "たら", "えば", "けば", "せば", "てば", "ねば", "めば", "べば", "げば", "と、", "直せ", "直す", "直る",
    "揃え", "揃う", "そろえ", "そろう", "治",
]
PROBABILITY_TERMS = [
    "確率", "可能性", "たぶん", "多分", "おそらく", "恐らく", "偶然", "きっと", "必ず", "確実", "絶対", "かもしれ",
    "だろう", "でしょう", "見込み", "はず", "らしい", "みたい", "%", "％",
]
# 評価・励まし・助言（主張の中身の言い換えになる）
EVALUATION_TERMS = [
    "良", "よく", "よい", "いい", "悪", "わる", "上手", "下手", "うま", "改善", "悪化", "癖", "くせ", "ダメ", "だめ",
    "正し", "間違", "失敗", "成功", "問題", "課題", "弱点", "欠点", "優れ", "素晴", "すばら", "頑張", "がんば", "直し",
]
# 体・クラブの動きの語のうち、LAYER_TERMS（L2/L3）に無い言い換え（つなぎの文だけで使う）
BODY_EXTRA_TERMS = [
    "こし", "かた", "お尻", "尻", "足", "脚", "ひざ", "手", "目線", "視線", "姿勢", "顔", "スタンス", "アドレス", "ヘッド",
    "フォロー", "バックスイング", "スイング", "トップ", "フィニッシュ", "テーク", "ダウン", "振り", "ふり", "踏",
]
# クラブの種類の呼び名（範囲の名前とは別に）
CLUB_TERMS = ["アイアン", "ウッド", "フェアウェイ", "ユーティリティ", "ハイブリッド", "ドライバー", "ウェッジ", "パター", "番手", "番"]
# 用語集の見出し語の部品（見出し語は「／（）」で割るだけだと「フェース」「パス」の単体が残る）
GLOSS_PART_TERMS = ["フェース", "パス", "打点", "芯", "帯", "窓", "スピン", "軸", "区間", "回帰", "ばらつき", "散", "はみ出", "ミスヒット", "狙い", "グッド"]

# ---- アクションプラン（docs/DESIGN_coaching.md §8）。どれも初期値・要較正 ----
PLAN_DEFAULT_PER_BLOCK = 8  # ばらつきが出せないときの1ブロックの球数（§8.3 の 8〜12球の下限）
PLAN_WARMUP_N = 5
PLAN_DRILL_N = (5, 3)  # A-B-B-A の2つのドリルのブロック
# 短い版（A-B-A・約30球）: 準備3 → A 6 → ドリル3 → B 12 → A 6
PLAN_SHORT_TEMPLATE = [
    {"kind": "warmup", "n": 3}, {"kind": "baseline", "n": 6}, {"kind": "drill", "n": 3},
    {"kind": "intervention", "n": 12}, {"kind": "baseline", "n": 6},
]
MEASURED_DROP = 0.20  # B で主 KPI が取れた割合が A よりこれ以上下がったら「測れた球だけ良くなっている可能性」
GUARD_OPPOSITE_EXTRA = 2  # 反対側へ外れて落ちた球が、A の割合から見込む数よりこれ以上多ければ「崩れた」
DEFAULT_GUARDRAILS = [{"metric": "club_speed", "goal": "increase"}, {"metric": "carry", "goal": "increase"}]
# 状態の移り方（§8.6）
PLAN_WORKED_RUNS = 2  # 「効いた」: 別の日の練習で続けて moderate 以上
PLAN_SETTLE_RUNS = 2  # 「定着した」: 効いたあと、別の日の練習で最初の A がプラン1回目の A より moderate 以上
PLAN_SETTLE_RUNS_SLOW = 3  # setup / compensation のドリルは1回延ばす
PLAN_FAIL_RUNS = 3  # この回数で「効いた」に届かなければ「効かなかった」
PLAN_WORSE_RUNS = 2  # 主 KPI の worse がこの回数で「効かなかった」、ガードレールの worse なら「止める」
PLAN_ALTERNATE_MAX_PAIRS = 8  # 「移せていない」の型（ドリルと本番を1球ずつ交互）の組の数の上限（型は20段まで）
STAGE_MIN_COUNTED = 10  # 段の目標の物差し（1回目の A で帯を判定できた球）の最小（§7.3 の10球と同じ）
GOOD_GRADES = ("strong", "moderate")

# ---- 要点の層（画面の最初に出す言葉。gist.py）: 利用者の方針（2026-09-29）
# 数字・角度・専門用語を使わず、決まった言葉の対応で決定的に作る。ここが1か所の表。
# 向きの語（右／左）は claims.dir_word で利き手ごとに入れ替える（R9）。ネック／先は入れ替えない。
GIST_VERSION = "gist/0.2"
PLAIN_TERMS = {
    "club_path": "振る方向",          # クラブパス
    "face_angle": "クラブの面の向き",  # フェース
    "face": "クラブの面",
    "impact_offset": "当たる場所",     # 打点
    "heel": "ネック寄り",              # ヒール
    "toe": "先寄り",                   # トゥ
    "center": "芯",
    "carry": "飛んだ距離",             # キャリー
    "band": "狙いの幅",                # 帯・Good の幅
    "good": "狙いどおりの球",
    "face_to_path": "曲がり方",        # フェース・トゥ・パス
    "launch": "打ち出した向き",        # 打ち出し方向
    "impact": "当たる瞬間",            # インパクト
    "thin": "薄い当たり",
}
# 件数 → 量の言葉（割合 = 件数 / 全体。上から最初に当たったもの）。全体が PLAIN_MIN_N 未満なら言わない
PLAIN_FREQ = [(0.75, "ほとんど"), (0.5, "半分以上"), (0.25, "多い"), (0.1, "ときどき"), (1e-9, "まれ")]
PLAIN_FREQ_NONE = "ない"
PLAIN_MIN_N = 5
# 狙いの幅に落ちた球の割合 → 「理想との差」の散らばりの行（上から最初に当たったもの）
PLAIN_LANDED = [
    (0.75, "ほとんどが狙いの幅に落ちています。"),
    (0.5, "半分以上が狙いの幅に落ちています。"),
    (0.1, "狙いの幅に落ちない球のほうが多いです。"),
    (1e-9, "狙いの幅に落ちた球はまれです。"),
]
PLAIN_LANDED_NONE = "狙いの幅に落ちた球はありません。"
# 左右に大きく外れたぶん（はみ出した距離）のうち、ある原因が占める割合 → 言葉
PLAIN_EXCESS = [(0.5, "半分以上"), (0.25, "大きな部分"), (1e-9, "一部")]
# 「左右のずれは主に面の向きで決まっていた」と言う条件（単回帰の R²。R4 の約束どおり単回帰だけを使う）
PLAIN_FACE_DRIVES_R2 = 0.5
PLAIN_FACE_DRIVES_RATIO = 2.0  # 面の R² がパスの R² のこの倍以上
# 大きく外れた当たり（極端な打点）の球が同じ側へ飛んだと言う割合
PLAIN_SAME_SIDE = 0.75
# 画面の最初に出してはいけない語（数字は文字の種類で、英字も全部禁じる）
PLAIN_FORBIDDEN = [
    "パス", "フェース", "打点", "ヒール", "トゥ", "帯", "窓", "スピン", "ロフト", "回帰", "区間", "中央値", "平均",
    "標準偏差", "キャリー", "インパクト", "ミスヒット", "°", "度", "%", "％", "ヤード", "±", "＋", "−",
]
# 最初の面で数字を出してよい「構造のラベル」の形（docs/DESIGN_v2.md §3 R1）。所見の文には check_plain を当て、
# ラベル（P の番号・回数・日付・料金・時間・クラブ）はこの形だけを許す。画面では data-label="<種類>" の中に置く。
# brand はアプリの名前だけ（英字の例外）。形を広げるときは、ここと scripts/ui_check.py の両方が読むこの表だけを直す。
PLAIN_LABEL_PATTERNS = {
    "p": r"^P(?:10|[1-9])(?:\.5)?$",
    "count": r"^(?:全)?\d+(?:球|回|件|本|つ|日)(?:中\d+(?:球|回|件|本|つ))?目?$|^\d+\s*/\s*\d+$",
    "date": r"^\d{1,2}月\d{1,2}日(?:（[日月火水木金土今昨]日?）)?$|^\d{4}-\d{2}-\d{2}$",
    "cost": r"^約?\$\d+(?:\.\d+)?$|^約?\d+円$",
    "duration": r"^約?\d+(?:分|秒|時間)$",
    "club": r"^\d{1,2}番(?:アイアン|ウッド|ユーティリティ)$|^(?:ドライバー|パター|ピッチングウェッジ|アプローチウェッジ|ギャップウェッジ|サンドウェッジ|ロブウェッジ)$",
    "brand": r"^Swing Lab$",
}
