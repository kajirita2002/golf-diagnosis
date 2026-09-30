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
プラン（run_plan。docs/DESIGN_coaching.md §8・§11 Phase 1b）: 実データの解説の⑦の候補からプランを作り
（型と params は分析サービスの /v1/plan/build が作る）、練習の日を2回（作った球で）回して
取り込み → ブロックの割り当て → 評価 → 状態（効いたかも → 効いた）まで通す。
ドリル集は人が確かめたものが無いので、/v1/drills は空で、確かめていないドリルではプランを作れないことも見る。

使い方: python3 scripts/e2e.py（リポジトリの直下で）
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
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

    def __init__(self, fake_screenshot: str | None = None, fake_narrative: str | None = None, fake_vision: str | None = None):
        self.fake_screenshot = fake_screenshot
        # fake_vision に偽の Claude の答えのファイルを渡すと、見た目の評価（段2c）がその偽物で動く（本物の API は呼ばない）
        self.fake_vision = fake_vision
        # fake_narrative に偽の Claude の答えのファイルを渡すと、REPORT_LLM=on で起動する（本物の API は呼ばない）
        self.fake_narrative = fake_narrative

    def __enter__(self):
        ap, gp = free_port(), free_port()
        tmp = tempfile.mkdtemp()
        # go run だと止めたときに子のバイナリが残るので、先にビルドして直に動かす
        binary = os.path.join(tmp, "server")
        subprocess.run(["go", "build", "-o", binary, "./cmd/server"], cwd=os.path.join(ROOT, "api"), check=True)
        py_env = {**os.environ}
        if self.fake_screenshot:
            py_env["SCREENSHOT_FAKE_RESPONSE"] = self.fake_screenshot
        # つなぎの文（1c）。既定は off（手元の環境変数に on が残っていても off で確かめる）
        llm = "on" if self.fake_narrative else "off"
        py_env["REPORT_LLM"] = llm
        py_env.pop("NARRATIVE_FAKE_RESPONSE", None)
        if self.fake_narrative:
            py_env["NARRATIVE_FAKE_RESPONSE"] = self.fake_narrative
        py_env.pop("VISION_FAKE_RESPONSE", None)
        if not self.fake_screenshot:
            # 鍵が無い状態で確かめる（手元の環境変数に鍵が残っていても）
            py_env.pop("ANTHROPIC_API_KEY", None)
            py_env.pop("ANTHROPIC_AUTH_TOKEN", None)
        if self.fake_vision:
            py_env["VISION_FAKE_RESPONSE"] = self.fake_vision
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
                "REPORT_LLM": llm,
                # 偽のスクショの答えがあるときは、Claude の鍵がある状態として画面に出す（/healthz の anthropic_key）。
                # 無いときは、鍵が無い状態（手元の環境変数に鍵が残っていても）
                "ANTHROPIC_API_KEY": "fake-for-test" if self.fake_screenshot else "",
                "ANTHROPIC_AUTH_TOKEN": "",
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


def practice_tsv(template: list[dict], seed: int = 1, effect_mm: float = 14.0, club_extra: int = 0) -> str:
    """プランの型どおりに打った練習の日（9番アイアン・画面の表の貼り付けと同じ形・メートル法）を作る。

    いつも通り・準備の打点はヒール 22mm 前後、本番（とドリル）はそこから effect_mm だけ芯へ寄せる。
    フェース・パス・キャリーは実データのアイアンくらいの大きさ。club_extra は打ち直しの球を最後に足す数。
    """
    import random
    rnd = random.Random(seed)
    head = "#\tClub Speed\tAttack Ang.\tCarry\tSide\tLaunch Dir.\tClub Path\tDyn. Loft\tFace Ang.\tSpin Axis\tImp. Offset"
    units = "m, m/s\tm/s\tDeg\tm\tm\tDeg\tDeg\tDeg\tDeg\tDeg\tmm"
    kinds = [b["kind"] for b in template for _ in range(b["n"])] + ["baseline"] * club_extra
    rows = []
    for i, k in enumerate(kinds, 1):
        off = rnd.gauss(-22 + (effect_mm if k in ("intervention", "drill") else 0), 7)
        face, path = rnd.gauss(2.5, 3), rnd.gauss(0.4, 1)
        launch, axis, carry = 0.75 * face + 0.25 * path, (face - path) * 2.2, rnd.gauss(122, 4)
        side = carry * (launch + 0.3 * axis) / 57.3
        rows.append(f"{i}.\t{rnd.gauss(34.5, 0.5):.1f}\t{rnd.gauss(0, 1):.1f}\t{carry:.1f}\t{abs(side):.1f}{'R' if side >= 0 else 'L'}"
                    f"\t{launch:.1f}\t{path:.1f}\t{rnd.gauss(26, 1.5):.1f}\t{face:.1f}\t{axis:.1f}\t{off:.0f}")
    return "\n".join([head, units, *rows]) + "\n"


