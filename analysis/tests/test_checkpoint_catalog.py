"""チェックポイントのカタログの形（docs/DESIGN_v2.md §16）。"""

from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from golf_analysis import checkpoints as cp  # noqa: E402
from golf_analysis import config, gist  # noqa: E402
from golf_analysis.checkpoints import judge as cj  # noqa: E402
from golf_analysis.checkpoints import measure as cm  # noqa: E402

import synthetic_swing as syn  # noqa: E402

ITEMS = cp.items()
BY = {it["id"]: it for it in ITEMS}
NOTES = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "PGA_GUIDE_NOTES.md")

# §5.7 の要確認の一覧（ここに挙げた項目はどれも unclear。一覧とカタログの食い違いを止める）
UNCLEAR_REQUIRED = [
    "driver.p1.fo.side_bend",                                   # 1 ドライバー P1 正面の側屈の読み方
    "driver.p3.dtl.shaft",                                      # 2 ドライバー P3 後ろのシャフト角の食い違い
    *[f"err.trail_elbow.p{i}" for i in range(1, 8)],            # 3 右肘の後ろからの角度
    "iron.p4.dtl.shoulder", "iron.p5.dtl.shoulder_kept", "iron.p8.dtl.shoulder", "driver.p8.dtl.shoulder",  # 4 肩の角度の測り方
    "iron.p7.fo.shaft",                                         # 5 アイアン P7 正面のシャフト角
    "err.early_release.p6_5",                                   # 6 アーリーリリース P6.5 の基準の線
    "driver.p9.dtl.shaft", "driver.p9.fo.cap", "driver.p10.dtl.shaft_up",  # 7 ドライバー P9 / P10 の基準の線
    "err.sway.p4",                                              # 8 スエー P4 の二線
    "driver.p4.dtl.shaft_vs_forearm", "driver.p4.dtl.shoulder", "driver.p5.dtl.shoulder_kept",
    "driver.p1.dtl.knees", "driver.p7.dtl.hand_height",         # 9 ドライバーの記述が無い部位
    "pow.active_backswing",                                     # 10 アクティブバックスイング
    "iron.p7.impact_weight",                                    # 11 当たる瞬間の重心（90% と 83%）
]
REQUIRED = ("id", "group", "p", "view", "clubs", "measure", "judgeable", "judge", "domino_rank", "guide", "definition_status")


def test_版と件数():
    assert cp.version() == "checkpoints/1.0-pgag"
    assert all(it["version"] == cp.version() for it in ITEMS)
    assert 160 <= len(ITEMS) <= 200  # 見込みは約180（§5.3）
    assert len({it["id"] for it in ITEMS}) == len(ITEMS)
    assert len(cp.load()["camera"]) == 4


@pytest.mark.parametrize("it", ITEMS, ids=lambda it: it["id"])
def test_全項目が決まった欄を持つ(it):
    for k in REQUIRED:
        assert k in it and it[k] not in (None, ""), k
    assert it["measure"].get("how") in cp.HOWS
    assert it["guide"].get("pages")
    assert it["group"] in cp.GROUPS
    assert it["view"] in cp.VIEWS
    assert set(it["clubs"]) <= set(cp.CLUBS) and it["clubs"]
    assert it["judgeable"] in cp.JUDGEABLE
    assert it["judge"] in cp.JUDGES
    assert it["definition_status"] in ("ok", "unclear")
    assert set(it.get("tags") or []) <= set(cp.TAGS)
    assert it["faults"], "外れた向きの文が一つも無い"
    assert isinstance(it["domino_rank"], int) and 1 <= it["domino_rank"] <= 10
    assert it["checked_by"] == ""  # 初版は人が突き合わせていない（§5.2）


def test_必須のPそれぞれにアイアンとドライバーの後ろと正面の項目がある():
    for club in ("iron", "driver"):
        for p in cp.P_REQUIRED:
            for view in ("dtl", "fo"):
                got = [it for it in ITEMS if club in it["clubs"] and it["p"] == p and it["view"] == view]
                assert got, f"{club} {p} {view} の項目が無い"


def test_六つの群がどれも空でない():
    for g in cp.GROUPS:
        assert any(it["group"] == g for it in ITEMS), g


