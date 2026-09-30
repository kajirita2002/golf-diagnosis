"""合成の棒人間のスイング（テスト・画面の確認・通しの確認で共用。本物の動画・人は使わない）。

右打ちの棒人間を、後ろから（dtl）と正面から（fo）の画面の上に置く（1280×720 の横向き）。P1〜P10 のコマごとに
MediaPipe Pose と同じ33点（0〜1 の割合・visibility）と、クラブのタップ（握りの端・先）とボールの二点を返す。
左打ちは画面を左右に反転し、体の左右の名前を入れ替えて作る（本物の左打ちの見え方と同じ）。

faults に入れると、その項目だけ範囲の外へ動かす:
  p2_inside … 上げ始めのクラブの先を手元より内側へ（ボール約三個）
  p3_bent   … 正面の P3 の {lead} 腕を曲げる（約百四十）
  p7_rise   … 後ろの P7 で手を構えより浮かせる（ボール約二個）
  p5_shallow … 後ろの P5 でクラブを寝かせる（先が体の側へ 58。ノート p70 の「シャロー」の例）
  p5_cross  … 後ろの P5 でクラブが垂直を越えてボールの側へ倒れる（35。極端に立っている側）
  p6_outside … 後ろの P6 でクラブの先を手元より外側へ（ボール約三個）
値の中心は範囲の真ん中に置いてある（初期の誤差 ±5°・ボール ±0.5個 でも範囲の中に入るもの）。
"""

from __future__ import annotations

import copy
import json
import math

W, H = 1280, 720
BALL_D = 16.0  # ボール一個の画素（靴の長さ 100px ÷ 6.3 とほぼ同じにして、物差しの食い違いを出さない）

NAMES = ["left_shoulder", "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip",
         "left_knee", "right_knee", "left_ankle", "right_ankle", "left_heel", "right_heel", "left_toe", "right_toe", "nose"]
IDX = {"nose": 0, "left_shoulder": 11, "right_shoulder": 12, "left_elbow": 13, "right_elbow": 14, "left_wrist": 15, "right_wrist": 16,
       "left_hip": 23, "right_hip": 24, "left_knee": 25, "right_knee": 26, "left_ankle": 27, "right_ankle": 28,
       "left_heel": 29, "right_heel": 30, "left_toe": 31, "right_toe": 32}


def _arm(shoulder, elbow_deg, upper, lower, dir_deg, bend_sign=1):
    """肩から、向き dir_deg（画面の真上から時計回り）に上腕、肘で曲げて前腕。肘の内角 elbow_deg。"""
    a = math.radians(dir_deg)
    e = (shoulder[0] + upper * math.sin(a), shoulder[1] - upper * math.cos(a))
    b = math.radians(dir_deg + bend_sign * (180 - elbow_deg))
    w = (e[0] + lower * math.sin(b), e[1] - lower * math.cos(b))
    return e, w


def _elbow(shoulder, wrist, angle, side=1):
    """肩と手首を決めて、肘の内角が angle になる肘の位置（上腕と前腕を同じ長さにする）。"""
    sx, sy = shoulder
    wx, wy = wrist
    d = math.hypot(wx - sx, wy - sy)
    a = d / math.sqrt(2 * (1 - math.cos(math.radians(angle))))
    h = math.sqrt(max(a * a - (d / 2) ** 2, 0))
    mx, my = (sx + wx) / 2, (sy + wy) / 2
    nx, ny = -(wy - sy) / d, (wx - sx) / d
    return (mx + side * h * nx, my + side * h * ny)


def _club(grip, ang_from_vertical, length, up=True, toward=-1):
    """握りの端から、垂直からの傾き ang（toward: +1 は +x 側・-1 は -x 側）に長さ length の先。up は上向き。"""
    a = math.radians(ang_from_vertical)
    dx, dy = toward * length * math.sin(a), length * math.cos(a) * (-1 if up else 1)
    return (grip[0] + dx, grip[1] + dy)