def import_practice(base: str, session_id: int, tsv: str, club: str = "9 Iron") -> dict:
    return call("POST", f"{base}/sessions/{session_id}/import?units=metric&club={club.replace(' ', '%20')}", raw=tsv.encode(), ctype="text/plain")


def expect_status(method: str, url: str, body, want: int) -> dict:
    """want の状態で断られることを確かめる（本文を返す）。"""
    try:
        call(method, url, body)
    except urllib.error.HTTPError as e:
        if e.code != want:
            raise RuntimeError(f"{method} {url}: {e.code}（{want} を期待）: {e.read().decode()[:300]}") from None
        return json.loads(e.read() or b"{}")
    raise RuntimeError(f"{method} {url}: 通ってしまった（{want} を期待）")


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
    # 最初に見える要点（数字も専門用語も使わない。根拠は同じ範囲の定型文の id）。Go を通っても落ちない
    for x in scopes:
        g = x.get("gist")
        if (g is not None) != (x["kind"] == "main"):
            errors.append(f"{x['scope_id']}: 要点が本体の範囲にだけ付いていない")
            continue
        if not g:
            continue
        ids = {c["id"] for sec in x["sections"] for c in sec["claims"]} | {c["id"] for c in rep["report"]["cross_club"]["claims"]}
        blocks = [b["id"] for b in g.get("blocks") or []]
        if blocks != ["now", "issue", "gap", "action"]:
            errors.append(f"{x['scope_id']}: 要点の4つの塊が無い: {blocks}")
        lines = [ln for b in g.get("blocks") or [] for ln in b["lines"]] + [{"text": r["now"], "evidence": r["evidence"]} for r in g["compare"]]
        for ln in lines:
            if not ln.get("evidence") or not set(ln["evidence"]) <= ids:
                errors.append(f"{x['scope_id']}: 要点の根拠が定型文を指さない: {ln['text'][:20]} {ln.get('evidence')}")
        texts = [g["headline"], g["summary"]] + [ln["text"] for ln in lines] + [r["ideal"] for r in g["compare"]]
        bad = [t for t in texts if t and (re.search(r"[0-9０-９°%A-Za-z]|度", t) or any(w in t for w in ("パス", "フェース", "打点", "ヒール", "トゥ", "帯", "キャリー")))]
        if bad:
            errors.append(f"{x['scope_id']}: 要点に数字・専門用語: {bad[:2]}")
        if "C1" not in (x.get("figures") or {}):
            errors.append(f"{x['scope_id']}: 理想との差の比べる図（C1）が無い")
    if iron and (iron.get("gist") or {}).get("headline") != "まず取り組むのは「ネック寄りの当たりを減らす」です。":
        errors.append(f"アイアンの要点の見出しが違う: {(iron.get('gist') or {}).get('headline')}")
    if errors:
        sys.exit("失敗（実データ）: " + " / ".join(errors))
    print("OK（実データ）")


def create_plan_from_report(base: str, player_id: int, session_id: int, scope_id: str = "group:iron", which: str = "now") -> dict:
    """解説の⑦の候補からプランを作る（画面の「このプランで始める」と同じ本文）。"""
    rep = call("GET", f"{base}/sessions/{session_id}/report")["report"]
    sc = next(x for x in rep["scopes"] if x["scope_id"] == scope_id)
    win = (sc.get("band_shape") or {}).get("window")
    return call("POST", f"{base}/players/{player_id}/plans", {
        "from_session": session_id, "scope_id": scope_id, "candidate_id": sc["candidates"][which],
        "cue": "向こうにボールがあるつもりで打つ", "window": win,
    })


