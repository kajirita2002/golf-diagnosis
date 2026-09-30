"""段5: 誤差の予算を測る仕組みと、カタログの直しの流れ（docs/DESIGN_v2.md §15 段5・§18）。"""

from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from golf_analysis import config  # noqa: E402
from golf_analysis.checkpoints import budget, review  # noqa: E402
from golf_analysis.checkpoints import measure as cm  # noqa: E402

import synthetic_swing as syn  # noqa: E402


def _states(r: dict) -> dict:
    return {x["id"]: x for x in r["items"]}


def test_同じ動画を2回処理した差を項目ごとに数える():
    a = [{"items": [{"id": "x", "value": {"v": 30.0, "unit": "deg"}}, {"id": "y", "value": None}]}] * 12
    b = [{"items": [{"id": "x", "value": {"v": 32.0, "unit": "deg"}}, {"id": "y", "value": {"v": 1, "unit": "ball"}}]}] * 12
    s = budget.summarize(budget.repeat_samples(a, b))
    assert "y" not in s["items"]  # 片方で測れなかったものは数えない
    x = s["items"]["x"]
    assert x["n"] == 12 and x["p95"] == 2.0 and x["assumed"] == config.CP_ANGLE_ERR_DEG and x["candidate"] is True


def test_組が少ないか差が大きければ候補にしない():
    few = budget.summarize({"x": [(1.0, "deg")] * (budget.MIN_PAIRS - 1)})
    big = budget.summarize({"x": [(config.CP_ANGLE_ERR_DEG + 1, "deg")] * 20})
    assert few["items"]["x"]["candidate"] is False and big["items"]["x"]["candidate"] is False


def test_同じ動画を2回測ると差は0():
    sw = syn.swing()
    s = budget.summarize(budget.repeat_samples([cm.measure_swing(sw)], [cm.measure_swing(copy.deepcopy(sw))]))
    assert s["items"] and all(v["p95"] == 0 for v in s["items"].values())


def test_タップと物差しのばらつき():
    t = budget.tap_samples([{"a": [0, 0], "b": [3, 4], "ball_d": 10}, {"a": [0, 0], "b": [1, 1]}])
    assert t == [0.5]
    s = budget.scale_samples([{"ball_px": 10, "shoe_px": 10 * config.CP_SHOE_LEN_BALLS}])
    assert s == [0.0]


def test_人がbudget_jsonに書いた項目だけ目安を外す(monkeypatch):
    sw = syn.swing()
    iid = "iron.p1.dtl.hands"
    assert _states(cm.measure_swing(sw))[iid]["basis"] == "measured_approx"
    monkeypatch.setattr(budget, "_CAL", {iid})
    got = _states(cm.measure_swing(sw))
    assert got[iid]["basis"] == "measured"
    assert all(x["basis"] != "measured" for k, x in got.items() if k != iid)
    budget.reset_cache()


def test_budget_jsonの初期は空():
    with open(budget.BUDGET_PATH, encoding="utf-8") as f:
        assert json.load(f)["calibrated"] == []


def test_カタログの直しの残りと印(tmp_path):
    cat = review.load()
    r = review.remaining(cat)
    assert r["total"] == len(cat["items"]) and len(r["unclear"]) > 0 and len(r["unchecked"]) > 0
    c2 = copy.deepcopy(cat)
    iid = cat["items"][0]["id"]
    review.mark(c2, iid, "確かめた人", page=12)
    assert iid not in review.remaining(c2)["unchecked"] and iid not in review.remaining(c2)["page_range"]
    try:
        review.mark(c2, iid, "  ")
        raise AssertionError("空の名前を通した")
    except ValueError:
        pass
    # 書き戻しで直した行以外が変わらない（いまのファイルと同じ書き方）
    p = tmp_path / "c.json"
    review.save(cat, str(p))
    with open(review.CATALOG_PATH, encoding="utf-8") as f:
        assert p.read_text(encoding="utf-8") == f.read()
