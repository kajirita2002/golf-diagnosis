"""画面を本物のブラウザで操作して確かめる（Playwright）。

  - 4つのタブ（診断・1球ずつ・前回と比べる・実験）が、実際に押して中身を出すか
  - スマホ幅（360px）でページが横に溢れないか（表は枠の中だけで横に動く）
  - JavaScript のエラーが1つも出ないか
  - 診断の「解説」（docs/DESIGN_coaching.md §11 の画面の確認）: 360px で横に溢れない・図の data-shot の数が
    描いた球数と一致・F3 の「この図にない球」が coverage と一致・F5 でヒール（負）の点が右半分（左打ちは左半分）・
    fill="# が無い・点をタップして1球の面が出る・窓の文が帯の形で埋まる・「この仮説で実験を始める」で実験タブに入る
  - Claude のつなぎの文（§11 Phase 1c）: off（既定）では「文章はすべて定型文」で頼むボタンが無い。
    on・偽の Claude では、ボタン → ジョブ → 「定型文＋Claude のつなぎ（検証済み）」と料金・つなぎの文が出る・横に溢れない
  - 今日の練習（§11 Phase 1b）: ⑦の「このプランで始める」→ 確かめ済みのドリルが無いときは「まだありません」と
    意識する1点の欄 → 今日の練習が先頭に出る・横に溢れない・下のボタンが 64px 以上・通信を止めても
    ブロックを進められる（開き直しても続きから）・取り込んだ球の帯で境目を1球ずつ動かす・判定と次の手・進捗・あなたの記録
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
from e2e import Services, call, fake_narrative_file, import_practice, practice_tsv, seed, seed_real  # noqa: E402


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
    # REPORT_LLM が off（既定）: 文章は定型文だけで、Claude を頼むボタンは出ない（§11 Phase 1c）
    page.click("#diagTabs button[data-sub=rep]")
    meta = page.locator("#repOut .repmeta[data-gen]")
    if meta.count() != 1 or meta.get_attribute("data-gen") != "template" or "文章はすべて定型文" not in meta.inner_text():
        errors.append(f"[{tag}] off のときの「定型文」の表示が無い")
    if page.locator("#repOut button[data-act=narrate]").count() or page.locator("#repOut .bridge").count():
        errors.append(f"[{tag}] off なのに Claude のつなぎのボタンか文が出ている")
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


FAKE_DRILLS = {"catalog_version": "drills/0.1", "n_total": 1, "n_unchecked": 0, "measures": [], "drills": [
    {"id": "strike.two_balls", "title": "2球並べ（試験用）", "issues": ["strike_heel"], "levers": [], "checked_by": "試験",
     "coach_reviewed": False, "steps": ["a"], "cue_transfer": "向こうにボールがあるつもりで打つ", "diagram_svg": "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><script>1</script></svg>"}]}


def check_plan(browser, root: str, base: str, shots_dir: str, tag: str, w: int, h: int, errors: list[str]) -> None:
    """今日の練習（§8.7・§11 Phase 1b）。Service Worker を生かした別の文脈で、圏外の開き直しまで見る。"""
    who = call("POST", f"{base}/players", {"name": f"Plan-{tag}", "handedness": "R"})
    sid = seed_real(base, who["id"])
    ctx = browser.new_context(viewport={"width": w, "height": h})
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"[{tag}-plan] JS エラー: {e}"))
    page.on("console", lambda m: m.type == "error" and "ERR_INTERNET_DISCONNECTED" not in m.text and "Failed to load resource" not in m.text
            and errors.append(f"[{tag}-plan] console: {m.text}"))
    page.goto(root + "/")
    page.wait_for_selector("#work:not([hidden])")
    page.select_option("#player", str(who["id"]))
    page.wait_for_function(f"document.querySelector('#session').value === '{sid}'")
    page.wait_for_function("document.querySelectorAll('#shotTable tr[data-id]').length === 57")
    # プランが無いときの「今日の練習」
    page.click("#topNav button[data-top=today]")
    if "動いているプランはありません" not in page.inner_text("#todayOut"):
        errors.append(f"[{tag}] プランが無いときの今日の練習の文が無い: {page.inner_text('#todayOut')[:120]!r}")
    no_overflow(page, tag, "今日の練習（プランなし）", errors)
    page.click("#topNav button[data-top=study]")
    page.click("button[data-tab=diag]")
    page.click("#analyzeBtn")
    page.wait_for_selector("#repOut .rcard")
    check_plain_first(page, tag, errors)
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-9g-gist.png"), full_page=True)
    # 診断タブを開いた最初の画面（スクロールしない、見えている範囲だけ）
    page.locator("#repOut article.rcard[data-scope='group:iron']").scroll_into_view_if_needed()
    page.evaluate("() => document.querySelector(\"#repOut article.rcard[data-scope='group:iron']\").scrollIntoView({block: 'start'})")
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-9g0-gist-first.png"), full_page=False)
    page.locator("#repOut article.rcard[data-scope='group:iron'] > .gist > .gb-gap").screenshot(path=os.path.join(shots_dir, f"{tag}-9g1-gist-gap.png"))
    page.locator("#repOut article.rcard[data-scope='group:iron'] > .gist > .gb-now > details.why > summary").click()
    page.evaluate("() => document.querySelector(\"#repOut article.rcard[data-scope='group:iron'] > .gist > .gb-now\").scrollIntoView({block: 'start'})")
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-9h-gist-why.png"), full_page=False)
    page.locator("#repOut article.rcard[data-scope='group:iron'] > .gist > .gb-now > details.why > summary").click()
    page.evaluate("() => document.querySelectorAll('#repOut details').forEach((d) => { d.open = true; })")
    card = page.locator("#repOut article.rcard[data-scope='group:iron']")

    # 確かめ済みのドリルがあれば選べる（中身は試験用の偽物。図の script は外して描く）
    page.route("**/v1/drills*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(FAKE_DRILLS)))

    # 候補の口（/plan-candidates）の答えにも、同じ偽物のドリルを差し込む（画面はこちらのドリルを出す）
    def fake_cands(route):
        resp = route.fetch()
        body = resp.json()
        for sc0 in body.get("scopes") or []:
            for c0 in (sc0.get("candidates") or {}).get("candidates") or []:
                if isinstance(c0.get("drills"), dict):
                    c0["drills"]["drills"] = [d for d in FAKE_DRILLS["drills"] if set(d.get("issues") or []) & set(c0.get("issue_names") or [c0["id"]])]
        route.fulfill(response=resp, body=json.dumps(body))
    page.route("**/plan-candidates*", fake_cands)
    card.locator("button[data-act=startplan][data-which=now]").first.click()
    sheet = page.locator("#planSheet")
    sheet.wait_for(state="visible")
    try:
        page.wait_for_function("document.querySelectorAll('#planSheet label.drl').length === 2", timeout=5000)
    except Exception:
        errors.append(f"[{tag}] 確かめ済みのドリルが候補の画面に出ない: {sheet.inner_text()[:200]!r}")
    sheet.locator("button[data-ps=close]").click()
    page.unroute("**/v1/drills*")
    page.unroute("**/plan-candidates*")

    # 本物のドリル集は、人が確かめたものがまだ無い → 「まだありません」と意識する1点の欄
    card.locator("[data-gist] button[data-act=startplan]").click()
    sheet.wait_for(state="visible")
    sheet.locator("[data-t=nodrill]").wait_for()
    if "確かめ済みのドリルはまだありません" not in sheet.inner_text():
        errors.append(f"[{tag}] 確かめ済みのドリルが無いときの文が無い: {sheet.inner_text()[:200]!r}")
    no_overflow(page, tag, "プランを作る面", errors)
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-9a-plan-sheet.png"))
    # 意識する1点は、要点の「意識する一点」の文が初めから入っている（直せる）
    pre = sheet.locator("[data-ps=cue]").input_value()
    if not pre or plain_bad(pre):
        errors.append(f"[{tag}] 意識する1点に要点の文が入っていない、または専門用語を含む: {pre!r}")
    sheet.locator("[data-ps=cue]").fill("")
    sheet.locator("button[data-ps=go]").click()
    if "意識する1点" not in sheet.locator("[data-ps=err]").inner_text():
        errors.append(f"[{tag}] 意識する1点が空でもプランを作ろうとした")
    sheet.locator("[data-ps=cue]").fill("向こうにボールがあるつもりで打つ")
    sheet.locator("button[data-ps=go]").click()
    page.wait_for_selector("[data-top-pane=today]:not([hidden]) #tBlocks li", timeout=20000)
    today = call("GET", f"{base}/players/{who['id']}/today")
    tpl = today["plan"]["template"]
    if page.locator("#tBlocks li").count() != len(tpl):
        errors.append(f"[{tag}] 今日の練習のブロックの数が型と違う: {page.locator('#tBlocks li').count()} / {len(tpl)}")
    if page.locator("#todayOut [data-t=head] [data-t=nodrill]").count() != 1:
        errors.append(f"[{tag}] 今日の練習に「確かめ済みのドリルはまだありません」が無い")
    nb = page.locator("#tNext").bounding_box()
    if not nb or nb["height"] < 64:
        errors.append(f"[{tag}] 下のボタンが 64px 未満: {nb}")
    if 'fill="#' in page.content():
        errors.append(f"[{tag}] 今日の練習に fill=\"#…\" がある")
    head = plain_text(page, "#todayOut [data-t=head] .ttitle")
    if not head or plain_bad(head):
        errors.append(f"[{tag}] 今日の練習の見出しが空か、数字・専門用語を含む: {head!r} {plain_bad(head or '')}")
    if page.locator("#todayOut [data-t=planwhy][open]").count():
        errors.append(f"[{tag}] 「なぜこれをやる？」が最初から開いている")
    no_overflow(page, tag, "今日の練習", errors)
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-9b-today.png"), full_page=True)

    # 通信を止めても、ブロックを進められる（開き直しても続きから）
    page.evaluate("() => navigator.serviceWorker.ready.then(() => true)")
    page.reload()
    page.wait_for_selector("[data-top-pane=today]:not([hidden]) #tBlocks li")
    ctx.set_offline(True)
    for act in ("#tNext", "#tNext", "#tbar [data-t=plus]"):
        page.click(act)
    cur = page.get_attribute("#tBlocks li.cur", "data-i")
    n2 = page.inner_text("#tBlocks li.cur .bn")
    if cur != "2" or n2 != f"{tpl[2]['n'] + 1}球":
        errors.append(f"[{tag}] 圏外でブロックが進まない／球を足せない: いま {cur}・{n2}")
    page.click("#tbar [data-t=minus]")
    try:
        page.reload()
        page.wait_for_selector("[data-top-pane=today]:not([hidden]) #tBlocks li.cur", timeout=10000)
        if page.get_attribute("#tBlocks li.cur", "data-i") != "2" or page.locator("#todayOut [data-t=offline]").count() != 1:
            errors.append(f"[{tag}] 圏外で開き直すと続きから出ない: {page.get_attribute('#tBlocks li.cur', 'data-i')}")
        page.click("#tNext")
        if page.get_attribute("#tBlocks li.cur", "data-i") != "3":
            errors.append(f"[{tag}] 圏外で開き直したあと、ブロックを進められない")
        no_overflow(page, tag, "今日の練習（圏外）", errors)
        page.screenshot(path=os.path.join(shots_dir, f"{tag}-9c-offline.png"), full_page=True)
    except Exception as e:  # noqa: BLE001
        errors.append(f"[{tag}] 圏外で開き直せない: {e}")
    ctx.set_offline(False)

    # 取り込み（作った球。型どおり）→ 練習を記録 → 球の帯 → 境目を動かす → 判定
    s2 = call("POST", f"{base}/sessions", {"player_id": who["id"], "date": "2026-09-30", "location": "練習場"})
    import_practice(base, s2["id"], practice_tsv(tpl, seed=21))
    page.reload()
    page.wait_for_selector("[data-top-pane=today]:not([hidden]) #tSess")
    # 既定のセッションは、きっかけの診断のセッション（プランを作る前の球）にしない
    if page.input_value("#tSess") == str(sid):
        errors.append(f"[{tag}] 練習のセッションの既定が、診断したセッションになっている")
    # 手順は「セッションを選ぶ → 取り込む → 判定」の順（選ぶ前に取り込むと、前に選んでいた診断のセッションに入る）
    if page.locator("#tAfter ol.steps > li").first.locator("#tSess").count() != 1:
        errors.append(f"[{tag}] 練習が終わったらの1番目が「セッションを選ぶ」になっていない")
    for bsel in ("#tAfter button[data-t=goimport]",):
        bb = page.locator(bsel).bounding_box()
        if not bb or bb["height"] < 44:
            errors.append(f"[{tag}] 練習が終わったらのボタンが 44px 未満: {bb}")
    page.select_option("#tSess", str(s2["id"]))
    page.locator("#tRun button[data-t=record]").click()
    total = sum(b["n"] for b in tpl)
    try:
        page.wait_for_function(f"document.querySelectorAll('#tRun .strip .cell').length === {total}", timeout=10000)
    except Exception:
        errors.append(f"[{tag}] 球の帯が出ない: {page.inner_text('#tRun')[:200]!r}")
        ctx.close()
        return
    # 予定と打った数が合っていれば、記録した時点でその区切りを保存する（仮の区切りのまま判定させない）
    try:
        page.wait_for_function("document.querySelector('#tRun [data-t=src]').textContent.includes('保存')", timeout=10000)
    except Exception:
        errors.append(f"[{tag}] 予定どおりの球数なのに、記録した区切りが保存されない: {page.inner_text('#tRun [data-t=src]')!r}")
    if not page.locator("#tbar").is_hidden():
        errors.append(f"[{tag}] 記録したあとも下の「次のブロックへ」の段が残っている")
    page.locator("#tRun button[data-mv='1'][data-d='-1']").click()
    page.wait_for_function(f"document.querySelectorAll('#tRun .strip .cell.k-baseline').length === {tpl[1]['n'] - 1 + tpl[-1]['n']}")
    run = call("GET", f"{base}/sessions/{s2['id']}/plan-run")
    if run["counts"][1] != tpl[1]["n"] - 1 or run["counts"][2] != tpl[2]["n"] + 1:
        errors.append(f"[{tag}] 境目を1球動かしても保存されない: {run['counts']}")
    page.locator("#tRun button[data-mv='1'][data-d='1']").click()
    page.wait_for_function(f"document.querySelectorAll('#tRun .strip .cell.k-baseline').length === {tpl[1]['n'] + tpl[-1]['n']}")
    no_overflow(page, tag, "球の帯", errors)
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-9d-strip.png"), full_page=True)
    page.locator("#tRun .strip .cell").nth(6).click()
    try:
        page.locator("#shotSheet").wait_for(state="visible", timeout=5000)
        if "#7" not in page.inner_text("#shotSheet"):
            errors.append(f"[{tag}] 球の帯の7球目を押しても #7 の面が出ない")
        page.locator("#shotSheet button[data-act=close]").click()
    except Exception as e:  # noqa: BLE001
        errors.append(f"[{tag}] 球の帯のマスを押しても1球の面が出ない: {e}")
    page.click("#topNav button[data-top=today]")
    page.wait_for_selector("#tRun button[data-t=judge]")
    page.locator("#tRun button[data-t=judge]").click()
    try:
        page.wait_for_selector("#tJudge [data-t=judgeout]", timeout=20000)
        jt = page.inner_text("#tJudge")
        if page.get_attribute("#tJudge [data-t=judgeout]", "data-state") != "maybe" or "効いたかも" not in jt:
            errors.append(f"[{tag}] 1回目の判定が「効いたかも」にならない: {jt[:200]!r}")
        if "次の手" not in jt:
            errors.append(f"[{tag}] 判定に次の手が無い")
        # 最初に見える判定は数字も専門用語も無い言葉だけ。根拠（定型文）は「なぜそう言える？」の中で、閉じている
        top = plain_text(page, "#tJudge [data-t=judgeout]")
        if plain_bad(top):
            errors.append(f"[{tag}] 判定の最初に見える文に数字・専門用語: {plain_bad(top)} {top[:200]!r}")
        if page.locator("#tJudge [data-t=judgewhy][open]").count() or not page.locator("#tJudge [data-t=judgewhy] .claim").count():
            errors.append(f"[{tag}] 判定の根拠が「なぜそう言える？」に畳まれていない")
        # 今日の練習の面ぜんたい（畳んだ中とボタンを除く）にも専門用語を出さない（「帯」「ガードレール」など）
        whole = page.evaluate("""() => { const c = document.querySelector('#todayOut').cloneNode(true);
          c.querySelectorAll('details, button, select').forEach((x) => x.remove()); return c.textContent.replace(/\\s+/g, ' '); }""")
        jb = [t for t in PLAIN_JARGON + ["ガードレール", "TrackMan"] if t in whole]
        if jb:
            errors.append(f"[{tag}] 今日の練習の面に専門用語: {jb}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"[{tag}] 判定が出ない: {e} {page.inner_text('#tRun')[-200:]!r}")
    no_overflow(page, tag, "判定", errors)
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-9e-judge.png"), full_page=True)
    page.locator("#tProg > summary").click()
    try:
        page.wait_for_selector("#tProgOut [data-t=progwhy] > summary", timeout=10000)
        page.locator("#tProgOut [data-t=progwhy] > summary").click()
        page.wait_for_selector("#tProgOut [data-t=progfig] circle[data-t=apt]", timeout=10000)
    except Exception:
        errors.append(f"[{tag}] 進捗の図に最初の A の点が無い: {page.inner_text('#tProgOut')[:200]!r}")
    page.locator("#tRec > summary").click()
    try:
        page.wait_for_selector("#tRecOut [data-t=rec]", timeout=10000)
        if "判定した 1回" not in page.inner_text("#tRecOut"):
            errors.append(f"[{tag}] あなたの記録に1回ぶんが無い: {page.inner_text('#tRecOut')[:200]!r}")
    except Exception:
        errors.append(f"[{tag}] あなたの記録が出ない")
    no_overflow(page, tag, "進捗と記録", errors)
    page.screenshot(path=os.path.join(shots_dir, f"{tag}-9f-progress.png"), full_page=True)
    # 今日のセッションが無ければ、ここで作れる（作ったら選ばれて、記録のボタンが出る）
    page.locator("#todayOut button[data-t=newsess]").click()
    try:
        page.wait_for_function("document.querySelector('#tSess').selectedOptions[0].textContent.includes('練習場') && !!document.querySelector('#tRun button[data-t=record]')", timeout=10000)
    except Exception:
        errors.append(f"[{tag}] 「今日のセッションを作る」で選ばれない: {page.inner_text('#tAfter')[:200]!r}")
    no_overflow(page, tag, "今日のセッションを作ったあと", errors)
    ctx.close()


# 最初に見える面に出してはいけない語（analysis/golf_analysis/config.py の PLAIN_FORBIDDEN と同じ考え）
PLAIN_JARGON = ["パス", "フェース", "打点", "ヒール", "トゥ", "帯", "窓", "スピン", "ロフト", "D-plane", "R²", "回帰",
                "区間", "中央値", "平均", "標準偏差", "moderate", "strong", "weak", "worse", "A-B", "キャリー", "インパクト",
                "ミスヒット", "Good", "°", "%", "％", "ヤード", "±", "ガードレール"]  # 度は数字の後ろだけ（「もう一度」は使ってよい）


def plain_bad(text: str) -> list[str]:
    """数字は件数（「26球中9球」「7球」）だけを許す。角度・割合・長さの数字と英字（クラブ名は呼ぶ側で外す）は拾う。"""
    bad = [t for t in PLAIN_JARGON if t in text]
    if re.search(r"[0-9０-９.]+\s*(°|度|%|％|mm|m\b|ｍ|cm)", text):
        bad.append("数字の単位")
    rest = re.sub(r"[0-9]+球中[0-9]+球|[0-9]+球", "", text)
    if re.search(r"[0-9０-９]", rest):
        bad.append("数字")
    if re.search(r"[A-Za-z]", rest):
        bad.append("英字")
    return bad


# 要素の見えている文（畳んだ「なぜ？」・操作のボタンの中身は除く）
PLAIN_TEXT_JS = """(sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const c = el.cloneNode(true);
  c.querySelectorAll('details.why, button, select').forEach((x) => x.remove());
  return c.textContent.replace(/\\s+/g, ' ').trim();
}"""


def plain_text(page, sel: str) -> str | None:
    return page.evaluate(PLAIN_TEXT_JS, sel)


def check_plain_first(page, tag: str, errors: list[str]) -> None:
    """利用者の方針（2026-09-29）: 解説を開いて最初に見えるのは、数字も専門用語も無い要点だけ。
    根拠の数字（定型文）と①〜⑧・図は、押したときだけ（最初は閉じている）。"""
    info = page.evaluate("""() => [...document.querySelectorAll('#repOut article.rcard.has-gist')].map((a) => ({
      scope: a.dataset.scope,
      gist: !!a.querySelector(':scope > .gist'),
      blocks: [...a.querySelectorAll(':scope > .gist > .gblock > h4')].map((h) => h.textContent),
      rows: a.querySelectorAll(':scope > .gist .grow').length,
      cmpFig: a.querySelectorAll(':scope > .gist figure.fig-cmp svg[data-fig=C1]').length,
      whyOpen: a.querySelectorAll(':scope > .gist details.why[open]').length,
      whyClaims: a.querySelectorAll(':scope > .gist details.why .claim').length,
      whyBlocks: a.querySelectorAll(':scope > .gist > .gblock > details.why').length,
      secShown: [...a.querySelectorAll(':scope > .sec')].filter((s) => s.offsetParent !== null).length,
      start: a.querySelectorAll(':scope > .gist button[data-act=startplan]').length,
    }))""")
    if len(info) < 2:
        errors.append(f"[{tag}] 要点のある範囲が {len(info)}（実データでは本体の範囲が3つ）")
    for x in info:
        if x["blocks"] != ["いま", "課題", "理想との差", "意識すること・やること"]:
            errors.append(f"[{tag}] {x['scope']}: 要点の4つの塊が無い: {x['blocks']}")
        if x["rows"] < 3 or x["whyOpen"] or not x["whyClaims"] or x["secShown"] or x["whyBlocks"] != 4 or x["cmpFig"] != 1:
            errors.append(f"[{tag}] {x['scope']}: 比べる行・比べる図が無い／根拠が最初から開いている／くわしい解説が見えている: {x}")
        t = plain_text(page, f"#repOut article.rcard[data-scope='{x['scope']}'] > .gist")
        if plain_bad(t or ""):
            errors.append(f"[{tag}] {x['scope']}: 要点に数字・専門用語: {plain_bad(t)} {t[:200]!r}")
    # 見えている領域（ページ全体の描画された文字）でも確かめる: 解説のカードの見出しから下
    vis = page.evaluate("""() => {
      const card = document.querySelector("#repOut article.rcard[data-scope='group:iron']");
      return card ? card.innerText : "";
    }""")
    vis = vis.replace("アイアン（まとめ）", "")
    if plain_bad(vis):
        errors.append(f"[{tag}] 最初に見える領域に数字・専門用語: {plain_bad(vis)} {vis[:300]!r}")
    # 「なぜそう言える？」を開くと、根拠（数字入りの定型文）が出る
    why = page.locator("#repOut article.rcard[data-scope='group:iron'] > .gist > .gb-now > details.why")
    why.locator("summary").click()
    opened = page.evaluate("""() => {
      const d = document.querySelector("#repOut article.rcard[data-scope='group:iron'] > .gist > .gb-now > details.why");
      return {open: d.open, claims: [...d.querySelectorAll('.claim')].filter((c) => c.offsetParent !== null).map((c) => c.textContent)};
    }""")
    if not opened["open"] or not opened["claims"] or not any(re.search(r"[0-9]", c) for c in opened["claims"]):
        errors.append(f"[{tag}] 「なぜそう言える？」を開いても根拠（数字入りの定型文）が出ない: {opened}")
    no_overflow(page, tag, "なぜ？を開いた要点", errors)
    why.locator("summary").click()
    rs = plain_text(page, "#repOut .rsum ul")
    if rs is not None and plain_bad(rs.replace("4 Hybrid", "").replace("5 Wood", "")):
        errors.append(f"[{tag}] 今日のまとめに数字・専門用語: {plain_bad(rs)} {rs[:200]!r}")
    no_overflow(page, tag, "要点", errors)
    # くわしい解説を開くと①〜⑧が見える
    page.locator("#repOut article.rcard[data-scope='group:iron'] > details.deep > summary").click()
    page.wait_for_function("[...document.querySelectorAll(\"#repOut article.rcard[data-scope='group:iron'] > .sec\")].some((s) => s.offsetParent !== null)")
    page.locator("#repOut article.rcard[data-scope='group:iron'] > details.deep > summary").click()


def check_narrative(browser, shots_dir: str, errors: list[str]) -> None:
    """REPORT_LLM=on・偽の Claude（本物の API は呼ばない）で、つなぎの文を頼んで画面に出す（360px）。"""
    with Services(fake_narrative=fake_narrative_file()) as sv:
        p = call("POST", f"{sv.base}/players", {"name": "Narr", "handedness": "R"})
        sid = seed_real(sv.base, p["id"])
        tag = "phone-narr"
        page = browser.new_page(viewport={"width": 360, "height": 780}, service_workers="block")
        page.on("pageerror", lambda e: errors.append(f"[{tag}] JS エラー: {e}"))
        page.on("console", lambda m: m.type == "error" and errors.append(f"[{tag}] console: {m.text}"))
        open_report(page, sv.root, sid, p["id"])
        btn = page.locator("#repOut button[data-act=narrate]")
        if btn.count() != 1:
            errors.append(f"[{tag}] on なのに Claude のつなぎを頼むボタンが無い")
            page.close()
            return
        btn.click()
        page.wait_for_selector("#repOut .repmeta[data-gen=narrative]", timeout=60000)
        page.evaluate("() => document.querySelectorAll('#repOut details').forEach((d) => { d.open = true; })")
        meta = page.inner_text("#repOut .repmeta[data-gen]")
        if "定型文＋Claude のつなぎ（検証済み）" not in meta or "$" not in meta:
            errors.append(f"[{tag}] 「定型文＋Claude のつなぎ（検証済み）」と料金が出ない: {meta}")
        cards = page.locator("#repOut .repmeta[data-narr-meta]")
        rep = call("GET", f"{sv.base}/sessions/{sid}/report")["report"]
        mains = [x for x in rep["scopes"] if x["kind"] == "main"]
        if cards.count() != len(mains):
            errors.append(f"[{tag}] 範囲ごとの料金の行が {cards.count()}（本体の範囲 {len(mains)}）")
        bridges = page.locator("#repOut .bridge").all_inner_texts()
        if not bridges or any(re.search(r"[0-9０-９右左]", b) for b in bridges):
            errors.append(f"[{tag}] つなぎの文が出ない、または事実の語を含む: {bridges[:3]}")
        if page.locator("#repOut button[data-act=narrate]").count():
            errors.append(f"[{tag}] つなぎが付いたのに頼むボタンが残っている")
        # 定型文（主張）はそのまま全部出ている（つなぎの文が主張を置き換えていない）
        n_claims = page.locator("#repOut article.rcard[data-kind=main] > .sec .claim, #repOut article.rcard[data-kind=main] > .sec .lead").count()
        if n_claims == 0:
            errors.append(f"[{tag}] 定型文が消えた")
        no_overflow(page, tag, "つなぎの文", errors)
        page.screenshot(path=os.path.join(shots_dir, f"{tag}-report.png"), full_page=True)
        page.close()


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
            # 既存の確認は Service Worker を止める（route で差し替える通信が Service Worker を通らないように）
            page = browser.new_page(viewport={"width": w, "height": h}, service_workers="block")
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
            check_plan(browser, sv.root, sv.base, shots_dir, tag, w, h, errors)
        check_narrative(browser, shots_dir, errors)
        browser.close()
    print("スクリーンショット:", shots_dir)
    if errors:
        sys.exit("失敗:\n  " + "\n  ".join(errors))
    print("OK")


if __name__ == "__main__":
    main()