@pytest.mark.parametrize("it", [it for it in ITEMS if it.get("range")], ids=lambda it: it["id"])
def test_範囲の形(it):
    r = it["range"]
    assert r["unit"] in cp.UNITS
    side = r.get("side", "both")
    assert side in cp.SIDES
    if it["judge"] == "reference" or r["unit"] == "label":
        return
    if side == "both":
        assert isinstance(r["lo"], (int, float)) and isinstance(r["hi"], (int, float)) and r["lo"] <= r["hi"]
    elif side == "lo_only":
        assert isinstance(r["lo"], (int, float)) and r["hi"] is None
    else:
        assert isinstance(r["hi"], (int, float)) and r["lo"] is None


def test_この撮り方では判断できない項目は何があれば見られるかを持つ():
    for it in ITEMS:
        if it["judgeable"] == "not_in_2d":
            assert it["needs_note"].strip(), it["id"]


def test_見た目の質問は判断できないの選択肢を持つ():
    for it in ITEMS:
        v = it.get("vision")
        if not v:
            continue
        assert "判断できない" in v["options"], it["id"]
        assert set(v["ok_options"]) <= set(v["options"]), it["id"]
        assert set(v["fault_of"]) <= set(v["options"]), it["id"]
        fault_ids = {f["id"] for f in it["faults"]}
        assert set(v["fault_of"].values()) <= fault_ids, it["id"]
        assert "判断できない" not in v["ok_options"]


def test_ページはノートの節の範囲の中():
    for it in ITEMS + cp.load()["camera"]:
        for a, b in cp.page_ranges(it["guide"]["pages"]):
            assert a <= b
            assert any(lo <= a and b <= hi for lo, hi in cp.NOTE_RANGES), (it["id"], a, b)
        assert it["guide"]["page_exact"] is False  # いまは節の範囲（1ページに確かめたら true）


def _texts(it):
    yield it["title"]
    yield it["look_at"]
    yield it["ok_text"]
    for f in it["faults"]:
        yield f["label"]


def test_文に禁止語が無く左右を直書きしていない():
    for it in ITEMS:
        for t in _texts(it):
            assert "左" not in t and "右" not in t, (it["id"], t)
            for hand in ("R", "L"):
                bad = gist.check_plain(cp.fill(t, hand))
                assert not bad, (it["id"], t, bad)


def test_ガイドの文を写していない():
    """ノートの行と12字以上一致する文が無い（完全には機械で見られないので、人も確かめる）。"""
    notes = open(NOTES, encoding="utf-8").read()
    for it in ITEMS:
        for t in list(_texts(it)) + [it.get("note", ""), it["measure"].get("proxy", "")]:
            s = cp.fill(t, "R")
            for i in range(0, max(0, len(s) - 11)):
                chunk = s[i:i + 12]
                assert chunk not in notes, (it["id"], chunk)


def test_基準を確かめ中の項目は判定を返さない():
    for view in ("dtl", "fo"):
        for club in ("7 Iron", "Driver"):
            r = cm.measure_swing(syn.swing(view, club=club, ps=syn.P_ORDER))
            for x in r["items"]:
                it = BY[x["id"]]
                if it["definition_status"] == "unclear":
                    assert x["state"] in ("unknown", "same_as", "reference"), x
                    assert x["state"] != "reference" or it["judge"] == "reference"
                if it["judge"] == "reference":
                    assert x["state"] in ("reference", "unknown", "same_as"), x
                if it["judgeable"] == "not_in_2d":
                    assert x["state"] == "unknown" and x["reason"] == "not_in_2d"


def test_片側だけの基準は反対側に外れても範囲の中():
    it = BY["err.steep.p5"]
    r = it["range"]
    assert r["side"] == "lo_only"
    # 58（シャロー）はスティープのエラーではない
    assert cm.judge_value(58, config.CP_ANGLE_ERR_DEG, r["lo"], r["hi"], r["side"]) == "in"
    assert cm.judge_value(6, config.CP_ANGLE_ERR_DEG, r["lo"], r["hi"], r["side"]) == "out_lo"
    it = BY["err.steep.p6_5"]
    r = it["range"]
    assert r["side"] == "hi_only"
    assert cm.judge_value(0, config.CP_ANGLE_ERR_DEG, r["lo"], r["hi"], r["side"]) == "in"