def _dtl(p: str, faults: set) -> tuple[dict, dict]:
    """後ろから（右打ち）。+x がボールの側。"""
    base = {
        "left_ankle": (590, 628), "right_ankle": (582, 630), "left_heel": (578, 640), "right_heel": (570, 642),
        "left_toe": (676, 640), "right_toe": (670, 642), "left_knee": (606, 510), "right_knee": (600, 512),
        "left_hip": (556, 368), "right_hip": (548, 370), "left_shoulder": (626, 212), "right_shoulder": (616, 216),
        "left_elbow": (640, 330), "right_elbow": (634, 332), "left_wrist": (655, 450), "right_wrist": (651, 452), "nose": (672, 180),
    }
    pts = dict(base)
    taps = {}
    if p == "P1":
        taps = {"grip": (646, 446), "head": (836, 636)}
    elif p == "P2":
        pts.update({"left_shoulder": (630, 215), "right_shoulder": (606, 212), "left_elbow": (664, 300), "right_elbow": (650, 302),
                    "left_wrist": (688, 372), "right_wrist": (692, 370)})
        hx, hy = 690, 371
        taps = {"grip": (696, 373), "head": (hx - 48, hy - 6) if "p2_inside" in faults else (hx + 2, hy - 3)}
    elif p == "P3":
        pts.update({"left_shoulder": (640, 225), "right_shoulder": (590, 205), "left_elbow": (626, 262), "right_elbow": (588, 262),
                    "left_wrist": (602, 252), "right_wrist": (604, 250)})
        g = (603.0, 245.0)
        taps = {"grip": g, "head": _club(g, 35.0, 160, up=True, toward=-1)}
    elif p == "P4":
        pts.update({"left_shoulder": (646, 230), "right_shoulder": (586, 208), "left_elbow": (610, 205), "right_elbow": (560, 190),
                    "left_wrist": (590, 160), "right_wrist": (586, 156), "nose": (668, 182)})
        g = (596.0, 150.0)  # 靴の真ん中〜かかとの x（570〜620）の真ん中
        fx, fy = 590 - 610, 160 - 205  # {lead}前腕の向き（肘 → 手首）
        n = math.hypot(fx, fy)
        taps = {"grip": g, "head": (g[0] + 150 * fx / n, g[1] + 150 * fy / n)}
    elif p == "P5":
        pts.update({"left_shoulder": (640, 226), "right_shoulder": (594, 208), "left_elbow": (622, 262), "right_elbow": (590, 268),
                    "left_wrist": (602, 262), "right_wrist": (606, 260)})
        g = (604.0, 255.0)
        ang, tw = (58.0, -1) if "p5_shallow" in faults else (35.0, 1) if "p5_cross" in faults else (35.0, -1)
        taps = {"grip": g, "head": _club(g, ang, 160, up=True, toward=tw)}
    elif p == "P6":
        pts.update({"left_shoulder": (659, 220), "right_shoulder": (637, 214), "left_elbow": (680, 300), "right_elbow": (676, 298),
                    "left_wrist": (700, 372), "right_wrist": (704, 370)})
        hx, hy = 702, 371
        taps = {"grip": (708, 372), "head": (hx + 48, hy - 6) if "p6_outside" in faults else (hx + 3, hy - 2)}
    elif p == "P7":
        pts.update({"left_shoulder": (628, 214), "right_shoulder": (618, 216), "left_elbow": (648, 330), "right_elbow": (642, 334),
                    "left_wrist": (672, 450), "right_wrist": (668, 452)})
        if "p7_rise" in faults:
            pts["left_wrist"], pts["right_wrist"] = (672, 418), (668, 420)
        taps = {"grip": (660, 446), "head": (850, 636)}
    elif p == "P8":
        pts.update({"left_wrist": (760, 380), "right_wrist": (764, 378)})
    elif p == "P9":
        pts.update({"left_wrist": (606, 250), "right_wrist": (608, 248)})
        g = (607.0, 249.0)
        taps = {"grip": g, "head": _club(g, 15.0, 160, up=True, toward=-1)}
    elif p == "P10":
        pts.update({"left_wrist": (560, 180), "right_wrist": (562, 178)})
    return pts, taps


