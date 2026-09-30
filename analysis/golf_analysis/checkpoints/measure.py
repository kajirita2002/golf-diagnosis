"""1スイングのコマ（姿勢の点・タップ・ボール）から、カタログの項目を測って判定する（docs/DESIGN_v2.md §5.4・§6.6）。

- LLM を使わない。値は姿勢の点と本人のタップからだけ計算する（R6: Claude に数字を作らせない）。
- **角度は画面の上の角度**（体の3Dの角度とは言わない）。撮り方の検査（§6.2）に落ちたら角度の項目は判断できない。
- 左打ちは画面を左右に反転して右打ちの座標で見る（R9）。{lead} / {trail} の点も入れ替える。
- 画面の座標: x は右が正、y は下が正（画素）。後ろからは右打ちで +x がボールの側、正面からは +x が目標の側。
- 誤差は初期の値（config.CP_ANGLE_ERR_DEG / CP_POS_ERR_BALL。仮・要較正）。値の幅（値 ± 誤差）が範囲の端を
  またいだら「境目」＝判断できない（R4）。

入力（1スイング）:
  {view: dtl|fo, club, handedness: R|L, fps, width, height,
   ball: [[x, y], [x, y]]（構えのコマで押したボールの両端・画素）,
   frames: {P1: {t, landmarks: [{x, y, visibility}] × 33（0〜1 の割合）, taps: {grip: [x, y], head: [x, y], heel?, toe?}}, …},
   missing: [写っていない P], vision: {item_id: {option, visibility}}（段2c から）}
"""

from __future__ import annotations

import math
from typing import Any

from .. import config
from . import by_id, fill, items, stamp, version

# MediaPipe Pose の33点のうち使うもの（体の左右＝解剖学の左右）
LM = {
    "nose": 0, "left_shoulder": 11, "right_shoulder": 12, "left_elbow": 13, "right_elbow": 14, "left_wrist": 15, "right_wrist": 16,
    "left_hip": 23, "right_hip": 24, "left_knee": 25, "right_knee": 26, "left_ankle": 27, "right_ankle": 28,
    "left_heel": 29, "right_heel": 30, "left_toe": 31, "right_toe": 32,
}
PARTS = ("shoulder", "elbow", "wrist", "hip", "knee", "ankle", "heel", "toe")

REASON_TEXT = {
    "unclear": "基準を確かめ中",
    "not_in_2d": "この撮り方では判断できない",
    "view_dtl": "後ろから撮ると見られます",
    "view_fo": "正面から撮ると見られます",
    "no_frame": "コマが無い",
    "no_tap": "タップすると測れます",
    "no_ball": "ボールの大きさが無い",
    "no_head_width": "クラブの先の幅が無い",
    "low_visibility": "体の点が見えない",
    "camera": "カメラの置き方",
    "fps": "コマの速さが足りない",
    "fps_unknown": "コマの速さが分からない",
    "camera_unknown": "カメラの置き方を確かめられない",
    "view_mismatch": "向きを確かめてください",
    "club_short": "クラブが短く写っている",
    "cross": "クラブが垂直の向こうへ倒れている（読み方を確かめ中）",
    "combo": "ガイドに基準の無い組み合わせ",
    "border": "境目",
    "scale": "物差しの食い違い",
    "band_narrow": "範囲が狭く写っている",
    "ref_club": "この番手の基準はガイドに無い",
    "not_built": "測り方をまだ作っていない",
    "vision_pending": "見た目の評価はまだ",
    "conflict": "測った値と見た目の食い違い",
    "derive": "まとめる項目が判断できない",
    "split": "スイングごとに食い違う",
    "reference": "参考（合否を出さない）",
    "no_series": "全部のコマの動きが無い",
}

# 端の値ちょうどを範囲の中に数えるときの遊び（値は小数2桁で出すので、その丸めの中の差は同じに扱う）
EPS = 0.01
TAP_BASIS = "measured_tap"      # 測れた（あなたが示した点から）
POSE_BASIS = "measured_approx"  # 測れた（目安）: 誤差の予算を実測するまで


