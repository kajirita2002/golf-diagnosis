"""画面を本物のブラウザで操作して確かめる（Playwright）。

  - 4つのタブ（診断・1球ずつ・前回と比べる・実験）が、実際に押して中身を出すか
  - スマホ幅（360px）でページが横に溢れないか（表は枠の中だけで横に動く）
  - JavaScript のエラーが1つも出ないか
  - 診断の「解説」（docs/DESIGN_coaching.md §11 の画面の確認）: 360px で横に溢れない・図の data-shot の数が
    描いた球数と一致・F3 の「この図にない球」が coverage と一致・F5 でヒール（負）の点が右半分（左打ちは左半分）・
    fill="# が無い・点をタップして1球の面が出る・窓の文が帯の形で埋まる・「この仮説で実験を始める」で実験タブに入る
スクリーンショットは SHOTS_DIR（既定は一時ディレクトリ）に置く。

使い方: python3 scripts/ui_check.py（playwright が要る。PLAYWRIGHT_CHROMIUM で実行ファイルを指定できる）
"""

from __future__ import annotations

import json
import os
import re
import struct
import sys
import tempfile
import zlib

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e import Services, call, seed, seed_real  # noqa: E402


def no_overflow(page, tag: str, where: str, errors: list[str]) -> None:
    """ページ全体が横に溢れていないか。表は .scroll の枠の中だけで横に動けばよい。"""
    over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    if over > 0:
        errors.append(f"[{tag}] {where}: ページが横に {over}px 溢れている")


def check(page, root: str, shots_dir: str, tag: str, errors: list[str]) -> None:
    page.goto(root + "/")
    page.wait_for_selector("#work:not([hidden])")
    # 新しい順なので、先頭は今日のセッション
    page.click("#analyzeBtn")
    page.wait_for_selector("#repOut .rcard")
    page.click("#diagTabs button[data-sub=num]")
    page.wait_for_selector("#anOut .finding")
    text = page.inner_text("#anOut")
    for want in ("打点", "フェース・トゥ・パス", "Good"):
        if want not in text:
            errors.append(f"[{tag}] 診断に「{want}」が無い")
    no_overflow(page, tag, "診断", errors)
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-1-diag.png"), full_page=True)

    page.click("button[data-tab=shots]")
    rows = page.locator("#shotTable tr[data-id]").count()
    if rows != 30:
        errors.append(f"[{tag}] 1球ずつの行が {rows}")
    no_overflow(page, tag, "1球ずつ", errors)
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-2-shots.png"), full_page=True)

    page.click("button[data-tab=compare]")
    page.click("#cmpBtn")
    page.wait_for_selector("#cmpOut .finding")
    if "フェース・トゥ・パス" not in page.inner_text("#cmpOut"):
        errors.append(f"[{tag}] 比較の説明にフェース・トゥ・パスが無い")
    no_overflow(page, tag, "比較", errors)
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-3-compare.png"), full_page=True)

    page.click("button[data-tab=exp]")
    page.fill("#exHyp", "フェースが開いてプッシュフェードになる")
    page.fill("#exInt", "左手甲を目標に向けたまま振る")
    page.click("#exSave")
    box = page.locator("[data-ex]").last
    box.wait_for()
    for kind, a, b in (("baseline", 1, 10), ("intervention", 11, 20)):
        box = page.locator("[data-ex]").last
        box.locator("[data-f=kind]").select_option(kind)
        box.locator("[data-f=from]").fill(str(a))
        box.locator("[data-f=to]").fill(str(b))
        box.locator("button[data-act=block]").click()
        page.wait_for_function(f"document.querySelectorAll('[data-ex]')[document.querySelectorAll('[data-ex]').length-1].innerText.includes('#{a}〜{b}')")
    box = page.locator("[data-ex]").last
    box.locator("button[data-act=eval]").click()
    box.locator("[data-f=out] .finding").first.wait_for()
    if "はっきり効いた" not in box.inner_text():
        errors.append(f"[{tag}] 実験の評価が「はっきり効いた」にならない: {box.inner_text()}")
    no_overflow(page, tag, "実験", errors)
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-4-exp.png"), full_page=True)