def _fo(p: str, faults: set) -> tuple[dict, dict]:
    """正面から（右打ち）。+x が目標の側（{lead}）。"""
    base = {
        "left_ankle": (700, 630), "right_ankle": (580, 630), "left_heel": (704, 640), "right_heel": (576, 640),
        "left_toe": (720, 648), "right_toe": (560, 648), "left_knee": (690, 510), "right_knee": (590, 510),
        "left_hip": (670, 370), "right_hip": (610, 370), "left_shoulder": (690, 215), "right_shoulder": (590, 215),
        "left_elbow": (668, 318), "right_elbow": (612, 318), "left_wrist": (648, 425), "right_wrist": (636, 425), "nose": (640, 170),
    }
    pts = dict(base)
    taps = {}
    sh_l, sh_r = pts["left_shoulder"], pts["right_shoulder"]
    if p == "P1":
        g = (646.0, 410.0)
        # シャフトは垂直から目標の側へ 7.5（範囲 5〜10 の真ん中）。先 → 握りの端が +x へ倒れる
        a = math.radians(7.5)
        head = (g[0] - 220 * math.sin(a), g[1] + 220 * math.cos(a))
        taps = {"grip": g, "head": head}
    elif p == "P2":
        pts.update({"left_wrist": (560, 372), "right_wrist": (556, 368), "left_elbow": (610, 300), "right_elbow": (580, 300)})
    elif p == "P3":
        e, w = _arm(sh_l, 140.0 if "p3_bent" in faults else 165.0, 110, 110, 268, bend_sign=1)
        pts.update({"left_elbow": e, "left_wrist": w, "right_wrist": (w[0] + 4, w[1] - 4), "right_elbow": (w[0] + 30, w[1] + 20)})
        g = (w[0] - 6, w[1] - 4)
        taps = {"grip": g, "head": _club(g, 20.0, 100, up=True, toward=-1)}  # 画面の上に収まる長さ
    elif p == "P4":
        e, w = _arm(sh_l, 155.0, 110, 110, 300, bend_sign=1)
        pts.update({"left_elbow": e, "left_wrist": w})
        tw = (w[0] + 2, w[1] + 2)
        pts.update({"right_elbow": _elbow(sh_r, tw, 115.0, side=-1), "right_wrist": tw})
        g = (w[0] - 4, w[1] - 4)
        taps = {"grip": g, "head": _club(g, 55.0, 100, up=True, toward=1)}
    elif p == "P5":
        e, w = _arm(sh_l, 165.0, 110, 110, 262, bend_sign=1)
        pts.update({"left_elbow": e, "left_wrist": w, "right_wrist": (w[0] + 4, w[1]), "right_elbow": (w[0] + 34, w[1] + 18)})
        g = (w[0] - 6, w[1] - 3)
        taps = {"grip": g, "head": _club(g, 30.0, 160, up=True, toward=-1)}
        pts.update({"left_shoulder": (698, 215), "right_shoulder": (598, 215), "left_hip": (678, 370), "right_hip": (618, 370)})
    elif p == "P6":
        pts.update({"left_wrist": (620, 372), "right_wrist": (616, 370)})
        # 上体の {trail} 側屈 5（範囲 0〜10 の真ん中）
        pts.update({"left_shoulder": (690 - 13, 215), "right_shoulder": (590 - 13, 215)})
        taps = {"grip": (626, 370), "head": (470, 420)}
    elif p == "P7":
        # 胸と {lead} 骨盤がボール一個半ずつ {lead} へ（範囲 胸 1〜3・骨盤 1〜2）
        pts.update({"left_wrist": (674, 430), "right_wrist": (666, 432), "left_shoulder": (714, 215), "right_shoulder": (614, 215),
                    "left_hip": (694, 370), "right_hip": (634, 370)})
        taps = {"grip": (678, 420), "head": (660, 636)}
    elif p == "P8":
        e, w = _arm(sh_r, 165.0, 110, 110, 80, bend_sign=1)
        pts.update({"right_elbow": e, "right_wrist": w, "left_wrist": (w[0] - 4, w[1])})
    elif p in ("P9", "P10"):
        pts.update({"left_wrist": (720, 200), "right_wrist": (716, 204)})
    return pts, taps