class Invalid(Exception):
    """この項目はこのスイングでは測れない（理由のコードつき）。"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


# ------------------------------------------------------------ 番手

def club_class(club: str | None) -> tuple[str, int | None]:
    """TrackMan のクラブ名・撮る前に選んだ番手 → (種類, 番号)。分からなければ ("iron", None)。"""
    s = (club or "").strip().lower().replace(" ", "").replace(".", "")
    if s in CLUB_CLASS_WORDS:
        return CLUB_CLASS_WORDS[s], None
    import re

    m = re.fullmatch(r"(\d+)(iron|i|wood|w|hybrid|h|ut|u)", s)
    if m:
        n, k = int(m.group(1)), m.group(2)
        if k in ("iron", "i"):
            return "iron", n
        if k in ("wood", "w"):
            return ("driver", 1) if n == 1 else ("wood", n)
        return "hybrid", n
    return "iron", None


CLUB_CLASS_WORDS = {
    "driver": "driver", "dr": "driver", "1w": "driver", "ドライバー": "driver",
    "iron": "iron", "wood": "wood", "hybrid": "hybrid", "wedge": "wedge",
    "pw": "iron", "pitchingwedge": "iron",
    "aw": "wedge", "gw": "wedge", "sw": "wedge", "lw": "wedge", "approachwedge": "wedge", "gapwedge": "wedge", "sandwedge": "wedge", "lobwedge": "wedge",
}


def iron_group(num: int | None, club: str | None) -> str | None:
    s = (club or "").strip().lower().replace(" ", "")
    if s in ("pw", "pitchingwedge"):
        num = 10
    if num is None:
        return None
    for k, nums in config.CP_IRON_CLASS.items():
        if num in nums:
            return k
    return None


def applies(it: dict, cls: str, num: int | None, club: str | None) -> tuple[bool, bool]:
    """(この番手で一覧に出すか, 出すなら参考か)。docs/DESIGN_v2.md §5.6。"""
    clubs = it.get("clubs") or []
    setup = it.get("group") == "setup"
    tempo = it.get("group") == "tempo"
    if cls in ("iron", "driver"):
        if cls not in clubs:
            return False, False
        cf = it.get("club_filter")
        if cf and cls == "iron":
            g = iron_group(num, club)
            if g is None:
                # 番号が分からないアイアン: 真ん中のアイアンの項目だけ参考で出す
                return ("mid_iron" in cf), True
            return (g in cf), False
        return True, False
    # ウェッジ・ウッド・ユーティリティ: セットアップとテンポはその番手の値、P の項目はアイアンの値を参考に
    if setup or tempo:
        return (cls in clubs), False
    if "iron" in clubs and not it["id"].startswith("driver."):
        return True, True
    return False, False


# ------------------------------------------------------------ 点

def _mid(a, b):
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, min(a[2], b[2]))


def _lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, min(a[2], b[2]))


class Frame:
    """1コマの点（画素・右打ちの座標）。"""

    def __init__(self, raw: dict, w: float, h: float, hand: str, ball: dict | None, view: str):
        self.w, self.h, self.hand, self.ball, self.view = w, h, hand, ball, view
        self.raw = raw or {}
        lms = self.raw.get("landmarks") or []
        self.lm: dict[str, tuple[float, float, float]] = {}
        if len(lms) >= 33:
            mirror = hand == "L"
            for name, i in LM.items():
                p = lms[i] or {}
                x, y = float(p.get("x", 0)) * w, float(p.get("y", 0)) * h
                vv = p.get("visibility", p.get("v"))
                v = 1.0 if vv is None else float(vv)
                if mirror:
                    x = w - x
                self.lm[name] = (x, y, v)
            # 左打ちは {lead}＝体の右（画面を反転したあとで右打ちと同じ役）
            lead, trail = ("right", "left") if mirror else ("left", "right")
            for part in PARTS:
                self.lm[f"lead_{part}"] = self.lm[f"{lead}_{part}"]
                self.lm[f"trail_{part}"] = self.lm[f"{trail}_{part}"]
        self.taps: dict[str, tuple[float, float, float]] = {}
        for k, xy in (self.raw.get("taps") or {}).items():
            if isinstance(xy, (list, tuple)) and len(xy) == 2 and all(isinstance(v, (int, float)) for v in xy):
                x, y = float(xy[0]), float(xy[1])
                self.taps[k] = ((w - x) if hand == "L" else x, y, 1.0)

    @property
    def has_pose(self) -> bool:
        return bool(self.lm)

    def torso(self) -> float:
        a, b = self.point("sh_mid"), self.point("hip_mid")
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def point(self, name: str) -> tuple[float, float, float]:
        if name in ("grip", "head", "heel", "toe"):
            if name not in self.taps:
                raise Invalid("no_tap")
            return self.taps[name]
        if name == "ball":
            if not self.ball:
                raise Invalid("no_ball")
            return self.ball["c"]
        if not self.lm:
            raise Invalid("low_visibility", "体の点が無い")
        L = self.lm
        if name in L:
            return L[name]
        if name == "H":
            return _mid(L["lead_wrist"], L["trail_wrist"])
        if name == "sh_mid":
            return _mid(L["lead_shoulder"], L["trail_shoulder"])
        if name == "hip_mid":
            return _mid(L["lead_hip"], L["trail_hip"])
        if name == "stance_mid":
            return _mid(L["lead_ankle"], L["trail_ankle"])
        if name == "trail_thigh":
            return _mid(L["trail_hip"], L["trail_knee"])
        if name == "navel":
            return _lerp(self.point("hip_mid"), self.point("sh_mid"), config.CP_NAVEL)
        if name.startswith("foot."):
            # 後ろからは手前の足（{trail}足）がよく写る。つま先は +x（ボールの側）
            heel, toe = L["trail_heel"], L["trail_toe"]
            part = name[5:]
            if part == "heel":
                return heel
            if part == "toe":
                return toe
            if part == "bof":
                return _lerp(heel, toe, config.CP_BALL_OF_FOOT)
            if part == "mid":
                return _lerp(heel, toe, 0.5)
        raise KeyError(name)


def _ball(raw, w: float, hand: str) -> dict | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        (x1, y1), (x2, y2) = raw
        x1, y1, x2, y2 = float(x1), float(y1), float(x2), float(y2)
    except (TypeError, ValueError):
        return None
    if hand == "L":
        x1, x2 = w - x1, w - x2
    d = math.hypot(x2 - x1, y2 - y1)
    if d < 2:
        return None
    return {"c": ((x1 + x2) / 2, (y1 + y2) / 2, 1.0), "d": d}


def _series(raw, w: float, h: float, hand: str) -> dict | None:
    """保存した時系列（video.series_for の形・0〜1 の割合）→ 画素・右打ちの座標。形が違えば None（その項目は判断できない）。"""
    if not isinstance(raw, dict) or not isinstance(raw.get("t"), list) or not isinstance(raw.get("t0"), (int, float)):
        return None
    t = raw["t"]
    out = {"t0": float(raw["t0"]), "fps": float(raw.get("fps") or 0), "t": []}
    for k in ("hand", "hip", "chest"):
        out[k] = []
    for i, ti in enumerate(t):
        if not isinstance(ti, (int, float)):
            return None
        out["t"].append(float(ti))
        for k in ("hand", "hip", "chest"):
            arr = raw.get(k) or []
            q = arr[i] if i < len(arr) else None
            if isinstance(q, (list, tuple)) and len(q) >= 2 and all(isinstance(v, (int, float)) for v in q[:2]):
                x, y = float(q[0]) * w, float(q[1]) * h
                vis = float(q[2]) if len(q) > 2 and isinstance(q[2], (int, float)) else 1.0
                out[k].append((w - x if hand == "L" else x, y, vis))
            else:
                out[k].append(None)
    ds = sorted(math.hypot(c[0] - hh[0], c[1] - hh[1]) for c, hh in zip(out["chest"], out["hip"]) if c and hh)
    out["L"] = ds[len(ds) // 2] if ds else 0.0
    return out if len(out["t"]) >= 3 and out["L"] > 1 else None


def _at(series: dict, key: str, t: float):
    """時刻 t に一番近いコマの点（無ければ None）。"""
    best, bd = None, math.inf
    for ti, q in zip(series["t"], series[key]):
        if q is not None and abs(ti - t) < bd:
            best, bd = q, abs(ti - t)
    return best if bd <= 0.1 else None


def _cross_x(path: list[tuple[float, float]], y: float, rising: bool) -> float | None:
    """手の通り道が高さ y を（rising なら上向きに）最初に越える所の x（線でつなぐ）。"""
    for (x1, y1), (x2, y2) in zip(path, path[1:]):
        if (rising and y1 > y >= y2) or (not rising and y1 < y <= y2):
            s = (y - y1) / (y2 - y1) if y2 != y1 else 0.0
            return x1 + (x2 - x1) * s
    return None


def _need_series(ctx: "Ctx") -> dict:
    if not ctx.series:
        raise Invalid("no_series", "動画から自動で取り出すと、全部のコマの動きから見られます")
    ctx.used_pose = True
    return ctx.series


def _traj(ctx: "Ctx", spec: dict) -> dict:
    """時系列の項目（手の通り道の輪・骨盤と胸の戻し・テンポ）。"""
    from .. import video

    k = spec["kind"]
    se = _need_series(ctx)
    L = se["L"]
    if k == "loop":
        # 後ろから: 同じ高さで、下ろしの手が上げより体の側（-x）を通れば「低い所を通る」（右ループ）
        t4, t6 = ctx.frame(spec["p"]).raw.get("t"), ctx.frame(spec["p1"]).raw.get("t")
        if not isinstance(t4, (int, float)) or not isinstance(t6, (int, float)):
            raise Invalid("no_frame")
        up = [(q[0], q[1]) for ti, q in zip(se["t"], se["hand"]) if q and se["t0"] - 1e-6 <= ti <= t4 + 1e-6]
        down = [(q[0], q[1]) for ti, q in zip(se["t"], se["hand"]) if q and t4 - 1e-6 <= ti <= t6 + 1e-6]
        c, hp = _at(se, "chest", se["t0"]), _at(se, "hip", se["t0"])
        if not c or not hp or len(up) < 3 or len(down) < 3:
            raise Invalid("low_visibility", "手の通り道が途切れている")
        gaps = []
        for j in range(config.CP_LOOP_LEVELS):
            f = config.CP_LOOP_BAND[0] + (config.CP_LOOP_BAND[1] - config.CP_LOOP_BAND[0]) * j / max(1, config.CP_LOOP_LEVELS - 1)
            y = c[1] + (hp[1] - c[1]) * f
            xu, xd = _cross_x(up, y, True), _cross_x(down, y, False)
            if xu is not None and xd is not None:
                gaps.append((xu - xd) / L)
        if len(gaps) < max(2, config.CP_LOOP_LEVELS // 2 + 1):
            raise Invalid("low_visibility", "手の通り道が胸と腰のあいだを通っていない")
        return {"v": sum(gaps) / len(gaps), "err": config.CP_TRAJ_ERR_L, "unit": "ratio", "extra": {"levels": len(gaps)}}
    if k == "recenter":
        # 正面から: 始動〜P3 で一番 {trail}（-x）へ寄った所から、P4 で {lead} へ戻っているか
        t3, t4 = ctx.frame(spec["p0"]).raw.get("t"), ctx.frame(spec["p"]).raw.get("t")
        if not isinstance(t3, (int, float)) or not isinstance(t4, (int, float)):
            raise Invalid("no_frame")
        xs = [q[0] for ti, q in zip(se["t"], se[spec["pt"]]) if q and se["t0"] - 1e-6 <= ti <= t3 + 1e-6]
        q4 = _at(se, spec["pt"], t4)
        if len(xs) < 2 or not q4:
            raise Invalid("low_visibility", "骨盤と胸の点が途切れている")
        return {"v": (q4[0] - min(xs)) / L, "err": config.CP_TRAJ_ERR_L, "unit": "ratio", "extra": {}}
    if k == "tempo":
        t4, t7 = ctx.frame(spec["p"]).raw.get("t"), ctx.frame(spec["p1"]).raw.get("t")
        fps = ctx.fps or se["fps"]
        if not fps:
            raise Invalid("fps_unknown")
        tp = video.tempo(se["t0"], t4, t7, 1.0 / float(fps))
        if not tp:
            raise Invalid("no_frame", "始まり・トップ・当たる瞬間の順番が合わない")
        word, guide = video.tempo_word(tp, ctx.club_class)
        return {"v": tp["ratio"], "err": round((tp["hi"] - tp["lo"]) / 2, 3), "unit": "ratio",
                "extra": {"band_lo": tp["lo"], "band_hi": tp["hi"], "guide": guide, "word": word, "back_s": tp["back_s"], "down_s": tp["down_s"]}}
    raise Invalid("not_built", k)


# ------------------------------------------------------------ 幾何

def _ang_v(a, b) -> float:
    """線 a-b と垂直のなす角（0〜90°）。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 and dy == 0:
        raise Invalid("no_tap", "二点が重なっている")
    ang = math.degrees(math.atan2(abs(dx), abs(dy)))
    return ang


