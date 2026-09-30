"""理想との比較（docs/DESIGN_v2.md §7.2・§15 段3 のテスト）。LLM を使わない。

- 範囲の中の P では線を返さない（帯も返さない）。
- 動かすのは範囲の外の部位だけ・関節の長さが変わらない・動かしたあとの値を判定し直すと範囲の中。
- 見た目の項目には帯を返さない（見る場所の丸だけ）。
"""

from __future__ import annotations

import copy
import math
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from golf_analysis.app import app  # noqa: E402
from golf_analysis.checkpoints import by_id  # noqa: E402
from golf_analysis.checkpoints import ideal as ci  # noqa: E402

import synthetic_swing as syn  # noqa: E402

CASES = [
    ("dtl", "p2_inside", "iron.p2.dtl.head_vs_hands"),
    ("fo", "p3_bent", "iron.p3.fo.lead_arm"),
    ("dtl", "p5_shallow", "iron.p5.dtl.shaft"),
]
BODY_BONES = [b for b in ci.BONES if "grip" not in b]


def _apply_after(sw: dict, fx: dict) -> dict:
    sw2 = copy.deepcopy(sw)
    rf = ci.RawFrame(sw2["frames"][fx["p"]], sw2["width"], sw2["height"])
    for n, xy in fx["after"].items():
        rf.set(n, tuple(xy))
    return sw2


def _len(pts: dict, a: str, b: str) -> float:
    return math.hypot(pts[a][0] - pts[b][0], pts[a][1] - pts[b][1])


@pytest.mark.parametrize("hand", ["R", "L"])
@pytest.mark.parametrize("view,fault,item", CASES)
def test_範囲の外の部位だけを動かして判定し直すと範囲の中(view, fault, item, hand):
    sw = syn.swing(view, faults=(fault,), hand=hand)
    r = ci.ideal(sw, item)
    assert r["state"] == "out_range"
    assert r["zone"] is not None
    fx = r["fixed"]
    assert fx and fx["moved"]
    sw2 = _apply_after(sw, fx)
    assert ci._judge(sw2, by_id(item))["state"] == "in_range"
    # 動かしていない点は1画素も動かない
    w, h = sw["width"], sw["height"]
    before = ci.RawFrame(sw["frames"][fx["p"]], w, h).all()
    after = ci.RawFrame(sw2["frames"][fx["p"]], w, h).all()
    for n in before:
        if n not in fx["moved"]:
            assert after[n] == before[n], n
    # 体の関節の長さは変わらない
    for a, b in BODY_BONES:
        if a in before and b in before:
            assert _len(after, a, b) == pytest.approx(_len(before, a, b), abs=0.05), (a, b)
    # ほかのコマは変わらない
    for p, f in sw["frames"].items():
        if p != fx["p"]:
            assert sw2["frames"][p] == f


def test_クラブを回すときはクラブの長さも変わらない():
    sw = syn.swing("dtl", faults=("p5_shallow",))
    fx = ci.ideal(sw, "iron.p5.dtl.shaft")["fixed"]
    before = ci.RawFrame(sw["frames"]["P5"], sw["width"], sw["height"]).all()
    after = ci.RawFrame(_apply_after(sw, fx)["frames"]["P5"], sw["width"], sw["height"]).all()
    assert fx["moved"] == ["head"]
    assert _len(after, "grip", "head") == pytest.approx(_len(before, "grip", "head"), abs=0.05)


def test_最小の変化で範囲の中央へは寄せない():
    # 一段手前（動かす量を少し減らす）では、まだ範囲の中にならない
    sw = syn.swing("fo", faults=("p3_bent",))
    it = by_id("iron.p3.fo.lead_arm")
    fx = ci.ideal(sw, it["id"])["fixed"]
    w, h = sw["width"], sw["height"]
    b = ci.RawFrame(sw["frames"]["P3"], w, h).all()
    half = copy.deepcopy(sw)
    rf = ci.RawFrame(half["frames"]["P3"], w, h)
    for n, xy in fx["after"].items():
        rf.set(n, (b[n][0] + (xy[0] - b[n][0]) * 0.9, b[n][1] + (xy[1] - b[n][1]) * 0.9))
    assert ci._judge(half, it)["state"] != "in_range"


@pytest.mark.parametrize("view,fault,item", CASES)
def test_範囲の中のPでは線も範囲も返さない(view, fault, item):
    r = ci.ideal(syn.swing(view), item)
    assert r["state"] == "in_range"
    assert r["fixed"] is None and r["zone"] is None


def test_判断できない項目には何も描かない():
    sw = syn.swing("dtl", faults=("p5_shallow",), fps=30)  # コマの少ない動画: 下ろしのクラブは判断できない
    r = ci.ideal(sw, "iron.p5.dtl.shaft")
    assert r["state"] == "unknown"
    assert r["fixed"] is None and r["zone"] is None


def test_見た目の項目には範囲を返さない():
    sw = syn.swing("dtl")
    sw["vision"] = {"iron.p3.dtl.trail_knee": {"option": "曲がったまま", "visibility": "clear"}}
    r = ci.ideal(sw, "iron.p3.dtl.trail_knee")
    assert r["state"] == "out_range" and r["basis"] == "visual"
    assert r["zone"] is None and r["fixed"] is None
    assert r["look"] and r["look"]["r"] > 0
    # 答えがまだ無くても範囲は返さない
    r = ci.ideal(syn.swing("dtl"), "iron.p3.dtl.trail_knee")
    assert r["zone"] is None and r["fixed"] is None


def test_線を作れない測り方は範囲だけ():
    sw = syn.swing("dtl", faults=("p7_rise",))
    r = ci.ideal(sw, "iron.p7.dtl.hand_height")
    assert r["state"] == "out_range"
    assert r["zone"] and r["zone"]["kind"] == "band_y"
    assert r["fixed"] is None
    assert r["note"] == ci.NOTE_ZONE


def test_左打ちは元の画面の座標で返す():
    r = ci.ideal(syn.swing("dtl", faults=("p5_shallow",)), "iron.p5.dtl.shaft")
    l = ci.ideal(syn.swing("dtl", faults=("p5_shallow",), hand="L"), "iron.p5.dtl.shaft")
    assert l["zone"]["c"][0] == pytest.approx(syn.W - r["zone"]["c"][0], abs=0.01)
    assert l["zone"]["d0"][0] == pytest.approx(-r["zone"]["d0"][0], abs=1e-3)


def test_代わりの文と注記():
    r = ci.ideal(syn.swing("dtl", faults=("p2_inside",)), "iron.p2.dtl.head_vs_hands")
    assert r["alt"] and "{" not in r["alt"]
    assert "まだ確かめていません" in r["note"]


def test_知らない項目():
    r = ci.ideal(syn.swing("dtl"), "no.such")
    assert r["reason"] == "unknown_item" and r["zone"] is None


def test_API():
    c = TestClient(app)
    r = c.post("/v1/checkpoints/ideal", json={"swing": syn.swing("dtl", faults=("p2_inside",)), "item_id": "iron.p2.dtl.head_vs_hands"})
    assert r.status_code == 200, r.text
    assert r.json()["fixed"]["moved"] == ["head"]
    assert c.post("/v1/checkpoints/ideal", json={"swing": {"view": "x"}, "item_id": "a"}).status_code == 400
