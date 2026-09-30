"""画面の最初に出す「要点」（数字も専門用語も使わない、抽象化した言葉）。docs/DESIGN_coaching.md §0・§6.1。

利用者（ゴルファー本人）の方針（2026-09-29）:
  「現状どうなっていて何が課題か」「理想との差は何か（比べて分かる）」「差を埋めるための意識と
  アクション」を簡潔に。根拠の数字は「なぜそう言える？」を押したときだけ。

だから範囲ごとに**4つの塊**を返す（`blocks`）。どれも短い文で、数字・角度・専門用語を使わない。

  いま                    … 球がどうなっていて、クラブがどうなっているか
  課題                    … いま一番に直すもの1つと、その次1つ（理由は短く）
  理想との差              … いま と 理想 を並べて比べる（言葉の行 ＋ 比べる図 C1）
  意識すること・やること  … 次の練習で意識する1点と、やること（ドリルは確かめ済みのものだけ）

- **事実の文はコードが書く（R1）。** ここも定型文で、値は profile と候補からだけ入る。
  抽象化は**決まった言葉の対応**（config.PLAIN_TERMS / PLAIN_FREQ など1か所の表）で決定的に作る。
  偏りとばらつきは profile の bias / spread の区分をそのまま言葉にする。確率は言わない（R4）。
- **数字・角度・専門用語を書かない。** check_plain() が config.PLAIN_FORBIDDEN・数字・英字を禁じ、
  テストで全部の文を検査する。
- **根拠は id で持つ。** 各文の evidence は、同じ範囲の定型文（claims）の id。画面は「なぜそう言える？」を
  押したときだけ、その定型文（数字入り）と図を出す。
- **言い切れないところは言い切らない。** 「〜ことが多い」「今日の球では」。
- **左打ち（R9）。** 向きは claims.dir_word で入れ替える。当たる場所（ネック／先）は入れ替えない。
- 閾値はすべて初期値・要較正。変えたら config.GIST_VERSION を上げる。
"""

from __future__ import annotations

from . import config, drills
from .claims import dir_word
from .shots import is_extreme, m

VERSION = config.GIST_VERSION
T = config.PLAIN_TERMS
MIN_N = config.PLAIN_MIN_N
FREQ_NONE = config.PLAIN_FREQ_NONE
DIGITS = set("0123456789０１２３４５６７８９")