def _ang_v_signed(a, b) -> float:
    """a → b の向きの、真上からの傾き（+x 側へ倒れると正）。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 and dy == 0:
        raise Invalid("no_tap", "二点が重なっている")
    return math.degrees(math.atan2(dx, -dy))


def _ang_side(a, b, side: int) -> float:
    """線 a-b と垂直のなす角を、b が a から side の向き（+1 は +x・-1 は -x）に倒れているとき正で返す（-90〜90）。

    向きを捨てる _ang_v だと、垂直を越えて反対側へ倒れた線を「ちょうどよい」や逆の言葉で判定してしまう
    （後ろからのシャフトは、垂直を越えてボールの側へ倒れるほど立っている）。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 and dy == 0:
        raise Invalid("no_tap", "二点が重なっている")
    return math.degrees(math.atan2(side * dx, abs(dy)))


def _joint(a, b, c) -> float:
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        raise Invalid("low_visibility", "点が重なっている")
    cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(cos))


def _line_angle(a, b) -> float:
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def _between(a1, b1, a2, b2) -> float:
    d = abs(_line_angle(a1, b1) - _line_angle(a2, b2)) % 180
    return min(d, 180 - d)


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ------------------------------------------------------------ 判定の芯

def judge_value(v: float, err: float, lo: float | None, hi: float | None, side: str = "both") -> str:
    """値の幅（v ± err）と範囲 → in / out_lo / out_hi / border（§5.5）。

    lo_only / hi_only は NO の側の端だけで決める（反対側にどれだけ離れても範囲の中）。"""
    # 端の値ちょうどは範囲の中（丸めで端から EPS だけずれても同じに扱う）
    a, b = v - err + EPS, v + err - EPS
    if side == "lo_only":
        if lo is None:
            raise ValueError("lo_only なのに lo が無い")
        return "in" if a >= lo else ("out_lo" if b < lo else "border")
    if side == "hi_only":
        if hi is None:
            raise ValueError("hi_only なのに hi が無い")
        return "in" if b <= hi else ("out_hi" if a > hi else "border")
    if lo is None or hi is None:
        raise ValueError("both なのに lo か hi が無い")
    if a >= lo and b <= hi:
        return "in"
    if b < lo:
        return "out_lo"
    if a > hi:
        return "out_hi"
    return "border"