def open_report(page, root: str, session_id: int, player_id: int | None = None) -> None:
    """セッションを選んで「分析する」を押し、解説の節を全部開く。"""
    page.goto(root + "/")
    page.wait_for_selector("#work:not([hidden])")
    if player_id is not None:
        page.select_option("#player", str(player_id))
        page.wait_for_function(f"[...document.querySelector('#session').options].some(o => o.value === '{session_id}')")
    page.select_option("#session", str(session_id))
    page.wait_for_function("document.querySelectorAll('#shotTable tr[data-id]').length === 57")
    page.click("button[data-tab=diag]")
    page.click("#analyzeBtn")
    page.wait_for_selector("#repOut .rcard")
    page.evaluate("() => document.querySelectorAll('#repOut details').forEach((d) => { d.open = true; })")


# 図の点を数える JS。範囲のカードの直下の節にある図だけを見る（畳んだ1本の短い版の中は数えない）
COUNT_JS = """(scope) => {
  const card = [...document.querySelectorAll('#repOut article.rcard')].find((a) => a.dataset.scope === scope);
  if (!card) return null;
  const out = {};
  for (const f of card.querySelectorAll(':scope > .sec figure.fig')) {
    const id = f.dataset.fig;
    if (!(id in out)) out[id] = f.querySelectorAll('[data-shot]').length;
  }
  return out;
}"""

# F5 の点が図の左右どちらにあるか（負＝ヒール）。図の真ん中より右なら right
HEEL_JS = """(scope) => {
  const card = [...document.querySelectorAll('#repOut article.rcard')].find((a) => a.dataset.scope === scope);
  const f = card && card.querySelector(':scope > .sec figure.fig[data-fig=F5]');
  if (!f) return null;
  const svg = f.querySelector('svg'), r = svg.getBoundingClientRect(), mid = r.left + r.width / 2;
  return [...f.querySelectorAll('g[data-shot]')].map((g) => {
    const b = g.querySelector('.g-hit').getBoundingClientRect();
    return [Number(g.dataset.offset), b.left + b.width / 2 > mid ? 'right' : 'left'];
  });
}"""


