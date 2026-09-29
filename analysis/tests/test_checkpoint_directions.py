"""判定の向き・確かめられない条件・まとめ方の再現テスト（段2a のレビューで見つかったもの）。

合成データを範囲の真ん中にだけ置いていると、外れの向きの誤り（寝すぎ／立ちすぎ・内側／外側）を捕まえられない。
ここでは点を範囲の端の外へ動かし、出てくる外れの語の向きを確かめる。LLM を使わない。
"""

from __future__ import annotations

import copy
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from golf_analysis import checkpoints as cp  # noqa: E402
from golf_analysis.checkpoints import judge as cj  # noqa: E402
from golf_analysis.checkpoints import measure as cm  # noqa: E402

import synthetic_swing as syn  # noqa: E402


def states(r: dict) -> dict:
    return {x["id"]: x for x in r["items"]}


def agg(swings: list[dict], **kw) -> dict:
    out = []
    for i, sw in enumerate(swings):
        r = cm.measure_swing(sw)
        r["swing_id"] = i + 1
        out.append(r)
    return cj.aggregate(out, **kw)


def _set_tap(sw, p, key, xy):
    sw["frames"][p]["taps"][key] = [round(xy[0], 2), round(xy[1], 2)]


def _club_at(sw, p, ang, toward, length=160):
    g = sw["frames"][p]["taps"]["grip"]
    a = math.radians(ang)
    _set_tap(sw, p, "head", (g[0] + toward * length * math.sin(a), g[1] - length * math.cos(a)))


# ------------------------------------------------------------ 1 シャフトの向き

def test_垂直を越えてボールの側へ倒れたシャフトは立ちすぎ():
    s = states(cm.measure_swing(syn.swing("dtl", faults=("p5_cross",))))
    x = s["iron.p5.dtl.shaft"]
    assert x["state"] == "out_range" and x["fault"] == "steep", x
    assert x["value"]["v"] < 0          # 向きつきの値（体の側が正）
    # P3 も同じ
    sw = syn.swing("dtl")
    _club_at(sw, "P3", 35.0, +1)
    assert states(cm.measure_swing(sw))["iron.p3.dtl.shaft"]["fault"] == "steep"


def test_寝すぎは寝すぎと出て立ちすぎの札を添えない():
    a = agg([syn.swing("dtl", faults=("p5_shallow",)) for _ in range(3)])
    row = next(i for i in a["items"] if i["id"] == "iron.p5.dtl.shaft")
    assert row["state"] == "out_range" and row["fault"] == "shallow"
    assert not row.get("also"), "寝すぎの行に「下ろしでクラブが立つ」を添えない"
    b = agg([syn.swing("dtl", faults=("p5_cross",)) for _ in range(3)])
    row = next(i for i in b["items"] if i["id"] == "iron.p5.dtl.shaft")
    assert row["fault"] == "steep" and any(x["id"] == "err.steep.p5" for x in row["also"])


def test_向きが分からない側へ倒れたら判断できない():
    sw = syn.swing("dtl", ps=syn.P_ORDER)
    _club_at(sw, "P9", 20.0, +1)      # フォローで垂直を越えてボールの側（ガイドに向きの記述が無い）
    x = states(cm.measure_swing(sw))["iron.p9.dtl.shaft"]
    assert x["state"] == "unknown" and x["reason"] == "cross"


def test_正面の手首の折れの向き():
    # 下ろしの腕が水平のとき、クラブが体の外（{trail}側）へ倒れるほど折れがほどけている
    sw = syn.swing("fo")
    _club_at(sw, "P5", 60.0, -1)
    assert states(cm.measure_swing(sw))["iron.p5.fo.cock"]["fault"] == "early"
    sw = syn.swing("fo")
    _club_at(sw, "P5", 10.0, +1)      # 垂直を越えて目標の側 = 折れすぎ
    assert states(cm.measure_swing(sw))["iron.p5.fo.cock"]["fault"] == "over"
    # 上げの腕が水平: 垂直から大きく離れる = 折れが浅い
    sw = syn.swing("fo")
    _club_at(sw, "P3", 60.0, -1, length=100)
    assert states(cm.measure_swing(sw))["iron.p3.fo.cock"]["fault"] == "under"


# ------------------------------------------------------------ 2 腕とクラブ（後ろからの P6）

def test_短く写ったクラブの線から傾きを測らない():
    s = states(cm.measure_swing(syn.swing("dtl")))
    x = s["path.p6_arm_plane"]
    assert x["state"] == "unknown" and x["reason"] == "club_short"