class Ctx:
    def __init__(self, swing: dict):
        self.view = swing.get("view") if swing.get("view") in ("dtl", "fo") else "dtl"
        self.hand = "L" if swing.get("handedness") == "L" else "R"
        self.w = float(swing.get("width") or 0) or 1.0
        self.h = float(swing.get("height") or 0) or 1.0
        self.fps = swing.get("fps")
        self.ball = _ball(swing.get("ball"), self.w, self.hand)
        self.missing = set(swing.get("missing") or [])
        self.frames = {p: Frame(f, self.w, self.h, self.hand, self.ball, self.view) for p, f in (swing.get("frames") or {}).items() if p not in self.missing}
        self.used_pose = False
        self.used_tap = False
        self.used_ball = False
        self.club_class = "iron"
        self.series = _series(swing.get("series"), self.w, self.h, self.hand)

    def frame(self, p: str) -> Frame:
        f = self.frames.get(p)
        if f is None:
            raise Invalid("no_frame")
        return f

    def pt(self, p: str, name: str):
        f = self.frame(p)
        q = f.point(name)
        if name in ("grip", "head", "heel", "toe"):
            self.used_tap = True
        elif name == "ball":
            self.used_ball = True
        else:
            self.used_pose = True
            if q[2] < config.CP_MIN_VISIBILITY:
                raise Invalid("low_visibility", name)
        return q

    def ball_d(self) -> float:
        if not self.ball:
            raise Invalid("no_ball")
        self.used_ball = True
        return self.ball["d"]

    def pos_err_px(self, p: str) -> float:
        if self.ball:
            return config.CP_POS_ERR_BALL * self.ball["d"]
        return config.CP_POS_ERR_L * max(self.frame(p).torso(), 1.0)

    def ref(self, p: str, spec) -> tuple[float, float, float]:
        if isinstance(spec, str):
            return self.pt(p, spec)
        base = self.pt(p, spec["ref"])
        x, y = base[0], base[1]
        if "dx_ball" in spec:
            x += spec["dx_ball"] * self.ball_d()
        if spec.get("toward"):
            t = self.pt(p, spec["toward"])
            x += (t[0] - x) * spec.get("frac", 0)
        return (x, y, base[2])


def _unit_len(ctx: Ctx, p: str, unit: str) -> float:
    if unit == "head":
        f = ctx.frame(p)
        if "heel" not in f.taps or "toe" not in f.taps:
            raise Invalid("no_head_width")
        d = _dist(f.taps["heel"], f.taps["toe"])
        if d < 2:
            raise Invalid("no_head_width")
        ctx.used_tap = True
        return d
    return ctx.ball_d()


