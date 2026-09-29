"""2つのサービスを本当に立ち上げて、取り込み → 分析 → 比較 → 実験の評価まで通す。

ダミーの CSV は答えが分かっている:
  trackman_dummy_session.csv（今日・30球）
    1〜10 基準（フェース・トゥ・パス +3°前後） / 11〜20 介入（+0.8°前後）
    21〜25 定着（+1.5°前後） / 26〜30 ヒール打ち（打点が原因の曲がり）
  trackman_dummy_yesterday.csv（前日・20球。+0.8°前後・打点は真ん中）
なので、次が出なければ失敗にする。
  - 介入の判定が strong か moderate
  - 26〜30 のうち4球以上が「打点が原因」
  - 分析の findings に打点の話が出る
  - 前日と比べて、スピン軸の変化の説明にフェース・トゥ・パスが入る
  - 解説（/report）の「次にやること」のステップ1が打点に**ならない**
    （26〜30球目のヒールは 14〜19mm で、極端なヒール（30mm 超）ではない。docs/DESIGN_coaching.md §11）
実データ（run_real）では、解説の範囲が2つ以上あり、アイアンの窓が −1.9°〜+2.1° になることも見る。

使い方: python3 scripts/e2e.py（リポジトリの直下で）
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 環境の HTTP プロキシを通さない（localhost への呼び出しがプロキシに行って止まる）
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait(url: str, proc: subprocess.Popen, name: str) -> None:
    for _ in range(150):
        if proc.poll() is not None:
            raise RuntimeError(f"{name} が起動せずに終了しました")
        try:
            OPENER.open(url, timeout=1)
            return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"{name} が起動しません: {url}")


def call(method: str, url: str, body=None, raw: bytes | None = None, ctype: str = "application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": ctype})
    with OPENER.open(req, timeout=60) as r:
        return json.loads(r.read())


def multipart(path: str) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        content = f.read()
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"trackman.csv\"\r\n"
        f"Content-Type: text/csv\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


class Services:
    """分析サービスと API（画面つき）を立てて、止める。with で使う。

    fake_screenshot に JSON のファイルを渡すと、スクショの読み取りは Claude を呼ばずにその中身を返す。
    """

    def __init__(self, fake_screenshot: str | None = None):
        self.fake_screenshot = fake_screenshot

    def __enter__(self):
        ap, gp = free_port(), free_port()
        tmp = tempfile.mkdtemp()
        # go run だと止めたときに子のバイナリが残るので、先にビルドして直に動かす
        binary = os.path.join(tmp, "server")
        subprocess.run(["go", "build", "-o", binary, "./cmd/server"], cwd=os.path.join(ROOT, "api"), check=True)
        py_env = {**os.environ}
        if self.fake_screenshot:
            py_env["SCREENSHOT_FAKE_RESPONSE"] = self.fake_screenshot
        self.py = subprocess.Popen(
            ["uv", "run", "uvicorn", "golf_analysis.app:app", "--port", str(ap), "--log-level", "warning"],
            cwd=os.path.join(ROOT, "analysis"),
            env=py_env,
        )
        self.go = subprocess.Popen(
            [binary],
            cwd=os.path.join(ROOT, "api"),
            env={
                **os.environ,
                "PORT": str(gp),
                "ANALYSIS_URL": f"http://127.0.0.1:{ap}",
                "DB_PATH": os.path.join(tmp, "e2e.db"),
                "WEB_DIR": os.path.join(ROOT, "web"),
            },
        )
        try:
            wait(f"http://127.0.0.1:{ap}/healthz", self.py, "分析サービス")
            wait(f"http://127.0.0.1:{gp}/healthz", self.go, "API")
        except BaseException:
            self.__exit__()
            raise
        self.root = f"http://127.0.0.1:{gp}"
        self.base = f"{self.root}/v1"
        return self

    def __exit__(self, *exc):
        for p in (self.go, self.py):
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def import_csv(base: str, session_id: int, name: str) -> dict:
    body, ctype = multipart(os.path.join(ROOT, "testdata", name))
    return call("POST", f"{base}/sessions/{session_id}/import", raw=body, ctype=ctype)


def seed(base: str) -> dict:
    """選手1人・前日と今日のセッションを作って、ダミーを取り込む。"""
    player = call("POST", f"{base}/players", {"name": "Rita", "handedness": "R"})
    yday = call("POST", f"{base}/sessions", {"player_id": player["id"], "date": "2026-09-26", "location": "練習場"})
    today = call("POST", f"{base}/sessions", {"player_id": player["id"], "date": "2026-09-27", "location": "練習場"})
    import_csv(base, yday["id"], "trackman_dummy_yesterday.csv")
    imp = import_csv(base, today["id"], "trackman_dummy_session.csv")
    return {"player": player, "yesterday": yday["id"], "today": today["id"], "import": imp}


def run(base: str) -> None:
    s = seed(base)
    sid = s["today"]
    print(f"取り込み: {s['import']['imported']}球（飛ばした行 {s['import']['skipped']}）")

    shots = call("GET", f"{base}/sessions/{sid}/shots")
    heel = [x["decomposition"]["curve_cause"] for x in shots[25:]]
    print(f"26〜30球目の曲がりの原因: {heel}")

    analysis = call("GET", f"{base}/sessions/{sid}/analysis")
    print("findings:")
    for f in analysis["findings"]:
        print("  ", json.dumps(f, ensure_ascii=False))

    cmp = call("GET", f"{base}/sessions/{sid}/compare?with={s['yesterday']}")
    explanations = cmp["clubs"]["7 Iron"]["explanations"]
    print("前日との比較:")
    for e in explanations:
        print("  ", json.dumps(e, ensure_ascii=False))

    ex = call("POST", f"{base}/sessions/{sid}/experiments", {
        "hypothesis": "フェース・トゥ・パスが開いているのでプッシュフェードになる",
        "intervention": "左手甲を目標に向けたまま振る",
        "target_metric": "face_to_path",
        "goal": "reduce_abs",
    })
    for kind, a, b in (("baseline", 1, 10), ("intervention", 11, 20), ("retention", 21, 25)):
        call("POST", f"{base}/experiments/{ex['id']}/blocks", {"kind": kind, "seq_from": a, "seq_to": b})
    ev = call("GET", f"{base}/experiments/{ex['id']}/evaluation")
    print("実験の評価:", json.dumps({k: ev[k] for k in ("intervention_vs_baseline", "retention_vs_baseline")}, ensure_ascii=False))

    rep = call("GET", f"{base}/sessions/{sid}/report")
    mains = [x for x in (rep.get("report") or {}).get("scopes", []) if x["kind"] == "main"]
    steps1 = {x["scope_id"]: x["candidates"].get("now") for x in mains}
    print("解説のステップ1:", steps1)

    errors = []
    if not rep.get("available"):
        errors.append(f"解説が出ない: {rep.get('reason')}")
    elif not mains:
        errors.append("解説に本体の範囲が無い")
    for scope_id, now in steps1.items():
        if now and now.startswith("strike_"):
            errors.append(f"{scope_id} のステップ1が打点（{now}）になった。26〜30球目は極端なヒールではない")
    if ev["intervention_vs_baseline"]["grade"] not in ("strong", "moderate"):
        errors.append(f"介入の判定が {ev['intervention_vs_baseline']['grade']}")
    if heel.count("strike") < 4:
        errors.append(f"ヒール打ちのうち打点が原因と出たのが {heel.count('strike')} 球")
    if not any(f.get("cause") == "strike" for f in analysis["findings"]):
        errors.append("findings に打点の話が出ない")
    axis = next((e for e in explanations if e["outcome"] == "spin_axis"), None)
    if not axis or "face_to_path" not in [c["metric"] for c in axis["explained_by"]]:
        errors.append(f"前日との比較でスピン軸の変化をフェース・トゥ・パスで説明していない: {axis}")
    if errors:
        sys.exit("失敗: " + " / ".join(errors))
    print("OK")


REAL = [("6i", "6 Iron"), ("7i", "7 Iron"), ("8i", "8 Iron"), ("9i", "9 Iron"), ("4h", "4 Hybrid"), ("5w", "5 Wood")]


def seed_real(base: str, player_id: int) -> int:
    """実データ（testdata/real/2026-09-17）を画面の表の貼り付けと同じ形で取り込む。セッションの id を返す。"""
    s = call("POST", f"{base}/sessions", {"player_id": player_id, "date": "2026-09-17", "location": "実データ"})
    for f, club in REAL:
        with open(os.path.join(ROOT, "testdata", "real", "2026-09-17", f + ".tsv"), "rb") as fh:
            q = "units=metric&club=" + club.replace(" ", "%20")
            call("POST", f"{base}/sessions/{s['id']}/import?{q}", raw=fh.read(), ctype="text/plain")
    return s["id"]


def run_real(base: str) -> None:
    """実データを取り込み、診断を確かめる。"""
    p = call("POST", f"{base}/players", {"name": "Real", "handedness": "R"})
    s = {"id": seed_real(base, p["id"])}
    shots = {x["id"]: x for x in call("GET", f"{base}/sessions/{s['id']}/shots")}
    an = call("GET", f"{base}/sessions/{s['id']}/analysis")
    print("実データの findings:")
    for f in an["findings"]:
        print("  ", json.dumps({k: v for k, v in f.items() if k != "shot_ids"}, ensure_ascii=False))

    def label(i):
        return f"{shots[i]['club']} #{[x for x in shots.values() if x['club'] == shots[i]['club']].index(shots[i]) + 1}"

    errors = []
    iron = [f for f in an["findings"] if f["club"] == "アイアン（まとめ）"]
    if not any(f["kind"] == "curve_cause" and f["cause"] == "face_to_path" and f["strength"] == "strong" for f in iron):
        errors.append("アイアンの曲がりの主因がフェース・トゥ・パスと出ない")
    ext = next((f for f in iron if f["kind"] == "extreme_strike"), None)
    # ヒール 30mm 超は5球（6番#5・8番#3・9番#2・#4・#6）。うち4球は TrackMan がクラブを見失った
    if not ext or ext["evidence"]["count"] != 5 or ext["evidence"]["no_club_data"] != 4:
        errors.append(f"アイアンの極端なヒール打ちが5球（うちクラブの値なし4球）と出ない: {ext}")
    else:
        print("極端なヒール:", [label(i) for i in ext["shot_ids"]])
    cands = [label(c["id"]) for c in (c for club in an["clubs"] for c in club["mishit_candidates"])]
    print("ミスヒットの候補:", cands)
    if "4 Hybrid #4" not in cands:
        errors.append("4番ユーティリティの4球目（キャリー12m）が候補に出ない")

    # 解説（/report）: 範囲が2つ以上・アイアンの帯の形（Go）が付いている・ステップ1が打点
    rep = call("GET", f"{base}/sessions/{s['id']}/report")
    scopes = (rep.get("report") or {}).get("scopes", [])
    print("解説の範囲:", [(x["scope_id"], x["kind"]) for x in scopes])
    if not rep.get("available"):
        errors.append(f"実データで解説が出ない: {rep.get('reason')}")
    if len(scopes) < 2 or len([x for x in scopes if x["kind"] == "main"]) < 2:
        errors.append(f"実データの解説の範囲が2つ以上ない: {len(scopes)}")
    iron = next((x for x in scopes if x["scope_id"] == "group:iron"), None)
    win = ((iron or {}).get("band_shape") or {}).get("window") or {}
    if not win.get("ok") or abs(win.get("face_min", 99) + 1.9) > 0.2 or abs(win.get("face_max", 99) - 2.1) > 0.2:
        errors.append(f"アイアンの窓が −1.9°〜+2.1° にならない: {win}")
    if iron and iron["candidates"].get("now") != "strike_heel":
        errors.append(f"アイアンのステップ1が打点（strike_heel）にならない: {iron['candidates'].get('now')}")
    if len(rep.get("shots") or []) != 57:
        errors.append(f"解説の応答の分解が57球でない: {len(rep.get('shots') or [])}")
    if errors:
        sys.exit("失敗（実データ）: " + " / ".join(errors))
    print("OK（実データ）")


def main() -> None:
    with Services() as sv:
        run(sv.base)
        run_real(sv.base)


if __name__ == "__main__":
    main()