def check_report(page, base: str, session_id: int, shots_dir: str, tag: str, errors: list[str], hand: str = "R") -> None:
    """解説（§11 の画面の確認）。答えは同じ API の /report から取って突き合わせる（手で写した数と比べない）。"""
    rep = call("GET", f"{base}/sessions/{session_id}/report")["report"]
    mains = [x for x in rep["scopes"] if x["kind"] == "main"]
    no_overflow(page, tag, f"解説（{hand}）", errors)
    text = page.inner_text("#repOut")
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
        # 一番多い枠が同数の範囲（5番ウッド）は典型の1球を決めないので、F4 が無い
        rep4 = 1 if (sc["figures"].get("F4") or {}).get("representative") else 0
        if (got.get("F4") or 0) != rep4:
            errors.append(f"[{tag}] {sc['scope_id']} の F4 の data-shot が {got.get('F4')}（{rep4}）")
        # 左打ちでも打点のトゥ／ヒールは反転しない。ヒールを描く側だけが入れ替わる
        heel_side = "right" if hand == "R" else "left"
        for off, side in page.evaluate(HEEL_JS, sc["scope_id"]) or []:
            if off < 0 and side != heel_side:
                errors.append(f"[{tag}] {sc['scope_id']} の F5 でヒール {off}mm の点が {side} にある（{hand}）")
                break
    # 畳んだ範囲（1本の短い版）には図を出さない
    folded_figs = page.evaluate("() => document.querySelectorAll('#repOut .folded figure.fig').length")
    if folded_figs:
        errors.append(f"[{tag}] 1本の短い版に図が {folded_figs} 枚出ている")
    iron = next(x for x in rep["scopes"] if x["scope_id"] == "group:iron")
    card = page.locator("#repOut article.rcard[data-scope='group:iron']")
    missing = iron["facts"]["group:iron/coverage.face.missing"]["value"]
    note = card.locator(":scope > .sec figure.fig[data-fig=F3] .fig-note[data-not-shown]").first
    if note.get_attribute("data-not-shown") != str(missing) or f"この図にない球: {missing}" not in note.inner_text():
        errors.append(f"[{tag}] F3 の「この図にない球」が coverage（{missing}）と合わない: {note.inner_text()!r}")
    # ⑦のステップ: 1ブロックは12球まで（§8.3）・「動かさないもの」は窓の数字で埋まっている
    chips = page.evaluate("() => [...document.querySelectorAll('#repOut .cand .blocks .chip')].map((c) => Number((c.textContent.match(/\\d+/) || [0])[0]))")
    if chips and max(chips) > 12:
        errors.append(f"[{tag}] ⑦の組み方に12球を超えるブロックがある: {max(chips)}")
    gtext = card.locator(".cand[data-step='2'] .kv").evaluate_all(
        "(xs) => (xs.find((x) => x.firstElementChild && x.firstElementChild.textContent === '動かさないもの') || {}).textContent || ''")
    edge = "−1.9°"  # 左打ちは右打ちのデータを左右反転して入れているので、表示（実際の向き）は右打ちと同じになる
    if "{band:" in gtext or edge not in gtext:
        errors.append(f"[{tag}] ⑦のステップ2に「動かさないもの」（窓の端 {edge}）が無い: {gtext!r}")
    # まとめの F1 の横軸（左右 ÷ キャリー）は % の目盛り（0.25 を「0.3」と丸めて書かない）
    f1ticks = card.locator(":scope > .sec figure.fig[data-fig=F1]").first.locator("text.t11").all_text_contents()
    if not any(t.endswith("%") for t in f1ticks):
        errors.append(f"[{tag}] まとめの F1 の横軸が % の目盛りになっていない: {f1ticks}")
    # 帯を使わない種類（5番ウッド）は帯を描かず、理由を書く
    wood = page.locator("#repOut article.rcard[data-scope='club:5 Wood'] > .sec figure.fig[data-fig=F3]").first
    if wood.locator(".g-band").count() or "帯を描いていません" not in wood.inner_text():
        errors.append(f"[{tag}] 5番ウッドの F3 に帯が描かれている／理由が無い")
    if hand != "R":
        return
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-5a-real-report.png"), full_page=True)

    # 点をタップすると「1球ずつ」と同じ面が下から出る
    pt = card.locator(":scope > .sec figure.fig[data-fig=F1] g[data-shot]").first
    pt.scroll_into_view_if_needed()
    pt.locator(".g-hit").click(force=True)
    sheet = page.locator("#shotSheet")
    try:
        sheet.wait_for(state="visible", timeout=3000)
        sid = int(sheet.get_attribute("data-id"))
        seq = next(p["seq"] for p in iron["figures"]["F1"]["points"] if p["shot_id"] == sid)
        if f"#{seq}" not in sheet.inner_text():
            errors.append(f"[{tag}] 1球の面に #{seq} が無い")
        no_overflow(page, tag, "1球の面", errors)
        page.screenshot(path=os.path.join(shots_dir, f"{tag}-5b-sheet.png"))
        sheet.locator("button[data-act=goto]").click()
        if page.locator("[data-pane=shots]").is_hidden() or page.locator(f"#shotTable tr.sel[data-id='{sid}']").count() != 1:
            errors.append(f"[{tag}] 1球の面から「1球ずつ」の該当の球へ飛べない")
    except Exception as e:  # noqa: BLE001
        errors.append(f"[{tag}] 図の点をタップしても1球の面が出ない: {e}")
    page.click("button[data-tab=diag]")

    # ⑧ の「打点が - の球」から1球ずつへ
    card.locator(":scope > .sec[data-sec=s8] button.jump").first.click()
    if page.locator("#shotTable tr.sel").count() != 1:
        errors.append(f"[{tag}] ⑧の打点が「-」の球から1球ずつへ飛べない")
    page.click("button[data-tab=diag]")

    # ⑦「この仮説で実験を始める」→ 実験タブに仮説・指標・目標・クラブが入る
    card.locator("button[data-act=startexp][data-which=now]").click()
    got = {k: page.input_value(k) for k in ("#exHyp", "#exMetric", "#exGoal", "#exClub")}
    if not (got["#exHyp"].startswith("9 Iron") and got["#exMetric"] == "impact_offset" and got["#exGoal"] == "reduce_abs" and got["#exClub"] == "9 Iron"):
        errors.append(f"[{tag}] 「この仮説で実験を始める」で実験タブに値が入らない: {got}")
    if page.locator("[data-pane=exp]").is_hidden():
        errors.append(f"[{tag}] 「この仮説で実験を始める」で実験タブが開かない")
    no_overflow(page, tag, "実験（解説から）", errors)
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-5c-exp-from-report.png"))
    page.click("button[data-tab=diag]")