def compute(ctx: Ctx, spec: dict) -> dict:
    """spec → {v, err, unit, extra}。測れなければ Invalid。"""
    k = spec["kind"]
    p = spec.get("p")
    ang_err = config.CP_ANGLE_ERR_DEG
    if k in TRAJ_KINDS:
        return _traj(ctx, spec)
    if k in CLUB_LINE_KINDS and _uses_club(spec):
        for q in [x for x in (spec.get("p0"), p) if x]:
            _club_line_ok(ctx, q)
    if k in ("band_x", "band_y"):
        ax = 0 if k == "band_x" else 1
        P = ctx.pt(p, spec["pt"])
        A, B = ctx.ref(p, spec["a"]), ctx.ref(p, spec["b"])
        width = abs(B[ax] - A[ax])
        min_w = config.CP_MIN_BAND_BALL * ctx.ball["d"] if ctx.ball else 0.02 * max(ctx.frame(p).torso(), 1.0)
        if width < max(min_w, 1.0):
            raise Invalid("band_narrow")
        t = (P[ax] - A[ax]) / (B[ax] - A[ax])
        err = ctx.pos_err_px(p) / width
        extra = {}
        lo_x, hi_x = sorted((A[ax], B[ax]))
        if ax == 0 and spec.get("fault_x") and not lo_x <= P[ax] <= hi_x:
            # 外れの語を画面の向き（+x / -x）で決める（A と B の並びが体の形で入れ替わっても、語が逆にならない）
            extra["fault_id"] = spec["fault_x"]["+x" if P[ax] > hi_x else "-x"]
        if ctx.ball:
            # 範囲の端からボール何個ぶん外か（数字を見るの中だけ）
            out = 0.0 if lo_x <= P[ax] <= hi_x else (lo_x - P[ax] if P[ax] < lo_x else P[ax] - hi_x)
            extra["edge_ball"] = round(out / ctx.ball["d"], 2)
        return {"v": t, "err": err, "unit": "ratio", "extra": extra}
    if k == "butt_dir":
        # クラブの線を先 → 握りの端の向きに伸ばし、腰の縦線に届いた高さを、おへそ〜腰のポケットの帯で見る
        G, C = ctx.pt(p, "grip"), ctx.pt(p, "head")
        hip, navel = ctx.pt(p, "hip_mid"), ctx.pt(p, "navel")
        dx = G[0] - C[0]
        if abs(dx) < 1e-6 or (hip[0] - G[0]) / dx < 0:
            raise Invalid("no_tap", "クラブの線が体へ向いていない")
        s = (hip[0] - G[0]) / dx
        y_hit = G[1] + s * (G[1] - C[1])
        height = hip[1] - navel[1]
        if abs(height) < 1:
            raise Invalid("band_narrow")
        t = (y_hit - navel[1]) / height
        reach = math.hypot(hip[0] - G[0], y_hit - G[1])
        err = reach * math.tan(math.radians(ang_err)) / abs(height)
        return {"v": t, "err": err, "unit": "ratio", "extra": {}}
    if k == "offset_x":
        P, R = ctx.pt(p, spec["pt"]), ctx.ref(p, spec["ref"])
        d = ctx.ball_d()
        return {"v": (P[0] - R[0]) / d, "err": config.CP_POS_ERR_BALL, "unit": "ball", "extra": {}}
    if k == "dist":
        P, Q = ctx.pt(p, spec["pt"]), ctx.pt(p, spec["to"])
        unit = spec.get("unit", "ball")
        d = _unit_len(ctx, p, unit)
        # 外れた向き（後ろからは +x がボールの側＝外側）。範囲の side と名前を分ける
        way = "inside" if P[0] < Q[0] else "outside"
        # axis: x は左右の離れだけ（ドライバーのトップの「手元から左右にヘッド一個」）
        gap = abs(P[0] - Q[0]) if spec.get("axis") == "x" else _dist(P, Q)
        return {"v": gap / d, "err": config.CP_POS_ERR_BALL, "unit": unit, "extra": {"dir": way}}
    if k == "dy":
        P, R = ctx.pt(p, spec["pt"]), ctx.pt(p, spec["ref"])
        d = _unit_len(ctx, p, spec.get("unit", "ball"))
        return {"v": (R[1] - P[1]) / d, "err": config.CP_POS_ERR_BALL, "unit": spec.get("unit", "ball"), "extra": {}}
    if k == "shaft_v":
        v = _ang_side(ctx.pt(p, spec["a"]), ctx.pt(p, spec["b"]), int(spec.get("side", 1)))
        if spec.get("cross") == "unknown" and v < -ang_err:
            raise Invalid("cross")
        return {"v": v, "err": ang_err, "unit": "deg", "extra": {}}
    if k == "ang_v":
        return {"v": _ang_v(ctx.pt(p, spec["a"]), ctx.pt(p, spec["b"])), "err": ang_err, "unit": "deg", "extra": {}}
    if k == "ang_h":
        return {"v": 90 - _ang_v(ctx.pt(p, spec["a"]), ctx.pt(p, spec["b"])), "err": ang_err, "unit": "deg", "extra": {}}
    if k == "ang_v_signed":
        return {"v": spec.get("sign", 1) * _ang_v_signed(ctx.pt(p, spec["a"]), ctx.pt(p, spec["b"])), "err": ang_err, "unit": "deg", "extra": {}}
    if k == "joint":
        return {"v": _joint(ctx.pt(p, spec["a"]), ctx.pt(p, spec["b"]), ctx.pt(p, spec["c"])), "err": ang_err, "unit": "deg", "extra": {}}
    if k == "ang_between":
        return {"v": _between(ctx.pt(p, spec["a1"]), ctx.pt(p, spec["b1"]), ctx.pt(p, spec["a2"]), ctx.pt(p, spec["b2"])), "err": ang_err, "unit": "deg", "extra": {}}
    if k == "shift_x":
        p0 = spec["p0"]
        a, b = ctx.pt(p0, spec["pt"]), ctx.pt(p, spec["pt"])
        return {"v": (b[0] - a[0]) / ctx.ball_d(), "err": config.CP_POS_ERR_BALL, "unit": "ball", "extra": {}}
    if k == "shift_y_up":
        p0 = spec["p0"]
        a, b = ctx.pt(p0, spec["pt"]), ctx.pt(p, spec["pt"])
        return {"v": (a[1] - b[1]) / ctx.ball_d(), "err": config.CP_POS_ERR_BALL, "unit": "ball", "extra": {}}
    if k == "tilt_change":
        p0 = spec["p0"]
        v0 = _ang_v_signed(ctx.pt(p0, spec["a"]), ctx.pt(p0, spec["b"]))
        v1 = _ang_v_signed(ctx.pt(p, spec["a"]), ctx.pt(p, spec["b"]))
        return {"v": abs(v1 - v0), "err": ang_err, "unit": "deg", "extra": {}}
    if k == "line_change":
        p0 = spec["p0"]
        return {"v": _between(ctx.pt(p0, spec["a"]), ctx.pt(p0, spec["b"]), ctx.pt(p, spec["a"]), ctx.pt(p, spec["b"])), "err": ang_err, "unit": "deg", "extra": {}}
    if k == "shoulder_tilt_change":
        def tilt(q):
            lead, trail = ctx.pt(q, "lead_shoulder"), ctx.pt(q, "trail_shoulder")
            return math.degrees(math.atan2(lead[1] - trail[1], abs(lead[0] - trail[0]) or 1e-6))
        return {"v": tilt(p) - tilt(spec["p0"]), "err": ang_err, "unit": "deg", "extra": {}}
    if k == "ratio":
        a = _dist(ctx.pt(p, spec["a1"]), ctx.pt(p, spec["b1"]))
        b = _dist(ctx.pt(p, spec["a2"]), ctx.pt(p, spec["b2"]))
        if b < 1:
            raise Invalid("low_visibility")
        return {"v": a / b, "err": 0.1 * a / b, "unit": "ratio", "extra": {}}
    if k == "dist_ratio":
        p0 = spec["p0"]
        a = _dist(ctx.pt(p0, spec["pt"]), ctx.pt(p0, spec["to"]))
        b = _dist(ctx.pt(p, spec["pt"]), ctx.pt(p, spec["to"]))
        if a < 1:
            raise Invalid("low_visibility")
        return {"v": b / a, "err": 0.1 * b / a, "unit": "ratio", "extra": {}}
    raise Invalid("not_built", k)


