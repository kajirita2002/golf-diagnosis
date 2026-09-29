"""tests/data/real_2026-09-17_shots.json を作り直す（Go の分解が変わったとき）。

本物の Go API と分析サービスを立て、testdata/real/2026-09-17 を scripts/e2e.py の seed_real で取り込み、
/shots の出力を分析サービスが受け取る形（ShotPayload と同じキー）で保存する。人名は入れない。

使い方（リポジトリの直下で）: python3 analysis/tests/data/make_real_fixture.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import e2e  # noqa: E402

KEYS = ["id", "seq", "club", "club_category", "metrics", "estimated", "manual", "excluded", "good_override", "decomposition"]

with e2e.Services() as sv:
    p = e2e.call("POST", f"{sv.base}/players", {"name": "Real", "handedness": "R"})
    sid = e2e.seed_real(sv.base, p["id"])
    shots = e2e.call("GET", f"{sv.base}/sessions/{sid}/shots")
out = [{k: s.get(k) for k in KEYS} for s in shots]
for s in out:
    s["estimated"] = s["estimated"] or []
    s["manual"] = s["manual"] or []
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "real_2026-09-17_shots.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"{len(out)}球を書きました: {path}")