def run_plan(base: str) -> None:
    """プランの通し: 作る → run → 取り込み → ブロック → 評価（2回の練習で「効いたかも」→「効いた」）。"""
    errors = []
    p = call("POST", f"{base}/players", {"name": "Plan", "handedness": "R"})
    sid = seed_real(base, p["id"])

    drills = call("GET", f"{base}/drills")
    print(f"ドリル集: 確かめ済み {len(drills['drills'])} / 全部 {drills.get('n_total')}（未確認 {drills.get('n_unchecked')}）")
    if drills["drills"] or not drills.get("n_unchecked"):
        errors.append(f"確かめていないドリルが出ている、または未確認の数が無い: {len(drills['drills'])} / {drills.get('n_unchecked')}")
    rep = call("GET", f"{base}/sessions/{sid}/report")["report"]
    iron = next(x for x in rep["scopes"] if x["scope_id"] == "group:iron")
    # 確かめていないドリルではプランを作れない（分析サービスでも Go でも断る）
    try:
        expect_status("POST", f"{base}/players/{p['id']}/plans", {"from_session": sid, "scope_id": "group:iron",
                      "candidate_id": iron["candidates"]["now"], "drill_id": "strike.two_balls"}, 422)
    except RuntimeError as e:
        if "400" not in str(e):
            errors.append(str(e))

    plan = create_plan_from_report(base, p["id"], sid)
    tpl = plan["template"]
    print("プラン:", plan["issue"], plan["club"], [(b["kind"], b["n"]) for b in tpl], "／", plan["hypothesis"])
    if plan["issue"] != "strike_heel" or plan["club"] != "9 Iron" or plan["drill_id"] != "":
        errors.append(f"プランの issue・番手・ドリルが違う: {plan['issue']} {plan['club']} {plan['drill_id']!r}")
    if any(b["kind"] == "drill" for b in tpl) or max(b["n"] for b in tpl) > 12:
        errors.append(f"自分で書く1点のプランの型にドリルのブロックがある／1ブロック12球を超える: {tpl}")
    if not (plan["params"].get("window") or {}).get("face_min"):
        errors.append("params に窓（作った時点の帯）が入っていない")
    if (plan["trigger"].get("plain") or {}).get("title") != "ネック寄りの当たりを減らす":
        errors.append(f"プランに最初に出す言葉（trigger.plain）が無い: {plan['trigger'].get('plain')}")
    if "仮説" not in plan["hypothesis"] or not (plan["trigger"].get("title")):
        errors.append(f"仮説の文・見出しが無い: {plan['hypothesis']!r} {plan['trigger'].get('title')!r}")
    dup = expect_status("POST", f"{base}/players/{p['id']}/plans", {"from_session": sid, "scope_id": "group:iron",
                        "candidate_id": iron["candidates"]["now"], "cue": "x"}, 409)
    if dup.get("existing_id") != plan["id"]:
        errors.append(f"2つ目のプランの 409 に前のプランの id が無い: {dup}")

    states = []
    for day, (date, seed) in enumerate((("2026-09-30", 11), ("2026-10-02", 12))):
        s = call("POST", f"{base}/sessions", {"player_id": p["id"], "date": date, "location": "練習場"})
        run = call("POST", f"{base}/plans/{plan['id']}/runs", {"session_id": s["id"]})
        import_practice(base, s["id"], practice_tsv(tpl, seed=seed, club_extra=1 if day == 0 else 0))
        v = call("GET", f"{base}/plan-runs/{run['id']}")
        if day == 0:
            # 打ち直しで1球多い: 予定と違うので黄色・余った球は最後に残る
            if not v["mismatch"] or len(v["unassigned_seqs"]) != 1 or v["blocks_source"] != "planned":
                errors.append(f"1球多いのに mismatch / unassigned にならない: {v['mismatch']} {v['unassigned_seqs']}")
            an = call("GET", f"{base}/sessions/{s['id']}/analysis")
            if (an.get("n_practice_excluded") or {}).get("warmup") != tpl[0]["n"]:
                errors.append(f"準備の球が診断から外れていない: {an.get('n_practice_excluded')}")
            # 境目を1球ずつ動かす: 最後のブロックに余った球をもらう → 保存した境目になり、余りが無くなる
            counts = list(v["counts"])
            counts[-1] += 1
            v = call("PUT", f"{base}/plan-runs/{run['id']}/blocks", {"counts": counts})
            if v["blocks_source"] != "saved" or v["mismatch"] or v["unassigned_seqs"]:
                errors.append(f"境目を動かしても保存されない: {v['blocks_source']} {v['mismatch']} {v['unassigned_seqs']}")
        ev = call("GET", f"{base}/plan-runs/{run['id']}/evaluation")["evaluation"]
        pr = ev.get("progress") or {}
        states.append(pr.get("state"))
        print(f"{day + 1}回目（{date}）: 判定 {ev.get('grade')}・状態 {pr.get('state')}（{pr.get('title')}）")
        if ev.get("grade") not in ("strong", "moderate"):
            errors.append(f"{day + 1}回目の判定が {ev.get('grade')}（本番で打点を 14mm 寄せた球なので効くはず）")
        if not ev.get("claims"):
            errors.append(f"{day + 1}回目の評価に定型文が無い")
    if states != ["maybe", "worked"]:
        errors.append(f"状態の移り方が「効いたかも → 効いた」にならない: {states}")

    prog = call("GET", f"{base}/plans/{plan['id']}/progress")
    trend = ((prog.get("latest") or {}).get("progress") or {}).get("trend") or []
    if len(prog["runs"]) != 2 or len(trend) != 2 or not all(t["first_a"]["n"] for t in trend):
        errors.append(f"進捗に2回ぶんの最初の A が無い: {len(prog['runs'])} {len(trend)}")
    rec = call("GET", f"{base}/players/{p['id']}/record")
    if len(rec) != 1 or [r["evaluated"] for r in rec[0]["runs"]] != [True, True] or rec[0].get("state") != "worked":
        errors.append(f"あなたの記録が2回ぶんにならない: {json.dumps(rec, ensure_ascii=False)[:300]}")
    today = call("GET", f"{base}/players/{p['id']}/today")
    if today["plan"]["id"] != plan["id"] or today["next_index"] != 2:
        errors.append(f"今日の練習が次の3回目を指さない: {today['next_index']}")
    if errors:
        sys.exit("失敗（プラン）: " + " / ".join(errors))
    print("OK（プラン）")