# 全部のコマの時系列を使う測り方（段2b）
TRAJ_KINDS = ("loop", "recenter", "tempo")

# クラブの線の向きを使う測り方（線が短く写ると、1画素で角度が大きく動く）
CLUB_LINE_KINDS = ("shaft_v", "ang_v", "ang_h", "ang_v_signed", "ang_between", "line_change", "butt_dir")


def _club_line_ok(ctx: "Ctx", p: str) -> None:
    f = ctx.frame(p)
    if "grip" not in f.taps or "head" not in f.taps:
        raise Invalid("no_tap")
    n = _dist(f.taps["grip"], f.taps["head"])
    need = config.CP_MIN_CLUB_LINE_BALL * ctx.ball["d"] if ctx.ball else config.CP_MIN_CLUB_LINE_L * max(f.torso() if f.has_pose else 0.0, 1.0)
    if n < need:
        raise Invalid("club_short", "クラブが画面の上で短く写っていて、向きを測れない")


def _spec_ps(spec: dict | None) -> list[str]:
    if not spec:
        return []
    out = []
    for key in ("p0", "p"):
        if spec.get(key) and spec[key] not in out:
            out.append(spec[key])
    for part in spec.get("parts") or []:
        for q in _spec_ps(part):
            if q not in out:
                out.append(q)
    return out


def _uses_club(spec: dict | None) -> bool:
    if not spec:
        return False
    vals = [v for k, v in spec.items() if k in ("pt", "to", "a", "b", "c", "a1", "b1", "a2", "b2", "ref")]
    if spec.get("kind") == "butt_dir":
        return True
    if any(v in ("grip", "head") for v in vals if isinstance(v, str)):
        return True
    return any(_uses_club(p) for p in spec.get("parts") or [])


# ------------------------------------------------------------ 撮り方の検査・物差し

def camera_check(ctx: Ctx) -> dict:
    """P1 のコマで、ガイドのカメラの合わせ方（§6.2）に近いか。直す向きの文を返す。"""
    f = ctx.frames.get("P1")
    if f is None or not f.has_pose:
        return {"ok": None, "checks": [], "reason": "構えのコマが無いので、撮り方を確かめられません"}
    checks = []
    tol = config.CP_CAMERA_TOL
    # 反転前の画面の座標で見る（直す向きは実際のカメラの向き）
    def raw_x(x: float) -> float:
        return ctx.w - x if ctx.hand == "L" else x

    try:
        hip = f.point("hip_mid")
        dy = hip[1] / ctx.h - 0.5
        checks.append({"id": f"cam.{ctx.view}.horizontal", "ok": abs(dy) <= tol,
                       "hint": "" if abs(dy) <= tol else ("カメラをもう少し高く（腰の高さへ）" if dy < 0 else "カメラをもう少し低く（腰の高さへ）")})
        if ctx.view == "dtl":
            x = raw_x(f.point("H")[0])
        else:
            x = raw_x(f.point("stance_mid")[0])
        dx = x / ctx.w - 0.5
        where = "手元" if ctx.view == "dtl" else "スタンスの真ん中"
        checks.append({"id": f"cam.{ctx.view}.vertical", "ok": abs(dx) <= tol,
                       "hint": "" if abs(dx) <= tol else (f"カメラをもう少し右へ（{where}を真ん中へ）" if dx > 0 else f"カメラをもう少し左へ（{where}を真ん中へ）")})
    except Invalid:
        return {"ok": None, "checks": [], "reason": "体の点が見えないので、撮り方を確かめられません"}
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def view_check(ctx: Ctx) -> dict:
    """P1 の姿勢から向きを確かめる（§6.1）。正面なら両肩が横に並び、後ろなら重なる。食い違えば画面が聞く。"""
    f = ctx.frames.get("P1")
    if f is None or not f.has_pose:
        return {"ok": None}
    try:
        a, b = f.point("lead_shoulder"), f.point("trail_shoulder")
        t = f.torso()
    except (Invalid, KeyError):
        return {"ok": None}
    if min(a[2], b[2]) < config.CP_MIN_VISIBILITY or t < 1:
        return {"ok": None}
    spread = abs(a[0] - b[0]) / t
    guess = "fo" if spread >= config.CP_VIEW_FO_SPREAD else ("dtl" if spread <= config.CP_VIEW_DTL_SPREAD else None)
    return {"ok": None if guess is None else guess == ctx.view, "guess": guess, "spread": round(spread, 2)}


def scale_check(ctx: Ctx) -> dict:
    """ボールの直径と靴の長さの二つの物差しの食い違い（後ろからだけ。§5.4）。"""
    if ctx.view != "dtl" or not ctx.ball:
        return {"ok": None}
    f = ctx.frames.get("P1")
    if f is None or not f.has_pose:
        return {"ok": None}
    heel, toe = f.lm["trail_heel"], f.lm["trail_toe"]
    if min(heel[2], toe[2]) < config.CP_MIN_VISIBILITY:
        return {"ok": None}
    shoe = _dist(heel, toe) / config.CP_SHOE_LEN_BALLS
    ratio = shoe / ctx.ball["d"]
    return {"ok": abs(ratio - 1) <= config.CP_SCALE_MISMATCH, "ratio": round(ratio, 3)}


# ------------------------------------------------------------ 1項目

def _vision_state(it: dict, ans: dict | None) -> tuple[str | None, str | None]:
    """Claude の選択肢 → (in / out / None, fault)。"""
    v = it.get("vision")
    if not v or not ans:
        return None, None
    if ans.get("visibility") == "not_visible":
        return None, None
    opt = ans.get("option")
    if opt in (v.get("ok_options") or [v.get("ok_option")]):
        return "in", None
    if opt in (v.get("fault_of") or {}):
        return "out", v["fault_of"][opt]
    return None, None


