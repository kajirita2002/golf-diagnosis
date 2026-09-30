"""動画の姿勢の時系列から、スイングの区間と P1〜P10（と中間の P5.5・P6.5）を取り出す（docs/DESIGN_v2.md §6.3・§6.4。版 video/0.3）。

- **LLM を使わない。** 規則は全部ここに1か所で持ち、pytest で固定する（tests/test_video.py）。
- 入力は端末が取った姿勢の点の時系列（数値だけ。動画そのものは来ない）。保存しない。
- 画面の座標: x は右が正、y は下が正。点は 0〜1 の割合で来て、ここで画素に直す。
  左打ちは画面を左右に反転し、右打ちの座標で見る（R9）。{lead} / {trail} の点も入れ替える。
- 時間は秒（`t`）で見る。コマの間隔がそろっていなくてよい（粗い走査と細かい走査を混ぜて送れる）。
- 決めた P には `status`（ok / estimated / failed）と `confidence`（high / low）を付ける。
  `estimated` は「代わりの規則で決めた目安」（後ろからの P2・P3・P6 など。§6.4 の表の注記）、
  `low` は「本人に確かめてもらうコマ」（点が見えない・コマが粗い・順番や時間がおかしい）。R2 に出すのは low だけ。

記号（§6.4）: H＝手の中心の代わり（両手首の中点）、Sl / St＝{lead} / {trail} 側の肩、hip＝両腰の中点、
L＝胴の長さ（両肩の中点〜両腰の中点の、全コマの中央値）。

スイングの区間を探すとき（構えの静止・始動 t₀・P1）だけ、手は**腰からの相対**（Hr＝中央値でなめらかにした手 −
なめらかにした腰）で見る。手持ちで撮るとカメラの揺れで全部の点が同じだけ動くので、腰との差なら揺れが消える
（video/0.3。本番で、練習場のモニターを手持ちで撮った動画が「スイングが見つかりませんでした」で止まったため）。
測る項目（スエー・リセンタリングなど）の値にはこの相対を使わない（測る側は元の点を見る）。
スイングが一本も見つからないときは `diag`（体の点が取れたコマの割合・手が胸より上に上がったか・構えがあったか・
理由の記号）を返し、画面が具体的な理由と次の手を出す。

入力:
  {view: dtl|fo, handedness: R|L, fps: 動画の fps（分からなければ 0）, width, height,
   frames: [{t, lm: [[x, y, v] × 33] | null}],   ← 0〜1 の割合。lm の代わりに landmarks: [{x, y, visibility}] でもよい
   roi: [0〜1 | null]（任意。frames と同じ並び。構えのボールのまわりが構えのコマからどれだけ変わったか）,
   ball_seen: [{t, d}]（任意。各スイングの構えのコマで、丸の中が最初にタップしたボールの絵とどれだけ違うか 0〜1。
              似ていなければ、そのスイングの素振りの判定をしない＝新しいボールが丸から横に置かれたかもしれない）,
   club_taps: [{t, grip: [x, y], head: [x, y]}]（任意。正面の P2・P6 の寄せ直しに使う。画素）,
   practice_fallback: 全部が素振りに見えたら外さずに返すか（既定は真）}
- 点の値が数でないコマは、そのコマの点の欠けとして扱う（落ちない）。
"""

from __future__ import annotations

import math
from typing import Any

from . import config

# MediaPipe Pose の33点のうち使うもの
_I = {"nose": 0, "left_shoulder": 11, "right_shoulder": 12, "left_wrist": 15, "right_wrist": 16, "left_hip": 23, "right_hip": 24}

PS_REQUIRED = ("P1", "P2", "P3", "P4", "P5", "P6", "P7")
PS_ALL = ("P1", "P2", "P3", "P4", "P5", "P5_5", "P6", "P6_5", "P7", "P8", "P9", "P10")

REASONS = {
    "not_found": "見つからない",
    "order": "順番が合わない",
    "low_visibility": "体の点が見えない",
    "coarse": "コマが粗い",
    "back_time": "上げの時間がふつうと違う",
    "down_time": "下ろしの時間がふつうと違う",
    "no_ball_change": "ボールが動いていない",
    "p1_far": "構えから始動までが長い",
    "proxy": "代わりの規則で決めた目安",
    "addr_guess": "構えの静止が見つからないので推定",
}

NAN = float("nan")


def _isnan(v: float) -> bool:
    return v != v


# ------------------------------------------------------------ 入力 → 時系列