def check_real(page, root: str, base: str, session_id: int, shots_dir: str, tag: str, errors: list[str]) -> None:
    """実データのセッションで、解説と、数字の段（極端なヒール・ミスヒットの候補・アイアンのまとめ）が画面に出るか。"""
    open_report(page, root, session_id)
    check_report(page, base, session_id, shots_dir, tag, errors)
    page.click("#diagTabs button[data-sub=num]")
    page.wait_for_selector("#anOut .finding")
    text = page.inner_text("#anOut")
    for want in ("アイアン（まとめ）", "ネック寄り", "ミスヒット", "単回帰"):
        if want not in text:
            errors.append(f"[{tag}] 実データの診断に「{want}」が無い")
    if re.search(r"\d+%は", text):
        errors.append(f"[{tag}] 数字の段に share の読み違いの文（◯%はフェース）が残っている")
    no_overflow(page, tag, "実データの診断", errors)
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-5-real-diag.png"), full_page=True)
    page.click("button[data-tab=shots]")
    if page.locator("#shotTable .chip", has_text="除外候補").count() != 5:
        errors.append(f"[{tag}] 1球ずつの「除外候補」が5つでない")
    no_overflow(page, tag, "実データの1球ずつ", errors)
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-6-real-shots.png"), full_page=True)


def tiny_png() -> bytes:
    """1×1 の本物の PNG（中身で形式を判定するので、見た目だけの偽物では通らない）。"""
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + chunk(b"IEND", b"")


def big_photo_png(path: str, w: int = 3000, h: int = 2400) -> int:
    """スマホの写真くらいの大きさ（5MB 超）の本物の PNG。雑音なので圧縮で小さくならない。"""
    raw = b"".join(b"\x00" + os.urandom(w * 3) for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(data)
    return len(data)


def fake_screenshot_answer(path: str) -> None:
    """偽の Claude の答え: 実データの6番（正しく読めた）と、9番の1か所を読み違えたもの。"""
    real = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "testdata", "real", "2026-09-17")

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