def test_腕とクラブは先が内側のときだけ範囲の外():
    sw = syn.swing("dtl")
    h = (702, 371)
    # 先がはっきり外側（手からボール約五個）で、腕とクラブは大きく離れている → ガイドに無い組み合わせ
    _set_tap(sw, "P6", "grip", (h[0] + 6, h[1] + 1))
    _set_tap(sw, "P6", "head", (h[0] + 80, h[1] - 10))
    s = states(cm.measure_swing(sw))
    assert s["iron.p6.dtl.head_vs_hands"]["fault"] == "outside"
    assert s["path.p6_arm_plane"]["state"] == "unknown" and s["path.p6_arm_plane"]["reason"] == "combo"
    # 先が内側 → 範囲の外（内側）
    _set_tap(sw, "P6", "head", (h[0] - 80, h[1] - 10))
    s = states(cm.measure_swing(sw))
    assert s["path.p6_arm_plane"]["state"] == "out_range" and s["path.p6_arm_plane"]["fault"] == "inside"


def test_保つ幅の無い項目は参考():
    s = states(cm.measure_swing(syn.swing("dtl")))
    assert s["path.p4_p5_shaft_kept"]["state"] == "reference"


# ------------------------------------------------------------ 3 向きの食い違い

def test_向きが食い違うと向きのある項目は全部判断できない():
    wrong = syn.swing("fo")
    wrong["view"] = "dtl"
    r = cm.measure_swing(wrong)
    assert r["view_check"]["ok"] is False
    s = states(r)
    judged = [k for k, x in s.items() if x["state"] in ("in_range", "out_range") and cp.by_id(k).get("view") in ("dtl", "fo")]
    assert judged == []
    assert s["iron.p3.dtl.shaft"]["reason"] == "view_mismatch"


# ------------------------------------------------------------ 12 確かめられなかった条件

def test_コマの速さが分からないと下ろしのクラブは判断しない():
    sw = syn.swing("dtl")
    sw.pop("fps")
    s = states(cm.measure_swing(sw))
    assert s["iron.p5.dtl.shaft"]["reason"] == "fps_unknown"
    assert s["iron.p3.dtl.shaft"]["state"] == "in_range"


def test_カメラの置き方を確かめられないと角度は判断しない():
    sw = syn.swing("dtl")
    sw["missing"] = ["P1"]
    r = cm.measure_swing(sw)
    assert r["camera"]["ok"] is None
    s = states(r)
    assert s["iron.p3.dtl.shaft"]["reason"] == "camera_unknown"
    assert s["iron.p2.dtl.head_vs_hands"]["state"] in ("in_range", "out_range")   # 角度でない項目は測る


# ------------------------------------------------------------ 13 起き上がりは P5・P6

def test_起き上がりはP6も見る():
    sw = syn.swing("fo")
    # P6 で胸だけ大きく傾ける（骨盤と胸の線の差 約二十）
    lm = sw["frames"]["P6"]["landmarks"]
    lm[11]["y"] -= 0.06   # 体の左の肩（{lead}）を上げる
    s = states(cm.measure_swing(sw))
    x = s["err.early_ext.fo"]
    assert x["state"] == "out_range" and x["fault"] == "ext"


# ------------------------------------------------------------ 10 まとめる項目

def test_まとめる項目は課題にも件数にも入れず元の行に添える():
    a = agg([syn.swing("fo", faults=("p3_bent",)) for _ in range(3)])
    w = next(i for i in a["items"] if i["id"] == "pow.width")
    assert w["state"] == "out_range" and w["derived"] and not w["candidate"]
    src = next(i for i in a["items"] if i["id"] == "iron.p3.fo.lead_arm")
    assert src["state"] == "out_range" and any(x["id"] == "pow.width" for x in src["also"])
    assert a["next"] != "pow.width" and a["focus"] != "pow.width"
    main = [x for x in a["items"] if not x.get("optional") and not x.get("derived")]
    assert a["counts"]["out_range"] == sum(1 for x in main if x["state"] == "out_range")


def test_腕が突っ張りすぎは輪が小さいにしない():
    sw = syn.swing("fo")
    e, w = syn._arm((690, 215), 179.0, 110, 110, 268, bend_sign=1)
    lm = sw["frames"]["P3"]["landmarks"]
    lm[13].update(x=e[0] / syn.W, y=e[1] / syn.H)
    lm[15].update(x=w[0] / syn.W, y=w[1] / syn.H)
    s = states(cm.measure_swing(sw))
    assert s["iron.p3.fo.lead_arm"]["fault"] == "over"
    assert s["pow.width"]["state"] != "out_range"


# ------------------------------------------------------------ 6・8 代わりの点と向き

def test_ドライバーの正面P6の握りの端の向き():
    sw = syn.swing("fo", club="Driver")
    knee_x = 590.0   # {trail}膝
    d = syn.BALL_D
    for x, want in ((knee_x + 3 * d, "lead"), (knee_x - 5 * d, "trail")):
        s2 = copy.deepcopy(sw)
        _set_tap(s2, "P6", "grip", (x, 370))
        got = states(cm.measure_swing(s2))["driver.p6.fo.butt_pos"]
        assert got["state"] == "out_range" and got["fault"] == want, (x, got)
    # 脚の内側と外側のあいだ（膝の点から少し外）は範囲の中
    s2 = copy.deepcopy(sw)
    _set_tap(s2, "P6", "grip", (knee_x - 0.5 * d, 370))
    assert states(cm.measure_swing(s2))["driver.p6.fo.butt_pos"]["state"] == "in_range"


