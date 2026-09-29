"""解説レポート（POST /v1/report）。docs/DESIGN_coaching.md §6・§7・§8.2・§10.1。

入力は /v1/session と同じ球の並び（＋利き手）。内部で analyze_session を1回だけ呼び、
範囲ごとに ①〜⑧ の節（定型文だけ）・図の中身 F1〜F7・理想・次にやることの候補を返す。

- 事実の文は全部 claims.py の定型文（R1）。値は profile から Facts を通してだけ入る（R2）。
- 帯の形と窓の数字は Go（physics.Band）が作る。ここは範囲ごとに band_request（Go の bandRequest と同じキー）を出し、
  Go がその隣に band_shape を足す。窓の数字が要る文（needs_band: true）は `{band:lo}` / `{band:hi}` の差し込み口を
  残し、band_shape.window の face_min / face_max で埋める（埋められなければ出さない）。
- ①〜⑧ を出すのは「まとめ」と「まとめに入らない1本」だけ。まとめに入る1本ずつは畳んだ短い版（①と⑧）、
  5球未満の範囲も①と⑧だけ（§6.1）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict

from . import band as band_mod
from . import config, figures, plan, video_candidates
from .claims import Claim, ClaimError, Facts, build_claim, dir_word
from .profile import cross_units
from .session import analyze_session
from .shots import dec, is_extreme, m

log = logging.getLogger(__name__)

SECTION_TITLES = {
    "s1": ("①", "ひとことで"),
    "s2": ("②", "今日の球筋"),
    "s3": ("③", "インパクトで何が起きていたか"),
    "s4": ("④", "一番大きなミスはどこから"),
    "s5": ("⑤", "クラブをまたぐ傾向"),
    "s6": ("⑥", "理想"),
    "s7": ("⑦", "次にやること"),
    "s8": ("⑧", "まだ言えないこと"),
}
# ⑥の図は帯を使う範囲だけ（帯を使わない種類では②③と同じ絵になるので出さない。§7.1）。
# ⑦には F6 を重ねて出さない（④と同じ図。文から④を指す）
SECTION_FIGURES = {"s2": ["F2", "F1"], "s3": ["F3", "F4"], "s4": ["F6", "F5"], "s5": ["F7"], "s6": ["F1", "F3", "F4"]}

# 図の読み上げに使う主張（上から順に、あるものを使う）
FIGURE_DESC = {
    "F1": ["ideal.today", "flight.good"],
    "F2": ["flight.top_cell", "flight.counts"],
    "F3": ["impact.face", "impact.path"],
    "F4": ["impact.representative"],
    "F5": ["miss.strike"],
    "F6": ["miss.budget"],
    "F7": [],
}

START_WORD = {"push": "{dir:right}に出て", "straight": "まっすぐ出て", "pull": "{dir:left}に出て"}
CURVE_WORD = {"right": "{dir:right}に曲がる", "none": "曲がらない", "left": "{dir:left}に曲がる"}
CAUSE_WORD = {
    "face_to_path": "フェースがパスより{dir:%s}を向いた当たり",
    "strike": "打点のずれで曲がった当たり",
    "mixed": "曲がりの原因が1つに決まらない当たり",
    "thin": "薄い当たり（トップ）",
    "unknown": "曲がりの原因が測れていない当たり",
    "none": "曲がりの小さい当たり",
}
BUDGET_WORD = {
    "extreme_only": "{ext}だけ",
    "extreme_and_face": "{ext}＋フェースも大きくずれた",
    "thin": "薄い当たり（トップ）",
    "face_to_path": "フェース・トゥ・パス",
    "strike": "打点のずれ",
    "mixed": "原因が1つに決まらない",
    "start": "打ち出しのずれ",
    "unknown": "原因が分からない",
}
CAND_TITLE = {
    "strike_heel": "ネック寄り（ヒール側）の当たりを減らす",
    "strike_toe": "先端寄り（トゥ側）の当たりを減らす",
    "strike_scatter": "打点のばらつきを減らす",
    "face": "フェースの向きを、まっすぐ落ちる範囲に入れる",
    "path": "クラブパスの向きを整える",
    "face_to_path": "フェースとパスの差（曲がり）を揃える",
    "start_right": "{dir:right}への打ち出しを減らす",
    "start_left": "{dir:left}への打ち出しを減らす",
    "start_var": "打ち出しの向きを揃える",
    "curve_right": "{dir:right}への曲がりを減らす",
    "curve_left": "{dir:left}への曲がりを減らす",
    "curve_var": "曲がりの向きを揃える",
}
# ばらつきを減らす候補（主 KPI が reduce_sd）の見出し。「範囲に入れる」と書くと仮説（揃える）と食い違う
CAND_TITLE_SD = {"face": "フェースの向きを揃える", "path": "クラブパスの向きを揃える", "face_to_path": "フェースとパスの差（曲がり）を揃える"}


def cand_title(c: dict) -> str:
    if c.get("kpi") and c["kpi"]["goal"] == "reduce_sd" and c["id"] in CAND_TITLE_SD:
        return CAND_TITLE_SD[c["id"]]
    return CAND_TITLE.get(c["id"], c["id"])


METRIC_WORD = {"impact_offset": "打点", "face_angle": "フェースの向き", "club_path": "クラブパス", "face_to_path": "フェース・トゥ・パス"}
GOAL_WORD = {"reduce_abs": "芯に寄せる（ずれの大きさを減らす）", "reduce_sd": "ばらつきを減らす", "decrease": "{dir:left}へ寄せる", "increase": "{dir:right}へ寄せる"}


# TrackMan のリンクで取っていない項目（⑧）。セッションの全部の球で無ければ末尾に1回だけ出す
MISSING_TILES = (
    ("impact_height", "打点の上下（Impact Height）は測れていません（TrackMan のリンクの10項目に入っていません。取り込みと手入力はもう対応しています）。"),
    ("ball_speed", "ボール初速・ミート率は取っていないので、芯を外してキャリーが落ちたぶんを分けられません。"),
    ("low_point", "最下点（Low Point）は測っていません（タイルが無い）。ダフリ・トップの傾向はここでは言えません。"),
)


class ScopeReport:
    """1つの範囲の節と図を作る。"""

    def __init__(self, ctx: dict, unit: dict, kind: str, label: str, shots: list[dict], skip: set):
        self.ctx = ctx
        self.unit = unit
        self.p = unit["profile"]
        self.kind = kind  # main / folded / short
        self.label = label
        self.shots = shots
        self.skip = skip
        self.hand = ctx["hand"]
        self.facts = Facts()
        self.sections: dict[str, list[dict]] = defaultdict(list)
        self.sid = self.p["scope_id"]
        self.n_use = self.p["n"] - self.p["mishit_excluded"]
        # 帯の依頼の id（差し込み口 {band:<id>:lo} に使うので、コロンと空白を含まない名前にする）
        self.cands = None
        self.band_request_id = None
        if self.p["band"]["used"] and kind == "main":
            ctx["band_seq"] = ctx.get("band_seq", 0) + 1
            self.band_request_id = f"band{ctx['band_seq']}"

    # ---- facts と claims

    def f(self, name: str, value, unit: str, n: int, note: str = "") -> str:
        return self.facts.add(f"{self.sid}/{name}", value, unit, scope_id=self.sid, n=n, label=self.label, note=note)

    def c(self, section: str, cid: str, template: str, layer: str = "L1", must: bool = True, q: str | None = None, finding_ids=None, needs_band: bool = False, basis=None) -> None:
        claim = Claim(id=f"{self.sid}/{cid}", section=section, layer=layer, template=template, must=must, qual=q, finding_ids=finding_ids or [], needs_band=needs_band, basis=basis or [])
        self.sections[section].append(build_claim(claim, self.facts, self.hand))

    def fid(self, kind: str, detail: str | None = None) -> list[str]:
        scope = "group" if self.p["scope"] == "group" else "club"
        name = self.unit.get("name") or self.unit.get("club")
        want = ":".join(x for x in (scope, name, kind, detail) if x)
        return [f["id"] for f in self.ctx["findings"] if f.get("id") == want]

    def excl_note(self, metric: str | None = None) -> str:
        """外した候補の数と番号（R6）。節によって数が変わらないよう、いつも範囲の候補の総数で書く
        （指標ごとに「その値があった候補」だけを数えると、同じ範囲で 3球・4球・1球と食い違って見える）。"""
        seqs = sorted(s["seq"] for s in self.shots if s["id"] in self.skip)
        if not seqs:
            return ""
        name = "mishit.all"
        k = self.f(f"{name}.n", len(seqs), "count", self.p["n"])
        s = self.f(f"{name}.seqs", seqs, "seqs", self.p["n"])
        return f"（ミスヒットの候補 {{n:{k}}}球 {{seqs:{s}}} を除く）"

    # ---- 部品

    def cell_phrase(self, key: str) -> str:
        st, cv = key.split("|")
        return START_WORD[st] + CURVE_WORD[cv]

    def breakdown_phrase(self, cell: dict) -> str:
        parts = []
        cn = self.f(f"cell.{cell['key']}.n", cell["n"], "count", cell["n"])
        for i, b in enumerate(cell["breakdown"]):
            k = self.f(f"cell.{cell['key']}.{b['cause']}", b["n"], "count", cell["n"])
            if b["cause"] == "extreme_strike":
                members = [s for s in self.shots if s["seq"] in b["seqs"]]
                heel = all(dec(s).get("contact") == "heel_extreme" for s in members)
                word = "ネック寄り（ヒール 30mm 超）の当たり" if heel else "ネック・先端寄り（芯から 30mm 超）の当たり"
            elif b["cause"] == "face_to_path":
                word = CAUSE_WORD["face_to_path"] % (cell["curve"] if cell["curve"] in ("right", "left") else "right")
            else:
                word = CAUSE_WORD.get(b["cause"], CAUSE_WORD["unknown"])
            if len(cell["breakdown"]) == 1 and b["n"] == cell["n"]:
                parts.append(f"{{n:{k}}}球{{qs:{k};{cn}}}{word}")  # 量の言葉は件数から選ぶ
            elif len(cell["breakdown"]) == 2 and i == 1:
                parts.append(f"残りの{{n:{k}}}球は{word}")
            else:
                parts.append(f"{{n:{k}}}球は{word}")
        if not parts:
            return ""
        if len(parts) == 1:
            return f"{parts[0]}です。"
        return "うち" + ("で、".join(parts) if len(parts) == 2 else "、".join(parts)) + "です。"

    def top_cell(self) -> dict | None:
        t = self.p["tendency"]
        return next((c for c in t["cells"] if c["key"] == t["top_cell"]), None)

    def ext_word(self) -> str:
        st = self.p["strike"]
        if st["toe_extreme"] and not st["heel_extreme"]:
            return "先端寄り"
        if st["heel_extreme"] and not st["toe_extreme"]:
            return "ネック寄り"
        return "ネック・先端寄り"

    # ---- ① ひとことで

    def s1(self) -> None:
        l1 = self.p["l1"]
        path, face = l1["club_path"], l1["face_angle"]
        if path.get("spread") and face.get("spread"):
            fp = self.f("l1.club_path.sd", path["sd"], "deg", path["n"], "候補を除く")
            ff = self.f("l1.face_angle.sd", face["sd"], "deg", face["n"], "候補を除く")
            if path["spread"] == "stable" and not path["bias"] and face["spread"] == "wide":
                self.c("s1", "summary.path_face", "クラブの進む向き（パス）はほぼまっすぐで揃っています。散っているのはフェースの向きです。", basis=[fp, ff])
            elif path["bias"] in ("right", "left") and face["spread"] == "wide" and not face["bias"]:
                pm = self.f("l1.club_path.mean", path["mean"], "deg_lat", path["n"], "候補を除く")
                self.c("s1", "summary.path_face", f"クラブの進む向き（パス）は{{dir:{path['bias']}}}向きに偏り、フェースの向きが左右に散っています。", basis=[fp, ff, pm])
            else:
                self.c("s1", "summary.path_face", f"クラブパスのばらつきは ±{{v:{fp}}}、フェースの向きのばらつきは ±{{v:{ff}}} でした。")
        cell = self.top_cell()
        t = self.p["tendency"]
        if cell:
            n = self.f("tendency.n", t["n"], "count", t["n"], "候補も数える")
            k = self.f(f"cell.{cell['key']}.n", cell["n"], "count", t["n"])
            self.c("s1", "summary.top_cell", f"一番多いのは{self.cell_phrase(cell['key'])}球で、{{n:{n}}}球中{{n:{k}}}球です。" + self.breakdown_phrase(cell), layer="L1")
        elif len(t.get("top_tie") or []) == 2:
            self.c("s1", "summary.top_cell", self.tie_phrase(), layer="L0")
        mb = self.p["miss_budget"]
        if mb["n_over"]:
            tot = self.f("miss_budget.total_m", mb["total_m"], "m", mb["n_side"], "候補も数える")
            ext_total = sum(b["excess_m"] for b in mb["by_cause"] if b["cause"] in ("extreme_only", "extreme_and_face"))
            others = [b for b in mb["by_cause"] if b["cause"] not in ("extreme_only", "extreme_and_face")]
            biggest_other = max((b["excess_m"] for b in others), default=0.0)
            if ext_total > 0 and ext_total >= biggest_other:
                n_ext = sum(b["n"] for b in mb["by_cause"] if b["cause"] in ("extreme_only", "extreme_and_face"))
                lo = self.f("miss_budget.strike_lo_m", mb["strike_range_m"][0], "m", mb["n_side"])
                hi = self.f("miss_budget.strike_hi_m", mb["strike_range_m"][1], "m", mb["n_side"])
                ne = self.f("miss_budget.extreme_n", n_ext, "count", mb["n_side"])
                # 打点のぶんは数える順で変わるので幅で言う（§5.3）
                rng = f"{{a:{lo}}}〜{{a:{hi}}}" if 0 < mb["strike_range_m"][0] < mb["strike_range_m"][1] else f"{{a:{hi}}}"
                self.c("s1", "summary.budget", f"左右に大きく外れたぶんのうち、{rng}（全体 {{a:{tot}}}）が{self.ext_word()}の{{n:{ne}}}球から来ています。")
            else:
                top = max(mb["by_cause"], key=lambda b: b["excess_m"])
                x = self.f(f"miss_budget.{top['cause']}.m", top["excess_m"], "m", mb["n_side"])
                k = self.f(f"miss_budget.{top['cause']}.n", top["n"], "count", mb["n_side"])
                self.c("s1", "summary.budget", f"左右に大きくはみ出したぶん（全体 {{a:{tot}}}）で一番大きいのは、{BUDGET_WORD[top['cause']].format(ext=self.ext_word())}の{{n:{k}}}球（{{a:{x}}}）です。")
        if t["mishit_included"] and (cell or t.get("top_tie") or mb["n_over"]):
            # ①の球筋と外れたぶんは、起きた球なので候補も数える（R6 の例外）。数えたことを書く
            k = self.f("tendency.mishit_included", t["mishit_included"], "count", t["n"])
            self.c("s1", "summary.mishit", f"※ ここの球数と距離は、ミスヒットの候補 {{n:{k}}}球も数えています（起きた球なので）。", layer="meta")
        if self.n_use < config.MIN_SCOPE_N:
            self.small_note("s1", "summary.small", "数字は参考です")

    def small_note(self, section: str, cid: str, tail: str) -> None:
        """球が少ない範囲の断り。候補を除いて減ったなら、除いたことを書く（R6）。"""
        n = self.f("n_use", self.n_use, "count", self.p["n"])
        if self.p["mishit_excluded"]:
            k = self.f("mishit.all.n", self.p["mishit_excluded"], "count", self.p["n"])
            text = f"この範囲はミスヒットの候補 {{n:{k}}}球を除くと{{n:{n}}}球なので、{tail}。"
        else:
            text = f"この範囲は{{n:{n}}}球なので、{tail}。"
        self.c(section, cid, text, layer="meta")

    def tie_phrase(self) -> str:
        """一番多い枠が2つ同数のとき（どちらかを選ぶと、右打ちと左打ちで結論が入れ替わる）。"""
        t = self.p["tendency"]
        a, b = t["top_tie"]
        k = self.f("cell.tie.n", next(c["n"] for c in t["cells"] if c["key"] == a), "count", t["n"])
        n = self.f("tendency.n", t["n"], "count", t["n"], "候補も数える")
        return f"一番多い球筋は1つに決まりません。{self.cell_phrase(a)}球と{self.cell_phrase(b)}球が同じ数（{{n:{n}}}球中{{n:{k}}}球ずつ）です。"

    # ---- ② 今日の球筋

    def s2(self) -> None:
        t = self.p["tendency"]
        st, cv = t["start"], t["curve"]
        ns = {k: self.f(f"tendency.start.{k}", st[k], "count", t["n"]) for k in ("push", "straight", "pull")}
        if st["unknown"] == t["n"]:
            # 測れていない球を黙って落とし、0球を並べない（R7）
            nu = self.f("tendency.start.unknown", st["unknown"], "count", t["n"])
            tn = self.f("tendency.n", t["n"], "count", t["n"], "候補も数える")
            text = f"打ち出しは{{n:{nu}}}球{{qs:{nu};{tn}}}測れていません。"
        else:
            text = f"打ち出しは{{dir:right}}（2° 超）が{{n:{ns['push']}}}球、まっすぐが{{n:{ns['straight']}}}球、{{dir:left}}が{{n:{ns['pull']}}}球でした。"
            if st["unknown"]:
                nu = self.f("tendency.start.unknown", st["unknown"], "count", t["n"])
                text = text.removesuffix("でした。") + f"で、測れていない球が{{n:{nu}}}球ありました。"
        if not cv["measured"] and cv["unknown"]:
            cn = self.f("tendency.curve.unknown", cv["unknown"], "count", t["n"])
            tn = self.f("tendency.n", t["n"], "count", t["n"], "候補も数える")
            text += f"曲がりを測れた球はありません（{{n:{tn}}}球中{{n:{cn}}}球が「-」）。"
        if cv["measured"]:
            me = self.f("tendency.curve.measured", cv["measured"], "count", t["n"])
            cu = self.f("tendency.curve.curved", cv["curved"], "count", cv["measured"])
            text += f"曲がりを測れた{{n:{me}}}球のうち、曲がったのは{{n:{cu}}}球で、"
            side = "right" if cv["right"] >= cv["left"] else "left"
            ks = ("fade", "slice") if side == "right" else ("draw", "hook")
            k = self.f(f"tendency.curve.{side}", cv[side], "count", cv["measured"])
            a = self.f(f"tendency.curve.{ks[0]}", cv[ks[0]], "count", cv["measured"])
            b = self.f(f"tendency.curve.{ks[1]}", cv[ks[1]], "count", cv["measured"])
            names = ("フェード", "スライス") if side == "right" else ("ドロー", "フック")
            text += f"{{n:{k}}}球が{{dir:{side}}}へ曲がりました（{names[0]}{{n:{a}}}・{names[1]}{{n:{b}}}）。"
        self.c("s2", "flight.counts", text, layer="L0")
        cell = self.top_cell()
        if cell:
            k = self.f(f"cell.{cell['key']}.n", cell["n"], "count", t["n"])
            self.c("s2", "flight.top_cell", f"9つの枠では「{self.cell_phrase(cell['key'])}」が{{n:{k}}}球で一番多く、" + self.breakdown_phrase(cell).removeprefix("うち").rstrip("。") + "（図 F2）。", layer="L0")
        elif len(t.get("top_tie") or []) == 2:
            self.c("s2", "flight.top_cell", "9つの枠では、" + self.tie_phrase().rstrip("。") + "（図 F2）。", layer="L0")
        good = self.p["good_reference"]
        g = self.f("good.n", len(good["good_seqs"]), "count", good["n"])
        n = self.f("good.of", good["n"], "count", good["n"])
        seqs = f"（{{seqs:{self.f('good.seqs', sorted(good['good_seqs']), 'seqs', good['n'])}}}）" if good["good_seqs"] else ""
        text = f"狙いの幅（Good）に入ったのは{{n:{n}}}球中{{n:{g}}}球でした{seqs}。"
        if t["side_median_m"] is not None:
            md = self.f("tendency.side_median_m", t["side_median_m"], "m_lat", t["side_n"])
            text += f"左右のずれの中央値は{{v:{md}}} です。"
        self.c("s2", "flight.good", text, layer="L0")
        if t["mishit_included"]:
            k = self.f("tendency.mishit_included", t["mishit_included"], "count", t["n"])
            self.c("s2", "flight.mishit", f"※ この段はミスヒットの候補 {{n:{k}}}球も数えています（起きた球なので）。", layer="meta")

    # ---- ③ インパクト

    def s3(self) -> None:
        l1 = self.p["l1"]
        path, face = l1["club_path"], l1["face_angle"]
        if path.get("n", 0) >= 2:
            n = self.f("l1.club_path.n", path["n"], "count", path["n"])
            mean = self.f("l1.club_path.mean", path["mean"], "deg_lat", path["n"], "候補を除く")
            lo = self.f("l1.club_path.min", path["min"], "deg_lat", path["n"])
            hi = self.f("l1.club_path.max", path["max"], "deg_lat", path["n"])
            sd = self.f("l1.club_path.sd", path["sd"], "deg", path["n"], "候補を除く")
            rng = self._range(lo, hi)
            if path["bias"]:
                ci = self._ci("l1.club_path", path)
                self.c("s3", "impact.path", f"クラブパスは{{n:{n}}}球で平均 {{v:{mean}}}、{rng} でした（ばらつき ±{{v:{sd}}}）。今日の球では、{{dir:{path['bias']}}}向きの偏りがはっきり出ていました（平均の95%区間 {ci}）。" + self.excl_note("club_path"))
            elif path.get("spread") == "stable":
                # 量の言葉は件数から選ぶ（範囲の中の球の数 / 測れた数）
                vals = [m(x, "club_path") for x in self.shots if x["id"] not in self.skip and m(x, "club_path") is not None]
                kin = self.f("l1.club_path.in_range", sum(1 for v in vals if path["min"] <= v <= path["max"]), "count", path["n"])
                self.c("s3", "impact.path", f"クラブパスは{{n:{n}}}球で平均 {{v:{mean}}}、{{q:{kin};{n}}}が {rng} に収まっています（ばらつき ±{{v:{sd}}}）。" + self.excl_note("club_path"), q="all")
            else:
                self.c("s3", "impact.path", f"クラブパスは{{n:{n}}}球で平均 {{v:{mean}}}、{rng} に散っています（ばらつき ±{{v:{sd}}}）。" + self.excl_note("club_path"))
        if face.get("n", 0) >= 2:
            n = self.f("l1.face_angle.n", face["n"], "count", face["n"])
            mean = self.f("l1.face_angle.mean", face["mean"], "deg_lat", face["n"], "候補を除く")
            lo = self.f("l1.face_angle.min", face["min"], "deg_lat", face["n"])
            hi = self.f("l1.face_angle.max", face["max"], "deg_lat", face["n"])
            vals = [m(s, "face_angle") for s in self.shots if s["id"] not in self.skip and m(s, "face_angle") is not None]
            k_r = sum(1 for v in vals if v > config.ISSUE_DIR_DEG)
            k_l = sum(1 for v in vals if v < -config.ISSUE_DIR_DEG)
            side = "right" if k_r >= k_l else "left"
            k = self.f(f"l1.face_angle.over2_{side}", max(k_r, k_l), "count", face["n"])
            sign = "+" if side == "right" else "−"
            if self.hand == "L":
                sign = "−" if sign == "+" else "+"
            text = f"フェースの向きは{{n:{n}}}球で平均 {{v:{mean}}}、{self._range(lo, hi)} まで散り、{{n:{n}}}球中{{n:{k}}}球が {sign}2° より{{dir:{side}}}を向いて当たりました。"
            # 区間は3球から（2球では作らない）。区間が無いのに差し込もうとして範囲ごと落とさない
            if face["bias"]:
                ci = self._ci("l1.face_angle", face)
                text += f"今日の球では、{{dir:{face['bias']}}}を向く偏りがはっきり出ていました（平均の95%区間 {ci}）。"
            elif face.get("ci95"):
                ci = self._ci("l1.face_angle", face)
                text += f"平均の95%区間は {ci} で0をまたぎ、今日の球では向きの偏りは言い切れません。"
            self.c("s3", "impact.face", text + self.excl_note("face_angle"))
        sens = self.p["sensitivity"]
        r2 = sens.get("r2_single") or {}
        # 値が全部同じ指標は単回帰の R² が出ない（ばらつき 0）。その文だけ省き、範囲ごと落とさない
        if sens["status"] == "ok" and sens["coef"]["face_angle"] > 0 and r2.get("face_angle") is not None:
            n = self.f("sensitivity.n", sens["n"], "count", sens["n"])
            r2f = self.f("sensitivity.r2_single.face_angle", r2["face_angle"], "pct", sens["n"], "単回帰")
            r2p = self.f("sensitivity.r2_single.club_path", r2["club_path"], "pct", sens["n"], "単回帰") if r2.get("club_path") is not None else None
            if self.p["scope"] == "group":
                cf = self.f("sensitivity.coef.face_angle", sens["coef"]["face_angle"], "pct", sens["n"], "重回帰の係数（キャリーに対する割合）")
                carry = self.p["carry_median_m"]
                cm = self.f("carry_median_m", carry, "m", self.n_use)
                mm = self.f("sensitivity.face_1deg_m", sens["coef"]["face_angle"] * carry, "m", sens["n"])
                first = f"今日の球では、フェースが 1° {{dir:right}}を向くと、左右のずれがキャリーの約 {{v:{cf}}}（{self.label}のキャリーの中央値 {{a:{cm}}} なら約 {{a:{mm}}}）{{dir:right}}へ動きました（フェースとパスの重回帰の係数・{{n:{n}}}球）。"
            else:
                cf = self.f("sensitivity.coef.face_angle_m", sens["coef"]["face_angle"], "m", sens["n"], "重回帰の係数")
                first = f"今日の球では、フェースが 1° {{dir:right}}を向くと、左右のずれが約 {{a:{cf}}} {{dir:right}}へ動きました（フェースとパスの重回帰の係数・{{n:{n}}}球）。"
            tail = f"。パスだけなら {{v:{r2p}}}" if r2p else "。パスは値がそろっていて、パスだけの説明の割合は出せません"
            self.c("s3", "impact.sensitivity", first + f"フェースの向きだけで、左右のずれのばらつきの {{v:{r2f}}} を説明できます（{{n:{n}}}球・単回帰{tail}）。", finding_ids=self.fid("dispersion_driver", "face_angle"))
        rep = self.p["representative"]
        if rep and None not in (rep["club_path"], rep["face_angle"], rep["launch_direction"], rep["spin_axis"], rep["side"]):
            n1 = 1
            s = self.f("rep.seq", [rep["seq"]], "seqs", n1)
            pa = self.f("rep.club_path", rep["club_path"], "deg_lat", n1)
            fa = self.f("rep.face_angle", rep["face_angle"], "deg_lat", n1)
            ld = self.f("rep.launch_direction", rep["launch_direction"], "deg_lat", n1)
            ax = self.f("rep.spin_axis", rep["spin_axis"], "deg_lat", n1)
            sd = self.f("rep.side", rep["side"], "m_lat", n1)
            self.c("s3", "impact.representative", f"典型の1球は {{seqs:{s}}} です。パス {{v:{pa}}} に対してフェースが {{v:{fa}}}、打ち出しは{{dirof:{ld}}} {{a:{ld}}}、スピン軸 {{v:{ax}}} で、{{v:{sd}}} に落ちました" + ("（図 F4）。" if self.kind == "main" else "。"))
        cov = self.p["coverage"]
        if cov["face"]["missing"]:
            fn = self.f("coverage.face.n", cov["face"]["n"], "count", cov["n"])
            fm = self.f("coverage.face.missing", cov["face"]["missing"], "count", cov["n"])
            # パスの文は別の球数（パスを測れた球）なので、対象をフェースの数字に絞って書く
            if cov["face"]["n"]:
                text = f"※ この段のフェースの数字は、フェースを測れた{{n:{fn}}}球だけの話です。"
            else:
                text = "※ フェースは1球も測れていないので、フェースの数字はありません。"
            if cov["face"]["missing_extreme"]:
                fe = self.f("coverage.face.missing_extreme", cov["face"]["missing_extreme"], "count", cov["face"]["missing"])
                text += f"測れなかった{{n:{fm}}}球のうち{{n:{fe}}}球は{self.ext_word()}の当たりでした。"
            else:
                text += f"測れなかった球が{{n:{fm}}}球あります。"
            self.c("s3", "impact.coverage", text, layer="meta")

    def _range(self, lo: str, hi: str) -> str:
        if self.hand == "L":
            lo, hi = hi, lo
        return f"{{v:{lo}}}〜{{v:{hi}}}"

    def _ci(self, name: str, l1: dict) -> str:
        unit = "strike" if l1["metric"] == "impact_offset" else "deg_lat"
        a = self.f(f"{name}.ci_lo", l1["ci95"][0], unit, l1["n"])
        b = self.f(f"{name}.ci_hi", l1["ci95"][1], unit, l1["n"])
        return self._range(a, b)

    # ---- ④ 一番大きなミス

    def s4(self) -> None:
        cov, mb, st = self.p["coverage"], self.p["miss_budget"], self.p["strike"]
        ext = cov["extreme"]
        if ext["n"] >= config.MIN_EXTREME_STRIKE_N:
            n = self.f("extreme.n", ext["n"], "count", cov["n"])
            s = self.f("extreme.seqs", sorted(ext["seqs"]), "seqs", cov["n"])
            text = f"{self.ext_word()}の当たりが{{n:{n}}}球ありました（{{seqs:{s}}}）。"
            if ext["mean_abs_side_m"] is not None and ext["mean_abs_side_others_m"]:
                a = self.f("extreme.mean_abs_side_m", ext["mean_abs_side_m"], "m", ext["n"])
                b = self.f("extreme.mean_abs_side_others_m", ext["mean_abs_side_others_m"], "m", cov["n"] - ext["n"])
                r = self.f("extreme.side_ratio", ext["mean_abs_side_m"] / ext["mean_abs_side_others_m"], "ratio", cov["n"])
                text += f"左右のずれは平均 {{a:{a}}} で、ほかの球（{{a:{b}}}）の約 {{v:{r}}}倍です。"
            self.c("s4", "miss.extreme", text, finding_ids=self.fid("extreme_strike", "heel_extreme") + self.fid("extreme_strike", "toe_extreme"))
            nf = ext["n"] - ext["with_face"]
            if nf:
                k = self.f("extreme.no_face", nf, "count", ext["n"])
                text = f"この{{n:{n}}}球のうち{{n:{k}}}球はフェースが測れていません。"
                if ext["with_path"] == ext["n"] and ext["path_others_min"] is not None:
                    lo = self.f("extreme.path_min", ext["path_min"], "deg_lat", ext["n"])
                    hi = self.f("extreme.path_max", ext["path_max"], "deg_lat", ext["n"])
                    usual = ext["path_min"] >= ext["path_others_min"] - config.MMD["club_path"] and ext["path_max"] <= ext["path_others_max"] + config.MMD["club_path"]
                    wp = self.f("extreme.with_path", ext["with_path"], "count", ext["n"])
                    both = f"{{qs:{wp};{n}}}"  # 件数から選ぶ（◯球とも）
                    if usual:
                        text += f"パスは{{n:{n}}}球{both}測れていて、{self._range(lo, hi)} と普段どおりでした。クラブの進む向きは変わらず、当たる場所だけが外れた球です。"
                    else:
                        text += f"パスは{{n:{n}}}球{both}測れていて、{self._range(lo, hi)} でした。"
                self.c("s4", "miss.extreme_coverage", text)
        if mb["n_over"]:
            tot = self.f("miss_budget.total_m", mb["total_m"], "m", mb["n_side"])
            no = self.f("miss_budget.n_over", mb["n_over"], "count", mb["n_side"])
            parts = []
            for b in mb["by_cause"]:
                k = self.f(f"miss_budget.{b['cause']}.n", b["n"], "count", mb["n_over"])
                x = self.f(f"miss_budget.{b['cause']}.m", b["excess_m"], "m", b["n"])
                word = BUDGET_WORD[b["cause"]].format(ext=self.ext_word())
                if b["cause"] == "extreme_and_face" and b["n"] <= 3:
                    word += f"（{{seqs:{self.f('miss_budget.extreme_and_face.seqs', b['seqs'], 'seqs', b['n'])}}}）"
                part = f"{word} {{n:{k}}}球 {{a:{x}}}"
                if b.get("no_strike", {}).get("n"):
                    ns = self.f("miss_budget.face_to_path.no_strike.n", b["no_strike"]["n"], "count", b["n"])
                    nx = self.f("miss_budget.face_to_path.no_strike.m", b["no_strike"]["excess_m"], "m", b["no_strike"]["n"])
                    part += f"（うち {{a:{nx}}} の{{n:{ns}}}球は打点が測れておらず、{self.ext_word()}だった可能性が残ります）"
                parts.append(part)
            text = f"はみ出した距離 {{a:{tot}}} の内訳（図 F6。はみ出した{{n:{no}}}球）: " + "／".join(parts) + "。"
            if mb["mishit_included"]:
                text += "ミスヒットの候補も数えています。"
            self.c("s4", "miss.budget", text)
        if st["measured"]:
            n = self.f("strike.measured", st["measured"], "count", st["n"])
            md = self.f("strike.median_m", st["median_m"], "strike", st["measured"], "候補を除く")
            text = f"打点を測れた{{n:{n}}}球では、中央値が{{v:{md}}}で、"
            h = self.f("strike.heel", st["heel"], "count", st["measured"])
            t = self.f("strike.toe", st["toe"], "count", st["measured"])
            if st["heel"] >= st["toe"]:
                text += f"{{n:{h}}}球がヒール側に 10mm 以上ずれていました。トゥ側は{{n:{t}}}球です（図 F5）。"
            else:
                text += f"{{n:{t}}}球がトゥ側に 10mm 以上ずれていました。ヒール側は{{n:{h}}}球です（図 F5）。"
            self.c("s4", "miss.strike", text + self.excl_note("impact_offset"))

    # ---- ⑤ クラブをまたぐ傾向

    def s5(self, cross_claims: list[dict]) -> None:
        for cl in cross_claims:
            if self.sid in cl["scope_ids"]:
                self.sections["s5"].append(cl["claim"])
                for k, v in cl["facts"].items():
                    self.facts.items.setdefault(k, cl["fact_objs"][k])

    # ---- ⑥ 理想

    def s6(self) -> None:
        b, st, cov = self.p["band"], self.p["strike"], self.p["coverage"]
        if not b["used"]:
            if b["reason"] == "residual_too_large" and b["resid_sd_m"] is not None:
                sd = self.f("band.resid_sd_m", b["resid_sd_m"], "m", b["n_resid"], "候補を除く")
                n = self.f("band.n_resid", b["n_resid"], "count", b["n_resid"])
                hw = self.f("band.half_width_m", b["half_width_m"], "m", self.n_use, "帯の半幅（キャリーの中央値で）")
                hh = self.f("band.half_width_half_m", b["half_width_m"] * config.BAND_RESID_RATIO, "m", self.n_use, "式を使う上限（帯の半幅の1/2）")
                # 狙いの幅（帯の半幅）と、式を使うかの閾値（その半分）を取り違えない
                text = f"この種類は打点の影響が大きく、フェースとパスの数字だけでは落ちる場所が決まりません（予測だけで出した左右の残差のばらつき {{a:{sd}}}・{{n:{n}}}球。狙いの幅 ±{{a:{hw}}} の半分の {{a:{hh}}} を超えるので、この式は使いません）。"
                cns = cov["curved_no_strike"]
                if cns["of"]:
                    a_ = self.f("coverage.curved_no_strike.n", cns["n"], "count", cns["of"])
                    o = self.f("coverage.curved_no_strike.of", cns["of"], "count", cns["of"])
                    text += f"まず打点を測ってください（曲がった球のうち打点の無い球 {{n:{a_}}}/{{n:{o}}}）。"
                self.c("s6", "ideal.no_band", text)
            else:
                why = band_mod.reason_text(b["reason"]) or "この範囲は"
                self.c("s6", "ideal.no_band", f"{why}、まっすぐ落ちる範囲を計算していません。", layer="meta")
        else:
            cnt = self.f("band.counted", b["counted"], "count", self.n_use)
            ins = self.f("band.in", b["in"], "count", b["counted"])
            out = self.f("band.out", b["counted"] - b["in"], "count", b["counted"])
            il = self.f("band.in_landed", b["in_landed"], "count", b["in"])
            ol = self.f("band.out_landed", b["out_landed"], "count", b["counted"] - b["in"])
            all_landed = b["in"] > 0 and b["in_landed"] == b["in"]
            if b["in"]:
                text = f"インパクトが帯に入った{{n:{ins}}}球は、"
                text += f"{{n:{ins}}}球{{qs:{il};{ins}}}狙いの幅に落ちました" if all_landed else f"実際に{{n:{il}}}球が狙いの幅に落ちました"
                # 左右のばらつきは3球から（2球の SD は出さない）
                if b["in_side_sd_m"] is not None and b["in"] >= 3:
                    sd = self.f("band.in_side_sd_m", b["in_side_sd_m"], "m", b["in"])
                    text += f"（左右のばらつき {{a:{sd}}}）"
                text += f"。入らなかった{{n:{out}}}球で狙いの幅に落ちたのは{{n:{ol}}}球でした（帯を判定できた{{n:{cnt}}}球）。"
            else:
                text = f"インパクトが帯に入った球はありませんでした（帯を判定できた{{n:{cnt}}}球）。狙いの幅に落ちたのは{{n:{ol}}}球でした。"
            self.c("s6", "ideal.today", text, layer="L1")
            if all_landed:
                self.c("s6", "ideal.today_caveat", "今日の球では、計算と落ちた場所が合っていた、というところまでです（係数を同じ球で当てています）。帯に入れる練習が効くかは、実験で実際に落ちた球の数で確かめます。", layer="meta")
            elif b["in"]:
                # 合っていないのに「合っていた」と書かない（R1・R4）
                self.c("s6", "ideal.today_caveat", f"帯に入った球でも{{n:{ins}}}球中{{n:{il}}}球しか狙いの幅に落ちていません。計算と実際に落ちた場所がずれているので、帯に入れる練習が効くかは、実験で実際に落ちた球の数で確かめます。", layer="meta")
            if b["k_source"] == "pooled":
                cats = "・".join(config.CATEGORY_LABEL.get(c, c) for c in b["pooled"]["categories"])
                pk = self.f("band.pooled_n", b["pooled"]["n"], "count", b["pooled"]["n"])
                self.c("s6", "ideal.pooled_k", f"この種類は球が少ないので、{cats}の球（{{n:{pk}}}球）をまとめた係数で計算しています。", layer="meta")
            it = b["ideal_type"]
            if it and it["shown"]:
                orr = self.f("band.out_right", b["out_right"], "count", b["counted"])
                oll = self.f("band.out_left", b["out_left"], "count", b["counted"])
                basis = [ins, orr, oll]
                if it["kind"] == "trim":
                    opp = "left" if it["side"] == "right" else "right"
                    self.c("s6", "ideal.type", f"真ん中の球は今のままでよく、{{dir:{it['side']}}}へ外れた球を減らすのが近道です。帯の中の{{n:{ins}}}球は、全体を寄せると{{dir:{opp}}}へ外れる側に動きます。", basis=basis)
                elif it["kind"] == "shift":
                    self.c("s6", "ideal.type", f"全体が{{dir:{it['side']}}}にずれています。全体を寄せるのが近道です。", basis=basis)
                elif it["kind"] == "scatter":
                    self.c("s6", "ideal.type", "左右に散っています。まず揃えるところからです。", basis=basis)
            elif it:
                self.c("s6", "ideal.type_small", f"帯に入った球 {{n:{ins}}}/{{n:{cnt}}}（参考。球が少ないので、直し方の型と次の目標は出しません）。", layer="meta")
            if self.band_request_id:
                path = self.p["l1"]["club_path"]
                pm = self.f("l1.club_path.median", path["median"], "deg_lat", path["n"])
                # 帯は斜め（パスが右の球ではフェースも少し右でよい）。今のパスの向きの側で書く
                pd = "right" if path["median"] >= 0 else "left"
                self.c(
                    "s6", "ideal.window",
                    f"パスが今のまま（{{v:{pm}}}）なら、フェースが {{band:lo}}〜{{band:hi}} のとき、計算の上では左右が狙いの幅に入ります。帯は斜めで、パスが{{dir:{pd}}}の球ではフェースも少し{{dir:{pd}}}でかまいません。",
                    needs_band=True,
                )
                self.c("s6", "ideal.window_caveat", "TrackMan の数字から計算した範囲です。今日の球の範囲の外では当てにならない目安です。", layer="meta")
            self.stage_claim(b)
        if st["measured"] and (abs(st["median_m"]) >= config.CENTER_STRIKE_M or st["heel_extreme"] or st["toe_extreme"]):
            md = self.f("strike.median_m", st["median_m"], "strike", st["measured"])
            n = self.f("strike.measured", st["measured"], "count", st["n"])
            if abs(st["median_m"]) >= config.CENTER_STRIKE_M:
                text = f"打点: いまは中央値で{{v:{md}}}（{{n:{n}}}球）→ 目標は芯から 10mm 以内。"
            else:
                # もう満たしている目標を「目標」と書かない
                text = f"打点: 中央値はいまも芯から 10mm 以内です（{{v:{md}}}・{{n:{n}}}球）。"
            ke = st["heel_extreme"] + st["toe_extreme"]
            if ke:
                k = self.f("strike.extreme", ke, "count", st["measured"])
                text += f"{self.ext_word()}（30mm 超）: いまは測れた{{n:{n}}}球中 {{n:{k}}}球 → 目標は0球。"
            self.c("s6", "ideal.strike_goal", text)
        gr = self.p["good_reference"]
        # 「帯に入り芯に当たった球」は帯を使う範囲でだけ数えられる（帯を使わない種類で「0球」と書かない）
        if b["used"] and not gr["l1_good_enough"]:
            k = self.f("good_reference.l1_good_n", len(gr["l1_good_seqs"]), "count", self.n_use)
            need = self.f("good_reference.l1_good_needed", gr["l1_good_needed"], "count", self.n_use)
            self.c("s6", "ideal.good_ref", f"本人の良い球（インパクトが帯に入り、芯に当たった球）はまだ{{n:{k}}}球で、理想の打点の分布はまだ出せません（{{n:{need}}}球から）。", layer="meta")

    def stage_claim(self, b: dict) -> None:
        """次の目標（§7.3）。件数で書く（R4）。物差しはプランの最初の「いつも通り」で、今日の値から目標を作らない（§2.2）。
        帯の判定ができた球が10球未満なら出さない。今日すでにゴールに届いていれば、次の目標は作らない。"""
        stg = b["stage"]
        if not stg or not stg["shown"]:
            return
        cnt = self.f("band.counted", b["counted"], "count", self.n_use)
        ins = self.f("band.in", b["in"], "count", b["counted"])
        if stg["reached"]:
            self.c("s6", "ideal.stage", f"帯に入る球は、今日は{{n:{cnt}}}球中{{n:{ins}}}球で、もうゴール（4球中3球）に届いています。次の目標は作らず、実験では帯に入る球が減っていないかを見ます。", layer="L1")
            return
        # 1ブロックの球数は、フェースの候補の組み方（番手1本）から。無ければ10球で言う
        pb_n = next((c["design"]["per_block"] for c in ((self.cands or {}).get("candidates") or []) if c.get("design") and c.get("kpi") and c["kpi"]["metric"] == "face_angle"), 10)
        add_n = max(1, -(-int(round(pb_n * stg["step"] * 100)) // 100))
        pb = self.f("band.stage.per_block", pb_n, "count", pb_n)
        add = self.f("band.stage.add", add_n, "count", pb_n)
        self.c(
            "s6", "ideal.stage",
            f"帯に入る球は、今日は{{n:{cnt}}}球中{{n:{ins}}}球でした（きっかけの数で、物差しにはしません）。次の目標は、実験の最初の「いつも通り」で数えた数より、{{n:{pb}}}球あたり{{n:{add}}}球多く入れることです（ゴールは4球中3球）。",
            layer="L1",
        )

    # ---- ⑦ 次にやること

    def s7(self, cands: dict) -> None:
        if not cands or not cands["candidates"]:
            self.c("s7", "next.none", "今日の球では、先に直すものを1つに決める材料がありません。", layer="meta")
            return
        by = {c["id"]: c for c in cands["candidates"]}
        mb = self.p["miss_budget"]
        for slot, cid in (("now", cands["now"]), ("next", cands["next"])):
            if not cid:
                continue
            c = by[cid]
            head = "いま" if slot == "now" else "次"
            title = cand_title(c)
            text = f"{head}: {title}。"
            if c.get("kind") == "measure":
                g0 = c["gate0"]
                k = self.f(f"cand.{cid}.g0.measured", g0["measured"], "count", g0["of"])
                o = self.f(f"cand.{cid}.g0.of", g0["of"], "count", g0["of"])
                text = f"{head}: まず{METRIC_WORD[c['kpi']['metric']]}を測る。{c['club']['club']}で{METRIC_WORD[c['kpi']['metric']]}が取れた球が{{n:{o}}}球中{{n:{k}}}球しかなく、このまま実験しても判定できません。"
                if c["kpi"]["metric"] == "impact_offset":
                    text += "インパクトシールで見て、「1球ずつ」で打点を入れてください。"
                self.c("s7", f"next.{slot}", text, layer="L1_plan")
                continue
            if slot == "now":
                gates = c["why_first"]["gates"]
                if "G1" in gates and mb["strike_range_m"] and mb["total_m"] > 0:
                    lo = self.f("cand.g1.share_lo", mb["strike_range_m"][0] / mb["total_m"], "pct", mb["n_side"])
                    hi = self.f("cand.g1.share_hi", mb["strike_range_m"][1] / mb["total_m"], "pct", mb["n_side"])
                    text += f"はみ出した距離の {{v:{lo}}}〜{{v:{hi}}} がここから。しかもこの球はフェースが測れないことが多いので、先にフェースの実験をすると結果が濁ります。"
                elif "G2" in gates:
                    g2 = cands["gates"]["G2"]
                    r = self.f("cand.g2.r", g2["r"], "coef", g2["n"])
                    n = self.f("cand.g2.n", g2["n"], "count", g2["n"])
                    rng = ""
                    if g2.get("ci95"):
                        a_ = self.f("cand.g2.ci_lo", g2["ci95"][0], "coef", g2["n"])
                        b_ = self.f("cand.g2.ci_hi", g2["ci95"][1], "coef", g2["n"])
                        rng = f"・95%区間 {{v:{a_}}}〜{{v:{b_}}}"
                    text += f"打点とフェースの向きが一緒に動いていて（相関 {{v:{r}}}{rng}・{{n:{n}}}球）、フェースだけを直した効果を読み分けられません。"
                    if g2["borderline"]:
                        text += "ただし、ぎりぎりの判定なので、フェースから始めてもかまいません（どちらを選んでも、もう一方も一緒に記録します）。"
                elif "G3" in gates:
                    text += "薄い当たり（トップ）の球では曲がりの原因が出ないので、先に減らします。"
                elif c["n_shots"]:
                    x = self.f(f"cand.{cid}.excess_m", c["excess_m"], "m", mb["n_side"])
                    k = self.f(f"cand.{cid}.n_shots", c["n_shots"], "count", mb["n_over"])
                    text += f"はみ出した距離が一番大きい（{{n:{k}}}球・{{a:{x}}}）。"
                if c["id"] in plan.STRIKE_ISSUES and cands["next"]:
                    text += "打点を先にするのは測りやすさの順で、物理の順ではありません。"
            else:
                if c["blocked_by"]:
                    first = c["blocked_by"][0].get("first")
                    if first:
                        text += f"「{cand_title(by[first]) if first in by else CAND_TITLE.get(first, first)}」が先です（理由は上のとおり）。"
                if c["n_shots"]:
                    # 件数ははみ出しが0より大きい球だけ（④の内訳と同じ数え方。§5.3）
                    x = self.f(f"cand.{cid}.excess_m", c["excess_m"], "m", mb["n_side"])
                    k = self.f(f"cand.{cid}.n_shots", c["n_shots"], "count", mb["n_over"])
                    text += f"残りのはみ出しのうち {{n:{k}}}球・{{a:{x}}} がここに当たります（④の内訳の図）。"
            self.c("s7", f"next.{slot}", text, layer="L1_plan")
            for it in c["issues"]:
                if it["borderline"] and it["alt_issue"]:
                    v = it["values"]
                    mean = self.f(f"cand.{cid}.{it['issue']}.mean", v["mean"], "deg_lat", v["n"])
                    half = self.f(f"cand.{cid}.{it['issue']}.half_sd", v["half_sd"], "deg", v["n"])
                    what = "曲がり" if it["issue"].startswith("curve") else "打ち出し"
                    metric = "フェース・トゥ・パス" if it["issue"].startswith("curve") else "フェースの向き"
                    if it["issue"].endswith("_var"):
                        alt_side = it["alt_issue"].rsplit("_", 1)[1]
                        self.c("s7", f"next.{slot}.borderline", f"{what}の向きは、ぎりぎりの判定で「左右に散る」にしました（{metric}の平均 {{v:{mean}}} が、ばらつきの半分 {{a:{half}}} にわずかに届かない）。「{{dir:{alt_side}}}へ{'曲がる' if what == '曲がり' else '出る'}」と読んでも、動かすのは同じ{METRIC_WORD.get(plan.LEVER_METRIC.get(c['lever'] or 'face', 'face_angle'), 'フェースの向き')}です。", layer="L1_plan")
                    else:
                        self.c("s7", f"next.{slot}.borderline", f"{what}の向きは、ぎりぎりの判定です（{metric}の平均 {{v:{mean}}}・ばらつきの半分 {{a:{half}}}）。「左右に散る」と読むこともできます。", layer="L1_plan")
            self.hypothesis(slot, c)
            self.guards(slot, c)
            self.advance(slot, c)
        for u in cands["untouched"]:
            if u["metric"] == "club_path" and u["sd"] is not None:
                sd = self.f("l1.club_path.sd", u["sd"], "deg", u["n"])
                self.c("s7", "next.untouched", f"今は触らないこと: パス（もう揃っています。ばらつき ±{{v:{sd}}}）。", layer="L1_plan")
        for ref in cands["reference"]:
            k = self.f("thin.n", ref["thin"], "count", ref["of"])
            o = self.f("thin.of", ref["of"], "count", ref["of"])
            self.c("s7", "next.reference_thin", f"参考: 薄い当たり（極端なトップ）が{{n:{o}}}球中{{n:{k}}}球ありました（極端なトップしか拾えない数え方です）。", layer="L1_plan")

    def guards(self, slot: str, c: dict) -> None:
        """動かさないもの（§8.2）。trim 型のときに decrease が「全体を反対へずらす」で良く出るのを塞ぐ。"""
        gs = c.get("guards") or []
        land = next((g for g in gs if g["kind"] == "landed_opposite"), None)
        win = next((g for g in gs if g["kind"] == "face_median_window"), None)
        if not land or not win or win["median"] is None:
            return
        opp = land["side"]
        k = self.f(f"cand.{c['id']}.guard.landed_opposite", land["n"], "count", land["of"])
        o = self.f(f"cand.{c['id']}.guard.of", land["of"], "count", land["of"])
        md = self.f("l1.face_angle.median", win["median"], "deg_lat", win["n"], "候補を除く")
        # 窓の端: 保存は右打ちの座標。左打ちは画面が {band:lo} / {band:hi} を入れ替えて埋めるので、表示の側で選び直す
        edge_lo = win["edge"] == "lo"
        if self.hand == "L":
            edge_lo = not edge_lo
        edge = "{band:lo} を下回らない" if edge_lo else "{band:hi} を上回らない"
        near = "right" if opp == "left" else "left"
        self.c(
            "s7", f"{slot}.guards",
            f"動かさないもの: 帯の{{dir:{opp}}}へ外れて落ちた球の数（今日は{{n:{o}}}球中{{n:{k}}}球）と、フェースの中央値（今日 {{v:{md}}}）が {edge}こと。どちらかが崩れたら「{{dir:{near}}}の球は減ったが、{{dir:{opp}}}へ外れる球が増えた」として、効いたに数えません。",
            layer="L1_plan", needs_band=True,
        )

    def advance(self, slot: str, c: dict) -> None:
        """次に進む条件（§8.2）。画面（ステップの表）はこの文をそのまま出す（画面で作らない）。"""
        if c.get("kind") == "measure" or not c.get("kpi"):
            return
        worked = "「効いた」（別の日の2回の練習で、続けて moderate 以上）"
        if c["id"] in ("strike_heel", "strike_toe"):
            text = f"次に進む条件: {worked}か、練習の最初の「いつも通り」で打点の中央値が芯から 10mm 以内。"
        elif c["kpi"]["metric"] == "face_angle" and self.p["band"]["used"]:
            text = f"次に進む条件: {worked}、帯に入る球の数が次の目標に届く、実際に帯へ落ちた球が減っていない"
            text += "、動かさないものが崩れていない。" if c.get("guards") else "。"
        else:
            text = f"次に進む条件: {worked}。"
        self.c("s7", f"{slot}.advance", text, layer="L1_plan")

    def hypothesis(self, slot: str, c: dict) -> None:
        ch = c.get("club")
        if not ch or not c["kpi"]:
            return
        metric = c["kpi"]["metric"]
        club = ch["club"]
        sid = f"{self.sid}/{slot}"
        # 番手の値は番手の範囲の fact にする（群の値を番手の名前で書かない。R2）
        cf = Facts()
        cscope = f"club:{club}"
        cp = (self.ctx.get("club_profiles") or {}).get(club)

        def add(name, value, unit, n):
            return cf.add(f"{cscope}/{slot}.{name}", value, unit, scope_id=cscope, n=n, label=club)

        def gadd(name, value, unit, n):
            return cf.add(f"{self.sid}/{slot}.{name}", value, unit, scope_id=self.sid, n=n, label=self.label)

        n = add("measured", ch["measured"], "count", ch["n"])
        if metric == "impact_offset":
            md = add("median", ch["median"], "strike", ch["measured"]) if ch.get("median") is not None else None
            ext = ch["heel_extreme"] if c["id"] != "strike_toe" else ch["toe_extreme"]
            k = add("extreme", ext, "count", ch["n"])
            word = "ヒール側" if c["id"] != "strike_toe" else "トゥ側"
            obs = f"{club}で{word}に当たっています（打点の中央値 {{v:{md}}}・{{n:{n}}}球、30mm を超える{'ヒール' if word == 'ヒール側' else 'トゥ'} {{n:{k}}}球）。" if md else ""
            if c["id"] == "strike_scatter":
                sd = add("sd", ch.get("sd") or 0.0, "mm", ch["measured"])
                obs = f"{club}で打点が散っています（ばらつき ±{{v:{sd}}}・{{n:{n}}}球）。"
                hyp = "仮説: 打点のばらつきを減らすと、左右に大きく外れる球が減る。"
            else:
                # 外れる向きは決め打ちにせず、極端な打点の球が実際に落ちた左右の平均の向きから入れる（R1）
                contact = "heel_extreme" if c["id"] == "strike_heel" else "toe_extreme"
                pool = [x for x in self.shots if x["id"] not in self.skip and dec(x).get("contact") == contact and m(x, "side") is not None]
                club_pool = [x for x in pool if x.get("club") == club]
                use = club_pool or pool
                if use:
                    mean_side = sum(m(x, "side") for x in use) / len(use)
                    sf = add("extreme_side_mean", mean_side, "m_lat", len(use)) if club_pool else gadd("extreme_side_mean", mean_side, "m_lat", len(use))
                    hyp = f"仮説: 打点を芯へ寄せると、{{dirof:{sf}}}へ大きく外れる球が減る。"
                else:
                    hyp = "仮説: 打点を芯へ寄せると、左右に大きく外れる球が減る。"
        else:
            mean = add("mean", ch.get("mean"), "deg_lat", ch["measured"]) if ch.get("mean") is not None else None
            goal = c["kpi"]["goal"]
            if goal == "reduce_sd":
                sd = add("sd", ch.get("sd") or 0.0, "deg", ch["measured"])
                obs = f"{club}で{METRIC_WORD[metric]}が散っています（ばらつき ±{{v:{sd}}}・{{n:{n}}}球）。"
                hyp = f"仮説: {METRIC_WORD[metric]}のばらつきを減らすと、左右に外れる球が減る。"
            else:
                # 直したい向き（外れている側）。パスは曲がりを減らすために逆へ動かすので、曲がりの向きから決める
                if metric == "club_path":
                    side = "left" if goal == "increase" else "right"  # 曲がっている側
                    obs = f"{club}でクラブパスが平均 {{v:{mean}}} です（{{n:{n}}}球）。" if mean else ""
                    move = "right" if goal == "increase" else "left"
                    hyp = f"仮説: クラブパスを少し{{dir:{move}}}へ寄せて、フェースとの差を小さくすると、{{dir:{side}}}へ曲がる球が減る。"
                elif metric == "face_to_path":
                    side = "right" if goal == "decrease" else "left"
                    obs = f"{club}でフェース・トゥ・パスが{{dir:{side}}}へ曲がる側に偏っています（平均 {{v:{mean}}}・{{n:{n}}}球）。" if mean else ""
                    hyp = f"仮説: フェースとパスの差を小さくすると、{{dir:{side}}}へ曲がる球が減る。"
                else:
                    side = "right" if goal == "decrease" else "left"
                    obs = f"{club}で{METRIC_WORD[metric]}が{{dir:{side}}}寄りです（平均 {{v:{mean}}}・{{n:{n}}}球）。" if mean else ""
                    hyp = f"仮説: {METRIC_WORD[metric]}をまっすぐ落ちる範囲へ寄せると、{{dir:{side}}}へ外れる球が減る。"
                # 番手だけでは偏りが言い切れないなら、そう書く（9番の③と逆のことを言わない。§6.1）
                cl1 = (cp or {}).get("l1", {}).get(metric) if cp else None
                if mean and cl1 and cl1.get("bias") != side:
                    if cl1.get("ci95"):
                        lo = add("ci_lo", cl1["ci95"][0], "deg_lat", cl1["n"])
                        hi = add("ci_hi", cl1["ci95"][1], "deg_lat", cl1["n"])
                        rng = self._range(lo, hi)
                        obs = f"{club}で{METRIC_WORD[metric]}は平均 {{v:{mean}}}（{{n:{n}}}球）です。{club}だけでは平均の95%区間が {rng} で0をまたぎ、偏りは言い切れません。"
                    else:
                        obs = f"{club}で{METRIC_WORD[metric]}は平均 {{v:{mean}}}（{{n:{n}}}球）です。球が少なく、{club}だけでは偏りは言い切れません。"
                    gl1 = self.p["l1"][metric]
                    if self.p["scope"] == "group" and gl1.get("bias") == side:
                        gm = gadd("group_mean", gl1["mean"], "deg_lat", gl1["n"])
                        gn = gadd("group_n", gl1["n"], "count", gl1["n"])
                        obs += f"{self.label}全体では平均 {{v:{gm}}}（{{n:{gn}}}球）で、{{dir:{side}}}寄りです。"
        claim = Claim(id=f"{sid}.hypothesis", section="s7", layer="L1_plan", template=obs + hyp)
        built = build_claim(claim, cf, self.hand)
        self.facts.items.update(cf.items)
        self.sections["s7"].append(built)
        d = c.get("design")
        if d:
            fs = Facts()
            # 組み方は番手1本で打つので、ばらつきも番手の値（design_scope == club）。出せないときだけ群の値で、範囲を群と書く
            by_club = c.get("design_scope") == "club"
            dscope, dlabel = (cscope, club) if by_club else (self.sid, self.label)

            def addd(name, value, unit, nn):
                return fs.add(f"{dscope}/{slot}.design.{name}", value, unit, scope_id=dscope, n=nn, label=dlabel)

            pb = addd("per_block", d["per_block"], "count", d["n_per_condition"])
            tot = addd("total", d["total"], "count", d["total"])
            text = f"組み方の目安: {club}で A-B-B-A（「いつも通り」と「本番」を各{{n:{pb}}}球ずつ2回。準備とドリルを入れて約{{n:{tot}}}球）。"
            if metric == "impact_offset" and d["per_block_if_missing"] != d["per_block"]:
                pm = addd("per_block_if_missing", d["per_block_if_missing"], "count", d["n_per_condition_if_missing"])
                text += f"打点が「-」の球はシールで見て「1球ずつ」で入れてください（入れないなら各{{n:{pm}}}球）。"
            if not d["one_session"]:
                sd_n = ch["measured"] if by_club else self.p["l1"][metric].get("n", 0)
                sd_id = addd("sd", d["sd"], "mm" if metric == "impact_offset" else "deg", sd_n)
                sdn = addd("sd_n", sd_n, "count", sd_n)
                nc = addd("n_per_condition", d["n_per_condition"], "count", d["n_per_condition"])
                ss = addd("sessions", d["sessions"], "count", d["n_per_condition"])
                who = club if by_club else f"{self.label}全体"
                text += (
                    f"{who}の今日のばらつき（±{{v:{sd_id}}}・{{n:{sdn}}}球）では、小さな変化を判定するのに条件ごとに約{{n:{nc}}}球が要り、1回の練習では届きません。"
                    f"同じ型を約{{n:{ss}}}回の練習で積んで見ます（1回でわかるのは、大きく動いたかどうかまでです）。"
                )
            built = build_claim(Claim(id=f"{sid}.design", section="s7", layer="L1_plan", template=text), fs, self.hand)
            self.facts.items.update(fs.items)
            self.sections["s7"].append(built)

    # ---- ⑧ まだ言えないこと

    def s8(self, hints: list[dict]) -> None:
        l1 = self.p["l1"]
        what = []
        if l1["face_angle"].get("bias"):
            what.append("フェースの向きが偏る理由")
        if self.p["strike"]["heel"] or self.p["strike"]["toe"]:
            what.append("打点がずれる理由")
        head = "・".join(what) if what else "ボールがこう飛んだ理由のうち、クラブより手前のこと"
        self.c("s8", "unknown.body", f"{head}（体の動き・構え）は、TrackMan の数字からは言えません。いま言えるのは、インパクトでクラブとボールがどうだったかまでです。", layer="hint")
        for h in hints:
            look = "」と「".join(h["look"])
            self.c("s8", f"unknown.video.{h['key']}", f"動画を入れたら、まず「{look}」を見る候補にします（調べる候補であって、診断ではありません）。", layer="hint")
        # 打点が「-」の球は、ミスヒットの候補も数える（候補は一番悪い球で、そこが欠けている。R6・R7）
        miss = [x for x in self.shots if m(x, "impact_offset") is None]
        if miss:
            k = self.f("strike.missing_all", len(miss), "count", self.p["n"])
            s = self.f("strike.missing_seqs", [x["seq"] for x in miss], "seqs", self.p["n"])
            nm = sum(1 for x in miss if x["id"] in self.skip)
            tail = ""
            if nm:
                km = self.f("strike.missing_mishit", nm, "count", len(miss))
                tail = f"。うちミスヒットの候補 {{n:{km}}}球"
            self.c("s8", "unknown.strike_missing", f"打点が「-」の球が{{n:{k}}}球あります（{{seqs:{s}}}{tail}）。インパクトシールの結果を「1球ずつ」で入れると、打点が原因かどうか分からない球が埋まります。", layer="meta")
        use = [s for s in self.shots if s["id"] not in self.skip]
        for key, text in MISSING_TILES:
            # セッションの全部の球で無い項目は、レポートの末尾に1回だけ出す（範囲ごとに同じ行を並べない）
            if key in self.ctx.get("session_missing", ()):
                continue
            if use and all(m(s, key) is None for s in use):
                self.c("s8", f"unknown.no_{key}", text, layer="meta")
        gr = self.p["good_reference"]
        if gr["lenient"]:
            clubs = sorted({s.get("club") for s in self.shots if s["seq"] in gr["lenient"]})
            s = self.f("good_reference.lenient", gr["lenient"], "seqs", gr["n"])
            self.c("s8", "unknown.lenient", f"{'・'.join(clubs)}は球が少なく（3球未満）、キャリーの判定ができていません（Good の {{seqs:{s}}} はキャリーを見ずに判定されています）。", layer="meta")
        lr = self.p["launch_residual_sd"]
        if lr and lr["below_info"] and lr["n"] >= config.MIN_SCOPE_N:
            v = self.f("launch_residual_sd", lr["value"], "deg", lr["n"], "候補を除く")
            n = self.f("launch_residual_n", lr["n"], "count", lr["n"])
            self.c("s8", "unknown.face_computed", f"フェースの値は、TrackMan が打ち出しとパスから計算している可能性があります（打ち出しの予測との差のばらつきが {{v:{v}}} と小さい・{{n:{n}}}球）。そのため答え合わせは、実際に落ちた場所で行います。", layer="meta", finding_ids=self.fid("face_maybe_computed"))
        if self.n_use < config.MIN_SCOPE_N:
            self.small_note("s8", "unknown.small", "図と詳しい節は出していません")
        cats = [x for x in self.sections["s8"]]
        if not cats:  # R5: ⑧は空にしない
            self.c("s8", "unknown.default", "体の動きは、動画があって初めて言えます。", layer="hint")

    # ---- 畳んだ版の①（その1本の数字だけ。群の結論を繰り返さない）

    def s1_short(self) -> None:
        n = self.f("n", self.p["n"], "count", self.p["n"])
        cell = self.top_cell()
        text = f"{self.label}は{{n:{n}}}球です。"
        if cell and cell["n"] >= 2:
            k = self.f(f"cell.{cell['key']}.n", cell["n"], "count", self.p["n"])
            text += f"一番多いのは{self.cell_phrase(cell['key'])}球で{{n:{k}}}球でした。"
        self.c("s1", "summary.short", text, layer="L0")
        l1 = self.p["l1"]
        parts = []
        for metric, word in (("face_angle", "フェースの向き"), ("club_path", "クラブパス")):
            x = l1[metric]
            if x.get("n", 0) >= 2:
                mean = self.f(f"l1.{metric}.mean", x["mean"], "deg_lat", x["n"])
                nn = self.f(f"l1.{metric}.n", x["n"], "count", x["n"])
                parts.append(f"{word}は平均 {{v:{mean}}}（{{n:{nn}}}球）")
        if parts:
            self.c("s1", "summary.short_l1", "、".join(parts) + "でした。" + self.excl_note())

    # ---- まとめ

    def build(self, cross_claims: list[dict], cands: dict | None, hints: list[dict], with_s3: bool) -> dict:
        self.cands = cands
        if self.kind == "main":
            self.s1()
            self.s2()
            self.s3()
            self.s4()
            self.s5(cross_claims)
            self.s6()
            self.s7(cands)
            self.s8(hints)
        else:
            self.s1_short()
            if self.n_use < config.MIN_SCOPE_N:
                self.small_note("s1", "summary.small", "数字は参考です")
            if with_s3:
                self.s3()
            self.s8(hints)
        sections = []
        for key in ("s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"):
            cl = self.sections.get(key)
            if not cl:
                continue
            no, title = SECTION_TITLES[key]
            figs = [f for f in SECTION_FIGURES.get(key, []) if self.kind == "main"]
            if key == "s6" and not self.p["band"]["used"]:
                figs = []  # 帯を使わない種類では、②③と同じ絵になるので⑥に図を出さない（§7.1）
            sections.append({"id": key, "no": no, "title": title, "lead": cl[0]["text"], "claims": cl, "figures": figs})
        return sections


def _hash(obj) -> str:
    def rnd(x):
        if isinstance(x, float):
            return round(x, 4)
        if isinstance(x, dict):
            return {k: rnd(v) for k, v in x.items()}
        if isinstance(x, list):
            return [rnd(v) for v in x]
        return x

    return hashlib.sha256(json.dumps(rnd(obj), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _cross_claims(cross: dict, hand: str) -> list[dict]:
    """⑤の定型文。単位ごとの fact はその範囲のもの（R2）。「〜だけ」とは言わない。"""
    out = []
    word = {
        ("impact_offset", "heel"): ("打点がヒール側に偏る傾向", "ヒール側"),
        ("impact_offset", "toe"): ("打点がトゥ側に偏る傾向", "トゥ側"),
        ("face_angle", "right"): ("フェースが{dir:right}を向く偏り", "{dir:right}"),
        ("face_angle", "left"): ("フェースが{dir:left}を向く偏り", "{dir:left}"),
        ("club_path", "right"): ("パスが{dir:right}向きに偏る傾向", "{dir:right}"),
        ("club_path", "left"): ("パスが{dir:left}向きに偏る傾向", "{dir:left}"),
    }
    for i, e in enumerate(cross["entries"]):
        facts = Facts()
        descs = []
        for u in e["units"]:
            sid, label = u["scope_id"], u["label"]
            unit = "strike" if e["metric"] == "impact_offset" else "deg_lat"
            n = facts.add(f"{sid}/cross.{e['metric']}.n", u["n"], "count", scope_id=sid, n=u["n"], label=label)
            mean = facts.add(f"{sid}/cross.{e['metric']}.mean", u["mean"], unit, scope_id=sid, n=u["n"], label=label, note="候補を除く")
            d = f"{{n:{n}}}球で平均 {{v:{mean}}}"
            mt = u.get("mishit_total", u["mishit_excluded"])
            if mt:
                # 除いた数は範囲の候補の総数で書き、指標を測れた球数は別に書く（指標ごとに除いた数が変わって見えないように）
                k = facts.add(f"{sid}/cross.mishit_total", mt, "count", scope_id=sid, n=u["n"] + mt, label=label)
                d = f"ミスヒットの候補 {{n:{k}}}球を除き、{METRIC_WORD[e['metric']]}を測れた" + d
            if u.get("center_inside"):
                d += "。芯の範囲の中で、ややヒール側" if u["mean"] < 0 else "。芯の範囲の中で、ややトゥ側"
            descs.append(f"{label}（{d}）")
        title, dirw = word[(e["metric"], e["direction"])]
        if e["kind"] == "common":
            both = "の両方" if len(descs) == 2 else f"の{len(descs)}つ"
            text = f"{title}は、{'と'.join(descs)}{both}に出ています。"
        elif e["kind"] == "same_direction_unclear":
            text = f"{'と'.join(descs)}も平均は{dirw}ですが、区間が0をまたいでいて、今日の球数では言い切れません。"
        else:
            if e["reason"] == "opposite_bias":
                text = f"{descs[0]}は型が違います（ほかと逆の向きに偏っています）。{title}です。"
            else:
                # 「ほかには無い」とは言わない（区間が0をまたぐのは、無いことの証拠ではない。§5.1）
                text = f"{descs[0]}は型が違います。{title}が出ています（ほかの範囲では、この向きの偏りははっきりとは出ていません）。"
        built = build_claim(Claim(id=f"cross/{i}.{e['metric']}.{e['kind']}", section="s5", layer="L1", template=text), facts, hand)
        # ⑤はクラブをまたぐ話なので、比べた単位（本体の範囲）の全部の⑤に同じ文を出す
        # ⑤はその傾向に出てくる範囲のカードにだけ出す（全部のカードに同じ5段落を並べない。全体は末尾に1回）
        out.append({"claim": built, "scope_ids": [u["scope_id"] for u in e["units"]], "facts": facts.as_dict(), "fact_objs": facts.items, "entry": e})
    return out


def build_report(shots: list[dict], handedness: str = "R", experiments: list | None = None) -> dict:
    hand = "L" if handedness == "L" else "R"
    an = analyze_session(shots)
    by_club: dict[str, list[dict]] = defaultdict(list)
    for s in shots:
        if s.get("excluded"):
            continue
        by_club[s.get("club") or "(クラブ不明)"].append(s)
    good_ids = {j["id"] for c in an["clubs"] for j in c["good"]["shots"] if j["good"]}
    grouped = {n: g for g in an["groups"] for n in g["clubs"]}
    carry_medians = {c["club"]: c["carry_median"] for c in an["clubs"]}
    club_profiles = {c["club"]: c["profile"] for c in an["clubs"]}
    live = [s for s in shots if not s.get("excluded")]
    session_missing = {key for key, _ in MISSING_TILES if live and all(m(s, key) is None for s in live)}
    ctx = {"hand": hand, "findings": an["findings"], "club_profiles": club_profiles, "session_missing": session_missing}

    cross = an["cross_club"]
    cross_claims = _cross_claims(cross, hand)
    units = cross_units(an["clubs"], an["groups"])

    scopes_out = []
    plans = {}
    chosen_clubs = set()

    order = [("group", g) for g in an["groups"]] + [("club", c) for c in an["clubs"]]
    # 1回目: 本体の範囲の候補（畳んだ1本に③を足すかを決めるため先に）
    for kind, u in order:
        name = u.get("name") or u.get("club")
        if kind == "club" and name in grouped:
            continue
        if u["profile"]["n"] - u["profile"]["mishit_excluded"] < config.MIN_SCOPE_N:
            continue
        clubs = u.get("clubs") or [u["club"]]
        sh = [s for c in clubs for s in by_club[c]]
        skip = {s["id"] for s in sh if s["seq"] in u["profile"]["mishit_seqs"]}
        plans[u["profile"]["scope_id"]] = plan.candidates(u["profile"], sh, skip, good_ids)
        now = next((c for c in plans[u["profile"]["scope_id"]]["candidates"] if c["id"] == plans[u["profile"]["scope_id"]]["now"]), None)
        if now and now.get("club") and kind == "group":
            chosen_clubs.add(now["club"]["club"])

    for kind, u in order:
        name = u.get("name") or u.get("club")
        p = u["profile"]
        clubs = u.get("clubs") or [u["club"]]
        sh = [s for c in clubs for s in by_club[c]]
        skip = {s["id"] for s in sh if s["seq"] in p["mishit_seqs"]}
        n_use = p["n"] - p["mishit_excluded"]
        if kind == "club" and name in grouped:
            sk = "folded" if n_use >= config.MIN_SCOPE_N else "short"
        else:
            sk = "main" if n_use >= config.MIN_SCOPE_N else "short"
        sr = ScopeReport(ctx, u, sk, name, sh, skip)
        symptoms = video_candidates.detect(p, sh, skip) if sk == "main" else []
        keys = [f"symptom:{x['id']}" for x in symptoms]
        for e in cross["entries"]:
            if e["kind"] == "common" and any(x["scope_id"] == p["scope_id"] for x in e["units"]):
                keys.append(f"cross_club.common:{e['metric']}:{e['direction']}")
        hints = video_candidates.hints_for(keys) if sk == "main" else []
        cands = plans.get(p["scope_id"])
        try:
            sections = sr.build(cross_claims, cands, hints, with_s3=(sk == "folded" and name in chosen_clubs))
        except ClaimError as e:  # 定型文の作りの誤り。黙って落とさず、範囲ごと理由を返す
            sections = [{"id": "error", "no": "", "title": "解説を作れませんでした", "lead": str(e), "claims": [], "figures": []}]
        except Exception as e:  # noqa: BLE001  1つの範囲の誤りで、ほかの範囲の解説まで消さない
            log.exception("解説の範囲 %s を作れませんでした", p["scope_id"])
            sections = [{"id": "error", "no": "", "title": "解説を作れませんでした", "lead": f"この範囲の解説を作る途中で誤りが出ました（{type(e).__name__}）。ほかの範囲と1球ずつの分解は使えます。", "claims": [], "figures": []}]
        figs = {}
        req = None
        if sk == "main" and sections and sections[0]["id"] != "error":
            req = figures.band_request(p, sr.band_request_id) if sr.band_request_id else None
            r2 = p["sensitivity"].get("r2_single", {}).get("face_angle") if p["sensitivity"]["status"] == "ok" else None
            not_shown = [s for s in sh if m(s, "face_angle") is None or m(s, "club_path") is None]
            ne = sum(1 for s in not_shown if is_extreme(s))
            missing_line = f"この図にない球: {len(not_shown)}" + (f"（うち{sr.ext_word()} {ne}）" if ne else "")
            r2_line = f"フェースの向きだけで左右の散りの{round(r2 * 100)}%を説明（{p['sensitivity']['n']}球・単回帰）" if r2 is not None else None
            figs = {
                "F1": figures.f1(p, sh, skip, good_ids, carry_medians),
                "F2": figures.f2(p),
                "F3": figures.f3(p, sh, skip, good_ids, sr.band_request_id if req else None, r2_line, missing_line, hand),
                "F4": figures.f4(p, sr.band_request_id if req else None),
                "F5": figures.f5(p, sh, skip, good_ids, hand),
                "F6": figures.f6(p),
                "F7": figures.f7(units),
            }
            figs = {k: v for k, v in figs.items() if v}
            # 読み上げ（desc）は、その図が伝えることを言う主張の定型文と同じ文（§5.6）
            texts = {c["id"].split("/", 1)[1]: c["text"] for sec in sections for c in sec["claims"]}
            s5 = next((sec["claims"][0]["text"] for sec in sections if sec["id"] == "s5" and sec["claims"]), None)
            for fid, keys in FIGURE_DESC.items():
                if fid not in figs:
                    continue
                figs[fid]["desc"] = next((texts[k] for k in keys if k in texts), s5 if fid == "F7" else None)
                figs[fid]["desc_claim"] = next((f"{p['scope_id']}/{k}" for k in keys if k in texts), None)
        scopes_out.append(
            {
                "scope_id": p["scope_id"],
                "scope": p["scope"],
                "kind": sk,
                "label": name,
                "category": p["category"],
                "clubs": clubs,
                "folded_into": f"group:{grouped[name]['club_category']}" if (kind == "club" and name in grouped) else None,
                "n": p["n"],
                "mishit_excluded": p["mishit_excluded"],
                "mishit_seqs": p["mishit_seqs"],
                "sections": sections,
                "figures": figs,
                "facts": sr.facts.as_dict(),
                "candidates": _public_candidates(cands) if cands and sk == "main" else None,
                "band_request": req,
                "symptoms": symptoms,
            }
        )

    # セッションの全部の球で取っていない項目（⑧の共通の行）。範囲ごとに並べず、末尾に1回だけ出す
    session_unknowns = [
        build_claim(Claim(id=f"session/unknown.no_{key}", section="s8", layer="meta", template=text), Facts(), hand)
        for key, text in MISSING_TILES if key in session_missing
    ]
    all_facts = {k: v for s in scopes_out for k, v in s["facts"].items()}
    for c in cross_claims:
        all_facts.update(c["facts"])
    return _slim_floats({
        "report_version": config.REPORT_VERSION,
        "engine_version": config.ENGINE_VERSION,
        "plan_version": config.PLAN_VERSION,
        "handedness": hand,
        "generated_by": "template",
        "facts_hash": _hash(all_facts),
        "n_shots": an["n_shots"],
        "n_excluded": an["n_excluded"],
        "scopes": scopes_out,
        "session_unknowns": session_unknowns,
        "cross_club": {**cross, "claims": [c["claim"] for c in cross_claims]},
        "bands": {k: {kk: vv for kk, vv in v.items() if kk != "targets"} for k, v in an["bands"].items()},
        "glossary": glossary(hand),
        "video_symptoms_version": video_candidates.VERSION,
    })


def _slim_floats(x):
    """応答を軽くするため、小数を有効数字6桁に丸める（打点は m なので桁数で切らない）。"""
    if isinstance(x, float):
        return float(f"{x:.6g}")
    if isinstance(x, dict):
        return {k: _slim_floats(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_slim_floats(v) for v in x]
    return x


def _public_candidates(c: dict) -> dict:
    """候補の中身（画面と Go のプランが読む）。球の dict は持たない。"""

    def slim(x):
        return {k: v for k, v in x.items() if k not in ("issues",)} | {"issues": [{kk: vv for kk, vv in i.items()} for i in x["issues"]]}

    return {**{k: v for k, v in c.items() if k not in ("candidates", "count_only")}, "candidates": [slim(x) for x in c["candidates"]], "count_only": [slim(x) for x in c["count_only"]]}


_GLOSSARY = None


def glossary(hand: str) -> list[dict]:
    import os

    global _GLOSSARY
    if _GLOSSARY is None:
        with open(os.path.join(os.path.dirname(__file__), "glossary.json"), encoding="utf-8") as f:
            _GLOSSARY = json.load(f)
    out = []
    for t in _GLOSSARY["terms"]:
        text = t["text"].replace("{dir:right}", dir_word("right", hand)).replace("{dir:left}", dir_word("left", hand))
        out.append({"id": t["id"], "term": t["term"], "text": text})
    return out

