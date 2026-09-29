"""TrackMan の画面の表のスクリーンショットを読む。

流れ: 画像 → Claude（画像を読んで表を JSON で返す）→ 取り込みと同じ形の TSV → 検算。

- **読んだ文字は変換しない。** 画面に出ている文字（"40.7R" / "-"）をそのまま写させる。
  数値にする・単位を揃えるのは Go の取り込み（1か所）の仕事。ここで換算すると2か所になる。
- **検算は Average の行で行う。** 読み取った各球の値の平均を計算し直し、画面の Average と
  比べる。1桁の読み違い（1 と 7）や R/L の読み落としは平均を大きく動かすので見つかる
  （2026-09-17 の実データを人が書き写したときも、同じ方法で全部の列を確かめた）。
- **取り込みはしない。** 人が確認・修正してから、既存の「貼り付けて取り込む」で入れる。
- 検算は TSV に対して行う（人が直した TSV もそのまま再検算できる）。
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

MODEL = "claude-opus-5-5"
# 画面の表を写すだけなので深く考えさせない。読み違いは検算で拾う。
EFFORT = "medium"
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # API の画像1枚の上限
MEDIA_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

CELL = re.compile(r"^-?\d+(\.\d+)?\s?[RLAB]?$|^-$")


class Table(BaseModel):
    club: str | None
    columns: list[str]
    units: list[str]
    row_labels: list[str]
    rows: list[list[str]]
    average: list[str] | None
    consistency: list[str] | None


class Extraction(BaseModel):
    tables: list[Table]


_COLS = {"type": "array", "items": {"type": "string"}}
SCHEMA = {
    "type": "object",
    "properties": {
        "tables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "club": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "columns": _COLS,
                    "units": _COLS,
                    "row_labels": _COLS,
                    "rows": {"type": "array", "items": _COLS},
                    "average": {"anyOf": [_COLS, {"type": "null"}]},
                    "consistency": {"anyOf": [_COLS, {"type": "null"}]},
                },
                "required": ["club", "columns", "units", "row_labels", "rows", "average", "consistency"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["tables"],
    "additionalProperties": False,
}

PROMPT = """\
This is a screenshot of a TrackMan golf data table. Transcribe every table in it exactly.

For each table:
- club: the club name shown above the table (for example "6Iron"), or null if none is visible.
- columns: the data column headers, left to right, on one line each (for example "Club Speed", "Attack Ang.", "Imp. Offset"). \
Do not include the shot-number column or icon columns.
- units: the unit shown under each header, in the same order ("m/s", "Deg", "m", "mm"; empty string if none).
- row_labels: the label of each shot row ("1.", "2.", ...).
- rows: one list per shot row, one cell per column, in the same order as columns.
- average: the cells of the "Average" row, or null if it is not visible.
- consistency: the cells of the "Consistency" row, or null if it is not visible.