def check_plain(text: str) -> list[str]:
    """画面の最初に出す文に、数字・専門用語・英字が入っていないか（入っていれば、その語の一覧）。"""
    bad = [t for t in config.PLAIN_FORBIDDEN if t in text]
    if any(ch in DIGITS for ch in text):
        bad.append("数字")
    if any(("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("ａ" <= ch <= "ｚ") or ("Ａ" <= ch <= "Ｚ") for ch in text):
        bad.append("英字")
    if "+" in text:
        bad.append("+")
    return bad


def check_plain_label(text: str, kinds: tuple[str, ...] = ("p", "count", "date", "cost", "duration")) -> bool:
    """構造のラベル（P の番号・回数・日付・料金・時間…）が、決めた形のどれかか（docs/DESIGN_v2.md §3 R1）。"""
    import re

    t = (text or "").strip()
    return any(re.fullmatch(config.PLAIN_LABEL_PATTERNS[k], t) for k in kinds if k in config.PLAIN_LABEL_PATTERNS)


def freq(k: int | None, n: int | None) -> str | None:
    """件数 → 量の言葉（固定の閾値）。全体が少なすぎれば言わない（None）。"""
    if k is None or not n or n < MIN_N:
        return None
    if k <= 0:
        return FREQ_NONE
    s = k / n
    return next(w for t, w in config.PLAIN_FREQ if s >= t)


def excess_word(part: float | None, total: float | None) -> str | None:
    if part is None or not total or total <= 0 or part <= 0:
        return None
    s = part / total
    return next(w for t, w in config.PLAIN_EXCESS if s >= t)


START = {"push": "{R}に出て", "straight": "まっすぐ出て", "pull": "{L}に出て"}
CURVE = {"right": "{R}に曲がる", "none": "曲がらない", "left": "{L}に曲がる"}
SPREAD = {"stable": "毎回ほぼ同じです", "some": "少しばらつきます", "wide": "毎回大きく変わります"}
# つなぐ形（「〜していて、」）
SPREAD_CONT = {"stable": "毎回ほぼ同じで", "some": "少しばらつき", "wide": "毎回大きく変わり"}


def _d(text: str, hand: str) -> str:
    return text.replace("{R}", dir_word("right", hand)).replace("{L}", dir_word("left", hand))


def plain_title(cand: dict | None, hand: str) -> str | None:
    """候補（またはプラン）を、何をするかの言葉にする。cand は {id|issue, kind?, lever?, kpi:{metric, goal}}。"""
    if not cand:
        return None
    cid = cand.get("id") or cand.get("issue") or ""
    kpi = cand.get("kpi") or {"metric": cand.get("target_metric"), "goal": cand.get("goal")}
    if cand.get("kind") == "measure":
        return f"まず{T['impact_offset']}を測れるようにする" if kpi.get("metric") == "impact_offset" else "まず測れるようにする"
    if cid == "strike_heel":
        return f"{T['heel']}の当たりを減らす"
    if cid == "strike_toe":
        return f"{T['toe']}の当たりを減らす"
    if cid == "strike_scatter":
        return f"{T['impact_offset']}を毎回そろえる"
    goal = kpi.get("goal")
    if cid in ("face", "start_right", "start_left", "start_var") or (cid.startswith("curve_") and cand.get("lever") == "face"):
        if goal == "decrease":
            return _d(f"{T['face']}が{{R}}を向きすぎる球を減らす", hand)
        if goal == "increase":
            return _d(f"{T['face']}が{{L}}を向きすぎる球を減らす", hand)
        return f"{T['face_angle']}を毎回そろえる"
    if cid == "path" or cand.get("lever") == "path":
        return f"{T['club_path']}を整える"
    if cid.startswith("curve_") or cid == "face_to_path":
        return f"{T['face_to_path']}をそろえる"
    return None


def why_first(cand: dict | None, share: str | None = None) -> str | None:
    """先に取り組む理由（ゲートと並べ方から。§5.4）。share は「大きく外れたぶんの◯◯」の言葉。"""
    if not cand:
        return None
    if cand.get("kind") == "measure":
        return "今のままでは測れない球が多く、練習の結果を確かめられないためです。"
    gates = (cand.get("why_first") or {}).get("gates") or []
    head = f"大きく外れたぶんの{share}がここから出ていて、" if share else "大きく外れる球の出どころで、"
    if "G1" in gates:
        return f"{head}しかもこの当たりがあると、{T['face_angle']}の練習の結果が読めないためです。"
    if "G2" in gates:
        return f"{T['impact_offset']}と{T['face_angle']}が一緒に動いていて、先にそろえないと面の練習が効いたのか見分けられないためです。"
    if "G3" in gates:
        return f"{T['thin']}が混ざると、曲がった理由が分からなくなるためです。"
    if share:
        return f"大きく外れたぶんの{share}が、ここから出ているためです。"
    return "左右に大きく外れたぶんが、いちばん多く出ているところだからです。"


def _line(text: str, evidence: list[str]) -> dict:
    return {"text": text, "evidence": evidence}


def _row(aspect: str, label: str, now: str, ideal: str, status: str, evidence: list[str]) -> dict:
    return {"id": aspect, "aspect": label, "now": now, "ideal": ideal, "status": status, "evidence": evidence}


def _extreme_side(shots: list[dict] | None) -> str | None:
    """大きく外れた当たりの球が、同じ側へ飛んだか（起きた球なので候補も数える）。"""
    if not shots:
        return None
    sides = [m(s, "side") for s in shots if is_extreme(s) and m(s, "side") is not None]
    if len(sides) < 2:
        return None
    r = sum(1 for x in sides if x > 0)
    if r >= config.PLAIN_SAME_SIDE * len(sides):
        return "right"
    if len(sides) - r >= config.PLAIN_SAME_SIDE * len(sides):
        return "left"
    return None


def scope_gist(p: dict, cands: dict | None, sections: list[dict], hand: str, cross_ids: list[str] | None = None,
               shots: list[dict] | None = None, compare_fig: dict | None = None) -> dict | None:
    """本体の範囲の要点。p は profile、cands は plan.candidates()、sections は解説の節（根拠の id を引く）。
    shots はこの範囲の球（大きく外れた当たりの飛んだ側を見るだけ）、compare_fig は比べる図 C1 の中身。"""
    hand = "L" if hand == "L" else "R"
    sid = p["scope_id"]
    have = {c["id"] for sec in sections for c in sec.get("claims") or []} | set(cross_ids or [])

    def ev(*suffixes: str, metric: str | None = None) -> list[str]:
        out = [f"{sid}/{s}" for s in suffixes if f"{sid}/{s}" in have]
        if metric:
            out += sorted(x for x in (cross_ids or []) if f".{metric}." in x)
        return out

    l1 = p.get("l1") or {}
    band = p.get("band") or {}
    it = (band.get("ideal_type") or {}) if band.get("used") else {}

    # ================================================================ 材料（言葉の対応）
    face, path = l1.get("face_angle") or {}, l1.get("club_path") or {}
    face_ok = (face.get("n") or 0) >= MIN_N
    path_ok = (path.get("n") or 0) >= MIN_N
    face_gap = face_ok and (bool(face.get("bias")) or face.get("spread") != "stable")
    path_stable = path_ok and path.get("spread") == "stable" and not path.get("bias")
    r2 = ((p.get("sensitivity") or {}).get("r2_single")) or {}
    r2f, r2p = r2.get("face_angle"), r2.get("club_path")
    face_drives = bool(face_gap and r2f is not None and r2f >= config.PLAIN_FACE_DRIVES_R2
                       and (r2p is None or r2f >= config.PLAIN_FACE_DRIVES_RATIO * r2p))

    s = p.get("strike") or {}
    io = l1.get("impact_offset") or {}
    n_all, meas = s.get("n") or 0, s.get("measured") or 0
    heel_ext, toe_ext = s.get("heel_extreme") or 0, s.get("toe_extreme") or 0
    ext = heel_ext + toe_ext
    med = s.get("median_m")
    strike_bias = io.get("bias") if (io.get("bias") in ("heel", "toe") and med is not None
                                     and abs(med) > config.CENTER_STRIKE_M) else None
    scatter = io.get("spread") == "wide" or bool(cands and any(i.get("issue") == "strike_scatter" for i in cands.get("issues") or []))
    ext_word = freq(ext, n_all) if ext and ext / max(n_all, 1) >= config.ISSUE_EXTREME_SHARE else None
    ext_side = _extreme_side(shots) if ext_word else None
    miss_w = freq(s.get("missing"), n_all)

    t = p.get("tendency") or {}
    n_t = t.get("n") or 0
    cell = next((c for c in t.get("cells") or [] if c.get("key") == t.get("top_cell")), None)

    cf = compare_fig or {}
    ideal_n = (cf.get("ideal") or {}).get("n") or 0
    landed_n = (cf.get("now") or {}).get("in_target") or 0
    now_n = (cf.get("now") or {}).get("n") or 0

    # ================================================================ いま
    now_lines: list[dict] = []
    ev_flight = ev("summary.top_cell", "flight.counts", "flight.top_cell")
    fw = freq(cell["n"], n_t) if cell else None
    if t.get("top_tie") or not cell:
        now_lines.append(_line("ボールは決まった形がなく、左右に散っています。", ev_flight))
    elif fw:
        now_lines.append(_line(_d(f"ボールは{START[cell['start']]}{CURVE[cell['curve']]}ことが{fw}です。", hand), ev_flight))
    else:
        now_lines.append(_line("球が少なく、ボールの形はまだ言えません。", ev_flight))

    # クラブ（振る方向 → 面の向き）
    path_cl = None
    if path_ok:
        if path_stable:
            path_cl = f"{T['club_path']}はもう安定していて"
        elif path.get("bias") in ("right", "left"):
            path_cl = f"{T['club_path']}は{dir_word(path['bias'], hand)}に偏っていて"
        else:
            path_cl = f"{T['club_path']}は{SPREAD_CONT.get(path.get('spread') or '', '')}"
    face_s = None
    if face_ok:
        if face.get("bias") in ("right", "left"):
            ph = f"{T['impact']}に{T['face']}が{dir_word(face['bias'], hand)}を向きやすい"
        elif face.get("spread") == "stable":
            ph = None
        else:
            ph = f"{T['impact']}の{T['face_angle']}が毎回変わる"
        if ph and face_drives:
            face_s = f"今日の球では、左右のずれの主な原因は{ph}ことです。"
        elif ph:
            face_s = f"{ph}です。" if ph.endswith("やすい") else f"{ph}ことがあります。"
        else:
            face_s = f"{T['impact']}の{T['face_angle']}は毎回ほぼ同じです。"
    if path_cl and face_s:
        club = f"{path_cl}、{face_s}"
    elif path_cl:
        club = path_cl.removesuffix("いて").removesuffix("て") + ("います。" if path_cl.endswith("いて") else "ます。")
    else:
        club = face_s
    if club:
        now_lines.append(_line(club, ev("impact.path", "impact.face", "summary.path_face", "impact.sensitivity", "impact.coverage", metric="face_angle")))

    # 当たる場所
    ev_strike = ev("miss.strike", "miss.extreme", "miss.budget", metric="impact_offset")
    fly = f"大きく{dir_word(ext_side, hand)}へ飛びます" if ext_side else "大きく外れます"
    if meas < MIN_N:
        now_lines.append(_line(f"{T['impact_offset']}は、ほとんど測れていません。", ev("miss.strike", "unknown.strike_missing") or ev_strike))
    elif strike_bias:
        word = T["heel"] if strike_bias == "heel" else T["toe"]
        k = s.get("heel") if strike_bias == "heel" else s.get("toe")
        same_ext = heel_ext if strike_bias == "heel" else toe_ext
        kw = freq(k, meas) or "多い"
        if same_ext and ext_word:
            now_lines.append(_line(f"{T['impact_offset']}は{word}が{kw}で、{ext_word}大きく{word}に外れて当たり、{fly}。", ev_strike))
        else:
            now_lines.append(_line(f"{T['impact_offset']}は{word}が{kw}です。", ev_strike))
    elif scatter:
        now_lines.append(_line(f"{T['impact_offset']}が毎回ばらつきます。", ev_strike))
    elif ext_word:
        now_lines.append(_line(f"{T['impact_offset']}はふだん{T['center']}の近くですが、{ext_word}大きく外れて当たり、{fly}。", ev_strike))
    else:
        now_lines.append(_line(f"{T['impact_offset']}は{T['center']}の近くです。", ev_strike))

    # ================================================================ 課題
    cl = {c["id"]: c for c in (cands or {}).get("candidates") or []}
    now_c = cl.get((cands or {}).get("now"))
    next_c = cl.get((cands or {}).get("next")) if (cands or {}).get("next") else None
    mb = p.get("miss_budget") or {}
    focus = None
    issue_lines: list[dict] = []
    if now_c:
        title = plain_title(now_c, hand)
        part = (mb.get("strike_range_m") or [None])[0] if now_c["id"] in ("strike_heel", "strike_toe") else now_c.get("excess_m")
        ew = excess_word(part, mb.get("total_m")) if now_c.get("kind") != "measure" else None
        why = why_first(now_c, ew)
        ev_now = ev("next.now", "summary.budget", "miss.budget", "now.hypothesis")
        focus = {"candidate_id": now_c["id"], "title": title, "why": why, "share_word": ew, "evidence": ev_now}
        if title:
            issue_lines.append(_line(f"まず「{title}」。{why}", ev_now))
    if next_c and plain_title(next_c, hand):
        issue_lines.append(_line(f"その次に「{plain_title(next_c, hand)}」。",
                                 ev("next.next", "next.hypothesis", "next.next.borderline") or ev("next.now")))
    if not issue_lines:
        issue_lines.append(_line("今日の球では、先に直すものを一つに決められません。", ev("next.now", "summary.budget", "summary.top_cell")))

    # ================================================================ 理想との差（言葉の行 ＋ 比べる図）
    rows: list[dict] = []
    if now_n < MIN_N:
        fl_now = "球が少なく、まだ言えません。"
    elif landed_n <= 0:
        fl_now = config.PLAIN_LANDED_NONE
    else:
        fl_now = next(w for th, w in config.PLAIN_LANDED if landed_n / now_n >= th)
    fl_ideal = "今日すでに打てている狙いどおりの球のように、狙いの幅にまとまる" if ideal_n else "狙いの幅にまとまる"
    rows.append(_row("flight", "球の散らばり", fl_now, fl_ideal, "gap",
                     ev("flight.good", "ideal.today", "ideal.no_band", "flight.counts")))
    strike_ev = ev("miss.strike", "miss.extreme", "ideal.strike_goal", "unknown.strike_missing", metric="impact_offset")
    if meas < MIN_N:
        rows.append(_row("strike", T["impact_offset"], "ほとんど測れていません。", f"まず{T['impact_offset']}を測る", "unknown", strike_ev))
    else:
        if strike_bias:
            kk = s.get("heel") if strike_bias == "heel" else s.get("toe")
            sn, st = f"{T['heel'] if strike_bias == 'heel' else T['toe']}が{freq(kk, meas) or '多い'}です。", "gap"
        elif scatter:
            sn, st = "毎回ばらつきます。", "gap"
        elif ext_word:
            sn, st = f"ふだんは{T['center']}の近くで、ときどき大きく外れます。", "gap"
        else:
            sn, st = f"{T['center']}の近くです。", "ok"
        if miss_w and miss_w not in (FREQ_NONE, "まれ", "ときどき"):
            sn += "測れていない球も多いです。"
        rows.append(_row("strike", T["impact_offset"], sn,
                         f"{T['center']}の近くに、毎回同じように当たる" if st == "gap" else "このままでよい", st, strike_ev))
    if face_ok:
        if face.get("bias") in ("right", "left"):
            fn = f"{dir_word(face['bias'], hand)}を向きやすく、{SPREAD.get(face.get('spread') or '', '')}。"
        else:
            fn = f"{SPREAD.get(face.get('spread') or '', '')}。"
        if not face_gap:
            fi = "このままでよい"
        elif it.get("shown") and it.get("kind") == "trim":
            fi = f"真ん中の球はそのままで、{dir_word(it['side'], hand)}へ外れる球だけが減る"
        elif it.get("shown") and it.get("kind") == "shift":
            fi = "全体が狙いの方向へ寄る"
        else:
            fi = "狙いの方向を向いて、毎回そろう"
        rows.append(_row("face", T["face_angle"], fn, fi, "gap" if face_gap else "ok",
                         ev("impact.face", "ideal.type", "ideal.window", "impact.sensitivity", "impact.coverage", metric="face_angle")))
    if path_ok:
        if path_stable:
            pn = "安定しています。"
        elif path.get("bias") in ("right", "left"):
            pn = f"{dir_word(path['bias'], hand)}に偏っています。"
        else:
            pn = f"{SPREAD.get(path.get('spread') or '', '')}。"
        untouched = any(u.get("metric") == "club_path" for u in (cands or {}).get("untouched") or [])
        pi = "もう安定しているので、今は触らない" if untouched or path_stable else "今は触らない（先にほかを整える）"
        rows.append(_row("path", T["club_path"], pn, pi, "keep", ev("impact.path", "next.untouched", "summary.path_face", metric="club_path")))

    head = ("理想は、今日すでに打てている狙いどおりの球です。" if ideal_n
            else "今日は狙いどおりの球がまだ少ないので、理想は狙いの幅にまとまる球です。")
    if it.get("shown") and it.get("kind") == "trim":
        tail = f"真ん中の球は今のままでよく、{dir_word(it['side'], hand)}へ外れる球を減らせば近づきます。"
    elif it.get("shown") and it.get("kind") == "shift":
        tail = f"全体が{dir_word(it['side'], hand)}にずれているので、全体を寄せると近づきます。"
    elif focus and focus.get("title"):
        tail = f"いちばんの差は「{focus['title']}」のところです。"
    else:
        tail = ""
    ev_ideal = ev("ideal.today", "ideal.type", "ideal.type_small", "ideal.no_band", "flight.good", "ideal.strike_goal")
    gap_lines = [_line(head + tail, ev_ideal or ev("flight.good", "flight.counts"))]

    # ================================================================ 意識すること・やること
    drill = None
    if now_c and now_c.get("kind") != "measure":
        try:
            ds = drills.for_candidate(now_c, p.get("category"), hand).get("drills") or []
        except Exception:  # noqa: BLE001  ドリル集が読めなくても要点は出す
            ds = []
        if ds:  # for_candidate は確かめ済み（checked_by のある）ドリルだけを返す
            drill = {"id": ds[0]["id"], "title": ds[0].get("title"), "cue": ds[0].get("cue_transfer")}
    is_measure = bool(now_c and now_c.get("kind") == "measure")
    if drill and drill.get("cue") and not check_plain(drill["cue"]):
        cue = f"「{drill['cue']}」"
    elif focus and is_measure:
        cue = "一球ずつ、当たった場所を確かめる。"
    elif focus and focus["candidate_id"].startswith("strike"):
        cue = "当たる瞬間、手元を構えたときと同じ場所（体の近く）へ低く戻すつもりで振る。"
    elif focus:
        cue = "切り返しから当たる瞬間まで、左手の甲を平らに保ったまま、体の回転で振り抜く。"
    else:
        cue = None
    ev_act = ev("now.hypothesis", "now.design", "now.advance") or ev("next.now")
    act_lines: list[dict] = []
    if cue:
        act_lines.append(_line(f"意識する一点: {cue}", ev_act))
    keep = [r["aspect"] for r in rows if r["status"] in ("ok", "keep")]
    if focus and focus.get("title"):
        act_lines.append(_line(f"やること: 次の練習で、いつも通りの球と意識した球を打ち比べ、「{focus['title']}」が進んだかを確かめる。"
                               "別の日にもう一回同じ結果が出たら本物です。", ev_act))
        if is_measure:
            pass
        elif drill and drill.get("title") and not check_plain(drill["title"]):
            act_lines.append(_line(f"道具を使う練習「{drill['title']}」も使えます。", ev_act))
        elif drill:
            act_lines.append(_line("道具を使う練習もあります（プランを始めると出ます）。", ev_act))
        else:
            act_lines.append(_line("ドリルのやり方は、くわしいレポートの「直し方」に出ています（プランを始めると練習の画面にも出ます）。", ev_act))
        if keep:
            act_lines.append(_line(f"{'・'.join(keep)}は、今は触らずそのままにします。", ev("next.untouched", "now.design") or ev_act))

    blocks = [
        {"id": "now", "title": "いま", "lines": now_lines},
        {"id": "issue", "title": "課題", "lines": issue_lines},
        {"id": "gap", "title": "理想との差", "lines": gap_lines, "rows": rows, "figure": "C1" if compare_fig else None},
        {"id": "action", "title": "意識すること・やること", "lines": act_lines,
         "start": focus["candidate_id"] if focus and not is_measure else None, "drill": drill},
    ]
    steps = []
    if focus and focus.get("title"):
        steps.append({"text": f"「{focus['title']}」を、何回かの練習で確かめる", "candidate_id": focus["candidate_id"], "which": "now"})
    if next_c and plain_title(next_c, hand):
        steps.append({"text": f"できたら「{plain_title(next_c, hand)}」に進む", "candidate_id": next_c["id"], "which": "next"})
    return {
        "version": VERSION,
        "scope_id": sid,
        "headline": f"まず取り組むのは「{focus['title']}」です。" if focus and focus.get("title") else None,
        # 今日のまとめ（範囲が2つ以上のとき）の1行: 「いま」の最初の文
        "summary": now_lines[0]["text"] if now_lines else None,
        "blocks": blocks,
        "compare": rows,
        "focus": focus,
        "steps": steps,
        "keep": keep,
        "candidates": {cid: {"title": plain_title(c, hand), "why": why_first(c)} for cid, c in cl.items()},
    }


def texts(g: dict) -> list[str]:
    """画面の最初に出す文の全部（検査用）。"""
    out = [g.get("headline"), g.get("summary")]
    for b in g.get("blocks") or []:
        out.append(b["title"])
        out += [x["text"] for x in b.get("lines") or []]
        for r in b.get("rows") or []:
            out += [r["aspect"], r["now"], r["ideal"]]
    f = g.get("focus") or {}
    out += [f.get("title"), f.get("why")]
    out += [s["text"] for s in g.get("steps") or []]
    for c in (g.get("candidates") or {}).values():
        out += [c.get("title"), c.get("why")]
    return [x for x in out if x]


def lines(g: dict) -> list[dict]:
    """根拠を持つ文の全部（検査用）。"""
    out = []
    for b in g.get("blocks") or []:
        out += list(b.get("lines") or [])
        out += [{"text": r["now"], "evidence": r["evidence"]} for r in b.get("rows") or []]
    return out
