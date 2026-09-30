"""理想との比較（docs/DESIGN_v2.md §7.2・§11.7。段3）。POST /v1/checkpoints/ideal の中身。

- **全身の理想の骨格は作らない。** 返すのは、課題の項目1つぶんの
  ① 範囲（帯＝位置・扇＝角度・円＝離れ）と、② 範囲の外の部位だけを動かした線（関節の長さを保つ最小の変化）だけ。
- 線は「動かしたあとの点を measure.py でもう一度判定すると範囲の中」になるところまでしか動かさない
  （範囲の中央へは寄せない。一番近い端の、誤差ぶん内側）。動かすのは範囲の外の部位だけで、ほかの点は1画素も動かさない。
- 範囲の中・境目・判断できない・参考・見た目の項目には線を返さない（見た目の項目は範囲も返さず、見る場所の丸だけ）。
- 座標は全部、元の画面の画素（左打ちも反転を戻して返す）。数字は画面の最初の面に出さない（描くのは形だけ）。
- LLM を使わない。保存しない。
"""

from __future__ import annotations

import copy
import math
from typing import Any

from . import by_id, fill
from .measure import LM, Ctx, Invalid, camera_check, judge_item, scale_check, view_check, _spec_ps

NOTE = "あなたの形のうち、色の部分だけをガイドの範囲に入れた目安です。この形で良い球になるかは、まだ確かめていません"
NOTE_ZONE = "色の面は、ガイドの範囲をあなたのコマの上に描いたものです。数字は「なぜそう言える？」の中にあります"

# 骨（親 → 子）。動かすときは子と、その先の点をまとめて回す（関節の長さを変えない）
CHILDREN = {"shoulder": ["elbow"], "elbow": ["wrist"], "hip": ["knee"], "knee": ["ankle"], "ankle": ["heel", "toe"]}
BONES = [("left_shoulder", "right_shoulder"), ("left_hip", "right_hip"), ("left_shoulder", "left_hip"), ("right_shoulder", "right_hip"),
         ("left_shoulder", "left_elbow"), ("left_elbow", "left_wrist"), ("right_shoulder", "right_elbow"), ("right_elbow", "right_wrist"),
         ("left_hip", "left_knee"), ("left_knee", "left_ankle"), ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
         ("left_ankle", "left_heel"), ("left_ankle", "left_toe"), ("right_ankle", "right_heel"), ("right_ankle", "right_toe"),
         ("grip", "head")]
ANGLE_KINDS = ("shaft_v", "ang_v", "ang_h", "ang_v_signed")
# 見た目の項目の「見る場所」（カタログの measure.points → 丸の中心にする点）
LOOK_AT = (("club", "H"), ("wrist", "H"), ("hand", "H"), ("grip", "H"), ("head", "nose"), ("hip", "hip_mid"), ("knee", "knees"),
           ("shoulder", "sh_mid"), ("arm", "H"), ("foot", "stance_mid"), ("feet", "stance_mid"))


def _side_names(hand: str) -> dict[str, str]:
    lead, trail = ("right", "left") if hand == "L" else ("left", "right")
    return {"lead": lead, "trail": trail, "left": "left", "right": "right"}


def _raw_name(name: str, hand: str) -> str:
    """{lead}_wrist → left_wrist（右打ち）。タップの名前はそのまま。"""
    if "_" in name:
        s, part = name.split("_", 1)
        sn = _side_names(hand)
        if s in sn:
            return f"{sn[s]}_{part}"
    return name


def _descendants(raw: str) -> list[str]:
    if "_" not in raw:
        return []
    side, part = raw.split("_", 1)
    out = []
    for c in CHILDREN.get(part, []):
        n = f"{side}_{c}"
        out += [n] + _descendants(n)
    return out


def _is_bone(parent: str, child: str) -> bool:
    if "_" not in parent or "_" not in child:
        return False
    ps, pp = parent.split("_", 1)
    cs, cp = child.split("_", 1)
    return ps == cs and cp in CHILDREN.get(pp, [])


# ------------------------------------------------------------ 元の画面の画素の点を読み書きする