def test_同じ量の項目は相手が在り同じPと向きと番手():
    n = 0
    for it in ITEMS:
        t = it.get("same_as")
        if not t:
            continue
        n += 1
        assert t in BY, it["id"]
        o = BY[t]
        assert (o["p"], o["view"], o["clubs"]) == (it["p"], it["view"], it["clubs"]), it["id"]
        assert not o.get("same_as"), "束ねた先がさらに束ねていない"
    assert n >= 4


def test_束ねた二つは一覧で一行と一回に数える():
    sws = []
    for i in range(3):
        r = cm.measure_swing(syn.swing("dtl", faults=("p2_inside",)))
        r["swing_id"] = i + 1
        sws.append(r)
    agg = cj.aggregate(sws)
    ids = [x["id"] for x in agg["items"]]
    assert "err.steep.p6" not in ids and "iron.p6.dtl.head_vs_hands" in ids
    row = next(x for x in agg["items"] if x["id"] == "iron.p6.dtl.head_vs_hands")
    assert any(a["id"] == "err.steep.p6" for a in row["also"])
    assert agg["counts"]["out_range"] == sum(1 for x in agg["items"] if x["state"] == "out_range")


def test_ドライバーの項目の数はアイアンの位置の項目と同じ():
    iron = [it for it in ITEMS if it["id"].startswith("iron.")]
    drv = [it for it in ITEMS if it["id"].startswith("driver.")]
    assert len(iron) == len(drv) > 40
    for it in drv:
        if it["needs_note"] == "ドライバーの基準がノートに無い":
            assert it["definition_status"] == "unclear" and it["judge"] == "reference", it["id"]


def test_よくあるエラーはアイアンだけ():
    assert not [it["id"] for it in ITEMS if it["id"].startswith("err.") and "driver" in it["clubs"]]


def test_番手で範囲が変わるセットアップの項目はidで分かれている():
    bp = [it for it in ITEMS if it["id"].startswith("setup.ball_pos.")]
    assert {it["id"].split(".")[-1] for it in bp} == {"driver", "wood", "hybrid", "long_iron", "mid_iron", "short_iron", "wedge"}
    assert BY["setup.ball_pos.hybrid"]["judge"] == "reference"
    sw = [it for it in ITEMS if it["id"].startswith("setup.stance_width.")]
    assert len(sw) == 3 and all(it["judge"] == "reference" for it in sw)


def test_飛ぶ力の束がパワーの群だけにならない():
    dist = [it for it in ITEMS if "distance" in (it.get("tags") or [])]
    assert any(it["group"] != "power" for it in dist)
    for need in ("iron.p3.fo.lead_arm", "iron.p4.fo.lead_arm", "iron.p4.fo.trail_elbow", "iron.p4.fo.cock", "iron.p5.fo.cock", "iron.p3.dtl.trail_knee", "iron.p7.dtl.hand_height"):
        assert "distance" in BY[need]["tags"], need


def test_要確認の一覧の項目はどれもunclear():
    for iid in UNCLEAR_REQUIRED:
        assert iid in BY, iid
        assert BY[iid]["definition_status"] == "unclear", iid


def test_測れる項目は測り方の中身を持つ():
    """judgeable が measurable で判定する項目は measure.spec を持つ（無いと黙って「判断できない」になる）。"""
    todo = []
    for it in ITEMS:
        how = it["measure"]["how"]
        if it["judgeable"] != "measurable" or it["definition_status"] != "ok" or how in ("vision", "none", "time", "trajectory") or it.get("same_as"):
            continue
        if not it["measure"].get("spec"):
            todo.append(it["id"])
    assert not todo, todo


def test_事前の表の症状():
    s1 = [it["id"] for it in ITEMS if "S1" in it["l1_links"]]
    assert "iron.p1.dtl.hands" in s1 and "err.early_ext.dtl" in s1
    assert all(set(it["l1_links"]) <= {"S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"} for it in ITEMS)


def test_見本の線画はPごとに二つの向き():
    svg = cp.svgs()
    for p in range(1, 11):
        for view in ("dtl", "fo"):
            assert f"p{p}.{view}" in svg, f"p{p}.{view}"
            s = svg[f"p{p}.{view}"]
            assert s.lstrip().startswith("<svg") and "<script" not in s.lower()
            assert not re.search(r'fill="#|stroke="#', s), "色は currentColor で（直書きしない）"
