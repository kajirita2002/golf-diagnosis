"""画面を本物のブラウザで操作して確かめる（Playwright）。

  - 4つのタブ（診断・1球ずつ・前回と比べる・実験）が、実際に押して中身を出すか
  - スマホ幅（390px）でページが横に溢れないか（表は枠の中だけで横に動く）
  - JavaScript のエラーが1つも出ないか
スクリーンショットは SHOTS_DIR（既定は一時ディレクトリ）に置く。

使い方: python3 scripts/ui_check.py（playwright が要る。PLAYWRIGHT_CHROMIUM で実行ファイルを指定できる）
"""

from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import zlib

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e import Services, seed, seed_real  # noqa: E402


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


def check_real(page, root: str, session_id: int, shots_dir: str, tag: str, errors: list[str]) -> None:
    """実データのセッションで、極端なヒール・ミスヒットの候補・アイアンのまとめが画面に出るか。"""
    page.goto(root + "/")
    page.wait_for_selector("#work:not([hidden])")
    page.select_option("#session", str(session_id))
    page.wait_for_function("document.querySelectorAll('#shotTable tr[data-id]').length === 57")
    page.click("button[data-tab=diag]")
    page.click("#analyzeBtn")
    page.wait_for_selector("#anOut .finding")
    text = page.inner_text("#anOut")
    for want in ("アイアン（まとめ）", "ネック寄り", "ミスヒット"):
        if want not in text:
            errors.append(f"[{tag}] 実データの診断に「{want}」が無い")
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

    cards.nth(0).locator("button[data-act=import]").click()
    try:
        page.wait_for_function(f"document.querySelectorAll('#shotTable tr[data-id]').length === {before + 8}", timeout=10000)
    except Exception:
        errors.append(f"[{tag}] スクショの表を取り込めない: {cards.nth(0).locator('[data-f=err]').inner_text()!r} / 行 {page.locator('#shotTable tr[data-id]').count()}（前 {before}）")


def main() -> None:
    shots_dir = os.environ.get("SHOTS_DIR") or tempfile.mkdtemp(prefix="ui-")
    os.makedirs(shots_dir, exist_ok=True)
    errors: list[str] = []
    fake = os.path.join(shots_dir, "fake_screenshot.json")
    fake_screenshot_answer(fake)
    with Services(fake_screenshot=fake) as sv, sync_playwright() as pw:
        seeded = seed(sv.base)
        real_id = seed_real(sv.base, seeded["player"]["id"])
        exe = os.environ.get("PLAYWRIGHT_CHROMIUM")
        browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        for tag, w, h in (("pc", 1280, 900), ("phone", 390, 844)):
            page = browser.new_page(viewport={"width": w, "height": h})
            page.on("pageerror", lambda e, tag=tag: errors.append(f"[{tag}] JS エラー: {e}"))
            page.on("console", lambda m, tag=tag: m.type == "error" and errors.append(f"[{tag}] console: {m.text}"))
            check(page, sv.root, shots_dir, tag, errors)
            check_real(page, sv.root, real_id, shots_dir, tag, errors)
            check_screenshot(page, sv.root, shots_dir, tag, errors)
            page.close()
        browser.close()
    print("スクリーンショット:", shots_dir)
    if errors:
        sys.exit("失敗:\n  " + "\n  ".join(errors))
    print("OK")


if __name__ == "__main__":
    main()
