"""2つのサービスを本当に立ち上げて、取り込み → 分析 → 実験の評価まで通す。

ダミーの CSV（testdata/trackman_dummy_session.csv）は答えが分かっている:
  1〜10 基準（フェース・トゥ・パス +3°前後） / 11〜20 介入（+0.8°前後）
  21〜25 定着（+1.5°前後） / 26〜30 ヒール打ち（打点が原因の曲がり）
なので、次の3つが出なければ失敗にする。
  - 介入の判定が strong か moderate
  - 26〜30 のうち4球以上が「打点が原因」
  - 分析の findings に打点の話が出る

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
            sys.exit(f"{name} が起動せずに終了しました")
        try:
            OPENER.open(url, timeout=1)
            return
        except OSError:
            time.sleep(0.2)
    sys.exit(f"{name} が起動しません: {url}")


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


def main() -> None:
    ap, gp = free_port(), free_port()
    tmp = tempfile.mkdtemp()
    py = subprocess.Popen(
        ["uv", "run", "uvicorn", "golf_analysis.app:app", "--port", str(ap), "--log-level", "warning"],
        cwd=os.path.join(ROOT, "analysis"),
    )
    # go run だと止めたときに子のバイナリが残るので、先にビルドして直に動かす
    binary = os.path.join(tmp, "server")
    subprocess.run(["go", "build", "-o", binary, "./cmd/server"], cwd=os.path.join(ROOT, "api"), check=True)
    go = subprocess.Popen(
        [binary],
        cwd=os.path.join(ROOT, "api"),
        env={**os.environ, "PORT": str(gp), "ANALYSIS_URL": f"http://127.0.0.1:{ap}", "DB_PATH": os.path.join(tmp, "e2e.db"), "WEB_DIR": ""},
    )
    try:
        wait(f"http://127.0.0.1:{ap}/healthz", py, "分析サービス")
        wait(f"http://127.0.0.1:{gp}/healthz", go, "API")
        base = f"http://127.0.0.1:{gp}/v1"

        player = call("POST", f"{base}/players", {"name": "Rita", "handedness": "R"})
        session = call("POST", f"{base}/sessions", {"player_id": player["id"], "date": "2026-09-27", "location": "練習場"})
        sid = session["id"]
        body, ctype = multipart(os.path.join(ROOT, "testdata", "trackman_dummy_session.csv"))
        imp = call("POST", f"{base}/sessions/{sid}/import", raw=body, ctype=ctype)
        print(f"取り込み: {imp['imported']}球（飛ばした行 {imp['skipped']}）")

        shots = call("GET", f"{base}/sessions/{sid}/shots")
        heel = [s["decomposition"]["curve_cause"] for s in shots[25:]]
        print(f"26〜30球目の曲がりの原因: {heel}")

        analysis = call("GET", f"{base}/sessions/{sid}/analysis")
        print("findings:")
        for f in analysis["findings"]:
            print("  ", json.dumps(f, ensure_ascii=False))
        club = analysis["clubs"][0]
        print(f"Good: {club['good']['n_good']} / {club['n']}")

        ex = call("POST", f"{base}/sessions/{sid}/experiments", {
            "hypothesis": "フェース・トゥ・パスが開いているのでプッシュフェードになる",
            "intervention": "左手甲を目標に向けたまま振る",
            "target_metric": "face_to_path",
            "goal": "reduce_abs",
        })
        for kind, a, b in (("baseline", 1, 10), ("intervention", 11, 20), ("retention", 21, 25)):
            call("POST", f"{base}/experiments/{ex['id']}/blocks", {"kind": kind, "seq_from": a, "seq_to": b})
        ev = call("GET", f"{base}/experiments/{ex['id']}/evaluation")
        print("実験の評価:")
        print(json.dumps(ev, ensure_ascii=False, indent=2))

        errors = []
        if ev["intervention_vs_baseline"]["grade"] not in ("strong", "moderate"):
            errors.append(f"介入の判定が {ev['intervention_vs_baseline']['grade']}")
        if heel.count("strike") < 4:
            errors.append(f"ヒール打ちのうち打点が原因と出たのが {heel.count('strike')} 球")
        if not any(f.get("cause") == "strike" for f in analysis["findings"]):
            errors.append("findings に打点の話が出ない")
        if errors:
            sys.exit("失敗: " + " / ".join(errors))
        print("OK")
    finally:
        for p in (go, py):
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