def _mirror(pts: dict, taps: dict) -> tuple[dict, dict]:
    m = {}
    for k, (x, y) in pts.items():
        k2 = k.replace("left_", "@").replace("right_", "left_").replace("@", "right_")
        m[k2] = (W - x, y)
    return m, {k: (W - x, y) for k, (x, y) in taps.items()}


def landmarks(pts: dict, vis: float = 0.95) -> list[dict]:
    lms = [{"x": 0.5, "y": 0.5, "visibility": 0.1} for _ in range(33)]
    for k, (x, y) in pts.items():
        lms[IDX[k]] = {"x": round(x / W, 6), "y": round(y / H, 6), "visibility": vis}
    return lms


P_ORDER = ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10")


def swing(view: str = "dtl", faults=(), hand: str = "R", club: str = "7 Iron", fps: float = 240.0, ps=P_ORDER[:7], vis: float = 0.95) -> dict:
    """measure の入力（1スイング）。"""
    fs = set(faults)
    frames = {}
    for i, p in enumerate(ps):
        pts, taps = (_dtl if view == "dtl" else _fo)(p, fs)
        if hand == "L":
            pts, taps = _mirror(pts, taps)
        frames[p] = {"t": round(i * 0.25, 3), "landmarks": landmarks(pts, vis), "taps": {k: [round(x, 2), round(y, 2)] for k, (x, y) in taps.items()}}
    if view == "dtl":
        ball = [[840 - BALL_D / 2, 640], [840 + BALL_D / 2, 640]]
    else:
        ball = [[648 - BALL_D / 2, 640], [648 + BALL_D / 2, 640]]
    if hand == "L":
        ball = [[W - x, y] for x, y in ball]
    for f in frames.values():
        for x, y in f["taps"].values():
            assert 0 <= x <= W and 0 <= y <= H, "タップが画面の外"
    return {"view": view, "handedness": hand, "club": club, "fps": fps, "width": W, "height": H, "ball": ball, "frames": frames, "missing": []}


def pose_json() -> dict:
    """画面の確認（ui_check）で window.__FAKE_POSE に渡す形: {view: {P: landmarks}}。"""
    out = {}
    for view in ("dtl", "fo"):
        sw = swing(view, faults=("p2_inside",) if view == "dtl" else (), ps=P_ORDER)
        out[view] = {p: f["landmarks"] for p, f in sw["frames"].items()}
        out[view + "_taps"] = {p: f["taps"] for p, f in sw["frames"].items()}
        out[view + "_ball"] = sw["ball"]
    return out



# ------------------------------------------------------------ 動画ぜんたいの時系列（段2b: 自動の取り出しのテスト）

# 1スイングの中の時刻（始まりからの秒）。P1 のあと手が動き始める（t₀ はそのすぐ後）
KEY_T = {"P1": 1.00, "P2": 1.35, "P3": 1.55, "P4": 1.80, "P5": 2.00, "P6": 2.07, "P7": 2.12, "P8": 2.18, "P9": 2.26, "P10": 2.70}
FINISH_HOLD = 0.7   # フィニッシュで止まっている時間
RELAX = 1.0         # フィニッシュから構えへ戻る時間
SWING_LEN = KEY_T["P10"] + FINISH_HOLD + RELAX  # 次のスイングの始まりまで（その始まりから静かな構えが1秒）