class RawFrame:
    """1コマの点を、元の画面の画素で読み書きする（反転しない）。"""

    def __init__(self, frame: dict, w: float, h: float):
        self.f, self.w, self.h = frame, w, h

    def get(self, name: str) -> tuple[float, float] | None:
        if name in LM:
            lms = self.f.get("landmarks") or []
            if len(lms) < 33 or not lms[LM[name]]:
                return None
            p = lms[LM[name]]
            return (float(p.get("x", 0)) * self.w, float(p.get("y", 0)) * self.h)
        t = (self.f.get("taps") or {}).get(name)
        if isinstance(t, (list, tuple)) and len(t) == 2:
            return (float(t[0]), float(t[1]))
        return None

    def set(self, name: str, xy: tuple[float, float]) -> None:
        if name in LM:
            p = self.f["landmarks"][LM[name]]
            p["x"], p["y"] = xy[0] / self.w, xy[1] / self.h
        else:
            self.f.setdefault("taps", {})[name] = [xy[0], xy[1]]

    def all(self) -> dict[str, list[float]]:
        out = {}
        for n in list(LM) + ["grip", "head"]:
            q = self.get(n)
            if q is not None:
                out[n] = [round(q[0], 2), round(q[1], 2)]
        return out


def _rot(p, c, ang):
    s, co = math.sin(ang), math.cos(ang)
    dx, dy = p[0] - c[0], p[1] - c[1]
    return (c[0] + dx * co - dy * s, c[1] + dx * s + dy * co)


# ------------------------------------------------------------ 動かし方（範囲の外の部位だけ）

def _move_plan(spec: dict, hand: str) -> dict | None:
    """測り方 → どの点をどう動かすか。関節の長さを保てない動かし方しか無ければ None（線は描かない）。"""
    k = spec.get("kind")
    sn = _side_names(hand)

    def arm(side_raw: str) -> list[tuple[str, list[str]]]:
        return [(f"{side_raw}_shoulder", [f"{side_raw}_elbow", f"{side_raw}_wrist"])]

    def seg(a: str, b: str) -> dict | None:
        ra, rb = _raw_name(a, hand), _raw_name(b, hand)
        if {a, b} <= {"grip", "head"}:
            return {"type": "rot", "groups": [("grip", ["head"])]}
        if _is_bone(ra, rb):
            return {"type": "rot", "groups": [(ra, [rb] + _descendants(rb))]}
        return None

    if k == "dist" and spec.get("pt") in ("head", "grip"):
        # クラブの先の位置: 画面の上でのクラブの見え方（奥行きで縮む）なので、先を手元へ向けて寄せる（体の関節は動かさない）
        return {"type": "toward", "name": spec["pt"], "to": spec["to"]}
    if k in ("band_x", "band_y", "offset_x", "dist", "dy", "shift_y_up", "shift_x"):
        pt = spec.get("pt")
        if pt == "H":
            return {"type": "rot", "groups": arm(sn["lead"]) + arm(sn["trail"]), "carry": ["grip", "head"]}
        if pt in ("head",):
            return {"type": "rot", "groups": [("grip", ["head"])]}
        if pt == "grip":
            return {"type": "shift", "names": ["grip", "head"]}
        raw = _raw_name(pt or "", hand)
        if raw.endswith(("_wrist", "_elbow")):
            return {"type": "rot", "groups": arm(raw.split("_", 1)[0])}
        return None
    if k in ANGLE_KINDS:
        return seg(spec["a"], spec["b"])
    if k == "joint":
        ra, rb, rc = (_raw_name(spec[x], hand) for x in ("a", "b", "c"))
        if _is_bone(rb, rc):
            return {"type": "rot", "groups": [(rb, [rc] + _descendants(rc))]}
        if _is_bone(rb, ra):
            return {"type": "rot", "groups": [(rb, [ra] + _descendants(ra))]}
        return None
    return None