class Series:
    """1本の動画の時系列（画素・右打ちの座標）。欠けは短いものだけ線でつなぐ。"""

    def __init__(self, body: dict):
        self.view = body.get("view") if body.get("view") in ("dtl", "fo") else "dtl"
        self.hand = "L" if body.get("handedness") == "L" else "R"
        self.w = float(body.get("width") or 0) or 1.0
        self.h = float(body.get("height") or 0) or 1.0
        self.fps = float(body.get("fps") or 0)
        raw = [f for f in (body.get("frames") or []) if isinstance(f, dict) and isinstance(f.get("t"), (int, float))]
        raw.sort(key=lambda f: f["t"])
        roi_in = body.get("roi")
        roi_by_t = None
        if isinstance(roi_in, list) and len(roi_in) == len(body.get("frames") or []):
            # 並べ替えの前の順で来るので、時刻で引けるようにしてから並べ直す
            roi_by_t = {}
            for f, r in zip(body.get("frames") or [], roi_in):
                if isinstance(f, dict) and isinstance(f.get("t"), (int, float)):
                    roi_by_t[round(float(f["t"]), 6)] = r if isinstance(r, (int, float)) else None
        self.t: list[float] = []
        self.pts: dict[str, list[list[float]]] = {k: [] for k in ("H", "hip", "chest", "Sl", "St", "wl", "wt", "nose")}
        self.missing: list[bool] = []
        self.roi: list[float | None] | None = [] if roi_by_t is not None else None
        last_t = None
        for f in raw:
            t = float(f["t"])
            if last_t is not None and t - last_t < 1e-6:
                continue  # 同じ時刻の二回目は捨てる
            last_t = t
            self.t.append(t)
            got = self._points(f)
            self.missing.append(got is None)
            for k in self.pts:
                self.pts[k].append(list(got[k]) if got else [NAN, NAN, 0.0])
            if self.roi is not None:
                self.roi.append(roi_by_t.get(round(t, 6)))
        self.n = len(self.t)
        # 体の点（手・腰・胸）が取れたコマの数（線でつなぐ前。スイングが見つからないときの理由に使う）
        self.n_pose = sum(1 for i in range(self.n) if all(not _isnan(self.pts[k][i][0]) for k in ("H", "hip", "chest")))
        self._fill_gaps(config.VIDEO_MAX_GAP_S)
        self.L = self._torso()
        # 平滑化した手（粗い走査の揺れと、姿勢推定の小さな揺れを消す）
        self.Hs = self._smooth(self.pts["H"], config.VIDEO_SMOOTH_S)
        # 区間を探すための手（中央値でなめらかに。一コマだけ跳んだ点を消す）と、腰からの相対（手持ちの揺れを消す）
        self.Hd = self._median(self.pts["H"], config.VIDEO_FIND_MED_S)
        hip_s = self._smooth(self.pts["hip"], config.VIDEO_FIND_HIP_S)
        self.Hr = [[NAN, NAN] if _isnan(h[0]) or _isnan(p[0]) else [h[0] - p[0], h[1] - p[1]] for h, p in zip(self.Hd, hip_s)]
        self.ball_seen: list[tuple[float, float]] = []
        for b in body.get("ball_seen") or []:
            try:
                self.ball_seen.append((float(b["t"]), float(b["d"])))
            except (KeyError, TypeError, ValueError):
                continue
        self.taps = []
        for c in body.get("club_taps") or []:
            try:
                g, hd = c["grip"], c["head"]
                gx, hx = float(g[0]), float(hd[0])
                if self.hand == "L":
                    gx, hx = self.w - gx, self.w - hx
                self.taps.append({"t": float(c["t"]), "grip": (gx, float(g[1])), "head": (hx, float(hd[1]))})
            except (KeyError, TypeError, ValueError, IndexError):
                continue

    def _points(self, f: dict) -> dict | None:
        lm = f.get("lm")
        if lm is None and isinstance(f.get("landmarks"), list):
            lm = [[p.get("x"), p.get("y"), p.get("visibility", 1.0)] if isinstance(p, dict) else None for p in f["landmarks"]]
        if not isinstance(lm, list) or len(lm) < 33:
            return None
        P = {}
        for name, i in _I.items():
            q = lm[i]
            if not isinstance(q, (list, tuple)) or len(q) < 2 or q[0] is None or q[1] is None:
                return None
            try:
                x, y = float(q[0]) * self.w, float(q[1]) * self.h
                v = float(q[2]) if len(q) > 2 and q[2] is not None else 1.0
            except (TypeError, ValueError):
                return None  # 数でない値はそのコマの欠け（500 にしない）
            if not all(math.isfinite(z) for z in (x, y, v)):
                return None
            if self.hand == "L":
                x = self.w - x
            P[name] = (x, y, v)
        # 左打ちは {lead}＝体の右（反転したあとで右打ちと同じ役）
        lead, trail = ("right", "left") if self.hand == "L" else ("left", "right")

        def mid(a, b):
            return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, min(a[2], b[2]))

        out = {
            "Sl": P[f"{lead}_shoulder"], "St": P[f"{trail}_shoulder"], "wl": P[f"{lead}_wrist"], "wt": P[f"{trail}_wrist"],
            "H": mid(P["left_wrist"], P["right_wrist"]), "hip": mid(P["left_hip"], P["right_hip"]),
            "chest": mid(P["left_shoulder"], P["right_shoulder"]), "nose": P["nose"],
        }
        # 見えにくい点は欠けとして扱う（線でつなぐか、つなげなければ判断できない）
        for k, p in out.items():
            if p[2] < config.CP_MIN_VISIBILITY:
                out[k] = (NAN, NAN, p[2])
        return out

    def _fill_gaps(self, max_gap: float) -> None:
        """短い欠け（max_gap 秒まで）は前後の点を線でつなぐ。つないだ点の visibility は 0（「確かめる」の印に使う）。"""
        self.filled = [False] * self.n
        for k, arr in self.pts.items():
            i = 0
            while i < self.n:
                if not _isnan(arr[i][0]):
                    i += 1
                    continue
                j = i
                while j < self.n and _isnan(arr[j][0]):
                    j += 1
                if i > 0 and j < self.n and self.t[j] - self.t[i - 1] <= max_gap + 1e-9:
                    a, b = arr[i - 1], arr[j]
                    for m in range(i, j):
                        s = (self.t[m] - self.t[i - 1]) / (self.t[j] - self.t[i - 1])
                        arr[m] = [a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s, 0.0]
                        self.filled[m] = True
                i = j

    def _torso(self) -> float:
        ds = []
        for c, h in zip(self.pts["chest"], self.pts["hip"]):
            if not _isnan(c[0]) and not _isnan(h[0]):
                ds.append(math.hypot(c[0] - h[0], c[1] - h[1]))
        ds.sort()
        return ds[len(ds) // 2] if ds else 0.0

    def _smooth(self, arr: list[list[float]], half: float) -> list[list[float]]:
        out = []
        n = self.n
        for i in range(n):
            if _isnan(arr[i][0]):
                out.append([NAN, NAN, arr[i][2]])
                continue
            sx = sy = 0.0
            c = 0
            j = i
            while j >= 0 and self.t[i] - self.t[j] <= half + 1e-9:
                if not _isnan(arr[j][0]):
                    sx += arr[j][0]
                    sy += arr[j][1]
                    c += 1
                j -= 1
            j = i + 1
            while j < n and self.t[j] - self.t[i] <= half + 1e-9:
                if not _isnan(arr[j][0]):
                    sx += arr[j][0]
                    sy += arr[j][1]
                    c += 1
                j += 1
            out.append([sx / c, sy / c, arr[i][2]])
        return out

    def _median(self, arr: list[list[float]], half: float) -> list[list[float]]:
        out = []
        for i in range(self.n):
            if _isnan(arr[i][0]):
                out.append([NAN, NAN, arr[i][2]])
                continue
            xs, ys = [], []
            j = i
            while j >= 0 and self.t[i] - self.t[j] <= half + 1e-9:
                j -= 1
            j += 1
            while j < self.n and self.t[j] - self.t[i] <= half + 1e-9:
                if not _isnan(arr[j][0]):
                    xs.append(arr[j][0])
                    ys.append(arr[j][1])
                j += 1
            out.append([_med(xs), _med(ys), arr[i][2]])
        return out

    def rok(self, i: int) -> bool:
        return not _isnan(self.Hr[i][0])

    def rdist(self, i: int, c: tuple[float, float]) -> float:
        return math.hypot(self.Hr[i][0] - c[0], self.Hr[i][1] - c[1]) if self.rok(i) else math.inf

    # ---- 便利 ----
    def y(self, name: str, i: int) -> float:
        return self.pts[name][i][1]

    def ok(self, i: int, *names: str) -> bool:
        return all(not _isnan(self.pts[k][i][0]) for k in names)

    def gap_at(self, i: int) -> float:
        """コマ i のまわりの、実際に取ったコマの間隔（前後の広いほう）。走査の間引き（240fps を 120Hz で取る）も入る。"""
        return max(self.t[i] - self.t[i - 1] if i > 0 else 0.0, self.t[i + 1] - self.t[i] if i + 1 < self.n else 0.0) or self.dt()

    def dt(self) -> float:
        """コマの間隔（動画の fps が分かればその間隔。無ければ送られた時刻の間隔の中央値）。"""
        if self.fps > 0:
            return 1.0 / self.fps
        d = sorted(b - a for a, b in zip(self.t, self.t[1:]) if b > a)
        return d[len(d) // 2] if d else 1.0 / 30


# ------------------------------------------------------------ スイングの区間


def _below_hip(s: Series, i: int) -> bool:
    return s.ok(i, "H", "hip") and s.Hd[i][1] > s.y("hip", i)


def _above_chest(s: Series, i: int) -> bool:
    return s.ok(i, "H", "chest") and s.Hd[i][1] < s.y("chest", i)


def _below_chest(s: Series, i: int) -> bool:
    return s.ok(i, "H", "chest") and s.Hd[i][1] >= s.y("chest", i)


def _med(v: list[float]) -> float:
    v = sorted(v)
    m = len(v)
    return v[m // 2] if m % 2 else (v[m // 2 - 1] + v[m // 2]) / 2


def _spread(s: Series, idx: list[int]) -> tuple[float, tuple[float, float]]:
    """コマの並び idx の手（腰からの相対）の、中央からの一番遠い距離と中央（平均）。窓を伸ばすか決めるのに使う。"""
    xs = [s.Hr[q][0] for q in idx]
    ys = [s.Hr[q][1] for q in idx]
    c = (sum(xs) / len(xs), sum(ys) / len(ys))
    return max(math.hypot(x - c[0], y - c[1]) for x, y in zip(xs, ys)), c


def _addr_of(s: Series, idx: list[int], cap_l: float = config.VIDEO_FIND_STATIC_L) -> tuple[tuple[float, float], float]:
    """構えの位置（中央値）と揺れの幅（中央値からの距離の中央値の2倍）。

    中央値にするのは、窓の終わりに始動の初めが少し入っても構えの位置がそちらへ寄らないようにするため
    （しきい値を緩めたぶん、窓の端に動き始めが入りやすい）。"""
    c = (_med([s.Hr[q][0] for q in idx]), _med([s.Hr[q][1] for q in idx]))
    tol = 2 * _med([math.hypot(s.Hr[q][0] - c[0], s.Hr[q][1] - c[1]) for q in idx])
    return c, min(tol, cap_l * s.L)


def _static_window(s: Series, lo: int, hi: int) -> dict | None:
    """[lo, hi] の中で、手（腰からの相対）がほとんど動かない区間（VIDEO_STATIC_S 秒以上、どの点も中央から VIDEO_FIND_STATIC_L×L 以内）のうち最後のもの。

    点の欠けは VIDEO_FIND_GAP_S 秒までなら飛ばして続ける（粗い走査で一コマ欠けても切らない）。
    返すのは {a: 始まり, b: 終わり, addr: 手の中央（相対）, tol: 実際に揺れていた幅, guess: False}。"""
    lim = config.VIDEO_FIND_STATIC_L * s.L
    best = None
    i = lo
    while i <= hi:
        if not s.rok(i):
            i += 1
            continue
        idx = [i]
        sx, sy = s.Hr[i][0], s.Hr[i][1]
        j = i
        q = i + 1
        while q <= hi:
            if not s.rok(q):
                if s.t[q] - s.t[idx[-1]] > config.VIDEO_FIND_GAP_S:
                    break
                q += 1
                continue
            if s.t[q] - s.t[idx[-1]] > config.VIDEO_FIND_GAP_S:
                break
            cx, cy = sx / len(idx), sy / len(idx)
            if math.hypot(s.Hr[q][0] - cx, s.Hr[q][1] - cy) > lim:
                break
            idx.append(q)
            sx += s.Hr[q][0]
            sy += s.Hr[q][1]
            j = q
            q += 1
        if len(idx) >= 2 and s.t[j] - s.t[i] >= config.VIDEO_STATIC_S - 1e-9:
            c, tol = _addr_of(s, idx)
            best = {"a": i, "b": j, "addr": c, "tol": tol, "guess": False}
            i = j + 1
        else:
            i += 1
    return best


def _quiet_window(s: Series, lo: int, hi: int) -> dict | None:
    """静かな構えが見つからないときの「構え（推定）」: 手が腰より下の区間 [lo, hi] が VIDEO_ADDR_MIN_S 秒以上あれば、
    その中の VIDEO_STATIC_S 秒の窓のうち、手（腰からの相対）の動きが一番小さい所（同じくらいなら後ろ＝始動に近いほう）。

    行き止まりにしないための道。P1 は必ず確かめてもらう（`addr_guess`）。"""
    if hi <= lo or s.t[hi] - s.t[lo] < config.VIDEO_ADDR_MIN_S - 1e-9:
        return None
    cands = []
    for i in range(lo, hi + 1):
        if not s.rok(i):
            continue
        idx = [q for q in range(i, hi + 1) if s.t[q] - s.t[i] <= config.VIDEO_STATIC_S + 1e-9 and s.rok(q)]
        if len(idx) < 2 or s.t[idx[-1]] - s.t[i] < 0.5 * config.VIDEO_STATIC_S:
            continue
        sp, _c = _spread(s, idx)
        cands.append((sp, i, idx[-1], idx))
    if not cands:
        return None
    lo_sp = min(x[0] for x in cands)
    if lo_sp > config.VIDEO_ADDR_GUESS_L * s.L:
        return None
    ok = [x for x in cands if x[0] <= max(1.5 * lo_sp, config.VIDEO_FIND_STATIC_L * s.L)]
    _sp, a, b, idx = max(ok, key=lambda x: x[1])
    # 揺れの幅は胴の長さの半分の、さらに半分まで（始動は揺れの幅の2倍より遠く＝腰の高さより手前で決まるように）
    c, tol = _addr_of(s, idx, cap_l=config.VIDEO_ADDR_GUESS_L / 2)
    return {"a": a, "b": b, "addr": c, "tol": tol, "guess": True}


def find_swings(s: Series, why: dict | None = None) -> list[dict]:
    """手が胸より上に上がった区間ごとに、その前に構え（手が腰より下で静か）があり、後で胸より下へ下りたものをスイングにする。

    - ワッグル（手が胸まで上がらない）はスイングにしない。
    - フォローとフィニッシュで手が上がっている区間は、前のスイングの上げの区間のすぐ後（VIDEO_FOLLOW_S 秒以内）か、
      直前の腰より下の区間が短く速い（当たる瞬間）ので、スイングにしない。
    - 静かな構えが見つからなくても、腰より下の区間が十分長ければ、一番動きの小さい所を「構え（推定）」にする（行き止まりにしない）。
    why に dict を渡すと、見つからなかった理由の材料（上がった区間の数・構えがあった数・下りた数）を数えて入れる。
    返すのは [{top, c_up, win: {a, b, addr, tol, guess}, run}]。"""
    out = []
    used: set[int] = set()
    i = 0
    n = s.n
    st = {"raised": 0, "address": 0, "static": 0, "down": 0}
    while i < n:
        if not _above_chest(s, i):
            i += 1
            continue
        j = i
        while True:
            while j + 1 < n and (_above_chest(s, j + 1) or not s.ok(j + 1, "H", "chest")):
                # 点の欠けが長く続くところで区間を切る（下ろしのコマが全部欠けると、上げとフィニッシュが一つの区間になり、
                # フィニッシュの手の高さをトップと取り違える）
                if not s.ok(j + 1, "H", "chest"):
                    q = j + 1
                    while q < n and not s.ok(q, "H", "chest"):
                        q += 1
                    last_ok = max((z for z in range(i, j + 1) if s.ok(z, "H", "chest")), default=i)
                    if q < n and s.t[q] - s.t[last_ok] > config.VIDEO_RUN_GAP_S + 1e-9:
                        break
                j += 1
            # 一瞬だけ胸より下に出たコマ（VIDEO_RUN_MERGE_S 秒まで・腰より下には下りていない）は切れ目にしない
            nxt = next((q for q in range(j + 1, n) if s.t[q] - s.t[j] <= config.VIDEO_RUN_MERGE_S + 1e-9 and _above_chest(s, q)), None)
            if nxt is None or any(_below_hip(s, q) for q in range(j + 1, nxt)):
                break
            j = nxt
        top = min(range(i, j + 1), key=lambda k: s.Hs[k][1] if s.ok(k, "H") else math.inf)
        # 前のスイングの上げのすぐ後に上がったのは、フォローとフィニッシュ
        if out and s.t[i] - s.t[out[-1]["run"][1]] < config.VIDEO_FOLLOW_S:
            i = j + 1
            continue
        st["raised"] += 1
        # 直前の、腰より下の区間（その最後のコマの次が P2 の目安）
        k = i - 1
        while k >= 0 and not _below_hip(s, k):
            k -= 1
        if k < 0:
            i = j + 1
            continue
        c_up = k + 1
        if s.t[i] - s.t[k] > config.VIDEO_RISE_MAX_S:
            # 上げの途中が見えていない（フィニッシュを上げと取り違えない）
            i = j + 1
            continue
        # 腰より下の区間の頭。腰の高さのあたりで点が揺れて一瞬だけ腰より上に出たコマ（VIDEO_FIND_BRIEF_S 秒まで）は切れ目にしない
        m = k
        q = k - 1
        while q >= 0:
            if _below_hip(s, q):
                m = q
            elif s.ok(q, "H", "hip") and s.t[m] - s.t[q] > config.VIDEO_FIND_BRIEF_S + 1e-9:
                break
            q -= 1
        win = _static_window(s, m, k)
        if win:
            st["static"] += 1
        else:
            win = _quiet_window(s, m, k)
        if win:
            st["address"] += 1
        # 後で胸より下へ下りるか（下ろし。粗い走査では腰より下のコマが一つも無いことがあるので、胸で見る）
        down = next((q for q in range(j + 1, n) if _below_chest(s, q)), None)
        if down is not None:
            st["down"] += 1
        # 同じ構えから二つ目の「上」は、フィニッシュ（点が欠けて下ろしが見えなかったとき）なので数えない
        if win and down is not None and c_up not in used and s.t[top] - s.t[win["b"]] <= config.VIDEO_MAX_BACK_S:
            used.add(c_up)
            out.append({"top": top, "c_up": c_up, "win": win, "run": (i, j)})
        i = j + 1
    if why is not None:
        why.update(st)
    return out


def diagnose(s: Series, why: dict) -> dict:
    """スイングが見つからなかった理由（画面が具体的な理由と次の手を出すため）。

    reason: no_pose（体の点がほとんど取れない）/ no_raise（手が胸より上に上がった区間が無い）/
            no_address（上がったが、その前に手が腰より下で落ち着いた構えが無い）/ no_down（上がったまま下りてこない）/ other"""
    ratio = s.n_pose / s.n if s.n else 0.0
    if ratio < config.VIDEO_DIAG_MIN_POSE:
        reason = "no_pose"
    elif not why.get("raised"):
        reason = "no_raise"
    elif not why.get("address"):
        reason = "no_address"
    elif not why.get("down"):
        reason = "no_down"
    else:
        reason = "other"
    return {"reason": reason, "pose_ratio": round(ratio, 3), "n_frames": s.n, "raised": bool(why.get("raised")),
            "address": bool(why.get("address")), "static": bool(why.get("static")), "down": bool(why.get("down"))}


# ------------------------------------------------------------ P を決める


def _first(s: Series, a: int, b: int, cond) -> int | None:
    for i in range(max(a, 0), min(b, s.n - 1) + 1):
        if cond(i):
            return i
    return None


def _cross(s: Series, a: int | None, b: int, cond) -> tuple[int | None, bool]:
    """cond が最初に真になるコマと、その直前のコマの点が欠けていたか（欠けの中で越えたなら、越えた時刻は分からない）。"""
    if a is None:
        return None, False
    i = _first(s, a, b, cond)
    if i is None:
        return None, False
    return i, (i > 0 and not s.ok(i - 1, "H", "hip", "chest"))


def _nearest(s: Series, t: float) -> int:
    lo, hi = 0, s.n - 1
    while lo < hi:
        m = (lo + hi) // 2
        if s.t[m] < t:
            lo = m + 1
        else:
            hi = m
    if lo > 0 and abs(s.t[lo - 1] - t) <= abs(s.t[lo] - t):
        return lo - 1
    return lo


def _arm_level(s: Series, i: int, sh: str, wr: str, side: int) -> bool:
    """肩 → 手首の線が水平から VIDEO_ARM_LEVEL_DEG 以内で、手首が肩から side の向き（+1 は +x）にある。"""
    if not s.ok(i, sh, wr):
        return False
    a, b = s.pts[sh][i], s.pts[wr][i]
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx * side <= 0:
        return False
    return math.degrees(math.atan2(abs(dy), abs(dx))) <= config.VIDEO_ARM_LEVEL_DEG + 1e-9


def _shaft_level(tap: dict) -> float:
    """タップしたクラブの線と水平のなす角（0〜90）。"""
    g, h = tap["grip"], tap["head"]
    dx, dy = h[0] - g[0], h[1] - g[1]
    return math.degrees(math.atan2(abs(dy), abs(dx) or 1e-9))


def _p(s: Series, name: str, i: int | None, method: str, status: str, reason: str = "") -> dict:
    if i is None:
        return {"p": name, "t": None, "frame": None, "source": "auto", "method": method, "status": "failed",
                "reason": "not_found" if reason in ("", "proxy") else reason, "confidence": "low"}
    return {"p": name, "t": round(s.t[i], 4), "frame": i, "source": "auto", "method": method, "status": status, "reason": reason, "confidence": "high"}


def _club_snap(s: Series, p: dict) -> dict:
    """正面でクラブをタップしたコマが P2・P6 の前後3コマにあれば、シャフトが水平に一番近いコマへ寄せ直す（§6.4）。

    寄せ直して「ok」と言うのは、一番水平に近いタップが VIDEO_SNAP_LEVEL_DEG 以内で、その前と後のコマにもタップがあるときだけ
    （前後がそろっていないと「一番近い」とは言えない。斜めのタップ一つで目安が ok に化けない）。それ以外は目安のまま。"""
    if s.view != "fo" or p["t"] is None or not s.taps:
        return p
    tol = (config.VIDEO_SNAP_FRAMES + 0.5) * s.dt()  # 半コマの余裕（時刻の丸めで端のコマを落とさない）
    near = [c for c in s.taps if abs(c["t"] - p["t"]) <= tol]
    if len(near) < 2:
        return p
    best = min(near, key=_shaft_level)
    if _shaft_level(best) > config.VIDEO_SNAP_LEVEL_DEG + 1e-9:
        return p
    if not (any(c["t"] < best["t"] - 1e-6 for c in near) and any(c["t"] > best["t"] + 1e-6 for c in near)):
        return p
    i = _nearest(s, best["t"])
    return {**p, "t": round(s.t[i], 4), "frame": i, "method": "club_tap", "status": "ok", "reason": ""}


def detect_one(s: Series, sw: dict) -> dict:
    """1スイングの P（§6.4 の表の規則）。"""
    L = s.L
    win = sw["win"]
    a, b, addr = win["a"], win["b"], win["addr"]
    top0 = sw["top"]
    end = s.n - 1
    # 構えの位置の近さ: 胴の長さの VIDEO_STATIC_L か、構えの区間で実際に揺れていた幅の大きいほう。
    # 始動はその2倍より遠く（揺れの幅の中で「離れた」と言わない）
    near_l = max(config.VIDEO_STATIC_L * L, win["tol"])
    t0_l = max(config.VIDEO_T0_L * L, 2 * near_l)

    def dist_addr(i: int) -> float:
        return s.rdist(i, addr)

    # t₀: 構えのあと、手（腰からの相対）が構えから 0.08L 離れ、そのまま P2 の目安まで離れ続けた最初のコマ
    t0i = None
    for i in range(b, sw["c_up"] + 1):
        if dist_addr(i) >= t0_l and all(dist_addr(q) >= t0_l for q in range(i, sw["c_up"] + 1) if s.rok(q)):
            t0i = i
            break
    if t0i is None:
        t0i = sw["c_up"]
    # 離れて 100ms 続かなければ、次のコマからにする（ほとんど起きない。c_up まで離れ続けているので）
    ps: dict[str, dict] = {}
    # P1: t₀ の直前で、手がまだ構えの位置にある最後のコマ（静止区間の最後。ゆっくり始めたときは少し後ろへ）
    p1 = None
    for i in range(t0i - 1, a - 1, -1):
        if dist_addr(i) <= near_l:
            p1 = i
            break
    ps["P1"] = _p(s, "P1", p1 if p1 is not None else b, "rule", "ok")
    if win["guess"]:
        # 静かな構えが見つからず、一番動きの小さい所を構えにした: P1 は必ず確かめてもらう
        ps["P1"].update(status="estimated", confidence="low", reason="addr_guess")
    elif p1 is not None and s.t[t0i] - s.t[p1] > config.VIDEO_P1_GAP_S:
        ps["P1"].update(confidence="low", reason="p1_far")
    # P2: t₀ のあと、手が腰の高さに達した最初のコマ（後ろからはシャフトが短く写るので目安。正面はタップで寄せ直す）
    i2 = _first(s, t0i, top0, lambda i: s.ok(i, "H", "hip") and s.Hs[i][1] <= s.y("hip", i))
    ps["P2"] = _club_snap(s, _p(s, "P2", i2, "rule", "estimated", "proxy"))
    # P4 の範囲を先に: 上げの頂点（なめらかにした手の一番高いコマ）
    run_a, run_b = sw["run"]
    # P3: 正面は {lead} 腕が水平に入った最初のコマ。後ろ（腕が胴に隠れる）と、正面で見つからないときは、手が {lead} 肩の高さに達したコマ（目安）
    i3 = None
    st3 = "ok"
    if s.view == "fo" and i2 is not None:
        i3 = _first(s, i2, top0, lambda i: _arm_level(s, i, "Sl", "wl", -1))
    if i3 is None and i2 is not None:
        i3 = _first(s, i2, top0, lambda i: s.ok(i, "H", "Sl") and s.Hs[i][1] <= s.y("Sl", i))
        st3 = "estimated"
    ps["P3"] = _p(s, "P3", i3, "rule", st3, "proxy" if st3 == "estimated" else "")
    # P4: P3 のあと、なめらかにした手が一番高いコマ（胸より上の区間の中）
    lo4 = i3 if i3 is not None else run_a
    cand = [i for i in range(max(lo4, run_a), run_b + 1) if s.ok(i, "H")]
    i4 = min(cand, key=lambda k: s.Hs[k][1]) if cand else None
    ps["P4"] = _p(s, "P4", i4, "rule", "ok")
    # P5: P4 のあと、手が {lead} 肩の高さを下向きに通った最初のコマ
    # 下ろしの P はトップから決まった時間の中だけで探す（点が欠けて下ろしが見えないとき、振り終わって手を下ろしたコマを拾わない）
    dend = (_first(s, i4, end, lambda i: s.t[i] - s.t[i4] > 2 * config.VIDEO_DOWN_RANGE[1]) or end) if i4 is not None else end
    i5 = _first(s, i4, dend, lambda i: s.ok(i, "H", "Sl") and s.Hs[i][1] >= s.y("Sl", i)) if i4 is not None else None
    ps["P5"] = _p(s, "P5", i5, "rule", "ok")
    # P6: P5 のあと、手が腰の高さに達した最初のコマ（P2 と同じく、後ろからは目安・正面はタップで寄せ直す）
    i6 = _first(s, i5, dend, lambda i: s.ok(i, "H", "hip") and s.Hs[i][1] >= s.y("hip", i)) if i5 is not None else None
    ps["P6"] = _club_snap(s, _p(s, "P6", i6, "rule", "estimated", "proxy"))
    i6 = ps["P6"]["frame"]
    # P7: ボールのまわりが構えから変わり、変わったままのコマ（挟み込み）。無ければ手が構えの高さへ戻ったコマ（目安）
    i7 = None
    low7 = False
    method7, st7, why7 = "ball_roi", "ok", ""
    practice = None
    # 探し始め: P6（無ければ P5・トップ）。P6 が無いときは、トップから下ろしの長さの上限まで探す
    def seen_before(q):  # そのコマの直前の点が見えている（欠けの向こうで越えたのではない）
        return q is not None and (q == 0 or s.ok(q - 1, "H", "hip", "chest"))

    a7 = i6 if seen_before(i6) and seen_before(i5) else i4
    if a7 is not None:
        span = config.VIDEO_P7_SEARCH_S if a7 == i6 else 2 * config.VIDEO_DOWN_RANGE[1]
        win_end = _first(s, a7, end, lambda i: s.t[i] - s.t[a7] > span) or end
        i6 = a7
        if s.roi is not None and any(s.roi[q] is not None for q in range(i6, win_end + 1)):
            thr = config.VIDEO_ROI_THRESHOLD
            for i in range(i6, win_end + 1):
                r = s.roi[i]
                if r is None or r < thr:
                    continue
                # 変わったまま続くか（クラブが一瞬通っただけでない）
                q = i
                ok = True
                while q <= end and s.t[q] - s.t[i] <= config.VIDEO_ROI_STAY_S:
                    if s.roi[q] is not None and s.roi[q] < thr:
                        ok = False
                        break
                    q += 1
                if ok:
                    i7 = i
                    break
            # 丸の中が変わらない＝素振り、と言えるのは、この構えで丸の中にボールが写っていたときだけ
            # （ボールの丸は最初のスイングで一回タップした位置。新しいボールが丸から横に置かれると、打っても丸の中は変わらない）
            if i7 is None and _ball_in_circle(s, s.t[a], s.t[t0i]):
                practice = True
            elif i7 is not None:
                practice = False
        if i7 is None:
            method7, st7, why7 = "rule", "estimated", "proxy"
            h1 = s.Hs[ps["P1"]["frame"]][1] if ps["P1"]["frame"] is not None and s.ok(ps["P1"]["frame"], "H") else None
            if h1 is not None:
                i7 = _first(s, i6, win_end, lambda i: s.ok(i, "H") and s.Hs[i][1] >= h1 - config.VIDEO_P7_BACK_L * L)
            if i7 is None:
                cand = [i for i in range(i6, win_end + 1) if s.ok(i, "H")]
                i7 = max(cand, key=lambda k: s.Hs[k][1]) if cand else None
                low7 = i7 is not None
    ps["P7"] = _p(s, "P7", i7, method7, st7, why7)
    if low7 and ps["P7"]["frame"] is not None:
        ps["P7"]["confidence"] = "low"
    if practice:
        ps["P7"]["reason"] = "no_ball_change"
    # P8: P7 のあと、手が腰の高さに達した最初のコマ
    i7 = ps["P7"]["frame"]
    i8 = _first(s, i7 + 1, end, lambda i: s.ok(i, "H", "hip") and s.Hs[i][1] <= s.y("hip", i)) if i7 is not None else None
    ps["P8"] = _p(s, "P8", i8, "rule", "ok")
    # P9: 正面は {trail} 腕が水平に入った最初のコマ。後ろと見つからないときは、手が {trail} 肩の高さに達したコマ（目安）
    i9, st9 = None, "ok"
    if i8 is not None:
        if s.view == "fo":
            i9 = _first(s, i8, end, lambda i: _arm_level(s, i, "St", "wt", 1))
        if i9 is None:
            i9 = _first(s, i8, end, lambda i: s.ok(i, "H", "St") and s.Hs[i][1] <= s.y("St", i))
            st9 = "estimated"
    ps["P9"] = _p(s, "P9", i9, "rule", st9, "proxy" if st9 == "estimated" else "")
    # P10: 手の速さが 0.3L/秒 未満の状態が 0.3秒続いた最初のコマ
    i10 = None
    start10 = i9 if i9 is not None else i8
    if start10 is not None:
        lim = config.VIDEO_P10_SPEED_L * L * config.VIDEO_P10_HOLD_S
        for i in range(start10, end + 1):
            if not s.ok(i, "H"):
                continue
            q = i
            ok = True
            while q + 1 <= end and s.t[q + 1] - s.t[i] <= config.VIDEO_P10_HOLD_S + 1e-9:
                q += 1
                if not s.ok(q, "H") or math.hypot(s.Hs[q][0] - s.Hs[i][0], s.Hs[q][1] - s.Hs[i][1]) > lim:
                    ok = False
                    break
            if ok and s.t[q] - s.t[i] >= config.VIDEO_P10_HOLD_S - 0.5 * s.dt():
                i10 = i
                break
    ps["P10"] = _p(s, "P10", i10, "rule", "ok")

    warnings: list[str] = []
    # 欠けの中で越えた P（直前のコマの点が無い）は、越えた時刻が分からないので「見つからない」にする。
    # トップ（P4）は前後2コマに欠けがあれば同じ（一番高いコマが欠けの中にあったかもしれない）
    for name in ("P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9"):
        p = ps[name]
        i = p["frame"]
        if i is None or (name == "P7" and p["method"] == "ball_roi"):
            continue
        if name == "P4":
            bad = any(not s.ok(q, "H") for q in range(max(0, i - 2), min(s.n, i + 3)))
        else:
            bad = i > 0 and not s.ok(i - 1, "H", "hip", "chest")
        if bad:
            ps[name] = _p(s, name, None, p["method"], "failed", "low_visibility")
            warnings.append("gap")
    # 順番の検査（P1＜P2＜…＜P7。反した P は見つからないにする）
    prev = None
    for name in PS_REQUIRED:
        p = ps[name]
        if p["t"] is None:
            continue
        if prev is not None and p["t"] <= ps[prev]["t"]:
            ps[name] = _p(s, name, None, p["method"], "failed", "order")
            warnings.append("order")
            continue
        prev = name
    # 時間の検査（上げ 0.6〜1.5秒・下ろし 0.2〜0.5秒。外れたら警告し、端の P を確かめてもらう）
    t0 = s.t[t0i]
    if ps["P4"]["t"] is not None:
        back = ps["P4"]["t"] - t0
        if not (config.VIDEO_BACK_RANGE[0] <= back <= config.VIDEO_BACK_RANGE[1]):
            warnings.append("back_time")
            ps["P4"].update(confidence="low", reason=ps["P4"]["reason"] or "back_time")
        if ps["P7"]["t"] is not None:
            down = ps["P7"]["t"] - ps["P4"]["t"]
            if not (config.VIDEO_DOWN_RANGE[0] <= down <= config.VIDEO_DOWN_RANGE[1]):
                warnings.append("down_time")
                ps["P7"].update(confidence="low", reason=ps["P7"]["reason"] or "down_time")
    # 中間（目安）: 両端の時刻の真ん中のコマ（両端が決まったときだけ）
    for name, x, y in (("P5_5", "P5", "P6"), ("P6_5", "P6", "P7")):
        if ps[x]["t"] is not None and ps[y]["t"] is not None:
            ps[name] = _p(s, name, _nearest(s, (ps[x]["t"] + ps[y]["t"]) / 2), "midpoint", "estimated", "proxy")
        else:
            ps[name] = _p(s, name, None, "midpoint", "failed")
    # 自信の低いコマ: 点が欠けてつないだ・点が見えない・まわりのコマが粗い
    coarse = config.VIDEO_COARSE_FRAMES * s.dt()
    for name in PS_ALL:
        p = ps[name]
        i = p["frame"]
        if i is None:
            continue
        if s.filled[i] or not s.ok(i, "H", "hip", "chest"):
            p.update(confidence="low", reason=p["reason"] if p["reason"] not in ("", "proxy") else "low_visibility")
        gap = max(s.t[i] - s.t[i - 1] if i > 0 else 0.0, s.t[i + 1] - s.t[i] if i + 1 < s.n else 0.0)
        if gap > coarse + 1e-9 and name in PS_REQUIRED:
            p.update(confidence="low", reason=p["reason"] if p["reason"] not in ("", "proxy") else "coarse")
        p["dt_ms"] = round(gap * 1000)
    for name in PS_ALL:
        p = ps[name]
        p["reason_text"] = REASONS.get(p["reason"], p["reason"]) if p["reason"] else ""
    last_i = next((ps[k]["frame"] for k in ("P10", "P9", "P8", "P7", "P6") if ps[k]["frame"] is not None), top0)
    # 細かく取る区間の頭は P1 の VIDEO_HEAD_S 秒前まで（構えが長くても、細かく取るコマが増えない）
    t_p1 = ps["P1"]["t"] if ps["P1"]["t"] is not None else s.t[b]
    head = max(s.t[a] - 0.3, t_p1 - config.VIDEO_HEAD_S)
    f4, f7 = ps["P4"]["frame"], ps["P7"]["frame"]
    return {
        "t0": round(t0, 4), "t0_frame": t0i, "ps": [ps[k] for k in PS_ALL], "warnings": warnings,
        "kind": "practice" if practice else "swing", "practice_known": practice is not None,
        "window": [round(max(0.0, head), 3), round(s.t[last_i] + config.VIDEO_TAIL_S, 3)],
        "tempo": tempo(t0, ps["P4"]["t"], ps["P7"]["t"], (s.gap_at(t0i), s.gap_at(f4) if f4 is not None else s.dt(), s.gap_at(f7) if f7 is not None else s.dt())),
    }


def _ball_in_circle(s: Series, ta: float, tb: float) -> bool:
    """構えの区間 [ta, tb] に、丸の中が最初にタップしたボールの絵と似ていた、という印（ball_seen）があるか。"""
    ds = [d for t, d in s.ball_seen if ta - 0.05 <= t <= tb + 0.05]
    return bool(ds) and min(ds) <= config.VIDEO_BALL_SAME + 1e-9


# ------------------------------------------------------------ テンポ（§5.3 G）


def tempo(t0: float | None, t4: float | None, t7: float | None, dt) -> dict | None:
    """上げ（t₀→P4）と下ろし（P4→P7）の時間の比と、時刻それぞれに ±1コマを足した比の幅。

    dt は1つの数か、t₀・P4・P7 それぞれの実際のコマの間隔（間引いて取ったコマは、間引いた間隔で幅を作る）。"""
    if t0 is None or t4 is None or t7 is None or not (t0 < t4 < t7):
        return None
    d0, d4, d7 = dt if isinstance(dt, (tuple, list)) else (dt, dt, dt)
    back, down = t4 - t0, t7 - t4
    rs = []
    for a in (-d0, 0.0, d0):
        for b in (-d4, 0.0, d4):
            for c in (-d7, 0.0, d7):
                bk, dn = (t4 + b) - (t0 + a), (t7 + c) - (t4 + b)
                if bk > 0 and dn > 0:
                    rs.append(bk / dn)
    return {"back_s": round(back, 4), "down_s": round(down, 4), "ratio": round(back / down, 3),
            "lo": round(min(rs), 3), "hi": round(max(rs), 3), "dt_s": round(max(d0, d4, d7), 5)}


def tempo_word(tp: dict | None, club_class: str) -> tuple[str | None, float | None, bool]:
    """(言葉, 目安, 近いと言えるか)。

    - 比の幅が、ガイドの目安 ± 人が決めた余白（CP_TEMPO_MARGIN）を丸ごと外れたときだけ言葉にする（fast / slow）。
      言葉は「上げに比べて下ろしが短め／長め」の中立の言い方（比からは、上げと下ろしのどちらのせいかは分からない）。
    - 「目安の近く」と言えるのは、幅が丸ごと目安 ± 余白の中に収まるときだけ。それ以外は言い切れない（近いとも外れたとも言わない）。"""
    g = config.CP_TEMPO_GUIDE.get(club_class)
    if not tp or g is None:
        return None, g, False
    m = config.CP_TEMPO_MARGIN
    if tp["lo"] > g + m:
        return "fast", g, False  # 上げに比べて下ろしが短め
    if tp["hi"] < g - m:
        return "slow", g, False  # 上げに比べて下ろしが長め
    return None, g, (g - m <= tp["lo"] and tp["hi"] <= g + m)


# ------------------------------------------------------------ 保存する時系列（swings.series_gz）


def series_for(body: dict, s: Series, sw: dict) -> dict:
    """スイングの区間だけの、手・腰・胸の中点の時系列（反転前の 0〜1 の割合。中点なので左右の名前に依らない）。"""
    w0, w1 = sw["window"]
    t, hand, hip, chest = [], [], [], []
    for i in range(s.n):
        if s.t[i] < w0 - 1e-9 or s.t[i] > w1 + 1e-9:
            continue

        def raw(p):
            if _isnan(p[0]):
                return None
            x = (s.w - p[0]) if s.hand == "L" else p[0]
            return [round(x / s.w, 5), round(p[1] / s.h, 5), round(p[2], 3)]

        t.append(round(s.t[i], 4))
        hand.append(raw(s.pts["H"][i]))
        hip.append(raw(s.pts["hip"][i]))
        chest.append(raw(s.pts["chest"][i]))
    return {"v": 1, "video_version": config.VIDEO_VERSION, "t0": sw["t0"], "fps": s.fps, "t": t, "hand": hand, "hip": hip, "chest": chest}


# ------------------------------------------------------------ 入口


def detect(body: dict) -> dict:
    """POST /v1/video/checkpoints の中身。"""
    s = Series(body)
    out: dict[str, Any] = {"video_version": config.VIDEO_VERSION, "n_frames": s.n, "torso_px": round(s.L, 2), "swings": [], "excluded": []}
    if s.n < 3 or s.L <= 1:
        out["reason"] = "体の点が足りないので、スイングを探せません"
        out["diag"] = diagnose(s, {})
        out["diag"]["reason"] = "no_pose"
        return out
    k = 0
    why: dict = {}
    for sw in find_swings(s, why):
        one = detect_one(s, sw)
        one["series"] = series_for(body, s, one)
        need = [p["p"] for p in one["ps"] if p["p"] in PS_REQUIRED and p["confidence"] == "low"]
        one["check"] = need
        if one["kind"] == "practice":
            one["index"] = None
            out["excluded"].append(one)
            continue
        k += 1
        one["index"] = k
        out["swings"].append(one)
    # 全部が素振りに見えたときは、素振りの判定をやめて返す（丸がずれた・ボールが小さく写った、のほうがありそう。
    # 全部外すと先へ進めず、同じ結果をくり返す＝行き止まり。§6.4）
    if not out["swings"] and out["excluded"] and body.get("practice_fallback", True) is not False:
        for one in out["excluded"]:
            k += 1
            one.update(index=k, kind="swing", practice_known=False)
            one["warnings"].append("practice_unsure")
            for p in one["ps"]:
                if p["p"] == "P7" and p["reason"] == "no_ball_change":
                    p.update(reason="proxy", reason_text=REASONS["proxy"])
            out["swings"].append(one)
        out["excluded"] = []
    if not out["swings"] and not out["excluded"]:
        out["diag"] = diagnose(s, why)
    return out
