"""画面を本物のブラウザで操作して確かめる（Playwright）。docs/DESIGN_v2.md §15 段1・§16。

新しい導線（下のタブ4つ: ホーム／記録／練習／経過）で、前の index.html・today.js でできたことが全部できるかを
押して確かめる。対応表は docs/v2_stage1_mapping.md。

  1. 書いてあるものの検査（ブラウザ無し）
     - alert( / confirm( が0・0.8125rem 未満の font-size が0・fill="# / color:# などの色の直書きが0・古い変数名が0
     - トークンの組み合わせのコントラスト（文字 4.5・部品 3.0。WCAG 2.x の相対輝度の式）
     - @media (prefers-color-scheme:dark) と [data-theme="dark"] の値が一致
  2. 画面（320 / 360 / 390px と PC）
     - 初回（使う人がいない）→「ようこそ」と［最初の記録を入れる］→ 記録の画面（日付の既定は今日）
     - 記録: 表の貼り付け（その日の記録が自動でできる）・CSV・スクショ（検算・直す・続きの結合・大きい写真・消す）・
       レポートのリンク
     - ホーム: 状態の見出しと主ボタン（homeState の全部の状態も関数で）・今日の一点・次に見る
     - 診断: 要点の4つの塊・「なぜそう言える？」のシート（数字入りの根拠・Esc で閉じてフォーカスが戻る）
     - くわしい解説: 図の点の数・「この図にない球」・左打ちのヒール・帯の無い範囲・窓の数字・⑦の組み方・
       図の点 → 1球の中身 → 一球ずつ・⑧の番号 → 一球ずつ・「一回だけ実験する」→ 実験の画面
     - 数字・一球ずつ（除外・Good の切替・打点）・一回だけの実験・2日の比較
     - プラン: ドリルを選べる／無いとき・意識する一点の初期値・空なら止める・作る → 練習 → 練習中（下のタブを隠す・
       「次」72px）→ 圏外で進める・開き直しても続き → 記録・球の帯・境目・判定（最初の面は言葉だけ）・経過・
       2つ目のプランは確認のシートで切り替える
     - Claude のつなぎの文: off なら定型文だけ・on（偽の Claude）ならボタン → ジョブ → 料金とつなぎの文
     - 設定: テーマ（再読み込みしても残る）・距離の単位・利き手
     - 全部のルートで: 横に溢れない・押せる要素が 44px 以上・再読み込みで同じ画面・JS のエラーが無い
     - ホーム・診断・練習の結果の最初の面に check_plain の違反が無い（ラベルは check_plain_label の形だけ）
     - 圏外でホームと練習が描ける
スクリーンショットは SHOTS_DIR（既定は一時ディレクトリ）に置く。

使い方: python3 scripts/ui_check.py（playwright が要る。PLAYWRIGHT_CHROMIUM で実行ファイルを指定できる）
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import re
import struct
import sys
import tempfile
import zlib

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e import REAL, ROOT, Services, call, create_plan_from_report, fake_narrative_file, import_csv, import_practice, practice_tsv, seed_real  # noqa: E402

WEB = os.path.join(ROOT, "web")

# 最初の面の禁止語とラベルの形は、分析サービスの config.py から読む（同じ表を2か所に持たない）
_spec = importlib.util.spec_from_file_location("golf_config", os.path.join(ROOT, "analysis", "golf_analysis", "config.py"))
CONFIG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CONFIG)


def check_plain(text: str) -> list[str]:
    """gist.check_plain と同じ（数字・英字・禁止語・+）。"""
    bad = [t for t in CONFIG.PLAIN_FORBIDDEN if t in text]
    if re.search(r"[0-9０-９]", text):
        bad.append("数字")
    if re.search(r"[A-Za-zＡ-Ｚａ-ｚ]", text):
        bad.append("英字")
    if "+" in text:
        bad.append("+")
    return bad


def check_plain_label(text: str, kind: str) -> bool:
    pat = CONFIG.PLAIN_LABEL_PATTERNS.get(kind)
    return bool(pat and re.fullmatch(pat, text.strip()))


# ============================================================ 1. 書いてあるものの検査

def web_files(exts=(".js", ".html", ".css", ".svg")) -> dict[str, str]:
    out = {}
    for f in sorted(os.listdir(WEB)):
        if f.endswith(exts) and os.path.isfile(os.path.join(WEB, f)):
            out[f] = open(os.path.join(WEB, f), encoding="utf-8").read()
    return out


def token_blocks(css: str) -> dict[str, dict[str, str]]:
    """:root（ライト）・[data-theme="dark"]・@media の中の :root:not([data-theme="light"]) の変数。"""
    def decls(body: str) -> dict[str, str]:
        return {k: v.strip() for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body)}
    light = re.search(r"(?m)^:root\{(.*?)\n\}", css, re.S)
    dark = re.search(r':root\[data-theme="dark"\]\{(.*?)\n\}', css, re.S)
    media = re.search(r'@media \(prefers-color-scheme:dark\)\{\s*:root:not\(\[data-theme="light"\]\)\{(.*?)\n  \}', css, re.S)
    return {"light": decls(light.group(1)) if light else {}, "dark": decls(dark.group(1)) if dark else {}, "media": decls(media.group(1)) if media else {}}


def lum(h: str) -> float:
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a: str, b: str) -> float:
    x, y = sorted([lum(a), lum(b)])
    return (y + 0.05) / (x + 0.05)


def static_checks(errors: list[str]) -> None:
    files = web_files()
    for name, src in files.items():
        code = re.sub(r"<!--.*?-->", "", src, flags=re.S)
        if re.search(r"(?<![\w.])(alert|confirm)\s*\(|window\.(alert|confirm)\s*\(", code):
            errors.append(f"[静的] {name}: alert( / confirm( がある")
        for m in re.finditer(r"font-size\s*:\s*([\d.]+)(px|rem|em)", code):
            v = float(m.group(1)) / (16 if m.group(2) == "px" else 1)
            if v < 0.8125 - 1e-9:
                errors.append(f"[静的] {name}: 0.8125rem 未満の font-size: {m.group(0)}")
        if re.search(r"--(card|accent|accent-weak|info|info-weak)\b|var\(--r\)|--r:", code):
            errors.append(f"[静的] {name}: 古い変数名（--card・--accent・--info・--r）が残っている")
        body = code
        if name == "tokens.css":
            # 変数を決める所（:root の3か所）だけは色を書いてよい
            body = re.sub(r"(?m)^:root\{.*?\n\}", "", body, flags=re.S)
            body = re.sub(r':root\[data-theme="dark"\]\{.*?\n\}', "", body, flags=re.S)
            body = re.sub(r"@media \(prefers-color-scheme:dark\)\{.*?\n\}", "", body, flags=re.S)
        for pat in (r'fill="#', r"fill:\s*#", r"stroke=\"#", r"stroke:\s*#", r"(?<![\w-])color:\s*#", r"background(-color)?:\s*#"):
            if re.search(pat, body):
                errors.append(f"[静的] {name}: 色の直書き（{pat}）")
    if os.path.exists(os.path.join(WEB, "today.js")):
        errors.append("[静的] 前の today.js が残っている（practice.js へ移した）")
    tb = token_blocks(files["tokens.css"])
    if not tb["light"] or not tb["dark"]:
        errors.append("[静的] tokens.css の色の変数が読めない")
        return
    if tb["media"] != tb["dark"]:
        diff = {k: (tb["dark"].get(k), tb["media"].get(k)) for k in set(tb["dark"]) | set(tb["media"]) if tb["dark"].get(k) != tb["media"].get(k)}
        errors.append(f"[静的] @media と [data-theme=dark] の値が違う: {diff}")
    for theme in ("light", "dark"):
        t = {**{k: v for k, v in tb["light"].items() if k.startswith("--")}, **(tb[theme] if theme == "dark" else {})}
        hexes = {k: v for k, v in t.items() if re.fullmatch(r"#[0-9A-Fa-f]{6}", v)}
        bgs = ["--bg", "--surface", "--surface-2"]
        text_pairs = [(fg, bg) for fg in ("--ink", "--sub", "--brand", "--good", "--warn", "--bad") for bg in bgs]
        text_pairs += [("--on-brand", "--brand"), ("--good", "--good-weak"), ("--warn", "--warn-weak"), ("--bad", "--bad-weak"),
                       ("--brand", "--brand-weak"), ("--ink", "--brand-weak"), ("--ink", "--warn-weak"), ("--ink", "--bad-weak"),
                       ("--ink", "--neutral-weak"), ("--sub", "--neutral-weak")]
        ui_pairs = [("--line-strong", bg) for bg in bgs + ["--brand-weak"]] + [("--focus", bg) for bg in bgs] + [("--brand", "--surface-2")]
        for pairs, need in ((text_pairs, 4.5), (ui_pairs, 3.0)):
            for fg, bg in pairs:
                if fg not in hexes or bg not in hexes:
                    errors.append(f"[静的] {theme}: {fg} か {bg} が無い")
                    continue
                cr = contrast(hexes[fg], hexes[bg])
                if cr < need:
                    errors.append(f"[静的] {theme}: {fg} × {bg} のコントラスト {cr:.2f} < {need}")
    # 映像の上の線は黒の縁つき（§11.7）
    for k in ("--ov-me", "--ov-range", "--ov-gap"):
        if contrast(tb["light"][k], tb["light"]["--ov-edge"]) < 3:
            errors.append(f"[静的] {k} と縁のコントラストが 3 未満")


# ============================================================ 2. 画面の道具

def no_overflow(page, tag: str, where: str, errors: list[str]) -> None:
    over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    if over > 0:
        errors.append(f"[{tag}] {where}: ページが横に {over}px 溢れている")


# 押せる要素の実寸（見えているものだけ）。図の点（SVG）は一球ずつの一覧が同じことをできるので除く（WCAG 2.5.8 の例外）。
# ラジオはラベルの行の高さで見る。見えない入力（visually-hidden）は、それを包むラベル（ドロップの枠）で見る。
TARGETS_JS = """(minNext) => {
  const bad = [];
  const els = document.querySelectorAll('button, a[href], input, select, textarea, summary, [role=button]');
  for (const el of els) {
    if (el.closest('svg') || el.closest('[hidden]')) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    let r = el.getBoundingClientRect();
    if (el.classList.contains('visually-hidden')) { const lb = el.closest('label'); if (!lb) continue; r = lb.getBoundingClientRect(); }
    if (el.type === 'radio' || el.type === 'checkbox') { const lb = el.closest('label'); if (lb) r = lb.getBoundingClientRect(); }
    if (r.width === 0 && r.height === 0) continue;
    if (r.width < 43.5 || r.height < 43.5) bad.push(`${el.tagName.toLowerCase()}${el.className ? '.' + String(el.className).split(' ')[0] : ''} 「${(el.textContent || el.getAttribute('aria-label') || el.name || '').trim().slice(0, 16)}」 ${Math.round(r.width)}×${Math.round(r.height)}`);
  }
  const nx = document.querySelector('[data-t=run] [data-t=next]');
  if (nx && nx.getBoundingClientRect().height < minNext) bad.push('練習の「次」 ' + Math.round(nx.getBoundingClientRect().height));
  return bad;
}"""


def targets(page, tag: str, where: str, errors: list[str]) -> None:
    bad = page.evaluate(TARGETS_JS, 64)
    if bad:
        errors.append(f"[{tag}] {where}: 44px 未満の押せる要素: {bad[:6]}{' …' if len(bad) > 6 else ''}")


# 最初の面の文（閉じた details の中・入力・図は除く）と、ラベル（data-label）を分けて返す
PLAIN_JS = """(sel) => {
  const root = document.querySelector(sel);
  if (!root) return null;
  const c = root.cloneNode(true);
  c.querySelectorAll('details:not([open]) > :not(summary), select, input, textarea, figure.fig, svg, .fig, .visually-hidden, script, style').forEach((x) => x.remove());
  const labels = [...c.querySelectorAll('[data-label]')].map((x) => [x.dataset.label, x.textContent.trim()]);
  c.querySelectorAll('[data-label]').forEach((x) => x.remove());
  return { text: c.textContent.replace(/\\s+/g, ' ').trim(), labels };
}"""


def plain_first(page, sel: str, tag: str, where: str, errors: list[str]) -> None:
    got = page.evaluate(PLAIN_JS, sel)
    if got is None:
        errors.append(f"[{tag}] {where}: {sel} が無い")
        return
    bad = check_plain(got["text"])
    if bad:
        errors.append(f"[{tag}] {where}: 最初の面に数字・英字・専門用語: {bad} {got['text'][:220]!r}")
    for kind, text in got["labels"]:
        if not check_plain_label(text, kind):
            errors.append(f"[{tag}] {where}: ラベル（{kind}）の形が違う: {text!r}")


def route_of(page) -> str:
    return page.get_attribute("#view", "data-route") or ""


def goto(page, root: str, path: str, wait: str | None = None, timeout: int = 20000) -> None:
    page.goto(f"{root}/#{path}")
    page.wait_for_function(f"document.querySelector('#view') && document.querySelector('#view').dataset.route !== undefined", timeout=timeout)
    if wait:
        page.wait_for_selector(wait, timeout=timeout)


def new_page(browser, w: int, h: int, errors: list[str], tag: str, player_id: int | None = None, sw: bool = False):
    ctx = browser.new_context(viewport={"width": w, "height": h}, service_workers="allow" if sw else "block")
    if player_id is not None:
        ctx.add_init_script(f"try {{ localStorage.setItem('golf.player', '{int(player_id)}'); }} catch (e) {{}}")
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"[{tag}] JS エラー: {e}"))
    page.on("console", lambda m: m.type == "error" and "ERR_INTERNET_DISCONNECTED" not in m.text and "Failed to load resource" not in m.text
            and errors.append(f"[{tag}] console: {m.text}"))
    return ctx, page


def shot(page, shots_dir: str, name: str, full: bool = True) -> None:
    page.screenshot(path=os.path.join(shots_dir, f"{name}.png"), full_page=full)


# ============================================================ 3. 画面ごとの確認

def check_home_states(page, errors: list[str]) -> None:
    """homeState の全部の状態（§10 H の表）を関数で確かめる。"""
    cases = [
        ("first", None, {}),
        ("first", {"sessions_with_shots": 0}, {}),
        ("finding", {"sessions_with_shots": 1, "focus_state": "unavailable", "latest": {"session_id": 3}}, {}),
        ("finding", {"sessions_with_shots": 1, "focus_state": "no_focus", "latest": {"session_id": 3}}, {}),
        ("found", {"sessions_with_shots": 1, "focus_state": "found", "focus": {"session_id": 3, "scope_id": "group:iron", "startable": True}}, {}),
        ("measure", {"sessions_with_shots": 1, "focus_state": "found", "focus": {"session_id": 3, "scope_id": "x", "startable": False}}, {}),
        ("plan", {"sessions_with_shots": 1, "plan": {"next_index": 1, "total": 37, "last": None}}, {}),
        ("stop", {"sessions_with_shots": 1, "plan": {"next_index": 2, "last": {"progress": {"next_action": "ask_continue"}}}}, {}),
        ("passed", {"sessions_with_shots": 1, "plan": {"next_index": 3, "last": {"progress": {"next_action": "recompute_candidates"}}}}, {}),
        ("passed", {"sessions_with_shots": 1, "plan": {"next_index": 3, "last": {"progress": {"advance": {"ok": True}, "next_action": "check_retention"}}}}, {}),
        ("video_resume", {"sessions_with_shots": 1}, {"videoPending": True}),
        ("setup", {"sessions_with_shots": 1}, {"setupMismatch": True}),
    ]
    want_label = {"first": "最初の記録を入れる", "finding": "診断を見る", "measure": "診断を見る", "found": "この一点で練習を組む", "plan": "練習を始める",
                  "stop": "続けるか選ぶ", "passed": "次の項目を見る", "video_resume": "続きから処理する", "setup": "撮り方を合わせる"}
    for key, h, local in cases:
        st = page.evaluate("([h, l]) => App.homeState(h, l)", [h, local])
        if st["key"] != key or st["primary"]["label"] != want_label[key]:
            errors.append(f"[homeState] {h} {local} → {st}（{key} を期待）")
    st = page.evaluate("() => App.homeState({sessions_with_shots: 1, plan: {next_index: 1, total: 37, last: null}})")
    if st.get("nth") != 2 or st["primary"]["href"] != "#/practice/run":
        errors.append(f"[homeState] プラン中の回数・行き先が違う: {st}")
    # 合格・止めるは、最後に判定した回を開く（判定と選ぶボタンが最初から出る）
    for last in ({"progress": {"next_action": "ask_continue"}}, {"progress": {"next_action": "recompute_candidates"}}):
        st = page.evaluate("(l) => App.homeState({sessions_with_shots: 1, plan: {next_index: 2, last: l}})", last)
        if st["primary"]["href"] != "#/practice/result?show=last":
            errors.append(f"[homeState] {st['key']} の行き先が最後に判定した回でない: {st['primary']['href']}")


def check_first_run(browser, root: str, shots_dir: str, errors: list[str]) -> None:
    """使う人がまだいない DB で開く: 名前を聞かず、利き手だけ選んで最初の記録へ。"""
    ctx, page = new_page(browser, 390, 844, errors, "first")
    goto(page, root, "/home", "[data-home-state=first]")
    check_home_states(page, errors)
    if page.locator("[data-primary]").inner_text().strip() != "最初の記録を入れる":
        errors.append("[first] 初回の主ボタンが「最初の記録を入れる」でない")
    if page.locator("input[placeholder*='名前'], #player").count():
        errors.append("[first] 初回に名前・選手を聞いている")
    plain_first(page, "#view", "first", "初回のホーム", errors)
    targets(page, "first", "初回のホーム", errors)
    shot(page, shots_dir, "first-0-welcome")
    page.click("[data-hand] button[data-v=L]")
    page.click("[data-first]")
    page.wait_for_selector("[data-record-date]")
    today = datetime.date.today().isoformat()
    if page.input_value("[data-date]") != today:
        errors.append(f"[first] 記録の日付の既定が今日でない: {page.input_value('[data-date]')}")
    ps = call("GET", root + "/v1/players")
    if len(ps) != 1 or ps[0]["handedness"] != "L":
        errors.append(f"[first] 初回で使う人が1人（左打ち）できていない: {ps}")
    call("PATCH", f"{root}/v1/players/{ps[0]['id']}", {"handedness": "R"})
    shot(page, shots_dir, "first-1-record")
    ctx.close()


def check_pending_hand(browser, shots_dir: str, errors: list[str]) -> None:
    """ようこそ画面を通らずに入る道: 設定で左打ちを選ぶ → 下のタブの「記録」から取り込む → 左打ちで作る。
    練習・経過のタブも、使う人がいなければ空の状態（圏外の文ではない）。"""
    with Services() as sv:
        ctx, page = new_page(browser, 390, 844, errors, "pending")
        goto(page, sv.root, "/practice", "[data-t=noplayer]")
        if "圏外" in page.inner_text("#view") or page.locator("[data-primary]").count() != 1:
            errors.append(f"[pending] 使う人がいない練習のタブが空の状態でない: {page.inner_text('#view')[:120]!r}")
        goto(page, sv.root, "/progress", "[data-t=noplayer]")
        goto(page, sv.root, "/settings", "[data-set=hand]")
        page.click("[data-set=hand] button[data-v=L]")
        page.click("#tabbar a[data-tab=record]")
        page.wait_for_selector("[data-paste]", state="attached")
        tsv = open(os.path.join(ROOT, "testdata", "real", "2026-09-17", "6i.tsv"), encoding="utf-8").read()
        # この Services はスクショを読めない（Claude の鍵が無い）ので、貼り付けが最初から主役で出ている
        if not page.locator("[data-noshot]").is_visible() or not page.locator("[data-paste]").is_visible():
            errors.append("[pending] スクショを読めないのに、貼り付けが主役になっていない")
        page.select_option("[data-units]", "metric")
        page.fill("[data-paste]", tsv)
        page.fill("[data-paste-club]", "6 Iron")
        page.click("[data-paste-go]")
        page.wait_for_selector("[data-imported]", timeout=20000)
        ps = call("GET", f"{sv.base}/players")
        if [p["handedness"] for p in ps] != ["L"]:
            errors.append(f"[pending] 設定で左打ちを選んで記録から入れたのに、左打ちで作られない: {ps}")
        # 同じ日にプランを始めたら、記録の既定の入れ先は、きっかけの記録ではなく新しい記録
        pid = ps[0]["id"]
        sid = call("GET", f"{sv.base}/players/{pid}/sessions")[0]["id"]
        for f, club in REAL:
            with open(os.path.join(ROOT, "testdata", "real", "2026-09-17", f + ".tsv"), "rb") as fh:
                call("POST", f"{sv.base}/sessions/{sid}/import?units=metric&club=" + club.replace(" ", "%20"), raw=fh.read(), ctype="text/plain")
        create_plan_from_report(sv.base, pid, sid)
        goto(page, sv.root, f"/record/{datetime.date.today().isoformat()}", "[data-target]")  # 同じ #/record だと描き直さないので日付つきで開く
        if page.input_value("[data-target]") != "new":
            errors.append(f"[pending] 同じ日にプランを始めたあと、記録の入れ先の既定がきっかけの記録になっている: {page.input_value('[data-target]')}")
        page.select_option("[data-target]", str(sid))
        if not page.locator("[data-trig-note]").is_visible():
            errors.append("[pending] きっかけの記録を選んでも注意が出ない")
        if page.locator("[data-loc-wrap]").is_visible():
            errors.append("[pending] 既にある記録を選んでいるのに、場所の欄が直せるように見える")
        shot(page, shots_dir, "pending-record-trigger")
        ctx.close()


def check_record(browser, root: str, base: str, shots_dir: str, tag: str, w: int, h: int, errors: list[str]) -> None:
    """記録: 貼り付け・CSV・スクショ・続き・レポートのリンク（取り込みと同時にその日の記録ができる）。"""
    who = call("POST", f"{base}/players", {"name": f"Rec-{tag}", "handedness": "R"})
    ctx, page = new_page(browser, w, h, errors, f"{tag}-rec", who["id"])
    date = "2026-01-02"
    goto(page, root, f"/record/{date}", "[data-drop]")
    if page.locator("[data-target-wrap]").is_visible():
        errors.append(f"[{tag}] 記録が無い日なのに「入れる先」が出ている")
    # 表の貼り付け（先にセッションを作らなくても取り込める）
    tsv = open(os.path.join(ROOT, "testdata", "real", "2026-09-17", "6i.tsv"), encoding="utf-8").read()
    page.fill("[data-loc]", "練習場")
    page.click("[data-other] > summary")
    page.select_option("[data-units]", "metric")
    page.fill("[data-paste]", tsv)
    page.fill("[data-paste-club]", "6 Iron")
    page.click("[data-paste-go]")
    page.wait_for_selector("[data-imported]")
    ss = call("GET", f"{base}/players/{who['id']}/sessions")
    if len(ss) != 1 or ss[0]["date"] != date or ss[0]["location"] != "練習場" or ss[0]["n_shots"] != 8:
        errors.append(f"[{tag}] 貼り付けでその日の記録ができない: {ss}")
    if page.locator("[data-day] [data-day-session]").count() != 1:
        errors.append(f"[{tag}] 「この日に入っているもの」に記録が出ない")
    # CSV（同じ日の、別の記録として入れる）
    page.select_option("[data-target]", "new")
    page.set_input_files("[data-csv]", os.path.join(ROOT, "testdata", "trackman_dummy_yesterday.csv"))
    page.select_option("[data-units]", "imperial")
    page.click("[data-csv-go]")
    page.wait_for_function("document.querySelectorAll('[data-day] [data-day-session]').length === 2")
    # レポートのリンク（本当に開くと外へ出るので、開こうとした URL だけ捕まえる）
    page.fill("[data-tm-url]", "https://web-dynamic-reports.trackmangolf.com/?a=26828541-c2b0-f111-8234-f42679e923bf")
    page.evaluate("() => { window.__opened = []; window.open = (u) => { window.__opened.push(u); return null; }; }")
    page.click("[data-tm-open]")
    page.wait_for_function("window.__opened.length === 1")
    url = page.evaluate("window.__opened[0]")
    if "v=clubData" not in url or "26828541" not in url:
        errors.append(f"[{tag}] レポートを開くリンクが違う: {url}")
    no_overflow(page, tag, "記録（取り込み後）", errors)
    targets(page, tag, "記録", errors)
    shot(page, shots_dir, f"{tag}-rec-1-imported")

    # スクショ: 読む → 検算 → 直す → 取り込む（偽の Claude は正しい6番と、1か所読み違えた9番を返す）
    png = os.path.join(shots_dir, "tiny.png")
    with open(png, "wb") as f:
        f.write(tiny_png())
    page.select_option("[data-target]", "new")
    page.set_input_files("[data-shot-file]", png)
    page.wait_for_function("document.querySelectorAll('.shotcard').length === 2")
    cards = page.locator(".shotcard")
    if "検算OK" not in cards.nth(0).inner_text():
        errors.append(f"[{tag}] 正しく読めた表が検算OKにならない")
    if "要確認" not in cards.nth(1).inner_text() or "★ Side" not in cards.nth(1).inner_text():
        errors.append(f"[{tag}] 読み違えた表で Side の列が★にならない")
    if cards.nth(0).locator("[data-f=club]").input_value() != "6 Iron":
        errors.append(f"[{tag}] クラブ名が「6 Iron」にそろわない")
    # 検算が合わないまま取り込むと、確かめのシートが出る（confirm を使わない）
    cards.nth(1).locator("button[data-act=import]").click()
    try:
        page.wait_for_selector("[data-sheet=confirm]", timeout=5000)
        shot(page, shots_dir, f"{tag}-rec-2-confirm", full=False)
        page.click("[data-sheet=confirm] [data-cancel]")
        page.wait_for_selector("[data-sheet=confirm]", state="detached")
    except Exception as e:  # noqa: BLE001
        errors.append(f"[{tag}] 検算が合わない表で確かめのシートが出ない: {e}")
    ta = cards.nth(1).locator("[data-f=tsv]")
    ta.fill(ta.input_value().replace("37.6L", "37.6R"))
    cards.nth(1).locator("button[data-act=recheck]").click()
    page.wait_for_function("document.querySelectorAll('.shotcard')[1].innerText.includes('検算OK')")
    # 大きい写真を CSV の欄から選んでも、縮めてスクショとして読む
    photo = os.path.join(shots_dir, "photo.png")
    if big_photo_png(photo) <= 5 * 1024 * 1024:
        errors.append(f"[{tag}] 試験用の写真が小さすぎる")
    page.set_input_files("[data-csv]", photo)
    try:
        page.wait_for_function("document.querySelectorAll('.shotcard').length === 4", timeout=60000)
    except Exception:
        errors.append(f"[{tag}] 大きい写真を CSV の欄から選んでも読めない")
    if page.input_value("[data-csv]") != "":
        errors.append(f"[{tag}] 写真が CSV の欄に残っている")
    n_before = sum(s["n_shots"] for s in call("GET", f"{base}/players/{who['id']}/sessions"))
    page.locator(".shotcard").nth(0).locator("button[data-act=import]").click()
    try:
        page.wait_for_function("document.querySelectorAll('.shotcard')[0].innerText.includes('取り込みました')", timeout=10000)
        n_after = sum(s["n_shots"] for s in call("GET", f"{base}/players/{who['id']}/sessions"))
        if n_after != n_before + 8:
            errors.append(f"[{tag}] スクショの表の球が増えていない: {n_before} → {n_after}")
    except Exception:
        errors.append(f"[{tag}] スクショの表を取り込めない: {page.locator('.shotcard').nth(0).inner_text()[-200:]!r}")
    no_overflow(page, tag, "スクショ", errors)
    shot(page, shots_dir, f"{tag}-rec-3-screenshot")
    ctx.close()
    check_continuation(browser, root, base, shots_dir, tag, w, h, who["id"], errors)


def check_continuation(browser, root, base, shots_dir, tag, w, h, pid, errors) -> None:
    """見出しの無い続きの画像（5W の 12〜23 球目）をつなげる・消す。読み取りの答えだけ差し替える。"""
    real = os.path.join(ROOT, "testdata", "real", "2026-09-17")
    lines = open(os.path.join(real, "5w.tsv"), encoding="utf-8").read().strip("\n").split("\n")
    head, units, body = lines[0], lines[1], [ln for ln in lines[2:] if not ln.startswith(("Average", "Consistency"))]
    avg = next(ln for ln in lines if ln.startswith("Average"))
    first = "\n".join([head, units, *body[:11]]) + "\n"
    cont = "\n".join(["#", "", *body[11:], avg]) + "\n"
    answers = [
        {"tables": [{"club": None, "tsv": cont, "check": {"ok": False, "continuation": True, "problems": ["続き"], "columns": []}}]},
        {"tables": [{"club": "5Wood", "tsv": first, "check": {"ok": False, "problems": ["Average"], "columns": []}}]},
        {"tables": [{"club": None, "tsv": cont, "check": {"ok": False, "continuation": True, "problems": ["続き"], "columns": []}}]},
    ]
    calls = []

    def answer(route):
        calls.append(1)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({**answers[len(calls) - 1], "usage": {"cost_usd": 0}}))

    ctx, page = new_page(browser, w, h, errors, f"{tag}-cont", pid)
    page.route("**/v1/screenshot", answer)
    goto(page, root, "/record/2026-01-03", "[data-drop]")
    a, b = os.path.join(shots_dir, "cont-a.png"), os.path.join(shots_dir, "cont-b.png")
    for p in (a, b):
        with open(p, "wb") as f:
            f.write(tiny_png())
    page.set_input_files("[data-shot-file]", [a, b])
    try:
        page.wait_for_function("document.querySelector('[data-shot-msg]').textContent.includes('読み取りました')"
                               " && document.querySelectorAll('.shotcard').length === 1"
                               " && document.querySelector('.shotcard').innerText.includes('検算OK')", timeout=15000)
    except Exception:
        errors.append(f"[{tag}] 続きの画像がつながらない: {page.inner_text('[data-shot-out]')[:300]!r}")
        ctx.close()
        return
    card = page.locator(".shotcard").first
    text = card.inner_text()
    if "23球" not in text or "12.〜23." not in text:
        errors.append(f"[{tag}] つなげた表の球数・注記が違う: {text[:300]!r}")
    if card.locator("[data-f=club]").input_value() != "5 Wood":
        errors.append(f"[{tag}] つなげた表のクラブが「5 Wood」にならない")
    tsv = card.locator("[data-f=tsv]").input_value().split("\n")
    if not (tsv[2].startswith("1.") and tsv[13].startswith("12.")):
        errors.append(f"[{tag}] つなげた表が番号順に並んでいない")
    shot(page, shots_dir, f"{tag}-rec-4-continuation")
    card.locator("button[data-act=drop]").click()
    if page.locator(".shotcard").count() != 0:
        errors.append(f"[{tag}] 「消す」で表が消えない")
    page.set_input_files("[data-shot-file]", a)
    page.wait_for_function("document.querySelectorAll('.shotcard').length === 1")
    card = page.locator(".shotcard").first
    if "続き" not in card.inner_text():
        errors.append(f"[{tag}] 見出しの無い画像で「続き」だと言わない")
    card.locator("button[data-act=import]").click()
    if "見出し" not in card.locator("[data-f=err]").inner_text():
        errors.append(f"[{tag}] 見出しの無い表を取り込もうとして止まらない")
    card.locator("button[data-act=drop]").click()
    if page.locator(".shotcard").count() != 0:
        errors.append(f"[{tag}] 見出しの無い表を「消す」で消せない")
    ctx.close()


def check_home(page, root: str, base: str, pid: int, shots_dir: str, tag: str, errors: list[str]) -> None:
    home = call("GET", f"{base}/players/{pid}/home")
    goto(page, root, "/home", "[data-home-state]")
    page.wait_for_selector("[data-primary]")
    state = page.get_attribute("[data-home-state]", "data-home-state")
    want = page.evaluate("(h) => App.homeState(h).key", home)
    if state != want:
        errors.append(f"[{tag}] ホームの状態が {state}（homeState は {want}）")
    if state == "found":
        if page.inner_text("[data-home=one]").strip() != home["focus"]["cue"]:
            errors.append(f"[{tag}] 今日の一点が要点の「意識する一点」でない")
        if home["focus"].get("next_title") and home["focus"]["next_title"] not in page.inner_text("[data-home=next]"):
            errors.append(f"[{tag}] 次に見るが無い")
    # 主ボタンは1つ（HIG）
    if page.locator("#view [data-primary]").count() != 1:
        errors.append(f"[{tag}] ホームの主ボタンが {page.locator('#view [data-primary]').count()} 個")
    # 主ボタンの位置は primary_in_view で見る（スクロールせずに見えること。ページの下半分に置くと図に押し出された）
    plain_first(page, "#view", tag, "ホーム", errors)
    no_overflow(page, tag, "ホーム", errors)
    targets(page, tag, "ホーム", errors)
    primary_in_view(page, tag, "ホーム", errors)
    page.wait_for_timeout(400)
    shot(page, shots_dir, f"{tag}-home")


def primary_in_view(page, tag: str, where: str, errors: list[str]) -> None:
    """主ボタンが、スクロールせずに見える所（下のタブより上）にある（図に押し出されていた）。"""
    got = page.evaluate("""() => { window.scrollTo(0, 0);
      const b = document.querySelector('#view [data-primary]'); if (!b) return null;
      const t = document.querySelector('#tabbar'); const top = t && t.offsetParent !== null ? t.getBoundingClientRect().top : window.innerHeight;
      return { bottom: b.getBoundingClientRect().bottom, limit: top }; }""")
    if got and got["bottom"] > got["limit"] + 0.5:
        errors.append(f"[{tag}] {where}: 主ボタンが最初の画面に入っていない（下端 {round(got['bottom'])} > タブの上端 {round(got['limit'])}）")


def check_home_fold(browser, root: str, pid: int, shots_dir: str, errors: list[str]) -> None:
    for w in (320, 360, 390):
        ctx, page = new_page(browser, w, 700, errors, f"fold{w}", pid)
        goto(page, root, "/home", "[data-primary]")
        page.wait_for_timeout(300)
        primary_in_view(page, f"fold{w}", "ホーム", errors)
        shot(page, shots_dir, f"fold{w}-home", full=False)
        ctx.close()


def check_diag(page, root: str, base: str, sid: int, shots_dir: str, tag: str, errors: list[str]) -> None:
    rep = call("GET", f"{base}/sessions/{sid}/report")["report"]
    goto(page, root, f"/session/{sid}", "[data-gist] .gblock")
    blocks = page.eval_on_selector_all("[data-gist] .gblock > h2", "(xs) => xs.map((x) => x.textContent)")
    if blocks != ["いま", "課題", "理想との差", "意識すること・やること"]:
        errors.append(f"[{tag}] 診断の4つの塊が無い: {blocks}")
    mains = [x for x in rep["scopes"] if x["kind"] == "main"]
    if len(mains) > 1 and page.locator("[data-scopes] a").count() != len(mains):
        errors.append(f"[{tag}] 範囲のセグメントの数が違う")
    if page.locator("[data-gist] figure.fig svg[data-fig=C1]").count() != 1:
        errors.append(f"[{tag}] 理想との差の比べる図（C1）が無い")
    plain_first(page, "#view", tag, "診断", errors)
    no_overflow(page, tag, "診断", errors)
    targets(page, tag, "診断", errors)
    page.wait_for_timeout(300)
    shot(page, shots_dir, f"{tag}-diag")
    # 「なぜそう言える？」: シート・数字入りの根拠・Esc で閉じ、押したボタンへフォーカスが戻る
    btn = page.locator("[data-gist] [data-why=now]")
    btn.click()
    page.wait_for_selector("[data-sheet=why].on")
    claims = page.locator("[data-sheet=why] .claim").all_inner_texts()
    if not claims or not any(re.search(r"[0-9]", c) for c in claims):
        errors.append(f"[{tag}] 「なぜそう言える？」に数字入りの根拠が無い: {claims[:2]}")
    if page.get_attribute("[data-sheet=why]", "aria-modal") != "true":
        errors.append(f"[{tag}] シートが aria-modal でない")
    no_overflow(page, tag, "なぜそう言える？", errors)
    page.wait_for_timeout(350)
    shot(page, shots_dir, f"{tag}-diag-why", full=False)
    page.keyboard.press("Escape")
    page.wait_for_selector("[data-sheet=why]", state="detached")
    if not page.evaluate("document.activeElement && document.activeElement.dataset.why === 'now'"):
        errors.append(f"[{tag}] シートを閉じてもフォーカスが「なぜそう言える？」へ戻らない")


# 図の点を数える JS（範囲のカードの直下の節の図だけ。畳んだ短い版の中は数えない）
COUNT_JS = """(scope) => {
  const card = [...document.querySelectorAll('article.rcard')].find((a) => a.dataset.scope === scope && !a.closest('.folded'));
  if (!card) return null;
  const out = {};
  for (const f of card.querySelectorAll(':scope > .sec figure.fig')) {
    const id = f.dataset.fig;
    if (!(id in out)) out[id] = f.querySelectorAll('[data-shot]').length;
  }
  return out;
}"""
HEEL_JS = """(scope) => {
  const card = [...document.querySelectorAll('article.rcard')].find((a) => a.dataset.scope === scope && !a.closest('.folded'));
  const f = card && card.querySelector(':scope > .sec figure.fig[data-fig=F5]');
  if (!f) return null;
  const svg = f.querySelector('svg'), r = svg.getBoundingClientRect(), mid = r.left + r.width / 2;
  return [...f.querySelectorAll('g[data-shot]')].map((g) => {
    const b = g.querySelector('.g-hit').getBoundingClientRect();
    return [Number(g.dataset.offset), b.left + b.width / 2 > mid ? 'right' : 'left'];
  });
}"""


def check_detail(page, root: str, base: str, sid: int, shots_dir: str, tag: str, errors: list[str], hand: str = "R") -> None:
    """くわしい解説（前の「解説」タブの確認をそのまま移した）。答えは同じ API の /report から取る。"""
    rep = call("GET", f"{base}/sessions/{sid}/report")["report"]
    mains = [x for x in rep["scopes"] if x["kind"] == "main"]
    goto(page, root, f"/session/{sid}/detail", "article.rcard")
    page.evaluate("() => document.querySelectorAll('article.rcard details').forEach((d) => { d.open = true; })")
    no_overflow(page, tag, f"くわしい解説（{hand}）", errors)
    text = page.inner_text("#view")
    if "{band:" in text:
        errors.append(f"[{tag}] 窓の文の {{band:…}} が埋まっていない")
    if "−1.9°〜+2.1°" not in text:
        errors.append(f"[{tag}] アイアンの窓の文に −1.9°〜+2.1° が無い（{hand}）")
    if 'fill="#' in page.content():
        errors.append(f"[{tag}] fill=\"#…\" が直書きされている")
    for sc in mains:
        got = page.evaluate(COUNT_JS, sc["scope_id"])
        if got is None:
            errors.append(f"[{tag}] {sc['scope_id']} のカードが無い")
            continue
        for fid in ("F1", "F3", "F5"):
            want = len(sc["figures"][fid]["points"])
            if got.get(fid) != want:
                errors.append(f"[{tag}] {sc['scope_id']} の {fid} の data-shot が {got.get(fid)}（描いた球 {want}）")
        rep4 = 1 if (sc["figures"].get("F4") or {}).get("representative") else 0
        if (got.get("F4") or 0) != rep4:
            errors.append(f"[{tag}] {sc['scope_id']} の F4 の data-shot が {got.get('F4')}（{rep4}）")
        heel_side = "right" if hand == "R" else "left"
        for off, side in page.evaluate(HEEL_JS, sc["scope_id"]) or []:
            if off < 0 and side != heel_side:
                errors.append(f"[{tag}] {sc['scope_id']} の F5 でヒール {off}mm の点が {side} にある（{hand}）")
                break
    if page.evaluate("() => document.querySelectorAll('.folded figure.fig').length"):
        errors.append(f"[{tag}] 1本の短い版に図が出ている")
    iron = next(x for x in rep["scopes"] if x["scope_id"] == "group:iron")
    card = page.locator("article.rcard[data-scope='group:iron']").first
    missing = iron["facts"]["group:iron/coverage.face.missing"]["value"]
    note = card.locator(":scope > .sec figure.fig[data-fig=F3] .fig-note[data-not-shown]").first
    if note.get_attribute("data-not-shown") != str(missing) or f"この図にない球: {missing}" not in note.inner_text():
        errors.append(f"[{tag}] F3 の「この図にない球」が coverage（{missing}）と合わない")
    chips = page.evaluate("() => [...document.querySelectorAll('.cand .blocks .chip')].map((c) => Number((c.textContent.match(/\\d+/) || [0])[0]))")
    if chips and max(chips) > 12:
        errors.append(f"[{tag}] ⑦の組み方に12球を超えるブロックがある: {max(chips)}")
    gtext = card.locator(".cand[data-step='2'] .kv").evaluate_all(
        "(xs) => (xs.find((x) => x.firstElementChild && x.firstElementChild.textContent === '動かさないもの') || {}).textContent || ''")
    if "{band:" in gtext or "−1.9°" not in gtext:
        errors.append(f"[{tag}] ⑦のステップ2に「動かさないもの」（窓の端 −1.9°）が無い: {gtext!r}")
    f1ticks = card.locator(":scope > .sec figure.fig[data-fig=F1]").first.locator("text.t11").all_text_contents()
    if not any(t.endswith("%") for t in f1ticks):
        errors.append(f"[{tag}] まとめの F1 の横軸が % の目盛りになっていない")
    wood = page.locator("article.rcard[data-scope='club:5 Wood'] > .sec figure.fig[data-fig=F3]").first
    if wood.locator(".g-band").count() or "帯を描いていません" not in wood.inner_text():
        errors.append(f"[{tag}] 5番ウッドの F3 に帯が描かれている／理由が無い")
    meta = page.locator(".repmeta[data-gen]")
    if meta.count() != 1 or meta.get_attribute("data-gen") != "template" or "文章はすべて定型文" not in meta.inner_text():
        errors.append(f"[{tag}] off のときの「定型文」の表示が無い")
    if page.locator("button[data-act=narrate]").count() or page.locator(".bridge").count():
        errors.append(f"[{tag}] off なのに Claude のつなぎのボタンか文が出ている")
    targets(page, tag, "くわしい解説", errors)
    if hand != "R":
        shot(page, shots_dir, f"{tag}-detail-lefty")
        return
    shot(page, shots_dir, f"{tag}-detail")
    # 図の点 → 1球の中身（シート）→ 一球ずつの該当の行
    pt = card.locator(":scope > .sec figure.fig[data-fig=F1] g[data-shot]").first
    pt.scroll_into_view_if_needed()
    pt.locator(".g-hit").click(force=True)
    try:
        page.wait_for_selector("[data-sheet=shot].on", timeout=3000)
        sheet_id = int(page.get_attribute("[data-sheet=shot]", "data-id"))
        seq = next(p["seq"] for p in iron["figures"]["F1"]["points"] if p["shot_id"] == sheet_id)
        if f"#{seq}" not in page.inner_text("[data-sheet=shot]"):
            errors.append(f"[{tag}] 1球の中身に #{seq} が無い")
        no_overflow(page, tag, "1球の中身", errors)
        page.wait_for_timeout(300)
        shot(page, shots_dir, f"{tag}-detail-shot", full=False)
        page.click("[data-sheet=shot] [data-act=goto]")
        page.wait_for_selector(f"tr.sel[data-id='{sheet_id}']")
    except Exception as e:  # noqa: BLE001
        errors.append(f"[{tag}] 図の点から1球の中身・一球ずつへ行けない: {e}")
    # ⑧ の「打点が - の球」から一球ずつへ
    goto(page, root, f"/session/{sid}/detail", "article.rcard")
    page.evaluate("() => document.querySelectorAll('article.rcard details').forEach((d) => { d.open = true; })")
    page.locator("article.rcard[data-scope='group:iron'] > .sec[data-sec=s8] button.jump").first.click()
    try:
        page.wait_for_selector("tr.sel[data-id]", timeout=5000)
    except Exception:
        errors.append(f"[{tag}] ⑧の打点が「-」の球から一球ずつへ飛べない")
    # ⑦「一回だけ実験する」→ 実験の画面に仮説・指標・目標・クラブが入る
    goto(page, root, f"/session/{sid}/detail", "article.rcard")
    page.evaluate("() => document.querySelectorAll('article.rcard details').forEach((d) => { d.open = true; })")
    page.locator("article.rcard[data-scope='group:iron'] button[data-act=startexp][data-which=now]").first.click()
    page.wait_for_selector("[data-ex-note]")
    got = {k: page.input_value(f"[data-f={k}]") for k in ("hyp", "metric", "goal", "club")}
    if not (got["hyp"].startswith("9 Iron") and got["metric"] == "impact_offset" and got["goal"] == "reduce_abs" and got["club"] == "9 Iron"):
        errors.append(f"[{tag}] 「一回だけ実験する」で実験の画面に値が入らない: {got}")
    # 数字の段
    goto(page, root, f"/session/{sid}/detail?tab=num", "[data-analysis] .finding")
    text = page.inner_text("[data-analysis]")
    for want in ("アイアン（まとめ）", "ネック寄り", "ミスヒット", "単回帰"):
        if want not in text:
            errors.append(f"[{tag}] 数字の段に「{want}」が無い")
    if re.search(r"\d+%は", text):
        errors.append(f"[{tag}] 数字の段に share の読み違いの文が残っている")
    no_overflow(page, tag, "数字", errors)
    shot(page, shots_dir, f"{tag}-numbers")
    # 一球ずつ: 57行・除外候補5つ・除外して戻す
    goto(page, root, f"/session/{sid}/shots", "tr[data-id]")
    if page.locator("tr[data-id]").count() != 57:
        errors.append(f"[{tag}] 一球ずつの行が {page.locator('tr[data-id]').count()}")
    try:
        page.wait_for_function("document.querySelectorAll('[data-shots] .chip').length && [...document.querySelectorAll('[data-shots] .chip')].filter((c) => c.textContent === '除外候補').length === 5", timeout=10000)
    except Exception:
        errors.append(f"[{tag}] 一球ずつの「除外候補」が5つでない")
    first = page.locator("tr[data-id]").first
    fid = first.get_attribute("data-id")
    first.locator("button[data-act=excl]").click()
    page.wait_for_selector(f"tr.excluded[data-id='{fid}']")
    page.locator(f"tr[data-id='{fid}'] button[data-act=excl]").click()
    page.wait_for_selector(f"tr[data-id='{fid}']:not(.excluded)")
    no_overflow(page, tag, "一球ずつ", errors)
    targets(page, tag, "一球ずつ", errors)
    shot(page, shots_dir, f"{tag}-shots")


def check_dummy(page, root: str, base: str, today_sid: int, yday_sid: int, shots_dir: str, tag: str, errors: list[str]) -> None:
    """ダミーの2日: 一回だけの実験（はっきり効いた）・2日の比較（フェース・トゥ・パス）・Good の切替・打点の入力。"""
    goto(page, root, f"/session/{today_sid}/experiments", "[data-f=save]")
    page.fill("[data-f=hyp]", "フェースが開いてプッシュフェードになる")
    page.fill("[data-f=int]", "左手甲を目標に向けたまま振る")
    page.click("[data-f=save]")
    page.wait_for_selector("[data-ex]")
    for kind, a, b in (("baseline", 1, 10), ("intervention", 11, 20)):
        box = page.locator("[data-ex]").last
        box.locator("[data-x=kind]").select_option(kind)
        box.locator("[data-x=from]").fill(str(a))
        box.locator("[data-x=to]").fill(str(b))
        box.locator("button[data-act=block]").click()
        page.wait_for_function(f"[...document.querySelectorAll('[data-ex]')].pop().innerText.includes('#{a}〜{b}')")
    box = page.locator("[data-ex]").last
    box.locator("button[data-act=eval]").click()
    box.locator("[data-x=out] .finding").first.wait_for()
    if "はっきり効いた" not in box.inner_text():
        errors.append(f"[{tag}] 実験の評価が「はっきり効いた」にならない")
    no_overflow(page, tag, "実験", errors)
    targets(page, tag, "実験", errors)
    shot(page, shots_dir, f"{tag}-experiments")
    goto(page, root, f"/progress/compare?a={yday_sid}&b={today_sid}", "[data-out] [data-t=cmpclub]")
    if "フェース・トゥ・パス" not in page.text_content("[data-out] [data-t=cmpnums]"):
        errors.append(f"[{tag}] 比較の「数字を見る」にフェース・トゥ・パスが無い")
    plain_first(page, "[data-out]", tag, "2日の比較", errors)
    no_overflow(page, tag, "2日の比較", errors)
    shot(page, shots_dir, f"{tag}-compare")
    goto(page, root, f"/session/{today_sid}/shots", "tr[data-id]")
    if page.locator("tr[data-id]").count() != 30:
        errors.append(f"[{tag}] ダミーの一球ずつの行が {page.locator('tr[data-id]').count()}")
    row = page.locator("tr[data-id]").nth(2)
    rid = row.get_attribute("data-id")
    row.locator("button[data-act=good]").click()
    page.wait_for_function(f"document.querySelector(\"tr[data-id='{rid}']\").innerText.includes('✎')")
    page.locator(f"tr[data-id='{rid}'] input[data-act=offset]").fill("-12")
    page.locator(f"tr[data-id='{rid}'] input[data-act=offset]").dispatch_event("change")
    page.wait_for_function(f"document.querySelector(\"tr[data-id='{rid}']\").innerText.includes('-12mm✎')")


FAKE_DRILLS = {"catalog_version": "drills/0.1", "n_total": 1, "n_unchecked": 0, "measures": [], "drills": [
    {"id": "strike.two_balls", "title": "2球並べ（試験用）", "issues": ["strike_heel"], "levers": [], "checked_by": "試験",
     "coach_reviewed": False, "steps": ["a"], "cue_transfer": "向こうにボールがあるつもりで打つ", "diagram_svg": "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><script>1</script></svg>"}]}


def check_plan(browser, root: str, base: str, shots_dir: str, tag: str, w: int, h: int, errors: list[str]) -> None:
    """プラン → 練習 → 練習中（圏外）→ 記録 → 球の帯 → 判定 → 経過。Service Worker を生かした文脈で。"""
    who = call("POST", f"{base}/players", {"name": f"Plan-{tag}", "handedness": "R"})
    sid = seed_real(base, who["id"])
    ctx, page = new_page(browser, w, h, errors, f"{tag}-plan", who["id"], sw=True)
    goto(page, root, "/practice", "[data-t=noplan]")
    if "動いているプランはありません" not in page.inner_text("#view"):
        errors.append(f"[{tag}] プランが無いときの練習の文が無い")
    no_overflow(page, tag, "練習（プランなし）", errors)
    # 確かめ済みのドリルがあれば選べる（偽物。図の script は外して描く）
    page.route("**/v1/drills*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(FAKE_DRILLS)))

    def fake_cands(route):
        resp = route.fetch()
        body = resp.json()
        for sc0 in body.get("scopes") or []:
            for c0 in (sc0.get("candidates") or {}).get("candidates") or []:
                if isinstance(c0.get("drills"), dict):
                    c0["drills"]["drills"] = [d for d in FAKE_DRILLS["drills"] if set(d.get("issues") or []) & set(c0.get("issue_names") or [c0["id"]])]
        route.fulfill(response=resp, body=json.dumps(body))
    page.route("**/plan-candidates*", fake_cands)
    goto(page, root, f"/session/{sid}?scope=group:iron", "[data-gist] [data-act=startplan]")
    page.click("[data-gist] [data-act=startplan]")
    page.wait_for_selector("[data-sheet=plan]")
    try:
        page.wait_for_function("document.querySelectorAll('[data-sheet=plan] label.drl').length === 2", timeout=5000)
    except Exception:
        errors.append(f"[{tag}] 確かめ済みのドリルがプランのシートに出ない")
    page.click("[data-sheet=plan] [data-sheet-close]")
    page.wait_for_selector("[data-sheet=plan]", state="detached")
    page.unroute("**/v1/drills*")
    page.unroute("**/plan-candidates*")
    # 本物のドリル集は確かめたものがまだ無い → 「まだありません」と意識する一点の欄（ホームからの道と同じ ?start=1）
    goto(page, root, f"/session/{sid}?scope=group:iron&start=1", "[data-sheet=plan] [data-t=nodrill]")
    page.wait_for_timeout(350)
    shot(page, shots_dir, f"{tag}-plan-sheet", full=False)
    no_overflow(page, tag, "プランのシート", errors)
    targets(page, tag, "プランのシート", errors)
    pre = page.input_value("[data-sheet=plan] [data-ps=cue]")
    if not pre or check_plain(pre):
        errors.append(f"[{tag}] 意識する一点に要点の文が入っていない、または専門用語を含む: {pre!r}")
    page.fill("[data-sheet=plan] [data-ps=cue]", "")
    page.click("[data-sheet=plan] [data-ps=go]")
    if "意識する1点" not in page.inner_text("[data-sheet=plan] [data-ps=err]"):
        errors.append(f"[{tag}] 意識する一点が空でもプランを作ろうとした")
    page.fill("[data-sheet=plan] [data-ps=cue]", "向こうにボールがあるつもりで打つ")
    page.click("[data-sheet=plan] [data-ps=go]")
    page.wait_for_selector("[data-route='/practice'] #tBlocks li", timeout=20000)
    today = call("GET", f"{base}/players/{who['id']}/today")
    tpl = today["plan"]["template"]
    if page.locator("#tBlocks li").count() != len(tpl):
        errors.append(f"[{tag}] 練習のブロックの数が型と違う")
    if page.locator("[data-t=head] [data-t=nodrill]").count() != 1:
        errors.append(f"[{tag}] 練習に「確かめ済みのドリルはまだありません」が無い")
    head = page.inner_text("[data-t=head] .ttitle")
    if not head or check_plain(head):
        errors.append(f"[{tag}] 練習の見出しが空か、数字・専門用語を含む: {head!r}")
    no_overflow(page, tag, "練習", errors)
    targets(page, tag, "練習", errors)
    shot(page, shots_dir, f"{tag}-practice")
    # 「なぜこれをやる？」はシート
    page.click("[data-t=planwhy]")
    page.wait_for_selector("[data-sheet=planwhy].on")
    page.keyboard.press("Escape")
    page.wait_for_selector("[data-sheet=planwhy]", state="detached")
    # ホーム: プラン中
    goto(page, root, "/home", "[data-home-state]")
    if page.get_attribute("[data-home-state]", "data-home-state") != "plan":
        errors.append(f"[{tag}] プランを作ったのにホームが「プラン中」でない")
    if page.inner_text("[data-home=one]").strip() != "向こうにボールがあるつもりで打つ":
        errors.append(f"[{tag}] プラン中のホームの今日の一点がプランの一点でない")
    plain_first(page, "#view", tag, "ホーム（プラン中）", errors)
    primary_in_view(page, tag, "ホーム（プラン中）", errors)
    page.wait_for_timeout(300)
    shot(page, shots_dir, f"{tag}-home-plan")
    # 練習中: 下のタブを隠す・「次」は 64px 以上・出口は中断1つ
    page.click("[data-primary]")
    page.wait_for_selector("[data-t=run] [data-t=next]")
    if page.locator("#tabbar").is_visible():
        errors.append(f"[{tag}] 練習中なのに下のタブが見えている")
    nb = page.locator("[data-t=run] [data-t=next]").bounding_box()
    if not nb or nb["height"] < 64:
        errors.append(f"[{tag}] 練習中の「次」が 64px 未満: {nb}")
    targets(page, tag, "練習中", errors)
    no_overflow(page, tag, "練習中", errors)
    shot(page, shots_dir, f"{tag}-run", full=False)
    # 圏外でも進められる・開き直しても続きから
    page.evaluate("() => navigator.serviceWorker.ready.then(() => true)")
    page.reload()
    page.wait_for_selector("[data-t=run] [data-t=next]")
    ctx.set_offline(True)
    for act in ("[data-t=next]", "[data-t=next]", "[data-t=plus]"):
        page.click(f"[data-t=run] {act}")
    cnt = page.inner_text("[data-t=run] [data-t=count]")
    if cnt != f"{tpl[2]['n'] + 1}球":
        errors.append(f"[{tag}] 圏外でブロックが進まない／球を足せない: {cnt}")
    page.click("[data-t=run] [data-t=minus]")
    try:
        page.reload()
        page.wait_for_selector("[data-t=run] [data-t=kind]", timeout=10000)
        if page.inner_text("[data-t=run] [data-t=count]") != f"{tpl[2]['n']}球" or "③" in page.inner_text("[data-t=run] [data-t=now]"):
            errors.append(f"[{tag}] 圏外で開き直すと続きから出ない: {page.inner_text('[data-t=run] [data-t=now]')!r}")
        if not page.locator("[data-banner=offline]").count():
            errors.append(f"[{tag}] 圏外なのに圏外の知らせが出ない")
        page.click("[data-t=run] [data-t=next]")
        if "③" not in page.inner_text("[data-t=run] [data-t=now]"):
            errors.append(f"[{tag}] 圏外で開き直したあと、ブロックを進められない")
        shot(page, shots_dir, f"{tag}-run-offline", full=False)
        # 圏外でホームと練習が描ける
        goto(page, root, "/home", "[data-home-state=plan]", timeout=10000)
        shot(page, shots_dir, f"{tag}-home-offline")
        goto(page, root, "/practice", "#tBlocks li", timeout=10000)
        if page.locator("[data-t=offline]").count() != 1:
            errors.append(f"[{tag}] 圏外の練習に「圏外です」が無い")
    except Exception as e:  # noqa: BLE001
        errors.append(f"[{tag}] 圏外で開き直せない: {e}")
    ctx.set_offline(False)
    goto(page, root, "/practice/run", "[data-t=run] [data-t=quit]")
    page.click("[data-t=run] [data-t=quit]")
    page.wait_for_selector("[data-route='/practice'] #tBlocks")

    # 取り込み（作った球・型どおり）→ 練習を記録 → 球の帯 → 境目 → 判定
    s2 = call("POST", f"{base}/sessions", {"player_id": who["id"], "date": "2026-09-30", "location": "練習場"})
    import_practice(base, s2["id"], practice_tsv(tpl, seed=21))
    goto(page, root, "/practice/result", "#tSess")
    page.wait_for_timeout(300)
    if page.input_value("#tSess") == str(sid):
        errors.append(f"[{tag}] 練習の記録の既定が、診断した記録になっている")
    page.select_option("#tSess", str(s2["id"]))
    href = page.get_attribute("[data-t=goimport]", "href")
    if f"target={s2['id']}" not in (href or "") or "from=practice" not in href:
        errors.append(f"[{tag}] 「取り込む画面へ」が選んだ記録を指さない: {href}")
    page.wait_for_selector("#tRun [data-t=record]")
    page.click("#tRun [data-t=record]")
    total = sum(b["n"] for b in tpl)
    try:
        page.wait_for_function(f"document.querySelectorAll('#tRun .strip .cell').length === {total}", timeout=10000)
        page.wait_for_function("document.querySelector('#tRun [data-t=src]').textContent.includes('保存')", timeout=10000)
    except Exception:
        errors.append(f"[{tag}] 球の帯が出ない／区切りが保存されない: {page.inner_text('#tRun')[:200]!r}")
        ctx.close()
        return
    page.click("#tRun button[data-mv='1'][data-d='-1']")
    page.wait_for_function(f"document.querySelectorAll('#tRun .strip .cell.k-baseline').length === {tpl[1]['n'] - 1 + tpl[-1]['n']}")
    run = call("GET", f"{base}/sessions/{s2['id']}/plan-run")
    if run["counts"][1] != tpl[1]["n"] - 1 or run["counts"][2] != tpl[2]["n"] + 1:
        errors.append(f"[{tag}] 境目を1球動かしても保存されない: {run['counts']}")
    page.click("#tRun button[data-mv='1'][data-d='1']")
    page.wait_for_function(f"document.querySelectorAll('#tRun .strip .cell.k-baseline').length === {tpl[1]['n'] + tpl[-1]['n']}")
    no_overflow(page, tag, "球の帯", errors)
    targets(page, tag, "練習の結果（区切り）", errors)
    shot(page, shots_dir, f"{tag}-result-strip")
    page.locator("#tRun .strip .cell").nth(6).click()
    try:
        page.wait_for_selector("[data-sheet=shot].on", timeout=5000)
        if "#7" not in page.inner_text("[data-sheet=shot]"):
            errors.append(f"[{tag}] 球の帯の7球目を押しても #7 の中身が出ない")
        page.keyboard.press("Escape")
        page.wait_for_selector("[data-sheet=shot]", state="detached")
    except Exception as e:  # noqa: BLE001
        errors.append(f"[{tag}] 球の帯のマスを押しても1球の中身が出ない: {e}")
    page.click("#tRun [data-t=judge]")
    try:
        page.wait_for_selector("#tJudge [data-t=judgeout]", timeout=20000)
        jt = page.inner_text("#tJudge")
        if page.get_attribute("#tJudge [data-t=judgeout]", "data-state") != "maybe" or "効いたかも" not in jt or "次の手" not in jt:
            errors.append(f"[{tag}] 1回目の判定が「効いたかも」と次の手にならない: {jt[:200]!r}")
        plain_first(page, "#tJudge [data-t=judgeout]", tag, "練習の結果", errors)
        page.click("#tJudge [data-t=whyjudge]")
        page.wait_for_selector("[data-sheet=judgewhy].on")
        if not page.locator("[data-sheet=judgewhy] .claim").count():
            errors.append(f"[{tag}] 判定の「なぜそう言える？」に根拠の定型文が無い")
        page.keyboard.press("Escape")
        page.wait_for_selector("[data-sheet=judgewhy]", state="detached")
    except Exception as e:  # noqa: BLE001
        errors.append(f"[{tag}] 判定が出ない: {e} {page.inner_text('#tRun')[-200:]!r}")
    no_overflow(page, tag, "判定", errors)
    targets(page, tag, "判定", errors)
    shot(page, shots_dir, f"{tag}-result-judge")
    # 経過: プランの進み（図は「なぜ？」のシート）・あなたの記録
    goto(page, root, "/progress", "[data-t=progwhy]")
    page.wait_for_selector("[data-part=plan] [data-t=progruns]", timeout=10000)
    if "判定した 1回" not in page.inner_text("[data-part=plan]"):
        errors.append(f"[{tag}] いまのプランに練習1回ぶんが無い")
    if page.locator("[data-part=rec] [data-t=rec]").count() != 0:
        errors.append(f"[{tag}] 進行中のプランが「これまでのプラン」にも並んでいる")
    page.click("[data-t=progwhy]")
    try:
        page.wait_for_selector("[data-sheet=progwhy] [data-t=progfig] circle[data-t=apt]", timeout=10000)
    except Exception:
        errors.append(f"[{tag}] 進みの図に最初の「いつも通り」の点が無い")
    page.keyboard.press("Escape")
    no_overflow(page, tag, "経過", errors)
    shot(page, shots_dir, f"{tag}-progress")
    # 今日の記録が無ければ練習の結果の画面で作れる（作ったら選ばれて、記録のボタンが出る）
    goto(page, root, "/practice/result", "#tSess")
    page.wait_for_selector("[data-t=newsess]:not([hidden])")
    page.click("[data-t=newsess]")
    try:
        page.wait_for_function("document.querySelector('#tSess').selectedOptions[0].textContent.includes('練習場') && !!document.querySelector('#tRun [data-t=record]')", timeout=10000)
    except Exception:
        errors.append(f"[{tag}] 「今日の記録を作る」で選ばれない")
    # 2つ目のプランは確かめのシートで切り替える（409 → 替えた）
    goto(page, root, f"/session/{sid}?scope=group:iron&start=1", "[data-sheet=plan] [data-ps=go]")
    page.fill("[data-sheet=plan] [data-ps=cue]", "もう一つの一点")
    page.click("[data-sheet=plan] [data-ps=go]")
    try:
        page.wait_for_selector("[data-sheet=confirm] [data-ok]", timeout=10000)
        page.click("[data-sheet=confirm] [data-ok]")
        page.wait_for_selector("[data-route='/practice'] #tBlocks li", timeout=20000)
        plans = call("GET", f"{base}/players/{who['id']}/plans")
        sts = sorted(p["status"] for p in plans)
        if sts != ["active", "switched"]:
            errors.append(f"[{tag}] 2つ目のプランに切り替えたあとの状態が違う: {sts}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"[{tag}] 2つ目のプランで確かめのシートが出ない: {e}")
    ctx.close()


def check_settings(browser, root: str, base: str, pid: int, sid: int, shots_dir: str, errors: list[str]) -> None:
    ctx, page = new_page(browser, 390, 844, errors, "settings", pid)
    goto(page, root, "/settings", "[data-set=theme]")
    plain = page.inner_text("#view")
    if "動画のチェックポイント" not in plain or "web/" not in plain:
        errors.append("[settings] このアプリについて（版・何を見ているか）が無い")
    page.click("[data-set=theme] button[data-v=dark]")
    if page.get_attribute("html", "data-theme") != "dark":
        errors.append("[settings] ダークに切り替わらない")
    targets(page, "settings", "設定", errors)
    shot(page, shots_dir, "settings-dark")
    goto(page, root, "/home", "[data-home-state]")
    page.reload()
    page.wait_for_selector("[data-home-state]")
    if page.get_attribute("html", "data-theme") != "dark":
        errors.append("[settings] 再読み込みでテーマが戻る")
    page.wait_for_timeout(400)
    shot(page, shots_dir, "home-dark")
    goto(page, root, f"/session/{sid}", "[data-gist]")
    page.wait_for_timeout(300)
    shot(page, shots_dir, "diag-dark")
    goto(page, root, "/settings", "[data-set=theme]")
    page.click("[data-set=theme] button[data-v=auto]")
    page.click("[data-set=unit] button[data-v=m]")
    page.wait_for_selector("#toast:not([hidden])")
    if call("GET", f"{base}/players/{pid}")["prefs"].get("dist_unit") != "m":
        errors.append("[settings] 距離の単位がサーバーに残らない")
    goto(page, root, f"/session/{sid}/shots", "tr[data-id]")
    if not re.search(r"\d+\.\dm\b", page.inner_text("[data-shots] tr[data-id]")):
        errors.append("[settings] メートルにしても一球ずつがメートルで出ない")
    goto(page, root, "/settings", "[data-set=unit]")
    page.click("[data-set=unit] button[data-v=yd]")
    page.click("[data-set=hand] button[data-v=L]")
    page.wait_for_function(f"fetch('/v1/players/{pid}').then(r => r.json()).then(p => p.handedness === 'L')")
    page.click("[data-set=hand] button[data-v=R]")
    page.wait_for_function(f"fetch('/v1/players/{pid}').then(r => r.json()).then(p => p.handedness === 'R')")
    ctx.close()


ROUTES = ["/home", "/record", "/record/2026-09-17", "/session/{sid}", "/session/{sid}/detail", "/session/{sid}/detail?tab=num",
          "/session/{sid}/shots", "/session/{sid}/experiments", "/practice", "/practice/result", "/progress", "/progress?tab=records",
          "/progress/compare", "/settings", "/video/2026-09-17", "/session/{sid}/check", "/guide/dtl", "/guide/fo"]


def check_routes(browser, root: str, pid: int, sid: int, shots_dir: str, errors: list[str]) -> None:
    """全部のルート × 320 / 360 / 390px: 横に溢れない・44px・再読み込みで同じ画面。"""
    for w, h in ((320, 640), (360, 780), (390, 844)):
        tag = f"w{w}"
        ctx, page = new_page(browser, w, h, errors, tag, pid)
        for r in ROUTES:
            path = r.format(sid=sid)
            goto(page, root, path)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(250)
            before = route_of(page)
            no_overflow(page, tag, path, errors)
            targets(page, tag, path, errors)
            page.reload()
            page.wait_for_function(f"document.querySelector('#view').dataset.route === {json.dumps(before)}", timeout=10000)
            page.wait_for_load_state("networkidle")
            if w == 320:
                shot(page, shots_dir, f"w320{path.replace('/', '_').replace('?', '_').replace('=', '-')}")
        # 200% の文字でも横に溢れない（1.4.4 / 1.4.10 の代わりに、根の文字を2倍にして見る）
        if w == 390:
            for r in ("/home", "/session/{sid}", "/practice"):
                goto(page, root, r.format(sid=sid))
                page.wait_for_load_state("networkidle")
                page.evaluate("() => { document.documentElement.style.fontSize = '200%'; }")
                page.wait_for_timeout(200)
                no_overflow(page, tag + "-200%", r, errors)
                page.evaluate("() => { document.documentElement.style.fontSize = ''; }")
        ctx.close()


def check_narrative(browser, shots_dir: str, errors: list[str]) -> None:
    """REPORT_LLM=on・偽の Claude で、つなぎの文を頼んで画面に出す（360px）。"""
    with Services(fake_narrative=fake_narrative_file()) as sv:
        me = call("POST", f"{sv.base}/me")["player"]
        sid = seed_real(sv.base, me["id"])
        tag = "narr"
        ctx, page = new_page(browser, 360, 780, errors, tag)
        goto(page, sv.root, f"/session/{sid}/detail", "button[data-act=narrate]")
        page.click("button[data-act=narrate]")
        page.wait_for_selector(".repmeta[data-gen=narrative]", timeout=60000)
        page.evaluate("() => document.querySelectorAll('article.rcard details').forEach((d) => { d.open = true; })")
        meta = page.inner_text(".repmeta[data-gen]")
        if "定型文＋Claude のつなぎ（検証済み）" not in meta or "$" not in meta:
            errors.append(f"[{tag}] 「定型文＋Claude のつなぎ（検証済み）」と料金が出ない: {meta}")
        rep = call("GET", f"{sv.base}/sessions/{sid}/report")["report"]
        mains = [x for x in rep["scopes"] if x["kind"] == "main"]
        if page.locator(".repmeta[data-narr-meta]").count() != len(mains):
            errors.append(f"[{tag}] 範囲ごとの料金の行の数が違う")
        bridges = page.locator(".bridge").all_inner_texts()
        if not bridges or any(re.search(r"[0-9０-９右左]", b) for b in bridges):
            errors.append(f"[{tag}] つなぎの文が出ない、または事実の語を含む: {bridges[:3]}")
        if page.locator("button[data-act=narrate]").count():
            errors.append(f"[{tag}] つなぎが付いたのに頼むボタンが残っている")
        if page.locator("article.rcard[data-kind=main] > .sec .claim, article.rcard[data-kind=main] > .sec .lead").count() == 0:
            errors.append(f"[{tag}] 定型文が消えた")
        no_overflow(page, tag, "つなぎの文", errors)
        shot(page, shots_dir, f"{tag}-detail")
        ctx.close()


# ============================================================ 4. 動画の P1〜P7（段2a）

SYN = os.path.join(ROOT, "testdata", "synthetic")
sys.path.insert(0, os.path.join(ROOT, "analysis", "tests"))
import synthetic_swing as SYNTH  # noqa: E402

FAKE_POSE_JS = """(() => {
  const POSE = %s;
  const PS = ["P1", "P2", "P3", "P4", "P5", "P6", "P7"];
  // 合成の動画は1つの P を 0.5 秒ずつ映す。時刻 → そのコマに写っている P の棒人間の点（MediaPipe の代わり）
  window.__FAKE_POSE = (m) => { const i = Math.max(0, Math.min(6, Math.floor((m.t + 1e-6) / 0.5))); return POSE[m.view][PS[i]]; };
})();"""


def canvas_click(page, sel: str, x: float, y: float, w: int = SYNTH.W, h: int = SYNTH.H) -> None:
    box = page.locator(sel).bounding_box()
    page.mouse.click(box["x"] + x / w * box["width"], box["y"] + y / h * box["height"])


def tap_points(page, pts: list[tuple[float, float]], p: str) -> None:
    # 前のコマの画面が残っているあいだに押さないよう、見出しがそのコマになるのを待つ
    page.wait_for_selector(f"[data-body] h2:text-matches('^{p} ')", timeout=15000)
    page.wait_for_selector("[data-stage] canvas.tapimg")
    for x, y in pts:
        canvas_click(page, "[data-stage] canvas.tapimg", x, y)
    page.click("[data-done]")


def seed_swings(base: str, sid: int, n: int, faults=("p2_inside",)) -> None:
    """同じ記録に、API で合成のスイングを足す（画面で選ぶのと同じ口）。"""
    for _ in range(n):
        sw = SYNTH.swing("dtl", faults=faults)
        s = call("POST", f"{base}/sessions/{sid}/swings", {"view": "dtl", "club": "7 Iron", "club_class": "iron", "fps": 60, "fps_source": "container",
                                                            "width": SYNTH.W, "height": SYNTH.H, "ball": sw["ball"]})
        frames = [{"checkpoint": p, "t": f["t"], "frame": int(f["t"] * 60), "landmarks": f["landmarks"], "taps": f["taps"]} for p, f in sw["frames"].items()]
        call("PUT", f"{base}/swings/{s['id']}/frames", {"frames": frames, "missing": []})


def check_video(browser, root: str, base: str, pid: int, shots_dir: str, errors: list[str]) -> None:
    """合成の棒人間の動画で: 向きと番手 → P1〜P7 を手で選ぶ → ボールとクラブのタップ（拡大鏡・1画素のボタン）→ チェック一覧・項目1つ。"""
    tag = "video"
    ses = call("POST", f"{base}/sessions", {"player_id": pid, "date": "2026-09-28", "location": "練習場"})
    sid = ses["id"]
    ctx, page = new_page(browser, 390, 844, errors, tag, pid)
    pose = SYNTH.pose_json()
    ctx.add_init_script(FAKE_POSE_JS % json.dumps({"dtl": pose["dtl"], "fo": pose["fo"]}))
    sent: list[tuple[str, str, int]] = []
    page.on("request", lambda r: sent.append((r.method, r.url, len(r.post_data_buffer or b""))) if r.method in ("POST", "PUT", "PATCH") else None)
    # 記録の画面から入る（入れ先の記録を渡す）
    goto(page, root, "/record/2026-09-28", "[data-video]")
    href = page.get_attribute("[data-video]", "href")
    if f"session={sid}" not in (href or ""):
        errors.append(f"[{tag}] 記録の「動画を入れる」が入れ先の記録を渡さない: {href}")
    shot(page, shots_dir, f"{tag}-record")
    page.click("[data-video]")
    page.wait_for_selector("[data-file]", state="attached")
    no_overflow(page, tag, "向きと番手", errors)
    targets(page, tag, "向きと番手", errors)
    shot(page, shots_dir, f"{tag}-1-setup")
    page.set_input_files("[data-file]", os.path.join(SYN, "stick_dtl.webm"))
    page.wait_for_selector("[data-vframe] video", timeout=30000)
    chips = page.locator("[data-pchips] .pchip")
    need = ["P1", "P2", "P3", "P4", "P5", "P6", "P7"]
    got = [c.get_attribute("data-p") for c in chips.all()]
    if got[:7] != need:
        errors.append(f"[{tag}] P1〜P7 のチップが7つ並ばない: {got}")
    if page.locator("[data-pinfo] svg").count() != 1:
        errors.append(f"[{tag}] 見本の線画が出ない")
    no_overflow(page, tag, "P を選ぶ", errors)
    targets(page, tag, "P を選ぶ", errors)
    shot(page, shots_dir, f"{tag}-2-choose")
    for i in range(7):
        page.click("[data-ok]")
        if i < 6:
            page.click('[data-mv="+0.5s"]')
            page.wait_for_timeout(120)
    page.wait_for_selector("[data-go-taps]")
    done = page.locator("[data-pchips] .pchip.done").count()
    if done != 7:
        errors.append(f"[{tag}] 選んだ P が7つにならない: {done}")
    page.click("[data-go-taps]")
    # ボールの両端: 拡大鏡が出て、1画素のボタンで動かせる
    page.wait_for_selector("[data-stage] canvas.tapimg")
    box = page.locator("[data-stage] canvas.tapimg").bounding_box()
    bx, by = SYNTH.pose_json()["dtl_ball"][0]
    page.mouse.move(box["x"] + bx / SYNTH.W * box["width"], box["y"] + by / SYNTH.H * box["height"])
    page.mouse.down()
    if page.locator("[data-loupe]").is_hidden():
        errors.append(f"[{tag}] 押しているあいだ拡大鏡が出ない")
    shot(page, shots_dir, f"{tag}-3-loupe", full=False)
    page.mouse.up()
    pts = json.loads(page.get_attribute("[data-body]", "data-pts") or "{}")
    before = pts.get("b1")
    page.click("[data-sel] [data-pi='0']")
    page.click('[data-nd="1,0"]')
    pts = json.loads(page.get_attribute("[data-body]", "data-pts") or "{}")
    if not before or not pts.get("b1") or pts["b1"][0] != before[0] + 1 or pts["b1"][1] != before[1]:
        errors.append(f"[{tag}] 1画素のボタンで点が動かない: {before} → {pts.get('b1')}")
    page.click('[data-nd="-1,0"]')
    targets(page, tag, "タップ", errors)
    no_overflow(page, tag, "タップ", errors)
    page.click("[data-sel] [data-pi='1']")
    b2 = SYNTH.pose_json()["dtl_ball"][1]
    canvas_click(page, "[data-stage] canvas.tapimg", b2[0], b2[1])
    shot(page, shots_dir, f"{tag}-3-ball")
    page.click("[data-done]")
    taps = SYNTH.pose_json()["dtl_taps"]
    tap_points(page, [taps["P2"]["grip"], taps["P2"]["head"]], "P2")
    page.wait_for_selector("[data-more]")
    page.click("[data-more]")
    for p in ("P1", "P3", "P4", "P5", "P6", "P7"):
        tap_points(page, [taps[p]["grip"], taps[p]["head"]], p)
    page.wait_for_function(f"location.hash === '#/session/{sid}/check'", timeout=60000)
    page.wait_for_selector("[data-summary]", timeout=30000)
    page.wait_for_load_state("networkidle")
    # 送ったもの: 動画そのものは送らない（大きな送信が無い）。全解像度のコマも送らない（Go が長辺 360px を超える画像を断る）
    big = [x for x in sent if x[2] > 3 * 1024 * 1024]
    if big:
        errors.append(f"[{tag}] 大きな送信がある（動画を送っていないか）: {big}")
    sw = call("GET", f"{base}/sessions/{sid}/swings")[0]
    if not (55 <= (sw.get("fps") or 0) <= 65) or sw.get("fps_source") != "playback":
        errors.append(f"[{tag}] 再生して測った fps が合わない: {sw.get('fps')} {sw.get('fps_source')}")
    if "perf" not in (sw.get("capture") or {}) or "pose_ms_per_frame" not in sw["capture"]["perf"]:
        errors.append(f"[{tag}] 処理の時間を残していない: {sw.get('capture')}")
    one = call("GET", f"{base}/swings/{sw['id']}")
    fr = {f["checkpoint"]: f for f in one["frames"]}
    if sorted(fr) != sorted(need) or not all(fr[p]["has_thumb"] and len(fr[p]["landmarks"]) == 33 for p in need):
        errors.append(f"[{tag}] P1〜P7 のコマ・点・サムネイルが揃わない: {sorted(fr)}")
    if abs(fr["P2"]["taps"]["head"][0] - taps["P2"]["head"][0]) > 3:
        errors.append(f"[{tag}] P2 のクラブの先のタップがずれて届いた: {fr['P2']['taps']}")
    # チェック一覧（1本だけ）: まずここは出さず、理由を書く。P のチップ7つ。範囲の中は畳む
    if page.locator("[data-pstrip] [data-pchip]").count() != 7:
        errors.append(f"[{tag}] チェックの P のチップが7つでない")
    if not page.locator("[data-nofocus]").count() or "一本だけ" not in page.inner_text("[data-nofocus]"):
        errors.append(f"[{tag}] 一本だけのときに、課題にしない理由が出ない")
    if page.locator("details[data-fold=in][open]").count():
        errors.append(f"[{tag}] 範囲の中が畳まれていない")
    unk = page.inner_text("details[data-fold=unknown] > summary")
    if "見た目の評価はまだ" not in unk or "正面から撮ると見られます" not in unk:
        errors.append(f"[{tag}] 判断できないの理由の束ねが違う: {unk}")
    plain_first(page, "#view", tag, "チェック一覧（1本）", errors)
    no_overflow(page, tag, "チェック一覧", errors)
    targets(page, tag, "チェック一覧", errors)
    shot(page, shots_dir, f"{tag}-4-check-one")
    # 同じ記録に2本足す（多数で決め、課題を一つ選ぶ）
    seed_swings(base, sid, 2)
    page.reload()
    page.wait_for_selector("[data-card=focus]", timeout=30000)
    if page.get_attribute("[data-card=focus]", "data-item") != "iron.p2.dtl.head_vs_hands":
        errors.append(f"[{tag}] まずここが P2 のクラブの先にならない: {page.get_attribute('[data-card=focus]', 'data-item')}")
    if "クラブが体の内側に引かれています" not in page.inner_text("[data-card=focus]"):
        errors.append(f"[{tag}] まずここの文が違う")
    if page.locator("[data-card=focus] img").count() != 1:
        errors.append(f"[{tag}] まずここにコマの写真が無い")
    plain_first(page, "#view", tag, "チェック一覧（3本）", errors)
    no_overflow(page, tag, "チェック一覧（3本）", errors)
    shot(page, shots_dir, f"{tag}-5-check-focus")
    page.click("[data-card=focus]")
    page.wait_for_selector("[data-why]")
    plain_first(page, "#view", tag, "項目1つ", errors)
    targets(page, tag, "項目1つ", errors)
    shot(page, shots_dir, f"{tag}-6-item")
    page.click("[data-why]")
    page.wait_for_selector(".sheet[data-sheet=why]")
    txt = page.inner_text(".sheet[data-sheet=why]")
    for need_txt in ("p38-52", "3本中3本が範囲の外", "スイングの前半ほど", "代わりの点", "まだ人がガイドと突き合わせていません"):
        if need_txt not in txt:
            errors.append(f"[{tag}] なぜそう言える？に {need_txt!r} が無い: {txt[:200]!r}")
    page.click(".sheet[data-sheet=why] [data-numbers] > summary")
    if "誤差" not in page.inner_text(".sheet[data-sheet=why]"):
        errors.append(f"[{tag}] 数字を見るに誤差が無い")
    shot(page, shots_dir, f"{tag}-7-why")
    page.keyboard.press("Escape")
    page.wait_for_selector(".sheet", state="detached")
    # 撮り方ガイド
    goto(page, root, "/guide/dtl", "[data-other]")
    page.wait_for_function("document.querySelector('[data-other]').textContent.includes('件')", timeout=15000)
    shot(page, shots_dir, f"{tag}-8-guide")
    ctx.close()
    # ファイルの中の fps（mp4 の moov が末尾）: mp4box で 240 と読む
    ctx, page = new_page(browser, 390, 844, errors, tag + "-mp4", pid)
    goto(page, root, "/record")
    import base64 as _b64
    data = _b64.b64encode(open(os.path.join(SYN, "stick_dtl_240.mp4"), "rb").read()).decode()
    info = page.evaluate("""async (d) => { const b = Uint8Array.from(atob(d), (c) => c.charCodeAt(0));
      return await Video.containerInfo(new File([b], 'swing.mp4', {type: 'video/mp4'})); }""", data)
    if not info or abs(info.get("fps", 0) - 240) > 1 or info.get("samples", 0) < 100:
        errors.append(f"[{tag}] mp4 の fps を読めない: {info}")
    # 同梱の MediaPipe が読めて動く（白いコマなので人は見つからない＝ found が false で成功）
    try:
        r = page.evaluate("Video.selfTest()")
        if not r or r.get("found"):
            errors.append(f"[{tag}] MediaPipe の自己確認: {r}")
        else:
            print("MediaPipe:", r)
    except Exception as e:  # noqa: BLE001
        errors.append(f"[{tag}] 同梱の MediaPipe が動かない: {e}")
    ctx.close()


def tiny_png() -> bytes:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + chunk(b"IEND", b"")


def big_photo_png(path: str, w: int = 3000, h: int = 2400) -> int:
    raw = b"".join(b"\x00" + os.urandom(w * 3) for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(data)
    return len(data)


def fake_screenshot_answer(path: str) -> None:
    real = os.path.join(ROOT, "testdata", "real", "2026-09-17")

    def table_from_tsv(name: str, club: str) -> dict:
        rows = [r.split("\t") for r in open(os.path.join(real, name), encoding="utf-8").read().strip("\n").split("\n")]
        body = [r for r in rows[2:] if r[0] not in ("Average", "Consistency")]
        avg = next(r for r in rows if r[0] == "Average")
        return {"club": club, "columns": rows[0][1:], "units": rows[1][1:], "row_labels": [r[0] for r in body],
                "rows": [r[1:] for r in body], "average": avg[1:], "consistency": None}
    ok = table_from_tsv("6i.tsv", "6Iron")
    bad = table_from_tsv("9i.tsv", "9Iron")
    bad["rows"][1][3] = "37.6L"  # R を L と読み違えた
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"tables": [ok, bad]}, f)


def main() -> None:
    shots_dir = os.environ.get("SHOTS_DIR") or tempfile.mkdtemp(prefix="ui-")
    os.makedirs(shots_dir, exist_ok=True)
    errors: list[str] = []
    static_checks(errors)
    fake = os.path.join(shots_dir, "fake_screenshot.json")
    fake_screenshot_answer(fake)
    with Services(fake_screenshot=fake) as sv, sync_playwright() as pw:
        exe = os.environ.get("PLAYWRIGHT_CHROMIUM")
        browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        check_first_run(browser, sv.root, shots_dir, errors)
        check_pending_hand(browser, shots_dir, errors)
        me = call("GET", f"{sv.base}/players")[0]
        real_id = seed_real(sv.base, me["id"])
        yday = call("POST", f"{sv.base}/sessions", {"player_id": me["id"], "date": "2026-09-26", "location": "練習場"})
        today = call("POST", f"{sv.base}/sessions", {"player_id": me["id"], "date": "2026-09-27", "location": "練習場"})
        import_csv(sv.base, yday["id"], "trackman_dummy_yesterday.csv")
        import_csv(sv.base, today["id"], "trackman_dummy_session.csv")
        lefty = call("POST", f"{sv.base}/players", {"name": "Lefty", "handedness": "L"})
        lefty_id = seed_real(sv.base, lefty["id"])
        for tag, w, h in (("phone", 390, 844), ("pc", 1280, 900)):
            ctx, page = new_page(browser, w, h, errors, tag, me["id"])
            check_home(page, sv.root, sv.base, me["id"], shots_dir, tag, errors)
            check_diag(page, sv.root, sv.base, real_id, shots_dir, tag, errors)
            check_detail(page, sv.root, sv.base, real_id, shots_dir, tag, errors)
            check_dummy(page, sv.root, sv.base, today["id"], yday["id"], shots_dir, tag, errors)
            ctx.close()
            check_record(browser, sv.root, sv.base, shots_dir, tag, w, h, errors)
            check_plan(browser, sv.root, sv.base, shots_dir, tag, w, h, errors)
        check_home_fold(browser, sv.root, me["id"], shots_dir, errors)
        ctx, page = new_page(browser, 360, 780, errors, "phone-L", lefty["id"])
        check_detail(page, sv.root, sv.base, lefty_id, shots_dir, "phone-L", errors, hand="L")
        ctx.close()
        check_settings(browser, sv.root, sv.base, me["id"], real_id, shots_dir, errors)
        check_video(browser, sv.root, sv.base, me["id"], shots_dir, errors)
        check_routes(browser, sv.root, me["id"], real_id, shots_dir, errors)
        check_narrative(browser, shots_dir, errors)
        browser.close()
    print("スクリーンショット:", shots_dir)
    if errors:
        sys.exit("失敗:\n  " + "\n  ".join(errors))
    print("OK")


if __name__ == "__main__":
    main()