def _apply(swing: dict, p: str, plan: dict, amount: float) -> tuple[dict, list[str]]:
    sw = copy.deepcopy(swing)
    rf = RawFrame(sw["frames"][p], float(sw.get("width") or 1), float(sw.get("height") or 1))
    moved: list[str] = []
    if plan["type"] == "shift":
        for n in plan["names"]:
            q = rf.get(n)
            if q is not None:
                rf.set(n, (q[0] + amount, q[1]))
                moved.append(n)
        return sw, moved
    if plan["type"] == "toward":
        q = rf.get(plan["name"])
        t = _raw_point(rf, plan["to"], swing)
        if q is not None and t is not None:
            rf.set(plan["name"], (q[0] + (t[0] - q[0]) * amount, q[1] + (t[1] - q[1]) * amount))
            moved.append(plan["name"])
        return sw, moved
    before_h = _hands(rf)
    for pivot, names in plan["groups"]:
        c = rf.get(pivot)
        if c is None:
            continue
        for n in names:
            q = rf.get(n)
            if q is not None:
                rf.set(n, _rot(q, c, amount))
                moved.append(n)
    # 手を動かしたら、握っているクラブも同じだけ平行に動かす（クラブの長さは変わらない）
    if plan.get("carry") and before_h:
        after_h = _hands(rf)
        dx, dy = after_h[0] - before_h[0], after_h[1] - before_h[1]
        for n in plan["carry"]:
            q = rf.get(n)
            if q is not None:
                rf.set(n, (q[0] + dx, q[1] + dy))
                moved.append(n)
    return sw, moved


def _raw_point(rf: RawFrame, name: str, swing: dict):
    if name == "H":
        return _hands(rf)
    return rf.get(_raw_name(name, "L" if swing.get("handedness") == "L" else "R"))


def _hands(rf: RawFrame):
    a, b = rf.get("left_wrist"), rf.get("right_wrist")
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) if a and b else None


def _judge(swing: dict, it: dict) -> dict:
    ctx = Ctx(swing)
    vis = (swing.get("vision") or {}).get(it["id"])
    return judge_item(ctx, it, camera_check(ctx), scale_check(ctx), vis, view_bad=view_check(ctx).get("ok") is False)


