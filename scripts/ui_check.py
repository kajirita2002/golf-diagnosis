"""画面を本物のブラウザで操作して確かめる（Playwright）。

  - 4つのタブ（診断・1球ずつ・前回と比べる・実験）が、実際に押して中身を出すか
  - スマホ幅（390px）でページが横に溢れないか（表は枠の中だけで横に動く）
  - JavaScript のエラーが1つも出ないか
スクリーンショットは SHOTS_DIR（既定は一時ディレクトリ）に置く。

使い方: python3 scripts/ui_check.py（playwright が要る。PLAYWRIGHT_CHROMIUM で実行ファイルを指定できる）
"""

from __future__ import annotations

import os
import sys
import tempfile

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


def main() -> None:
    shots_dir = os.environ.get("SHOTS_DIR") or tempfile.mkdtemp(prefix="ui-")
    os.makedirs(shots_dir, exist_ok=True)
    errors: list[str] = []
    with Services() as sv, sync_playwright() as pw:
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
            page.close()
        browser.close()
    print("スクリーンショット:", shots_dir)
    if errors:
        sys.exit("失敗:\n  " + "\n  ".join(errors))
    print("OK")


if __name__ == "__main__":
    main()
