"""カタログの直しの流れ（docs/DESIGN_v2.md §18・§15 段5）。

人がガイドと突き合わせた項目に `checked_by` を入れ、残りを一覧で見るための道具。
中身（範囲・測り方・ページ）は catalog.json を手で直す。ここは「確かめた」の印と残りの数だけを扱う。

    uv run python -m golf_analysis.checkpoints.review               # 残りの一覧（未確認・要確認・ページが節の範囲のまま）
    uv run python -m golf_analysis.checkpoints.review --mark ID --by 名前 [--page 42]

手順（§18）:
1. ノート（docs/PGA_GUIDE_NOTES.md）を先に直す。ノートに無いことはカタログに書かない。
2. catalog.json の項目を直し、`--mark` で `checked_by` を入れる（`--page` を付けると1ページに絞った印 `page_exact: true`）。
3. 版を上げるときは catalog.json の `version` を上げ、`uv run pytest -q` を通す。過去の評価は古い版のまま残る。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

CATALOG_PATH = os.path.join(os.path.dirname(__file__), "catalog.json")


def load(path: str = CATALOG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(cat: dict, path: str = CATALOG_PATH) -> None:
    # いまのファイルと同じ書き方（1字下げ・日本語のまま）。差分が直した行だけになる
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(cat, ensure_ascii=False, indent=1) + "\n")


def remaining(cat: dict) -> dict:
    """直しの残り: 未確認（checked_by が空）・要確認（definition_status: unclear）・ページが節の範囲のまま。"""
    its = cat["items"]
    return {
        "version": cat.get("version"),
        "total": len(its),
        "unchecked": [i["id"] for i in its if not i.get("checked_by")],
        "unclear": [i["id"] for i in its if i.get("definition_status") == "unclear"],
        "page_range": [i["id"] for i in its if not (i.get("guide") or {}).get("page_exact")],
    }


def mark(cat: dict, item_id: str, by: str, page: int | None = None) -> dict:
    """項目に確かめた人を入れる。名前が空なら断る（空は「未確認」の意味なので）。"""
    if not by.strip():
        raise ValueError("確かめた人の名前が空です")
    for it in cat["items"]:
        if it["id"] == item_id:
            it["checked_by"] = by.strip()
            if page is not None:
                g = it.setdefault("guide", {})
                g["pages"] = str(page)
                g["page_exact"] = True
            return it
    raise KeyError(item_id)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="カタログの直しの残りを見る・確かめた印を入れる")
    ap.add_argument("--mark")
    ap.add_argument("--by", default="")
    ap.add_argument("--page", type=int)
    a = ap.parse_args(argv)
    cat = load()
    if a.mark:
        mark(cat, a.mark, a.by, a.page)
        save(cat)
    r = remaining(cat)
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in r.items()}, ensure_ascii=False))
    if not a.mark:
        for k in ("unclear", "unchecked"):
            print(f"# {k}", file=sys.stdout)
            for i in r[k]:
                print(i)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