def _hands(view: str, p: str, body: dict, faults: set) -> tuple[tuple, tuple]:
    """P ごとの手首（{lead}, {trail}）。§6.4 の規則がちょうどその時刻に当たるように、しきい値から1画素だけ越した所に置く。"""
    hip = (body["left_hip"][1] + body["right_hip"][1]) / 2
    sl, st = body["left_shoulder"], body["right_shoulder"]
    over = "loop_over" in faults
    if view == "dtl":
        H = {"P1": (653, 451), "P2": (690, hip - 1), "P3": (604, sl[1] - 1), "P4": (588, 158),
             "P5": ((640, sl[1] + 1) if over else (590, sl[1] + 1)), "P6": ((720, hip + 1) if over else (660, hip + 1)),
             "P7": (668, 451), "P8": (762, hip - 1), "P9": (607, st[1] - 1), "P10": (561, 179)}[p]
        return (H[0] + 2, H[1] - 1), (H[0] - 2, H[1] + 1)
    if p == "P3":
        a = math.radians(9.5)
        wl = (sl[0] - 200 * math.cos(a), sl[1] + 200 * math.sin(a))
        return wl, (wl[0] + 4, wl[1] - 4)
    if p == "P9":
        a = math.radians(9.5)
        wt = (st[0] + 200 * math.cos(a), st[1] + 200 * math.sin(a))
        return (wt[0] - 4, wt[1] - 4), wt
    if p == "P4":
        pts, _ = _fo("P4", faults)
        return pts["left_wrist"], pts["right_wrist"]
    H = {"P1": (642, 425), "P2": (558, hip - 1), "P5": (490, sl[1] + 1), "P6": (618, hip + 1), "P7": (670, 431),
         "P8": (758, hip - 1), "P10": (718, 202)}[p]
    return (H[0] + 2, H[1]), (H[0] - 2, H[1])


# 正面の骨盤と胸の横の動き（胴の長さの割合・{trail} が負）: 上げの初めに {trail} へ動き、トップの前に {lead} へ戻り始める
SHIFT = {"P1": 0.0, "P2": -0.08, "P3": -0.10, "P4": -0.02, "P5": 0.05, "P6": 0.08, "P7": 0.10, "P8": 0.10, "P9": 0.10, "P10": 0.10}
SHIFT_STAY = {**SHIFT, "P3": -0.05, "P4": -0.12}


def _key_pose(view: str, p: str, faults: set) -> dict:
    pts, _ = (_dtl if view == "dtl" else _fo)(p, faults)
    pts = dict(pts)
    wl, wt = _hands(view, p, pts, faults)
    pts["left_wrist"], pts["right_wrist"] = wl, wt
    if view == "fo":
        L = 155.0
        dx = (SHIFT_STAY if "recenter_stay" in faults else SHIFT)[p] * L
        for k in ("left_hip", "right_hip", "left_shoulder", "right_shoulder", "nose"):
            pts[k] = (pts[k][0] + dx, pts[k][1])
    return pts


def _lerp_pose(a: dict, b: dict, s: float) -> dict:
    return {k: (a[k][0] + (b[k][0] - a[k][0]) * s, a[k][1] + (b[k][1] - a[k][1]) * s) for k in a}