def _steps(plan: dict, w: float) -> list[float]:
    if plan["type"] == "shift":
        n = int(w // 2)
        vals = [float(i) for i in range(1, n)]
    elif plan["type"] == "toward":
        vals = [i / 200 for i in range(1, 200)]
    else:
        vals = [math.radians(i * 0.25) for i in range(1, 361)]  # 0.25° ずつ 90° まで
    out = []
    for v in vals:
        out += [v, -v]
    return out


def fix(swing: dict, it: dict, p: str) -> dict | None:
    """範囲の外の部位だけを最小に動かして、判定し直すと範囲の中になる形。作れなければ None。"""
    spec = (it.get("measure") or {}).get("spec") or {}
    hand = "L" if swing.get("handedness") == "L" else "R"
    plan = _move_plan(spec, hand)
    if not plan or p not in (swing.get("frames") or {}):
        return None
    w = float(swing.get("width") or 1)
    for amount in _steps(plan, w):
        sw2, moved = _apply(swing, p, plan, amount)
        if not moved:
            return None
        if _judge(sw2, it)["state"] == "in_range":
            h = float(swing.get("height") or 1)
            before = RawFrame(swing["frames"][p], w, h).all()
            after = RawFrame(sw2["frames"][p], w, h).all()
            moved = sorted(set(moved))
            return {"p": p, "moved": moved, "before": {n: before[n] for n in moved}, "after": {n: after[n] for n in moved},
                    "segments": [[a, b] for a, b in BONES if a in moved or b in moved]}
    return None


# ------------------------------------------------------------ 範囲の形（元の画面の画素）

def _deg_dir(theta_deg: float, sx: float, sy: float) -> tuple[float, float]:
    """垂直から theta だけ倒した向き（sx: 横の向き・sy: 縦の向き）。"""
    t = math.radians(theta_deg)
    return (sx * math.sin(t), sy * math.cos(t))


def zone(ctx: Ctx, spec: dict, rng: dict) -> dict | None:
    """課題の項目の範囲を、本人のコマの上の形にする。描けない測り方は None（丸も描かない）。"""
    k = spec.get("kind")
    p = spec.get("p")
    lo, hi, side = rng.get("lo"), rng.get("hi"), rng.get("side", "both")
    if side == "lo_only":
        hi = None
    if side == "hi_only":
        lo = None
    L = ctx.hand == "L"
    W = ctx.w

    def rx(x: float) -> float:
        return W - x if L else x

    def rpt(q) -> list[float]:
        return [round(rx(q[0]), 2), round(q[1], 2)]

    try:
        if k == "band_x":
            A, B = ctx.ref(p, spec["a"]), ctx.ref(p, spec["b"])
            xs = [A[0] + t * (B[0] - A[0]) if t is not None else None for t in (lo, hi)]
            x0 = xs[0] if xs[0] is not None else (0.0 if B[0] > A[0] else W)
            x1 = xs[1] if xs[1] is not None else (W if B[0] > A[0] else 0.0)
            a, b = sorted((rx(x0), rx(x1)))
            return {"kind": "band_x", "x0": round(a, 2), "x1": round(b, 2)}
        if k == "offset_x":
            R, d = ctx.ref(p, spec["ref"]), ctx.ball_d()
            x0 = R[0] + lo * d if lo is not None else 0.0
            x1 = R[0] + hi * d if hi is not None else W
            a, b = sorted((rx(x0), rx(x1)))
            return {"kind": "band_x", "x0": round(a, 2), "x1": round(b, 2)}
        if k == "dy":
            R = ctx.pt(p, spec["ref"])
            d = ctx.ball_d() if spec.get("unit", "ball") == "ball" else None
            if d is None:
                return None
            y0 = R[1] - hi * d if hi is not None else 0.0
            y1 = R[1] - lo * d if lo is not None else ctx.h
            a, b = sorted((y0, y1))
            return {"kind": "band_y", "y0": round(a, 2), "y1": round(b, 2)}
        if k == "shift_y_up":
            A, d = ctx.pt(spec["p0"], spec["pt"]), ctx.ball_d()
            y0 = A[1] - hi * d if hi is not None else 0.0
            y1 = A[1] - lo * d if lo is not None else ctx.h
            a, b = sorted((y0, y1))
            return {"kind": "band_y", "y0": round(a, 2), "y1": round(b, 2)}
        if k == "dist" and spec.get("unit", "ball") == "ball" and spec.get("axis") != "x":
            Q, d = ctx.pt(p, spec["to"]), ctx.ball_d()
            return {"kind": "circle", "c": rpt(Q), "r_lo": round(lo * d, 2) if lo is not None else 0, "r_hi": round(hi * d, 2) if hi is not None else None}
        if k in ANGLE_KINDS:
            A, B = ctx.pt(p, spec["a"]), ctx.pt(p, spec["b"])
            dx, dy = B[0] - A[0], B[1] - A[1]
            sy = 1.0 if dy >= 0 else -1.0
            if k == "shaft_v":
                sx = float(spec.get("side", 1))
                thetas = [lo if lo is not None else -90.0, hi if hi is not None else 90.0]
            elif k == "ang_v":
                sx = 1.0 if dx >= 0 else -1.0
                thetas = [lo if lo is not None else 0.0, hi if hi is not None else 90.0]
            elif k == "ang_h":
                sx = 1.0 if dx >= 0 else -1.0
                thetas = [90 - (hi if hi is not None else 90.0), 90 - (lo if lo is not None else 0.0)]
            else:  # ang_v_signed: 真上からの傾き（+x へ倒れると正）
                sign = float(spec.get("sign", 1))
                sy, sx = -1.0, 1.0
                vals = [(lo if lo is not None else -90.0) / sign, (hi if hi is not None else 90.0) / sign]
                thetas = sorted(vals)
            r = math.hypot(dx, dy) * 1.1
            dirs = [_deg_dir(t, sx, sy) for t in thetas]
            return _fan(rpt(A), r, dirs, L)
        if k == "joint":
            A, B, C = ctx.pt(p, spec["a"]), ctx.pt(p, spec["b"]), ctx.pt(p, spec["c"])
            base = math.atan2(A[1] - B[1], A[0] - B[0])
            cur = math.atan2(C[1] - B[1], C[0] - B[0])
            turn = 1.0 if math.sin(cur - base) >= 0 else -1.0
            r = math.hypot(C[0] - B[0], C[1] - B[1]) * 1.1
            lo_, hi_ = (lo if lo is not None else 0.0), (hi if hi is not None else 180.0)
            dirs = [(math.cos(base + turn * math.radians(t)), math.sin(base + turn * math.radians(t))) for t in (lo_, hi_)]
            return _fan(rpt(B), r, dirs, L)
    except (Invalid, KeyError, TypeError, ZeroDivisionError):
        return None
    return None


def _fan(c: list[float], r: float, dirs: list[tuple[float, float]], mirror: bool) -> dict:
    ds = [[round(-d[0] if mirror else d[0], 4), round(d[1], 4)] for d in dirs]
    return {"kind": "fan", "c": c, "r": round(r, 2), "d0": ds[0], "d1": ds[1]}


def _look(ctx: Ctx, it: dict, p: str) -> dict | None:
    pts = " ".join((it.get("measure") or {}).get("points") or []) + " " + (it.get("look_at") or "")
    f = ctx.frames.get(p)
    if f is None or not f.has_pose:
        return None
    for key, name in LOOK_AT:
        if key in pts:
            try:
                if name == "knees":
                    a, b = f.point("lead_knee"), f.point("trail_knee")
                    q = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                else:
                    q = f.point(name)
            except (Invalid, KeyError):
                return None
            x = ctx.w - q[0] if ctx.hand == "L" else q[0]
            return {"c": [round(x, 2), round(q[1], 2)], "r": round(max(f.torso() * 0.35, 12.0), 2)}
    return None


# ------------------------------------------------------------ 入り口

def ideal(swing: dict, item_id: str) -> dict:
    """1スイング × 課題の項目1つ → 範囲の形・動かした線・注記・代わりの文。"""
    it = by_id(item_id)
    if not it:
        return {"item_id": item_id, "state": "unknown", "reason": "unknown_item", "zone": None, "fixed": None, "look": None}
    hand = "L" if swing.get("handedness") == "L" else "R"
    spec = (it.get("measure") or {}).get("spec") or {}
    ps = [x for x in _spec_ps(spec) if x] or ([it["p"]] if it.get("p") else [])
    p = spec.get("p") or (ps[-1] if ps else None)
    ctx = Ctx(swing)
    res = _judge(swing, it)
    fault = res.get("fault")
    fl = next((f["label"] for f in it.get("faults") or [] if f["id"] == fault), "") if fault else ""
    out: dict[str, Any] = {
        "item_id": item_id, "p": p, "state": res["state"], "fault": fault, "basis": res.get("basis"), "reason": res.get("reason", ""),
        "zone": None, "fixed": None, "look": None, "note": NOTE, "legend": {"now": "いまのあなた", "fixed": "範囲に入れた目安", "zone": "ガイドの範囲"},
        "alt": fill(f"{fl or it.get('look_at', '')}。目安: {it.get('ok_text', '')}", hand),
        "skeleton": RawFrame((swing.get("frames") or {}).get(p) or {}, ctx.w, ctx.h).all() if p else {},
        "bones": [list(b) for b in BONES],
    }
    visual = (it.get("measure") or {}).get("how") == "vision" or it.get("judgeable") == "visual" or res.get("basis") == "visual"
    if visual:
        # 見た目の項目: 範囲も線も描かない。見る場所を丸で囲むだけ
        out["reason"] = out["reason"] or "visual"
        out["look"] = _look(ctx, it, p) if p else None
        out["note"] = "見た目の項目なので、範囲は描いていません。丸の中を見てください"
        return out
    if res["state"] != "out_range" or res.get("basis") not in ("measured", "measured_approx", "measured_tap"):
        # 範囲の中・境目・判断できない・参考: 何も描かない
        out["note"] = ""
        return out
    out["zone"] = zone(ctx, spec, it.get("range") or {})
    out["fixed"] = fix(swing, it, p) if p else None
    if not out["fixed"]:
        out["note"] = NOTE_ZONE if out["zone"] else ""
    return out