def check_screenshot(page, root: str, shots_dir: str, tag: str, errors: list[str]) -> None:
    """レポートを開くリンク → スクショを入れる → 検算 → 取り込む。"""
    page.goto(root + "/")
    page.wait_for_selector("#work:not([hidden])")
    # 取り込み先は新しいセッション（ほかの確認の件数を変えない）
    page.fill("#nsDate", "2026-01-01")  # 古い日付にして、ほかの確認が開く「最新のセッション」を変えない
    page.fill("#nsLoc", f"スクショ {tag}")
    page.click("#nsSave")
    page.wait_for_function(f"document.querySelector('#session').selectedOptions[0].textContent.includes('スクショ {tag}')")
    before = page.locator("#shotTable tr[data-id]").count()
    page.fill("#tmUrl", "https://web-dynamic-reports.trackmangolf.com/?a=26828541-c2b0-f111-8234-f42679e923bf")
    # 本当に開くと TrackMan へ出ていく（この環境では届かない）ので、開こうとした URL だけを捕まえる
    # 文字列に => があると Playwright が関数として1回呼んでしまうので、関数の形で渡す
    page.evaluate("() => { window.__opened = []; window.open = (u) => { window.__opened.push(u); return null; }; }")
    page.click("#tmOpen")
    page.wait_for_function("window.__opened.length === 1")
    url = page.evaluate("window.__opened[0]")
    if page.inner_text("#tmErr"):
        errors.append(f"[{tag}] レポートを開くボタンで失敗: {page.inner_text('#tmErr')}")
    if "v=clubData" not in url or "26828541-c2b0-f111-8234-f42679e923bf" not in url:
        errors.append(f"[{tag}] レポートを開くリンクが違う: {url}")

    png = os.path.join(shots_dir, "tiny.png")
    with open(png, "wb") as f:
        f.write(tiny_png())
    page.set_input_files("#shotFile", png)
    page.wait_for_function("document.querySelectorAll('.shotcard').length === 2")
    cards = page.locator(".shotcard")
    if "検算OK" not in cards.nth(0).inner_text():
        errors.append(f"[{tag}] 正しく読めた表が検算OKにならない")
    if "要確認" not in cards.nth(1).inner_text() or "★ Side" not in cards.nth(1).inner_text():
        errors.append(f"[{tag}] 読み違えた表で Side の列が★にならない")
    if cards.nth(0).locator("[data-f=club]").input_value() != "6 Iron":
        errors.append(f"[{tag}] クラブ名が「6 Iron」にそろわない")
    no_overflow(page, tag, "スクショ", errors)
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-7-screenshot.png"), full_page=True)

    # 読み違えた表を直して再検算 → OK になる
    ta = cards.nth(1).locator("[data-f=tsv]")
    ta.fill(ta.input_value().replace("37.6L", "37.6R"))
    cards.nth(1).locator("button[data-act=recheck]").click()
    page.wait_for_function("document.querySelectorAll('.shotcard')[1].innerText.includes('検算OK')")

    # スマホの大きい写真を「CSV」のボタンから選んでも、縮めてスクショとして読む
    photo = os.path.join(shots_dir, "photo.png")
    size = big_photo_png(photo)
    if size <= 5 * 1024 * 1024:
        errors.append(f"[{tag}] 試験用の写真が小さすぎる（{size}）")
    page.set_input_files("#csv", photo)
    try:
        page.wait_for_function("document.querySelectorAll('.shotcard').length === 4", timeout=60000)
    except Exception:
        errors.append(f"[{tag}] 大きい写真を CSV のボタンから選んでも読めない: {page.inner_text('#shotOut')[-300:]!r}")
    if page.input_value("#csv") != "":
        errors.append(f"[{tag}] 写真が CSV の欄に残っている")
    cards = page.locator(".shotcard")

    cards.nth(0).locator("button[data-act=import]").click()
    try:
        page.wait_for_function(f"document.querySelectorAll('#shotTable tr[data-id]').length === {before + 8}", timeout=10000)
    except Exception:
        errors.append(f"[{tag}] スクショの表を取り込めない: {cards.nth(0).locator('[data-f=err]').inner_text()!r} / 行 {page.locator('#shotTable tr[data-id]').count()}（前 {before}）")