def _fault_for(it: dict, res: str, extra: dict, part_fault: str | None = None) -> str | None:
    fs = it.get("faults") or []
    if part_fault:
        return part_fault
    if extra.get("fault_id") and any(f["id"] == extra["fault_id"] for f in fs):
        return extra["fault_id"]
    want = {"out_lo": "lo", "out_hi": "hi"}.get(res)
    if extra.get("dir"):
        for f in fs:
            if f["when"] == extra["dir"]:
                return f["id"]
    for f in fs:
        if f["when"] == want:
            return f["id"]
    for f in fs:
        if f["when"] in ("out", "lo", "hi", "inside", "outside"):
            return f["id"]
    return fs[0]["id"] if fs else None


def _measure_item(ctx: Ctx, it: dict, cam: dict, scale: dict) -> dict:
    """測る → {res: in/out_lo/out_hi/border, value, basis, fault} か Invalid。"""
    m = it.get("measure") or {}
    spec = m.get("spec")
    r = it.get("range") or {}
    how = m.get("how", "none")
    if not spec:
        raise Invalid("not_built")
    ps = _spec_ps(spec)
    for p in ps:
        ctx.frame(p)
    unit = r.get("unit")
    angle = unit == "deg" or spec.get("kind") in ("butt_dir",) or any(pp.get("unit") == "deg" or pp.get("kind", "").startswith(("ang", "joint", "shaft")) for pp in spec.get("parts") or [])
    # 角度はガイドのカメラの合わせ方に通ったときだけ（§5.4）。確かめられなかったとき（構えのコマ・体の点が無い）も通ったことにしない
    if angle and cam.get("ok") is False:
        raise Invalid("camera")
    if angle and cam.get("ok") is None:
        raise Invalid("camera_unknown")
    # 下ろし〜当たる瞬間のクラブはコマの速さが要る（§6.2）。速さが分からないときも足りたことにしない
    if _uses_club(spec) and any(p in ("P5", "P5_5", "P6", "P6_5", "P7") for p in ps):
        if not ctx.fps:
            raise Invalid("fps_unknown")
        if ctx.fps < config.CP_MIN_FPS_CLUB:
            raise Invalid("fps")
    if "ball" in how and scale.get("ok") is False:
        raise Invalid("scale")
    ctx.used_pose = ctx.used_tap = ctx.used_ball = False
    if spec["kind"] == "all":
        results, values, faults = [], [], []
        for part in spec["parts"]:
            try:
                c = compute(ctx, part)
            except Invalid as e:
                results.append(("invalid", e.reason))
                continue
            pr = part.get("range") or {}
            res = judge_value(c["v"], c["err"], pr.get("lo"), pr.get("hi"), pr.get("side", "both"))
            results.append((res, None))
            values.append({"v": round(c["v"], 2), "err": round(c["err"], 2), "unit": c["unit"], "lo": pr.get("lo"), "hi": pr.get("hi"), "side": pr.get("side", "both"), "res": res})
            if res.startswith("out"):
                faults.append(part.get("fault") or _fault_for(it, res, c.get("extra") or {}))
        outs = [x for x in results if x[0].startswith("out")]
        if outs:
            res = outs[0][0]
        elif all(x[0] == "in" for x in results):
            res = "in"
        elif any(x[0] == "border" for x in results):
            res = "border"
        else:
            raise Invalid(next(x[1] for x in results if x[0] == "invalid"))
        value = {"q": r.get("quantity"), "parts": values}
        fault = faults[0] if faults else None
    else:
        c = compute(ctx, spec)
        side = r.get("side", "both")
        res = judge_value(c["v"], c["err"], r.get("lo"), r.get("hi"), side) if it.get("judge") != "reference" else "reference"
        value = {"q": r.get("quantity"), "v": round(c["v"], 2), "err": round(c["err"], 2), "unit": c["unit"], "lo": r.get("lo"), "hi": r.get("hi"), "side": side}
        value.update({k: v for k, v in (c.get("extra") or {}).items() if k != "fault_id"})
        # 範囲の外にするのに、位置の条件も要る基準（ノート p102〜118: 腕とクラブが25以上離れ「かつ」先が内側）。
        # 角度だけ外れて位置の条件がそろわないときは、ガイドに基準の無い組み合わせなので判断できない
        need = spec.get("out_if")
        if need and res.startswith("out"):
            a, b = ctx.pt(spec["p"], need["pt"]), ctx.pt(spec["p"], need["of"])
            gap = (b[0] - a[0]) if need.get("side") == "-x" else (a[0] - b[0])
            value["out_if_ball"] = round(gap / ctx.ball["d"], 2) if ctx.ball else None
            if gap <= ctx.pos_err_px(spec["p"]):
                raise Invalid("combo", need.get("why", ""))
        fault = _fault_for(it, res, c.get("extra") or {}) if res.startswith("out") else None
    basis = TAP_BASIS if ctx.used_tap else POSE_BASIS
    return {"res": res, "value": value, "basis": basis, "fault": fault}


