"""症状 → 動画で確かめる場所の表（データだけ）。docs/DESIGN_coaching.md §9.6・§6.3。

- 第2段（動画の所見と症状を結ぶ）・統計・画面・解説の「動画で見る候補」（⑧）が全部この表を読む。
  **Claude に結びつけさせない**（事前に決めた表を迂回した多重比較になる）。
- 判定は既存の出力の名前（decomposition.contact == "heel_extreme" など）と profile の値だけで行う。
- 解説の⑧に出す文（hints）は、人が書いて確かめたもの（checked_by がある行）だけを出す。
  文は必ず「調べる候補であって、診断ではありません」で終える（report が付ける）。
"""

from __future__ import annotations

from statistics import median

from .shots import dec, m

VERSION = "video_candidates/0.2"

# 症状 → 動画で見る項目（docs/DESIGN_v2.md §9.1。事前の表・1つだけ）。
# items はチェックポイントのカタログの id（`setup.ball_pos` は番手ごとの `setup.ball_pos.*` の束）。
# カタログの `l1_links` と両方向で一致することを pytest が見る（`linked_items` はカタログから逆引きする）。
# features は既存の特徴量の名前（カタログの pose 項目の中身として残す）。
# S8（振りの速さが伸びない）は**判定の規則を人が決めるまで detect で出さない**（rule が None）。
SYMPTOMS = [
    {"id": "S1", "title": "ネック寄りの当たり", "rule": "extreme_strike の contact == heel_extreme が2球以上",
     "items": ["setup.ball_pos", "iron.p1.dtl.hands", "iron.p7.dtl.hands", "err.early_ext.dtl", "iron.p7.dtl.hand_height"],
     "features": ["dtl.butt_line_gap", "dtl.hands_out", "dtl.spine_tilt_delta"], "view": "dtl", "needs_that_shot": True},
    {"id": "S2", "title": "打点が全体にヒール寄り", "rule": "打点の平均がヒール側に 10mm 超",
     "items": ["iron.p1.dtl.hands", "iron.p7.dtl.hands", "err.early_ext.dtl"],
     "features": ["dtl.butt_line_gap", "dtl.hands_out", "dtl.spine_tilt_delta"], "view": "dtl", "needs_that_shot": False},
    {"id": "S3", "title": "フェースが系統的に右", "rule": "フェースの bias が right",
     "items": ["setup.grip", "iron.p4.dtl.shaft_vs_forearm", "err.early_release.p6"],
     "features": [], "view": "both", "needs_that_shot": False},
    {"id": "S4", "title": "フェースが散発的に大きく右", "rule": "フェースが中央値から MAD の3倍より外の球",
     "items": ["setup.grip", "iron.p4.dtl.shaft_vs_forearm", "err.early_release.p6"],
     "features": [], "view": "both", "needs_that_shot": True},
    {"id": "S5", "title": "パスが右", "rule": "パスの平均が +2° 超",
     "items": ["path.loop", "err.steep.p5", "err.steep.p6", "iron.p2.dtl.head_vs_hands", "setup.ball_pos"],
     "features": ["dtl.hand_path_gap", "fo.ball_position", "fo.pelvis_shift_impact"], "view": "both", "needs_that_shot": False},
    {"id": "S6", "title": "パスが左", "rule": "パスの平均が −2° 超",
     "items": ["path.loop", "err.steep.p5", "err.steep.p6", "iron.p2.dtl.head_vs_hands", "setup.ball_pos"],
     "features": ["dtl.hand_path_gap", "fo.ball_position", "fo.pelvis_shift_impact"], "view": "both", "needs_that_shot": False},
    {"id": "S7", "title": "アイアンの入射角が浅い・上向き", "rule": "アイアンの入射角の平均が 0° 以上（基準の出典は未確認）",
     "items": ["err.sway.p7", "iron.p7.fo.side_bend", "iron.p7.fo.hands_ahead", "setup.ball_pos"],
     "features": ["fo.ball_position", "fo.head_shift_impact", "fo.pelvis_shift_impact"], "view": "fo", "needs_that_shot": False},
    {"id": "S8", "title": "振りの速さが伸びない", "rule": None,
     "items": ["pow.head_fb", "pow.recenter", "pow.width", "pow.trail_knee", "tempo.ratio"],
     "features": [], "view": "both", "needs_that_shot": False},
]
MAX_ITEMS = 5
# 画面の言い方（§9.1）。ガイドも動きを「可能性」として扱っているので、ここまでしか言わない
LINK_TEXT = "球の課題とつながる候補です（まだ確かめていません）"


def canonical(item_id: str) -> str:
    """表と突き合わせる形: 番手ごとのボールの位置は束ね、束ねた項目（same_as）は相手の id にする。"""
    from .checkpoints import by_id

    if item_id.startswith("setup.ball_pos."):
        return "setup.ball_pos"
    it = by_id(item_id) or {}
    return it.get("same_as") or item_id


def linked_items(symptom_id: str) -> set[str]:
    """カタログの l1_links から逆引きした、その症状の項目（canonical の形）。"""
    from .checkpoints import items

    return {canonical(it["id"]) for it in items() if symptom_id in (it.get("l1_links") or [])}