def fake_narrative_file() -> str:
    """偽の Claude（つなぎの文）。入力を読んで、検証を通る答えを作る（narrative.FakeClient の auto）。"""
    path = os.path.join(tempfile.mkdtemp(), "fake_narrative.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"mode": "auto", "input_tokens": 1200, "output_tokens": 300}, f)
    return path


def wait_jobs(base: str, jobs: list[dict]) -> list[dict]:
    out = []
    for j in jobs:
        if not j.get("job_id"):
            continue
        for _ in range(300):
            got = call("GET", f"{base}/jobs/{j['job_id']}")
            if got["status"] in ("done", "failed"):
                out.append(got)
                break
            time.sleep(0.1)
        else:
            sys.exit(f"つなぎの文のジョブが終わりません: {j}")
    return out


def run_narrative_off(base: str) -> None:
    """REPORT_LLM が off（既定）なら Claude を一切呼ばず、解説は定型文だけ（§11 Phase 1c）。"""
    errors = []
    p = call("POST", f"{base}/players", {"name": "NarrOff", "handedness": "R"})
    sid = seed_real(base, p["id"])
    r = call("POST", f"{base}/sessions/{sid}/report/narrative")
    if r.get("enabled") is not False or r.get("jobs"):
        errors.append(f"off なのにジョブを作った: {r}")
    rep = call("GET", f"{base}/sessions/{sid}/report")
    if rep.get("narrative_enabled") is not False:
        errors.append("narrative_enabled が false でない")
    if "narrative_inputs" in rep["report"]:
        errors.append("narrative_inputs を画面へ送った")
    mains = [x for x in rep["report"]["scopes"] if x["kind"] == "main"]
    if any(not (x.get("narrative") or {}).get("input_hash") or (x.get("narrative") or {}).get("result") for x in mains):
        errors.append("本体の範囲の鍵が無い、または off なのにつなぎの文が付いた")
    if errors:
        sys.exit("失敗（つなぎの文・off）: " + " / ".join(errors))
    print("OK（つなぎの文・off）")


def run_narrative_on(base: str) -> None:
    """REPORT_LLM=on・偽の Claude で、ジョブ → 保存 → 解説に付く → 同じ入力なら呼ばない → 球を除外すると作り直す。"""
    errors = []
    p = call("POST", f"{base}/players", {"name": "NarrOn", "handedness": "R"})
    sid = seed_real(base, p["id"])
    r = call("POST", f"{base}/sessions/{sid}/report/narrative")
    jobs = r.get("jobs") or []
    if r.get("enabled") is not True or sorted(j["scope_id"] for j in jobs) != ["club:4 Hybrid", "club:5 Wood", "group:iron"]:
        errors.append(f"本体の範囲（アイアン・4番UT・5番ウッド）ごとのジョブになっていない: {r}")
    done = wait_jobs(base, jobs)
    for j in done:
        res = j.get("result") or {}
        if j["status"] != "done" or res.get("generated_by") != "narrative" or res.get("fallback_sections"):
            errors.append(f"{j.get('scope_id')}: 検証を通ったつなぎの文になっていない: {j['status']} {res.get('fallback_sections')}")
        if j.get("model") != "claude-opus-5-5" or not j.get("cost_usd"):
            errors.append(f"{j.get('scope_id')}: 答えたモデルと料金が記録されていない: {j.get('model')} {j.get('cost_usd')}")
    rep = call("GET", f"{base}/sessions/{sid}/report")
    mains = [x for x in rep["report"]["scopes"] if x["kind"] == "main"]
    if rep.get("narrative_enabled") is not True or not all(((x.get("narrative") or {}).get("result") or {}).get("generated_by") == "narrative" for x in mains):
        errors.append("解説につなぎの文が付いていない")
    again = call("POST", f"{base}/sessions/{sid}/report/narrative")
    if not all(j.get("cached") for j in again["jobs"]) or again.get("used") != 3:
        errors.append(f"同じ入力で Claude を呼んだ: {again}")
    # 球を1つ除外すると、その範囲（アイアン）の入力の鍵が変わり作り直す。アイアンの球を使う範囲
    # （まとめた k で帯を見る4番UT・クラブをまたぐ⑤）も変わりうるが、アイアンと関わらない5番ウッドは呼ばない
    shots = call("GET", f"{base}/sessions/{sid}/shots")
    iron = next(x for x in shots if x["club_category"] == "iron")
    call("PATCH", f"{base}/shots/{iron['id']}", {"excluded": True})
    third = call("POST", f"{base}/sessions/{sid}/report/narrative")
    cached = {j["scope_id"]: bool(j.get("cached")) for j in third["jobs"]}
    if cached.get("group:iron") is not False or cached.get("club:5 Wood") is not True:
        errors.append(f"球を除外したのに作り直す範囲が違う: {cached}")
    wait_jobs(base, third["jobs"])
    if errors:
        sys.exit("失敗（つなぎの文・on）: " + " / ".join(errors))
    print("OK（つなぎの文・on）")


def run_home(base: str) -> None:
    """新しい導線の口（docs/DESIGN_v2.md §15 段1）: 使う人・設定・ホームの材料を本物の分析サービスで通す。"""
    errors = []
    me = call("POST", f"{base}/me", {"handedness": "R"})
    again = call("POST", f"{base}/me")
    if again["player"]["id"] != me["player"]["id"] or again["created"]:
        errors.append(f"使う人が2人になった: {me} / {again}")
    pid = me["player"]["id"]
    p = call("PATCH", f"{base}/players/{pid}", {"prefs": {"dist_unit": "m"}})
    if p["prefs"].get("dist_unit") != "m":
        errors.append(f"設定が残らない: {p}")
    call("PATCH", f"{base}/players/{pid}", {"prefs": {"dist_unit": "yd"}})
    sid = seed_real(base, pid)
    h = call("GET", f"{base}/players/{pid}/home")
    f = h.get("focus") or {}
    print("ホーム:", h.get("focus_state"), f.get("scope_id"), f.get("title"), "／", f.get("cue"), "／次:", f.get("next_title"))
    if h.get("focus_state") != "found" or f.get("session_id") != sid or f.get("title") != "ネック寄りの当たりを減らす" or not f.get("cue") or not f.get("figure"):
        errors.append(f"ホームの課題が実データの要点と合わない: {json.dumps(h, ensure_ascii=False)[:400]}")
    texts = [f.get("title"), f.get("cue"), f.get("next_title")]
    if any(t and (re.search(r"[0-9０-９A-Za-z°%]", t) or any(w in t for w in ("パス", "フェース", "打点", "ヒール", "キャリー"))) for t in texts):
        errors.append(f"ホームの言葉に数字・専門用語: {texts}")
    plan = create_plan_from_report(base, pid, sid)
    h = call("GET", f"{base}/players/{pid}/home")
    if not h.get("plan") or h["plan"]["id"] != plan["id"] or h["plan"]["title"] != "ネック寄りの当たりを減らす" or (h.get("focus") or {}).get("scope_id") != "group:iron":
        errors.append(f"プラン中のホームが違う: {json.dumps(h, ensure_ascii=False)[:400]}")
    if errors:
        sys.exit("失敗（ホーム）: " + " / ".join(errors))
    print("OK（ホーム）")


def run_video(base: str) -> None:
    """動画のチェックポイント（docs/DESIGN_v2.md §6・段2a）を、合成の棒人間の姿勢で API から通す。

    後ろから3本（P2 のクラブの先が内側）→ 多数で範囲の外 → まずここが P2 のクラブの先。
    正面から1本（P3 の {lead} 腕が曲がる）→ 1本だけの見立てなので課題にしない。左打ちでも同じ判定。
    長辺 360px を超えるサムネイルは断られ、DB に入らない。"""
    sys.path.insert(0, os.path.join(ROOT, "analysis", "tests"))
    import synthetic_swing as syn

    def put(pid_hand: str, view: str, faults, n: int) -> int:
        me = call("POST", f"{base}/players", {"name": f"video-{pid_hand}", "handedness": pid_hand})
        se = call("POST", f"{base}/sessions", {"player_id": me["id"], "date": "2026-09-29"})
        for _ in range(n):
            sw = syn.swing(view, faults=faults, hand=pid_hand)
            s = call("POST", f"{base}/sessions/{se['id']}/swings", {"view": view, "club": "7 Iron", "club_class": "iron", "fps": 240, "fps_source": "container",
                                                                    "width": syn.W, "height": syn.H, "ball": sw["ball"]})
            frames = [{"checkpoint": p, "t": f["t"], "frame": int(f["t"] * 240), "landmarks": f["landmarks"], "taps": f["taps"]} for p, f in sw["frames"].items()]
            out = call("PUT", f"{base}/swings/{s['id']}/frames", {"frames": frames, "missing": []})
            assert out["measure"]["camera"]["ok"] is True, out["measure"]["camera"]
        return se["id"]

    for hand in ("R", "L"):
        sid = put(hand, "dtl", ("p2_inside",), 3)
        c = call("GET", f"{base}/sessions/{sid}/checks")["checks"]
        assert c["focus"] == "iron.p2.dtl.head_vs_hands", c["focus"]
        f = next(x for x in c["items"] if x["id"] == c["focus"])
        assert f["n_out"] == 3 and f["fault"] == "inside" and f["basis"] == "measured_tap", f
        assert "左" not in f["title"] and "右" not in f["title"]
        assert c["counts"]["judged"] == c["counts"]["in_range"] + c["counts"]["out_range"]
        assert all(x["state"] != "out_range" or x["id"] != "err.steep.p6" for x in c["items"])  # 束ねた項目は一覧に出ない
        # 理想との比較（段3）: 範囲の外の項目は範囲の形と、範囲の外の部位だけを動かした線。範囲の中の項目には描かない
        sw0 = call("GET", f"{base}/sessions/{sid}/swings")[0]
        idl = call("GET", f"{base}/swings/{sw0['id']}/ideal?item={c['focus']}")
        assert idl["state"] == "out_range" and idl["zone"]["kind"] == "circle" and idl["fixed"]["moved"] == ["head"], idl
        ok = call("GET", f"{base}/swings/{sw0['id']}/ideal?item=iron.p1.dtl.hands")
        assert ok["zone"] is None and ok["fixed"] is None, ok
    sid = put("R", "fo", ("p3_bent",), 1)
    c = call("GET", f"{base}/sessions/{sid}/checks")["checks"]
    arm = next(x for x in c["items"] if x["id"] == "iron.p3.fo.lead_arm")
    assert arm["state"] == "out_range" and arm["single"] and c["focus"] is None, (arm["state"], arm["single"], c["focus"])
    assert "左腕" in arm["title"]
    # 全解像度のコマは受けない（Go が長辺 360px を超える JPEG を断る）
    sw = call("GET", f"{base}/sessions/{sid}/swings")[0]
    big_jpeg = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffc00011080400050003012200021101031101ffd9")  # 1280×1024 と名乗る JPEG の見出し
    import base64
    try:
        call("PUT", f"{base}/swings/{sw['id']}/frames", {"frames": [{"checkpoint": "P1", "thumb": base64.b64encode(big_jpeg).decode()}]})
        raise AssertionError("長辺 360px を超える画像を受けてしまった")
    except urllib.error.HTTPError as e:
        assert e.code == 400, e.code
    print("OK（動画のチェックポイント）")


def run_video_auto(base: str) -> None:
    """自動の取り出し（段2b）: 合成の姿勢の時系列を Go の口から送り、スイングの区間と P を受け取り、
    P のコマと時系列を送って測る。手の通り道の輪・テンポ（参考）が時系列から出る。時系列の中継は保存しない。"""
    sys.path.insert(0, os.path.join(ROOT, "analysis", "tests"))
    import synthetic_swing as syn

    me = call("POST", f"{base}/players", {"name": "video-auto", "handedness": "R"})
    se = call("POST", f"{base}/sessions", {"player_id": me["id"], "date": "2026-09-28"})
    body, truth = syn.motion("dtl", fps=60.0, n_swings=3, practice=(2,))
    r = call("POST", f"{base}/video/checkpoints", body)
    assert r["video_version"] == "video/0.2" and len(r["swings"]) == 2 and len(r["excluded"]) == 1, (len(r["swings"]), len(r["excluded"]))
    assert not call("GET", f"{base}/sessions/{se['id']}/swings"), "中継でスイングができた"
    for det in r["swings"]:
        m = syn.measure_input(body, det)
        s = call("POST", f"{base}/sessions/{se['id']}/swings", {"view": "dtl", "club": "7 Iron", "club_class": "iron", "fps": 60, "fps_source": "container",
                                                                "width": syn.W, "height": syn.H, "ball": m["ball"]})
        frames = [{"checkpoint": p, "t": f["t"], "frame": int(round(f["t"] * 60)), "source": "auto", "landmarks": f["landmarks"], "taps": {}} for p, f in m["frames"].items()]
        out = call("PUT", f"{base}/swings/{s['id']}/frames", {"frames": frames, "missing": [], "series": det["series"]})
        assert out["swing"]["has_series"] is True
    c = call("GET", f"{base}/sessions/{se['id']}/checks")["checks"]
    by = {x["id"]: x for x in c["items"]}
    assert by["path.loop"]["state"] == "in_range" and by["path.loop"]["n_judged"] == 2, by["path.loop"]
    assert by["tempo.ratio"]["state"] == "reference" and by["tempo.ratio"]["n_ref"] == 2, by["tempo.ratio"]
    print("OK（動画の自動の取り出し）")


# 長辺 1024px の JPEG の見出し（576×1024 と名乗る。Go は大きさだけを読み、分析サービスは頭の3バイトだけを見る）
JPEG_1024 = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffc0001108024004000301220002110103110" + "1ffd9")


def post_vision(base: str, session_id: int, swing_ids: list[int], ps=("P1", "P2", "P3")) -> dict:
    import uuid

    bd = uuid.uuid4().hex
    parts = [f"--{bd}\r\nContent-Disposition: form-data; name=\"meta\"\r\n\r\n".encode()
             + json.dumps({"session_id": session_id, "swings": [{"swing_id": i} for i in swing_ids]}).encode() + b"\r\n"]
    for i in swing_ids:
        for p in ps:
            parts.append(f"--{bd}\r\nContent-Disposition: form-data; name=\"f.{i}.{p}\"; filename=\"{p}.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".encode() + JPEG_1024 + b"\r\n")
    body = b"".join(parts) + f"--{bd}--\r\n".encode()
    return call("POST", f"{base}/swings/checks", raw=body, ctype=f"multipart/form-data; boundary={bd}")


def vision_session(base: str, name: str) -> tuple[int, list[int]]:
    sys.path.insert(0, os.path.join(ROOT, "analysis", "tests"))
    import synthetic_swing as syn

    me = call("POST", f"{base}/players", {"name": name, "handedness": "R"})
    se = call("POST", f"{base}/sessions", {"player_id": me["id"], "date": "2026-09-30"})
    ids = []
    for _ in range(2):
        sw = syn.swing("dtl")
        s = call("POST", f"{base}/sessions/{se['id']}/swings", {"view": "dtl", "club": "7 Iron", "club_class": "iron", "fps": 240, "fps_source": "container",
                                                                "width": syn.W, "height": syn.H, "ball": sw["ball"]})
        frames = [{"checkpoint": p, "t": f["t"], "frame": int(f["t"] * 240), "landmarks": f["landmarks"], "taps": f["taps"]} for p, f in sw["frames"].items()]
        call("PUT", f"{base}/swings/{s['id']}/frames", {"frames": frames, "missing": []})
        ids.append(s["id"])
    return se["id"], ids


def run_vision_off(base: str) -> None:
    """鍵が無い本番に近い状態（段2c）: 一覧は壊れず「まだ使えません」と出し、押しても Claude を呼ばない。"""
    sid, ids = vision_session(base, "vision-off")
    d = call("GET", f"{base}/sessions/{sid}/checks")
    assert d["vision"]["ready"] is False and d["vision"]["reason"], d["vision"]
    assert d["checks"] is not None
    out = post_vision(base, sid, ids)
    assert "job_id" not in out and out["reason"], out
    print("OK（見た目の評価・鍵なし）")


def fake_vision_file(pick: str = "fault") -> str:
    """偽の Claude（見た目の評価）。依頼の質問を読んで、良い側（ok）か外れ側（fault）のラベルを選ぶ。1回目だけ検証に落ちる答えを返す。"""
    path = os.path.join(tempfile.mkdtemp(), "vision.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"mode": "auto", "pick": pick, "bad_first": True, "extra": ["全体に落ち着いた構えに見えます"]}, f, ensure_ascii=False)
    return path


def run_vision_on(base: str) -> None:
    """偽の Claude で見た目の評価（段2c）: 202 → ジョブ → 見た目の項目が埋まる。二度目はキャッシュ。"""
    sid, ids = vision_session(base, "vision-on")
    before = call("GET", f"{base}/sessions/{sid}/checks")
    assert before["vision"]["ready"] is True and before["vision"]["n_target"] == 2, before["vision"]
    pend = [x for x in before["checks"]["items"] if x.get("reason") == "vision_pending"]
    assert pend, "見た目の項目が「まだ」になっていない"
    out = post_vision(base, sid, ids)
    job = wait_jobs(base, [{"job_id": out["job_id"]}])[0]
    assert job["status"] == "done", job
    assert "/9j/" not in json.dumps(job)
    after = call("GET", f"{base}/sessions/{sid}/checks")
    vis = [x for x in after["checks"]["items"] if x["basis"] == "visual"]
    assert vis and all(x["state"] == "out_range" and x["n_judged"] == 2 for x in vis), [(x["id"], x["state"]) for x in vis]
    assert any(x["candidate"] for x in vis)  # 二本で一致した見た目の項目は課題の候補
    assert after["vision"]["last"]["status"] == "done" and after["vision"]["last"]["n_dropped"] == 0
    again = post_vision(base, sid, ids)
    assert again.get("cached") is True, again
    print(f"OK（見た目の評価・偽の Claude。見た目で埋まった項目 {len(vis)}件）")


def main() -> None:
    with Services() as sv:
        run_vision_off(sv.base)
        run_video(sv.base)
        run_video_auto(sv.base)
        run_home(sv.base)
        run(sv.base)
        run_real(sv.base)
        run_plan(sv.base)
        run_narrative_off(sv.base)
    with Services(fake_narrative=fake_narrative_file()) as sv:
        run_narrative_on(sv.base)
    with Services(fake_vision=fake_vision_file()) as sv:
        run_vision_on(sv.base)


if __name__ == "__main__":
    main()