def motion(view: str = "dtl", hand: str = "R", fps: float = 60.0, n_swings: int = 1, faults=(), waggle: bool = False,
           practice: tuple = (), gaps: tuple = (), roi: bool = True, lead_in: float = 0.0) -> tuple[dict, list[dict]]:
    """動画ぜんたいの姿勢の時系列（POST /v1/video/checkpoints の入力）と、スイングごとの本当の時刻。

    - practice にスイングの番号（1から）を入れると、そのスイングは素振り（ボールのまわりが変わらない）。
    - gaps: [(始まりの秒, 長さ)] の間は体の点が無い（欠け）。
    - waggle: 最初のスイングの前に、手を小さく揺らしてから静かに構え直す。
    - lead_in: 最初のスイングの前に足す時間（ワッグルの場所）。"""
    fs = set(faults)
    keys = {p: _key_pose(view, p, fs) for p in P_ORDER}
    if waggle:
        lead_in = max(lead_in, 1.6)
    total = lead_in + n_swings * SWING_LEN
    truths = []
    frames, rois = [], []
    n = int(round(total * fps))
    for k in range(n):
        t = k / fps
        u = t - lead_in
        idx = int(u // SWING_LEN) if u >= 0 else -1
        if idx >= n_swings:
            idx = n_swings - 1
        s0 = lead_in + idx * SWING_LEN if idx >= 0 else 0.0
        r = t - s0 if idx >= 0 else None
        if r is None:
            pose = dict(keys["P1"])
            if waggle and 0.3 <= t <= 0.9:
                # ワッグル: 手を胴の長さの 0.15 ほど {lead} 側へ揺らして戻す（胸までは上げない）
                a = math.sin(math.pi * (t - 0.3) / 0.6) * 25
                pose["left_wrist"] = (pose["left_wrist"][0] + a, pose["left_wrist"][1] - a * 0.5)
                pose["right_wrist"] = (pose["right_wrist"][0] + a, pose["right_wrist"][1] - a * 0.5)
        elif r <= KEY_T["P1"]:
            pose = dict(keys["P1"])
        elif r <= KEY_T["P10"]:
            names = list(P_ORDER)
            for i in range(len(names) - 1):
                a, b = names[i], names[i + 1]
                if KEY_T[a] <= r <= KEY_T[b]:
                    pose = _lerp_pose(keys[a], keys[b], (r - KEY_T[a]) / (KEY_T[b] - KEY_T[a]))
                    break
        elif r <= KEY_T["P10"] + FINISH_HOLD:
            pose = dict(keys["P10"])
        else:
            s = min(1.0, (r - KEY_T["P10"] - FINISH_HOLD) / RELAX)
            pose = _lerp_pose(keys["P10"], keys["P1"], s)
        if hand == "L":
            pose, _ = _mirror(pose, {})
        missing = any(g0 <= t < g0 + gl for g0, gl in gaps)
        frames.append({"t": round(t, 5), "lm": None if missing else [[round(q["x"], 5), round(q["y"], 5), q["visibility"]] for q in landmarks(pose)]})
        hit = idx >= 0 and r is not None and r >= KEY_T["P7"] - 1e-9 and (idx + 1) not in practice
        rois.append(0.5 if hit else 0.0)
    for i in range(n_swings):
        s0 = lead_in + i * SWING_LEN
        truths.append({"index": i + 1, "practice": (i + 1) in practice, **{p: round(s0 + KEY_T[p], 5) for p in P_ORDER}})
    body = {"view": view, "handedness": hand, "fps": fps, "width": W, "height": H, "frames": frames}
    if roi:
        body["roi"] = rois
    return body, truths


# 合成の動画 testdata/synthetic/stick_motion_dtl.webm の中身（scripts/make_synthetic_video.py が作り、ui_check.py が同じ点を偽の姿勢推定に渡す）
MOTION = {"view": "dtl", "fps": 30.0, "n_swings": 3, "practice": (2,)}
MOTION_GROUND = 600       # 地面の上の端（ボールが地面の色の上に乗るように、ほかの動画より上げる）
MOTION_BALL = (840, 632)  # ボールの中心（画素）


def measure_input(body: dict, det: dict, club: str = "7 Iron") -> dict:
    """自動で取り出した1スイング（video.detect の swings の1つ）→ measure の入力（P のコマの点・時系列）。"""
    frames = {}
    for p in det["ps"]:
        if p["frame"] is None:
            continue
        lm = body["frames"][p["frame"]]["lm"]
        frames[p["p"]] = {"t": p["t"], "landmarks": [{"x": q[0], "y": q[1], "visibility": q[2]} for q in lm] if lm else [], "taps": {}}
    ball = [[840 - BALL_D / 2, 640], [840 + BALL_D / 2, 640]] if body["view"] == "dtl" else [[648 - BALL_D / 2, 640], [648 + BALL_D / 2, 640]]
    if body["handedness"] == "L":
        ball = [[W - x, y] for x, y in ball]
    return {"view": body["view"], "handedness": body["handedness"], "club": club, "fps": body["fps"], "width": W, "height": H,
            "ball": ball, "frames": frames, "missing": [], "series": det["series"]}


if __name__ == "__main__":
    print(json.dumps(pose_json()))