def table_items(symptom_id: str) -> set[str]:
    s = next((x for x in SYMPTOMS if x["id"] == symptom_id), None)
    return {canonical(i) for i in (s or {}).get("items", [])}


def symptoms_for_focus(found: list[dict], matched_seqs: set[int] | None = None) -> list[str]:
    """課題の「球の課題とつながる候補」の印に使う症状の id。

    その球の動画が要る症状（S1・S4）は、対応づけた（人が確かめた）スイングの球が症状の球に入っているときだけ数える。"""
    matched_seqs = matched_seqs or set()
    out = []
    for f in found:
        s = next((x for x in SYMPTOMS if x["id"] == f["id"]), None)
        if not s or s["rule"] is None:
            continue
        if s["needs_that_shot"] and not (set(f.get("seqs") or []) & matched_seqs):
            continue
        out.append(f["id"])
    return out


# 解説の⑧「動画で見る候補」（§6.3）。人が書いて確かめた文だけを出す（checked_by）。
# 鍵は「どの所見から」（cross_club の common / 症状の id）。
HINTS = [
    {
        "key": "cross_club.common:impact_offset:heel",
        "symptoms": ["S1", "S2"],
        "look": ["構えたときのボールとフェースの合わせ方", "インパクトでの手元と体の距離"],
        "checked_by": "docs/DESIGN_coaching.md §6.2（設計のレビュー）",
    },
    {
        "key": "symptom:S1",
        "symptoms": ["S1"],
        "look": ["構えたときのボールとフェースの合わせ方", "インパクトでの手元と体の距離"],
        "checked_by": "docs/DESIGN_coaching.md §6.2（設計のレビュー）",
    },
    {
        "key": "symptom:S3",
        "symptoms": ["S3"],
        "look": ["構えたときの目標側の手のナックルの見え方", "トップでの目標側の手首とフェースの向き"],
        "checked_by": None,  # 人が確かめるまで出さない
    },
]


def detect(p: dict, shots: list[dict], skip: set) -> list[dict]:
    """その範囲で当たった症状（id と根拠の球）。"""
    use = [s for s in shots if s["id"] not in skip]
    out = []
    heel_ext = [s for s in use if dec(s).get("contact") == "heel_extreme"]
    if len(heel_ext) >= 2:
        out.append({"id": "S1", "seqs": [s["seq"] for s in heel_ext]})
    off = p["l1"]["impact_offset"]
    if off.get("mean") is not None and off["mean"] < -0.010:
        out.append({"id": "S2", "value": off["mean"], "n": off["n"]})
    face = p["l1"]["face_angle"]
    if face.get("bias") == "right":
        out.append({"id": "S3", "value": face["mean"], "n": face["n"]})
    fv = [(s, m(s, "face_angle")) for s in use if m(s, "face_angle") is not None]
    if len(fv) >= 5:
        med = median(v for _, v in fv)
        mad = median(abs(v - med) for _, v in fv)
        far = [s for s, v in fv if mad > 0 and v - med > 3 * mad]
        if far:
            out.append({"id": "S4", "seqs": [s["seq"] for s in far], "median": med, "mad": mad})
    path = p["l1"]["club_path"]
    if path.get("mean") is not None and path["mean"] > 2.0:
        out.append({"id": "S5", "value": path["mean"], "n": path["n"]})
    if path.get("mean") is not None and path["mean"] < -2.0:
        out.append({"id": "S6", "value": path["mean"], "n": path["n"]})
    aa = p["l1"]["attack_angle"]
    if p["category"] == "iron" and aa.get("mean") is not None and aa["mean"] >= 0:
        out.append({"id": "S7", "value": aa["mean"], "n": aa["n"]})
    return out


def hints_for(keys: list[str]) -> list[dict]:
    """確かめた文だけを、重複を除いて返す。"""
    seen, out = set(), []
    for h in HINTS:
        if h["key"] in keys and h["checked_by"]:
            look = tuple(h["look"])
            if look in seen:
                continue
            seen.add(look)
            out.append(h)
    return out


def session_symptoms(shots: list[dict], categories: list[str] | None = None) -> list[dict]:
    """その日の球から症状を探す（クラブごと・解説の本体の範囲と同じ球数の条件）。categories を渡せばそのクラブの種類だけ。

    返すのは {id, club, seqs?} の並び。S8 は規則が無いので出さない。"""
    from . import config
    from .session import analyze_session
    from .shots import drop_practice

    use = [s for s in drop_practice(shots) if not s.get("excluded")]
    an = analyze_session(use)
    out = []
    for c in an["clubs"]:
        p = c["profile"]
        if categories and p.get("category") not in categories:
            continue
        if p["n"] - p["mishit_excluded"] < config.MIN_SCOPE_N:
            continue
        sh = [s for s in use if (s.get("club") or "(クラブ不明)") == c["club"]]
        skip = {s["id"] for s in sh if s["seq"] in p["mishit_seqs"]}
        for f in detect(p, sh, skip):
            out.append({**f, "club": c["club"]})
    return out