Copy each cell exactly as displayed. Keep the R or L suffix on side values ("40.7R", "2.2L"), keep minus signs, \
and write "-" for an empty cell. Do not convert, round, or compute anything. \
If a table is cut off at the top or bottom of the screenshot, transcribe only the rows that are fully visible."""


class Client(Protocol):
    """anthropic.Anthropic の、ここで使う部分だけ。テストでは偽物を渡す。"""

    beta: Any


class ExtractError(Exception):
    pass


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int

    @property
    def cost_usd(self) -> float:
        # Claude Opus 5.5: 入力 $4 / 出力 $20（100万トークンあたり）
        return self.input_tokens * 4e-6 + self.output_tokens * 20e-6


def extract(client: Client, image: bytes, media_type: str) -> tuple[Extraction, Usage]:
    """画像を Claude に読ませて表を返す。"""
    if media_type not in MEDIA_TYPES:
        raise ExtractError(f"画像の形式 {media_type} には対応していません（PNG / JPEG / WebP / GIF）")
    if len(image) > MAX_IMAGE_BYTES:
        raise ExtractError("画像が大きすぎます（5MB まで）。表の部分だけを切り取ってください")
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
        # 安全のための判定で断られたときに、別のモデルで自動でやり直す
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(image).decode()}},
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    )
    if resp.stop_reason == "refusal":
        raise ExtractError("画像を読めませんでした（断られました）")
    if resp.stop_reason == "max_tokens":
        raise ExtractError("表が大きすぎて途中で切れました。画像を分けてください")
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if text is None:
        raise ExtractError("読み取りの結果が空でした")
    try:
        ext = Extraction.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as e:
        raise ExtractError(f"読み取りの結果の形が不正です: {e}") from e
    return ext, Usage(resp.usage.input_tokens, resp.usage.output_tokens)


def to_tsv(t: Table) -> str:
    """取り込み（Go の TrackMan アダプタ）が読む形にする。"""
    lines = ["\t".join(["#", *t.columns]), "\t".join(["", *t.units])]
    for label, row in zip(t.row_labels or [str(i + 1) for i in range(len(t.rows))], t.rows):
        lines.append("\t".join([label, *row]))
    if t.average:
        lines.append("\t".join(["Average", *t.average]))
    if t.consistency:
        lines.append("\t".join(["Consistency", *t.consistency]))
    return "\n".join(lines) + "\n"


def _num(cell: str) -> float | None:
    c = cell.strip().replace(" ", "")
    if c in ("", "-"):
        return None
    sign = 1.0
    if c[-1] in "RLAB":
        sign = -1.0 if c[-1] in "LB" else 1.0
        c = c[:-1]
    return float(c) * sign


def _decimals(cell: str) -> int:
    m = re.search(r"\.(\d+)", cell)
    return len(m.group(1)) if m else 0


def verify_tsv(tsv: str) -> dict:
    """TSV を検算する。列ごとに、読んだ球の平均と画面の Average を比べる。

    許す差は Average の表示桁の 1.5 目盛り（小数1桁なら 0.15、整数なら 1.5）。
    画面の値は丸めてあるので、丸めた値の平均は少しずれる（実データで最大 0.06・0.38）。
    1桁の読み違いや R/L の落としは、8球でも平均を 0.7 以上動かすので超える。
    """
    rows = [r.split("\t") for r in tsv.strip("\n").split("\n") if r.strip()]
    if len(rows) < 3:
        return {"ok": False, "problems": ["表として読める行がありません"], "columns": []}
    header = rows[0]
    body = [r for r in rows[2:] if r and r[0] not in ("Average", "Consistency")]
    avg = next((r for r in rows if r and r[0] == "Average"), None)
    problems: list[str] = []
    cols: list[dict] = []

    for i, r in enumerate(body):
        if len(r) != len(header):
            problems.append(f"{r[0] or i + 1} の行の列の数が見出しと合いません（{len(r) - 1} / {len(header) - 1}）")
        for j, c in enumerate(r[1:], start=1):
            if not CELL.match(c.strip()):
                problems.append(f"{r[0]} の {header[j] if j < len(header) else j} 列目「{c}」は数値として読めません")

    if avg is None:
        problems.append("Average の行が無いので検算できません（画像に Average の行まで入れてください）")
    else:
        for j in range(1, len(header)):
            vals = [_num(r[j]) for r in body if j < len(r) and CELL.match(r[j].strip())]
            vals = [v for v in vals if v is not None]
            shown = _num(avg[j]) if j < len(avg) and CELL.match(avg[j].strip()) else None
            if not vals or shown is None:
                cols.append({"column": header[j], "status": "skip"})
                continue
            computed = sum(vals) / len(vals)
            tol = 1.5 * 10 ** -_decimals(avg[j])
            ok = abs(computed - shown) <= tol
            cols.append({"column": header[j], "status": "ok" if ok else "mismatch", "computed": round(computed, 3), "shown": shown, "tolerance": tol})
            if not ok:
                problems.append(f"{header[j]}: 読み取った値の平均 {computed:.2f} が画面の Average {avg[j]} と合いません。この列に読み違いがあります")

    return {"ok": not problems, "problems": problems, "columns": cols, "n_rows": len(body)}


def read_screenshot(client: Client, image: bytes, media_type: str) -> dict:
    """読み取り・TSV・検算をまとめて返す。取り込みはしない。"""
    ext, usage = extract(client, image, media_type)
    tables = []
    for t in ext.tables:
        tsv = to_tsv(t)
        tables.append({"club": t.club, "tsv": tsv, "check": verify_tsv(tsv)})
    return {
        "model": MODEL,
        "tables": tables,
        "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "cost_usd": round(usage.cost_usd, 4)},
    }
