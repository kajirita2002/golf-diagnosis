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

VERSION = "video_candidates/0.1"

SYMPTOMS = [
    {
        "id": "S1",
        "title": "ネック寄りの当たり",
        "rule": "extreme_strike の contact == heel_extreme が2球以上",
        "features": ["dtl.butt_line_gap", "dtl.hands_out", "dtl.spine_tilt_delta"],
        "questions": ["q.p1_ball_on_face", "q.p7_butt_line", "q.p7_hands_out"],
        "capture": "DTL。その球の動画が要る（散発的）",
        "view": "dtl",
        "needs_that_shot": True,
    },
    {
        "id": "S2",
        "title": "打点が全体にヒール寄り",
        "rule": "打点の平均がヒール側に 10mm 超",
        "features": ["dtl.butt_line_gap", "dtl.hands_out", "dtl.spine_tilt_delta"],
        "questions": ["q.p1_ball_on_face", "q.p7_butt_line"],
        "capture": "DTL。どのスイングでもよい（系統的）",
        "view": "dtl",
        "needs_that_shot": False,
    },
    {
        "id": "S3",
        "title": "フェースが系統的に右",
        "rule": "フェースの bias が right",
        "features": [],
        "questions": ["q.grip", "q.p4_lead_wrist", "q.p4_face"],
        "capture": "どのスイングでもよい",
        "view": "dtl",
        "needs_that_shot": False,
    },
    {
        "id": "S4",
        "title": "フェースが散発的に大きく右",
        "rule": "フェースが中央値から MAD の3倍より外の球",
        "features": [],
        "questions": ["q.grip", "q.p4_lead_wrist", "q.p4_face"],
        "capture": "その球の動画。S3 と同じ質問を、その球と普段の球で比べる",
        "view": "dtl",
        "needs_that_shot": True,
    },
    {
        "id": "S5",
        "title": "パスが右",
        "rule": "パスの平均が +2° 超",
        "features": ["dtl.hand_path_gap", "fo.ball_position", "fo.pelvis_shift_impact"],
        "questions": ["q.p6_shaft"],
        "capture": "入射角を一緒に見る（下に打つほどパスは右に出る）",
        "view": "dtl",
        "needs_that_shot": False,
    },
    {
        "id": "S6",
        "title": "パスが左",
        "rule": "パスの平均が −2° 超",
        "features": ["dtl.hand_path_gap", "fo.ball_position", "fo.pelvis_shift_impact"],
        "questions": ["q.p6_shaft"],
        "capture": "入射角を一緒に見る",
        "view": "dtl",
        "needs_that_shot": False,
    },
    {
        "id": "S7",
        "title": "アイアンの入射角が浅い・上向き",
        "rule": "アイアンの入射角の平均が 0° 以上（基準の出典は未確認）",
        "features": ["fo.ball_position", "fo.head_shift_impact", "fo.pelvis_shift_impact"],
        "questions": ["q.p7_head_height"],
        "capture": "Face-on（Phase 3）",
        "view": "face_on",
        "needs_that_shot": False,
    },
]

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