def judge_item(ctx: Ctx, it: dict, cam: dict, scale: dict, vision_ans: dict | None = None, ref_club: bool = False, view_bad: bool = False) -> dict:
    """1スイング × 1項目の判定（§6.6 の表）。"""
    out: dict[str, Any] = {"id": it["id"], "state": "unknown", "fault": None, "basis": "none", "reason": "", "value": None,
                           "p": it.get("p"), "frames": [p for p in _spec_ps((it.get("measure") or {}).get("spec")) if p] or ([it["p"]] if it.get("p") in ctx.frames else [])}

    def unknown(reason: str, detail: str = "") -> dict:
        out.update(state="unknown", reason=reason, detail=detail)
        return out

    if it.get("same_as"):
        out.update(state="same_as", reason="", target=it["same_as"])
        return out
    if it.get("judgeable") == "not_in_2d":
        return unknown("not_in_2d", it.get("needs_note", ""))
    if it.get("definition_status") == "unclear":
        return unknown("unclear", it.get("needs_note", ""))
    if it.get("view") in ("dtl", "fo") and it["view"] != ctx.view:
        return unknown(f"view_{it['view']}")
    if view_bad and it.get("view") in ("dtl", "fo"):
        # 構えの形が選んだ向きと食い違う（§6.1）。向きがずれたまま測った値は、どの向きの基準にも当てはまらない
        return unknown("view_mismatch", "構えの形が、選んだ向きと違って見えます")
    m = it.get("measure") or {}
    spec = m.get("spec")
    if spec and spec.get("kind") == "derive":
        return unknown("derive")  # まとめる項目は measure_swing の最後に決める
    measured = None
    if m.get("how") not in ("vision", "none") and spec:
        try:
            measured = _measure_item(ctx, it, cam, scale)
        except Invalid as e:
            measured = {"invalid": e.reason, "detail": e.detail}
    elif m.get("how") in ("time", "trajectory"):
        measured = {"invalid": "not_built", "detail": "全部のコマが要る（自動の取り出しから）"}
    if it.get("judge") == "reference" or ref_club:
        # 参考: 状態の印を出さない。測れた値は数字を見るの中だけ
        out.update(state="reference", reason="ref_club" if ref_club else "reference")
        if measured and "invalid" not in measured:
            out["value"] = measured["value"]
            out["basis"] = measured["basis"]
            # 参考の項目でも、言葉にしてよい外れ（テンポの「急ぎ気味」など。幅がガイドの目安＋余白を丸ごと外れたとき）
            word = (measured["value"] or {}).get("word")
            if word and any(f["id"] == word for f in it.get("faults") or []):
                out["fault"] = word
        elif measured:
            out["ref_reason"] = measured.get("invalid")
            out["detail"] = measured.get("detail", "")
        return out
    vstate, vfault = _vision_state(it, vision_ans)
    if vision_ans:
        out["vision"] = {"option": vision_ans.get("option"), "visual": vision_ans.get("visual", "")}
    mres = measured.get("res") if measured and "invalid" not in measured else None
    if mres in ("in", "out_lo", "out_hi"):
        mstate = "in_range" if mres == "in" else "out_range"
        out.update(value=measured["value"], basis=measured["basis"], fault=measured["fault"])
        if vstate is None or (vstate == "in") == (mstate == "in_range"):
            out["state"] = mstate
            if vstate is not None:
                out["vision_agrees"] = True
            return out
        # 逆の向き: タップは本人の指示なので採る。姿勢だけのときは食い違いとして判断できない
        if measured["basis"] == TAP_BASIS:
            out.update(state=mstate, conflict=True)
            return out
        out.update(state="unknown", reason="conflict", basis="conflict", fault=None)
        return out
    if measured and "invalid" not in measured:
        out["value"] = measured["value"]
    if vstate is not None:
        out.update(state="in_range" if vstate == "in" else "out_range", fault=vfault, basis="visual")
        if mres == "border":
            out["measured_border"] = True
        return out
    if mres == "border":
        return unknown("border")
    if measured and "invalid" in measured:
        return unknown(measured["invalid"], measured.get("detail", ""))
    if m.get("how") == "vision" or it.get("vision"):
        return unknown("vision_pending")
    return unknown("not_built")


def measure_swing(swing: dict, vision: dict | None = None) -> dict:
    """1スイングの全項目。POST /v1/checkpoints/measure の中身。"""
    ctx = Ctx(swing)
    vision = vision if vision is not None else (swing.get("vision") or {})
    cls, num = club_class(swing.get("club") or swing.get("club_class"))
    if swing.get("club_class") in ("iron", "driver", "wood", "hybrid", "wedge") and not swing.get("club"):
        cls = swing["club_class"]
    ctx.club_class = cls
    cam = camera_check(ctx)
    scale = scale_check(ctx)
    vc = view_check(ctx)
    view_bad = vc.get("ok") is False
    res = []
    for it in items():
        ok, ref = applies(it, cls, num, swing.get("club"))
        if not ok:
            continue
        res.append(judge_item(ctx, it, cam, scale, vision.get(it["id"]), ref_club=ref, view_bad=view_bad))
    # まとめる項目（pow.width など）: 元の項目がどれか範囲の外なら外、全部中なら中
    by = {r["id"]: r for r in res}
    for r in res:
        it = by_id(r["id"])
        spec = (it.get("measure") or {}).get("spec") or {}
        if spec.get("kind") != "derive" or r["state"] in ("reference", "same_as") or r.get("reason") not in ("derive",):
            continue
        src = (spec.get("from") or {}).get(cls if cls in ("iron", "driver") else "iron", [])
        got = [by[s] for s in src if s in by]
        # まとめる向きの外れだけを数える（{lead}腕が突っ張りすぎ・{trail}肘が開いているのは「輪が小さい」ではない）
        want = set(spec.get("faults") or [])
        hit = [g for g in got if g["state"] == "out_range" and (not want or g.get("fault") in want)]
        judged = [g for g in got if g["state"] in ("in_range", "out_range")]
        if hit:
            r.update(state="out_range", reason="", basis=hit[0]["basis"], fault=(it["faults"][0]["id"] if it["faults"] else None),
                     derived_from=[g["id"] for g in hit])
        elif got and len(judged) == len(src):
            r.update(state="in_range", reason="", basis=judged[0]["basis"], derived_from=[g["id"] for g in got])
    for r in res:
        if r["state"] == "unknown":
            r["reason_text"] = REASON_TEXT.get(r["reason"], r["reason"])
    return {
        "catalog_version": version(), "judge_version": config.JUDGE_VERSION, "stamp": stamp(), "view": ctx.view, "handedness": ctx.hand,
        "club_class": cls, "club_number": num, "fps": ctx.fps, "camera": cam, "scale": scale, "view_check": vc,
        "ball": bool(ctx.ball), "series": bool(ctx.series), "items": res,
    }


def ball_scale_px(swing: dict) -> float | None:
    b = _ball(swing.get("ball"), float(swing.get("width") or 1), "R")
    return b["d"] if b else None


def texts(item_id: str, hand: str) -> dict:
    it = by_id(item_id) or {}
    return {k: fill(it.get(k, ""), hand) for k in ("title", "look_at", "ok_text")}