def test_ガイドどおりのボール位置は範囲の中():
    # ドライバーは{lead}足の内側（かかとの点から足の半分の幅だけ{trail}側）
    sw = syn.swing("fo", club="Driver")
    heel_x = 704.0
    c = heel_x - syn.BALL_D
    sw["ball"] = [[c - syn.BALL_D / 2, 640], [c + syn.BALL_D / 2, 640]]
    assert states(cm.measure_swing(sw))["setup.ball_pos.driver"]["state"] == "in_range"


# ------------------------------------------------------------ 18 位置の項目の向きをまとめて確かめる

PLUS = {"fo": {"lead", "forward", "in", "early"}, "dtl": {"out", "front", "outside", "forward"}}
MINUS = {"fo": {"trail", "back", "out"}, "dtl": {"in", "back", "inside"}}
SIDE = {"lead": "left", "trail": "right"}


def _shift(sw: dict, p: str, name: str, dx: float) -> None:
    f = sw["frames"][p]
    if name in f["taps"]:
        f["taps"][name][0] += dx
        return
    if name == "ball":
        for q in sw["ball"]:
            q[0] += dx
        return
    names = {"H": ["lead_wrist", "trail_wrist"], "sh_mid": ["lead_shoulder", "trail_shoulder"], "hip_mid": ["lead_hip", "trail_hip"]}.get(name, [name])
    for n in names:
        if "_" in n:
            side, part = n.split("_", 1)
            idx = syn.IDX[f"{SIDE.get(side, side)}_{part}"]
        else:
            idx = syn.IDX[n]
        f["landmarks"][idx]["x"] += dx / syn.W


def _moved_points(spec: dict) -> list[tuple[str, str]]:
    if spec["kind"] == "all":
        return [q for part in spec["parts"] for q in _moved_points(part)]
    if spec["kind"] in ("band_x", "offset_x", "shift_x"):
        return [(spec["p"], spec["pt"])]
    return []


CASES = [("dtl", "7 Iron"), ("fo", "7 Iron"), ("dtl", "Driver"), ("fo", "Driver"), ("fo", "5 Wood"), ("fo", "4 Iron"), ("fo", "9 Iron"), ("fo", "SW")]


@pytest.mark.parametrize("view,club", CASES)
def test_位置の項目は動かした向きの外れの語が出る(view, club):
    base = syn.swing(view, club=club, ps=syn.P_ORDER)
    s0 = states(cm.measure_swing(base))
    checked = 0
    for iid, x in s0.items():
        it = cp.by_id(iid)
        if it.get("judge") != "binary" or x["state"] not in ("in_range", "out_range"):
            continue
        pts = _moved_points((it.get("measure") or {}).get("spec") or {})
        if not pts:
            continue
        ids = {f["id"] for f in it.get("faults") or []}
        for sign in (+1, -1):
            # 範囲の幅より大きく動かす。体の点を大きく動かすと構えの形（向き）まで変わるので、小さい方から試す
            for k in (3, 6, 10):
                sw = copy.deepcopy(base)
                for p, name in pts:
                    _shift(sw, p, name, sign * k * syn.BALL_D)
                got = states(cm.measure_swing(sw))[iid]
                if got["state"] == "out_range":
                    break
            want = (PLUS if sign > 0 else MINUS)[view] & ids
            if got["state"] == "out_range":
                assert got["fault"] in want, (iid, sign, got["fault"], want)
            elif got["state"] == "in_range":
                # 片側だけの基準は、反対の側へ動かしても範囲の外にしない
                assert not want or it["range"] is None or it["range"].get("side") != "both", (iid, sign, got["state"])
        checked += 1
    assert checked >= 1, checked


# ------------------------------------------------------------ 17・14 そのほか

def test_ドミノの段は形を見るPの番号():
    assert cp.by_id("pow.head_fb")["domino_rank"] == 4 and cp.by_id("pow.head_fb")["clubs"] == ["driver"]
    assert cp.by_id("tip.driver.side_bend_switch")["domino_rank"] == 3


def test_範囲の幅が誤差と同じくらいの項目には印を付ける():
    a = agg([syn.swing("fo") for _ in range(3)])
    arm = next(i for i in a["items"] if i["id"] == "iron.p3.fo.lead_arm")
    assert arm["tight"] is True
    bp = next(i for i in a["items"] if i["id"] == "err.early_ext.fo")
    assert bp["tight"] is False