def check_continuation(page, root: str, shots_dir: str, tag: str, errors: list[str]) -> None:
    """見出しの無い続きの画像（2026-09-29 の実機: 5W の 12〜23 球目）をつなげる・消す。

    読み取りの答えだけ差し替える（偽の Claude は1通りしか返せない）。検算は本物のサーバーがやる。
    選んだ順が「続き → 見出しのある表」でも、番号順につながること。
    """
    real = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "testdata", "real", "2026-09-17")
    lines = open(os.path.join(real, "5w.tsv"), encoding="utf-8").read().strip("\n").split("\n")
    head, units, body = lines[0], lines[1], [l for l in lines[2:] if not l.startswith(("Average", "Consistency"))]
    avg = next(l for l in lines if l.startswith("Average"))
    first = "\n".join([head, units, *body[:11]]) + "\n"
    cont = "\n".join(["#", "", *body[11:], avg]) + "\n"  # 見出しも単位も写っていない
    answers = [
        {"tables": [{"club": None, "tsv": cont, "check": {"ok": False, "continuation": True, "problems": ["続き"], "columns": []}}]},
        {"tables": [{"club": "5Wood", "tsv": first, "check": {"ok": False, "problems": ["Average"], "columns": []}}]},
        {"tables": [{"club": None, "tsv": cont, "check": {"ok": False, "continuation": True, "problems": ["続き"], "columns": []}}]},
    ]
    calls = []

    def answer(route):
        calls.append(1)
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({**answers[len(calls) - 1], "usage": {"cost_usd": 0}}))

    page.goto(root + "/")
    page.wait_for_selector("#work:not([hidden])")
    page.route("**/v1/screenshot", answer)
    a, b = os.path.join(shots_dir, "cont-a.png"), os.path.join(shots_dir, "cont-b.png")
    for p in (a, b):
        with open(p, "wb") as f:
            f.write(tiny_png())
    page.set_input_files("#shotFile", [a, b])
    try:
        page.wait_for_function("document.querySelector('#shotMsg').textContent.includes('読み取りました')"
                               " && document.querySelectorAll('.shotcard').length === 1"
                               " && document.querySelector('.shotcard').innerText.includes('検算OK')", timeout=15000)
    except Exception:
        errors.append(f"[{tag}] 続きの画像がつながらない: {page.inner_text('#shotOut')[:400]!r}")
        return
    card = page.locator(".shotcard").first
    text = card.inner_text()
    if "23球" not in text or "12.〜23." not in text:
        errors.append(f"[{tag}] つなげた表の球数・注記が違う: {text[:300]!r}")
    if card.locator("[data-f=club]").input_value() != "5 Wood":
        errors.append(f"[{tag}] つなげた表のクラブが「5 Wood」にならない")
    tsv = card.locator("[data-f=tsv]").input_value().split("\n")
    if not (tsv[2].startswith("1.") and tsv[13].startswith("12.")):
        errors.append(f"[{tag}] つなげた表が番号順に並んでいない: {tsv[2][:5]!r} {tsv[13][:5]!r}")
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-8-continuation.png"), full_page=True)
    card.locator("button[data-act=drop]").click()
    if page.locator(".shotcard").count() != 0:
        errors.append(f"[{tag}] 「消す」で表が消えない")

    # 見出しのある表が無いときは、続きだと分かる言葉で残し、取り込ませない。消せる。
    page.set_input_files("#shotFile", a)
    page.wait_for_function("document.querySelectorAll('.shotcard').length === 1")
    card = page.locator(".shotcard").first
    if "続き" not in card.inner_text():
        errors.append(f"[{tag}] 見出しの無い画像で「続き」だと言わない: {card.inner_text()[:200]!r}")
    card.locator("button[data-act=import]").click()
    if "見出し" not in card.locator("[data-f=err]").inner_text():
        errors.append(f"[{tag}] 見出しの無い表を取り込もうとして止まらない")
    card.locator("button[data-act=drop]").click()
    if page.locator(".shotcard").count() != 0:
        errors.append(f"[{tag}] 見出しの無い表を「消す」で消せない")
    page.unroute("**/v1/screenshot")


def main() -> None:
    shots_dir = os.environ.get("SHOTS_DIR") or tempfile.mkdtemp(prefix="ui-")
    os.makedirs(shots_dir, exist_ok=True)
    errors: list[str] = []
    fake = os.path.join(shots_dir, "fake_screenshot.json")
    fake_screenshot_answer(fake)
    with Services(fake_screenshot=fake) as sv, sync_playwright() as pw:
        seeded = seed(sv.base)
        real_id = seed_real(sv.base, seeded["player"]["id"])
        lefty = call("POST", f"{sv.base}/players", {"name": "Lefty", "handedness": "L"})
        lefty_id = seed_real(sv.base, lefty["id"])
        exe = os.environ.get("PLAYWRIGHT_CHROMIUM")
        browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        for tag, w, h in (("pc", 1280, 900), ("phone", 360, 780)):
            page = browser.new_page(viewport={"width": w, "height": h})
            page.on("pageerror", lambda e, tag=tag: errors.append(f"[{tag}] JS エラー: {e}"))
            page.on("console", lambda m, tag=tag: m.type == "error" and errors.append(f"[{tag}] console: {m.text}"))
            check(page, sv.root, shots_dir, tag, errors)
            check_real(page, sv.root, sv.base, real_id, shots_dir, tag, errors)
            if tag == "phone":
                open_report(page, sv.root, lefty_id, lefty["id"])
                check_report(page, sv.base, lefty_id, shots_dir, tag + "-L", errors, hand="L")
                page.screenshot(path=os.path.join(shots_dir, f"{tag}-5d-lefty-report.png"), full_page=True)
            check_screenshot(page, sv.root, shots_dir, tag, errors)
            check_continuation(page, sv.root, shots_dir, tag, errors)
            page.close()
        browser.close()
    print("スクリーンショット:", shots_dir)
    if errors:
        sys.exit("失敗:\n  " + "\n  ".join(errors))
    print("OK")


if __name__ == "__main__":
    main()
