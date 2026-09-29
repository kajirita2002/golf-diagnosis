"use strict";
/*
  診断（docs/DESIGN_v2.md §10 D）と、その下の画面（くわしい解説・数字・一球ずつ・一回だけの実験）。
  - 最初の面は要点（gist）の4つの塊だけ: いま／課題／理想との差／意識すること・やること。
    数字・角度・専門用語は出さない。根拠（数字入りの定型文）は「なぜそう言える？」のシートの中だけ。
  - 文は全部サーバーの定型文（claims・gist）をそのまま出す。ここで事実の文を作らない。
  - 範囲（アイアン／ユーティリティ／ウッド）はセグメントで切り替える。開く部分は1段だけ。
  - くわしい解説（①〜⑧・図）は別の画面（…/detail）。図の点を押すと1球の中身がシートで出る。
  - プランを始めるシート（確認はシートの中。confirm を使わない）もここに置く。
*/
const Report = (() => {
  const { $, $$, esc, lab, icon, S, api, fmt, LABEL } = App;

  const CURVE = { hook: "フック", draw: "ドロー", straight: "まっすぐ", fade: "フェード", slice: "スライス", unknown: "—" };
  const START = { push: "プッシュ", pull: "プル", straight: "まっすぐ", unknown: "—" };
  const CAUSE = { face_to_path: "フェース・トゥ・パス", strike: "打点", mixed: "両方", none: "—", unknown: "—" };
  // 当たる場所の呼び名は最初の面の「ネック寄り」「先端寄り」にそろえ、専門の言い方は括弧で添える
  const CONTACT = { center: "芯", heel: "ネック側（ヒール）", toe: "先端側（トゥ）", heel_extreme: "ネック寄り（ヒール側）", toe_extreme: "先端寄り（トゥ側）", unknown: "—" };
  const REASON = { thin: "トップ・薄い当たり", short_carry: "キャリーが極端に短い", extreme_axis: "スピン軸が極端" };
  const causeChip = (c) => `<span class="chip ${c === "strike" ? "warn" : c === "face_to_path" ? "brand" : "none"}">${esc(CAUSE[c] || c)}</span>`;
  const GRADE = {
    strong: ["はっきり効いた", "good"], moderate: ["効いた", "good"], weak: ["効いたか分からない", "none"],
    none: ["効いていない", "none"], worse: ["悪くなった", "bad"], insufficient: ["球が足りない", "warn"],
  };
  const STRENGTH = { strong: ["強い", "good"], moderate: ["中程度", "brand"], subset: ["一部の球", "warn"], info: ["入力のお願い", "none"] };
  const BLK = { warmup: "準備", baseline: "いつも通り", drill: "ドリル", intervention: "本番", retention: "定着" };
  const GOAL_L = { reduce_abs: "0 に近づける", reduce_sd: "ばらつきを減らす", increase: "増やす", decrease: "減らす" };
  const CAT_WORD = { iron: "アイアン", hybrid: "ユーティリティ", wood: "ウッド", driver: "ドライバー", wedge: "ウェッジ" };

  // 範囲の呼び名（まとめはそのまま。1本の範囲はクラブの日本語名）
  const scopeName = (sc) => (/^club:/.test(sc.scope_id || "") ? App.clubJa(sc.label) : sc.label);
  const scopeLab = (sc) => (/^club:/.test(sc.scope_id || "") ? lab("club", App.clubJa(sc.label)) : esc(sc.label));

  async function sessionOf(sid) {
    const ss = await App.sessions().catch(() => []);
    const hit = ss.find((s) => s.id === sid);
    if (hit) return hit;
    return api("GET", `/v1/sessions/${sid}`);
  }

  // 窓の文の {band:lo} / {band:hi} を、Go が足した帯の形（band_shape.window）で埋める。
  // 左打ちは定型文の符号を入れ替えて書いているので、lo＝−face_max・hi＝−face_min。形が無ければ null（その文は出さない）
  const deg1 = (v) => { const x = Math.round(v * 10) / 10; return (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toFixed(1) + "°"; };
  function fillBand(text, sc, hand) {
    if (text === null || text === undefined) return null;
    if (!/\{band:(lo|hi)\}/.test(text)) return text;
    const bs = sc.band_shape, w = bs && bs.ok ? bs.window : null;
    if (!w || !w.ok || w.face_min == null || w.face_max == null) return null;
    const lo = hand === "L" ? -w.face_max : w.face_min, hi = hand === "L" ? -w.face_min : w.face_max;
    return text.replace(/\{band:lo\}/g, deg1(lo)).replace(/\{band:hi\}/g, deg1(hi));
  }
  const claimOf = (sc, suffix) => {
    for (const sec of sc.sections || []) for (const c of sec.claims || []) if (c.id.endsWith("/" + suffix)) return c.text;
    return null;
  };
  function claimById(rep, sc, id) {
    for (const sec of sc.sections || []) for (const c of sec.claims || []) if (c.id === id) return c;
    const cc = (rep && rep.cross_club && rep.cross_club.claims) || [];
    return cc.find((c) => c.id === id) || null;
  }

  // ==== D 診断（要点） ====
  const GSTAT = { gap: ["△ 差がある", "warn"], ok: ["✓ このままでよい", "good"], keep: ["― 今は触らない", "none"], unknown: ["― まだ分からない", "none"] };
  const GIST_SEC = { now: "s1", issue: "s7", gap: "s6", action: "s7" };

  // 各塊は要約の1文だけを見せ、残りは開いたときに出す（最初の面が長く、プランを始めるボタンが遠かった）。
  // 「意識すること・やること」は短いので全部出す。理想との差の図と比べる表は「図で比べる」の中
  function gistHtml(sc) {
    const g = sc.gist;
    const cand = (id) => sc.candidates && (sc.candidates.candidates || []).find((c) => c.id === id);
    return g.blocks.map((b) => {
      const all = (b.lines || []).map((ln) => `<p class="gline">${esc(App.textJa(ln.text))}</p>`);
      const keep = b.id === "action" ? all.length : 1;
      const lines = all.slice(0, keep).join("") + (all.length > keep ? `<details class="gmore"><summary class="textbtn">続きを読む</summary>${all.slice(keep).join("")}</details>` : "");
      let extra = "";
      if (b.id === "gap") {
        const fig = b.figure && sc.figures && sc.figures[b.figure] ? `<div class="figslot" data-fig="${esc(b.figure)}"></div>` : "";
        const rows = (b.rows || []).map((r) => {
          const [sl, scls] = GSTAT[r.status] || [r.status, "none"];
          return `<div class="grow" data-grow="${esc(r.id)}"><div class="gasp">${esc(r.aspect)} <span class="chip ${scls}">${esc(sl)}</span></div>
            <div class="gpair"><span class="glab">いま</span><span>${esc(r.now)}</span><span class="glab">理想</span><span>${esc(r.ideal)}</span></div></div>`;
        }).join("");
        extra = fig || rows ? `<details class="gmore" data-gap-more><summary class="textbtn">図で比べる</summary>${fig}<div class="gcmp">${rows}</div></details>` : "";
      }
      if (b.id === "action") {
        const c0 = b.start && cand(b.start);
        if (c0 && c0.kind !== "measure") extra = `<div class="block"><button class="btn primary block" data-primary data-act="startplan" data-cand="${esc(c0.id)}" data-which="now">このプランで始める</button></div>`;
      }
      // 同じ名前のボタンが4つ並ばないよう、読み上げには塊の名前を足す
      return `<section class="gblock gb-${esc(b.id)}" data-block="${esc(b.id)}"><h2>${esc(b.title)}</h2>${lines}${extra}
        <button class="textbtn" data-why="${esc(b.id)}">なぜそう言える？<span class="visually-hidden">（${esc(b.title)}）</span></button></section>`;
    }).join("");
  }

  // 根拠の定型文1つ。「／」で原因を並べた長い文は、原因ごとの箇条書きにする（1段落に詰めない）
  function claimBody(c) {
    const t = App.textJa(c.text);
    const bits = t.split("／").map((x) => x.trim()).filter(Boolean);
    if (bits.length < 2) return `<p class="claim" data-claim="${esc(c.id)}">${esc(t)}</p>`;
    const m = /^([^：:]{1,24}[：:])\s*(.*)$/.exec(bits[0]);
    const head = m ? m[1] : "";
    if (m) bits[0] = m[2];
    return `<div class="claim" data-claim="${esc(c.id)}">${head ? `<p style="margin:0">${esc(head)}</p>` : ""}<ul class="parts">${bits.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`;
  }

  // 「なぜそう言える？」: 文ごとに、その根拠の定型文（数字入り）を並べたシート
  function openWhy(rep, sc, blockId, hand, sid) {
    const b = (sc.gist.blocks || []).find((x) => x.id === blockId);
    if (!b) return;
    const lines = (b.lines || []).concat((b.rows || []).map((r) => ({ text: `${r.aspect}: ${r.now}`, evidence: r.evidence })));
    const seen = new Set();
    let said = "";
    const parts = lines.map((ln) => {
      const cs = (ln.evidence || []).filter((id) => !seen.has(id) && seen.add(id)).map((id) => claimById(rep, sc, id)).filter(Boolean)
        .map((c) => ({ ...c, text: fillBand(c.text, sc, hand) })).filter((c) => c.text);
      if (!cs.length) return "";
      said += ln.text + cs.map((c) => c.text).join("");
      return `<div class="wgrp"><p class="wq">${esc(App.textJa(ln.text))}</p>${cs.map(claimBody).join("")}</div>`;
    }).join("");
    // 出てきた専門用語に一行の説明を添える（解説の用語集から。ここで説明を作らない）
    const gl = ((rep && rep.glossary) || []).filter((g) => g.term && said.includes(g.term));
    const sec = GIST_SEC[blockId];
    App.openSheet({ title: "なぜそう言える？", label: "why", size: "full",
      html: `${parts || `<p class="sub">この塊の根拠になる文はありません。</p>`}
        ${gl.length ? `<div class="gloss-list" data-why-gloss><p class="label">言葉の意味</p>${gl.map((g) => `<p class="gloss"><b>${esc(g.term)}</b>: ${esc(g.text)}</p>`).join("")}</div>` : ""}
        <p class="caption">文は全部、計測の数字から決まった型で作ったものです（AI の文ではありません）。</p>
        ${sec ? `<a class="btn block" href="#/session/${sid}/detail?scope=${encodeURIComponent(sc.scope_id)}&sec=${sec}">図と数字をくわしく見る</a>` : ""}` });
  }

  async function renderD({ el, params, query, alive }) {
    const sid = Number(params.id);
    el.innerHTML = `<div class="pagehead">${App.backBtn("#/record")}<h1>診断</h1></div><div data-body></div>`;
    const body = $("[data-body]", el);
    const stop = App.loading(body, "解説を作っています…");
    let r, se;
    try { [r, se] = await Promise.all([App.report(sid), sessionOf(sid)]); } finally { stop(); }
    if (!alive()) return;
    $(".pagehead", el).innerHTML = `${App.backBtn(`#/record/${se.date}`, "記録へ戻る")}<h1>${lab("date", App.dateJa(se.date, false))}の診断</h1>`;
    const links = `<ul class="navlist block">
      ${App.navItem(`#/session/${sid}/detail${query.scope ? "?scope=" + encodeURIComponent(query.scope) : ""}`, "球の散らばり・くわしい解説", "図と数字")}
      ${App.navItem(`#/session/${sid}/shots`, "一球ずつ", "除外・当たった場所の入力も")}
      ${App.navItem(`#/session/${sid}/experiments`, "一回だけの実験", "仮説とブロックを自分で決める")}</ul>`;
    if (!r.available || !r.report) {
      // 理由の文（サーバー）と同じことを2回言わない。こちらの1文だけにし、サーバーの文は「くわしく」に畳む
      body.innerHTML = `<div class="note warn" data-report-unavailable><p style="margin:0">分析のサービスが止まっているので、解説は出せません。一球ずつの分解は見られます。</p>
        ${r.reason ? `<details><summary class="textbtn">くわしく</summary><p class="caption">${esc(r.reason)}</p></details>` : ""}</div>${links}`;
      return;
    }
    const rep = r.report, hand = rep.handedness === "L" ? "L" : "R";
    const mains = (rep.scopes || []).filter((x) => x.kind === "main" && x.gist && (x.gist.blocks || []).length);
    if (!mains.length) {
      body.innerHTML = `<div class="note">${rep.n_shots ? "この記録の球は全部「除外」になっているので、解説は作れません。一球ずつの画面で除外を外すと解説が出ます。" : "この記録には球がありません。取り込むと解説が出ます。"}</div>${links}`;
      return;
    }
    const sc = mains.find((x) => x.scope_id === query.scope) || mains.find((x) => x.gist.focus) || mains[0];
    const seg = mains.length > 1 ? `<div class="seg" role="group" aria-label="範囲" data-scopes>${mains.map((x) =>
      `<a href="#/session/${sid}?scope=${encodeURIComponent(x.scope_id)}" ${x === sc ? 'aria-current="page"' : ""} data-scope="${esc(x.scope_id)}">${scopeLab(x)}</a>`).join("")}</div>` : "";
    // 範囲のセグメントがあるときは、同じ名前をカードの中でくり返さない
    body.innerHTML = `${seg}<article class="card block" data-gist data-scope="${esc(sc.scope_id)}">
        ${seg ? "" : `<p class="label">${scopeLab(sc)}</p>`}${gistHtml(sc)}</article>${links}`;
    const slot = $(".figslot", body);
    if (slot) {
      try {
        await App.loadFigures();
        const node = window.Figures.render(sc.figures[slot.dataset.fig], { hand, bandShape: sc.band_shape });
        if (node) slot.replaceWith(node); else slot.remove();
      } catch (e) { slot.outerHTML = `<p class="fielderr">${esc(e.message)}</p>`; }
    }
    body.addEventListener("click", (ev) => {
      const w = ev.target.closest("button[data-why]");
      if (w) { openWhy(rep, sc, w.dataset.why, hand, sid); return; }
      const sp = ev.target.closest("button[data-act=startplan]");
      if (sp) {
        const cand = sc.candidates.candidates.find((c) => c.id === sp.dataset.cand);
        if (cand) startPlan(sid, rep, sc, cand, sp.dataset.which);
      }
    });
    // ホームの「この一点で練習を組む」から来たら、プランを始めるシートを開く（1回だけ）
    if (query.start === "1") {
      const act = (sc.gist.blocks || []).find((b) => b.id === "action");
      const cand = act && act.start && sc.candidates.candidates.find((c) => c.id === act.start);
      history.replaceState(null, "", `#/session/${sid}?scope=${encodeURIComponent(sc.scope_id)}`);
      if (cand && cand.kind !== "measure") startPlan(sid, rep, sc, cand, "now");
    }
  }

  // ==== くわしい解説（①〜⑧・図・数字） ====
  function extrasFor(c, sc) {
    const f = (c.facts || []).map((id) => (sc.facts || {})[id]).find((x) => x && x.unit === "seqs" && /missing_seqs$/.test(x.id));
    if (!f || !Array.isArray(f.value) || !f.value.length) return "";
    return `<div class="row">${f.value.map((q) => `<button class="btn small jump" data-jump="${Number(q)}">#${Number(q)}</button>`).join("")}</div>`;
  }
  // Claude のつなぎの文（検証済み）。落ちた節は定型文のまま
  function narrOf(sc, secId) {
    const res = sc.narrative && sc.narrative.result;
    if (!res || res.generated_by !== "narrative") return null;
    if ((res.fallback_sections || []).some((f) => f.id === secId)) return null;
    const sec = (res.sections || []).find((x) => x.id === secId);
    if (!sec) return null;
    const n = { pre: [], before: {}, glossAfter: {}, post: [] };
    let pending = [], last = null;
    for (const b of sec.blocks || []) {
      if (b.t === "bridge") pending.push(b.text);
      else if (b.t === "claim") {
        if (last === null) n.pre.push(...pending); else (n.before[b.id] = n.before[b.id] || []).push(...pending);
        pending = []; last = b.id;
      } else if (b.t === "gloss") (n.glossAfter[last || ""] = n.glossAfter[last || ""] || []).push(b.id);
    }
    n.post = pending;
    return n;
  }
  const bridgeHtml = (arr, tag = "p") => (arr || []).map((t) => `<${tag} class="bridge" data-bridge>${esc(t)}</${tag}>`).join("");
  function glossHtml(rep, ids) {
    const all = (rep && rep.glossary) || [];
    return (ids || []).map((id) => all.find((g) => g.id === id)).filter(Boolean)
      .map((g) => `<p class="gloss" data-gloss="${esc(g.id)}"><b>${esc(g.term)}</b>: ${esc(g.text)}</p>`).join("");
  }
  function narrMeta(rep, sc) {
    const nr = sc.narrative;
    const res = nr && nr.result;
    if (res && res.reason === "validation") {
      const c = typeof nr.cost_usd === "number" ? `・料金 $${nr.cost_usd.toFixed(4)}` : "";
      return `<p class="repmeta" data-narr-meta="validation">Claude のつなぎは検証を通らなかったので、定型文のままです${esc(c)}</p>`;
    }
    if (!res || res.generated_by !== "narrative") return "";
    const cost = typeof nr.cost_usd === "number" ? `1回 約${Math.round(nr.cost_usd * 150 * 10) / 10}円（$${nr.cost_usd.toFixed(4)}）` : "料金は不明";
    const fb = (res.fallback_sections || []).length ? `・${res.fallback_sections.length}節は定型文だけ` : "";
    const asked = rep && rep.narrative_model;
    const other = asked && nr.model && nr.model !== asked ? "・別のモデルの答えを表示中" : "";
    return `<p class="repmeta" data-narr-meta title="答えたモデル: ${esc(nr.model || res.model || "不明")}">定型文＋Claude のつなぎ（検証済み）・${esc(cost)}${esc(fb)}${esc(other)}</p>`;
  }

  // ⑦ 次にやること: 候補（いま・次）のステップのカードと「このプランで始める」「一回だけ実験する」
  function planHtml(sc) {
    const cd = sc.candidates;
    if (!cd || !(cd.candidates || []).length) return "";
    const by = (id) => cd.candidates.find((c) => c.id === id);
    const now = by(cd.now), next = cd.next ? by(cd.next) : null;
    const title = (suffix, prefix) => (claimOf(sc, suffix) || "").replace(prefix, "").split("。")[0];
    const steps = [];
    if (now) steps.push({ no: 1, which: "now", c: now, what: title("next.now", /^いま:\s*/) });
    if (next) steps.push({ no: 2, which: "next", c: next, what: title("next.next", /^次:\s*/) });
    if (next && (sc.clubs || []).length > 1) steps.push({ no: 3, c: null, what: "ほかの番手で確かめる（ステップ2で効いた手段が、ほかの番手でも効くか）" });
    const kpi = (c) => (c && c.kpi ? `${esc(LABEL[c.kpi.metric] || c.kpi.metric)}を${esc(GOAL_L[c.kpi.goal] || c.kpi.goal)}` : "—");
    const blocks = (c) => {
      if (!c) return "A-B-A（短い版）";
      if (c.kind === "measure") return "計測プラン（先に測れるようにする）";
      const d = c.design || {};
      const chips = (d.template || []).map((b) => `<span class="chip ${b.kind === "baseline" ? "none" : b.kind === "intervention" ? "brand" : "warn"}">${esc(BLK[b.kind] || b.kind)} ${b.n}</span>`).join("");
      return `${c.club && c.club.club ? esc(c.club.club) + "・" : ""}A-B-B-A<div class="blocks" style="margin-top:var(--s1)">${chips}</div><span class="caption">1回 ${d.total}球${d.one_session === false ? "・1回の練習では判定できないので、同じ型を練習をまたいで積む" : ""}</span>`;
    };
    const hand = sc.__hand;
    const cond = (st) => (st.c ? esc((claimOf(sc, `${st.which}.advance`) || "—").replace(/^次に進む条件:\s*/, "")) : "—");
    const guard = (st) => {
      const t = st.c ? fillBand(claimOf(sc, `${st.which}.guards`), sc, hand) : null;
      return t ? `<div class="kv"><span>動かさないもの</span>${esc(t.replace(/^動かさないもの:\s*/, ""))}</div>` : "";
    };
    let h = "";
    for (const st of steps) {
      h += `<div class="cand" data-step="${st.no}"><div><b>ステップ ${st.no}</b>${st.which === "now" ? ` <span class="chip good">いま</span>` : st.which === "next" ? ` <span class="chip none">次</span>` : ""}${st.c && (st.c.issues || []).some((i) => i.borderline) ? ` <span class="chip warn">ぎりぎり</span>` : ""} ${esc(st.what)}</div>
        <div class="kv"><span>指標と目標</span>${kpi(st.c || next)}</div>
        <div class="kv"><span>組み方</span>${blocks(st.c)}</div>
        ${guard(st)}
        <div class="kv"><span>次に進む条件</span>${cond(st)}</div></div>`;
    }
    const btns = steps.filter((st) => st.c && st.c.kind !== "measure")
      .map((st) => `<button class="btn" data-act="startplan" data-scope="${esc(sc.scope_id)}" data-cand="${esc(st.c.id)}" data-which="${st.which}">このプランで始める（${st.which === "now" ? "いま" : "次"}）</button>`
        + `<button class="btn" data-act="startexp" data-scope="${esc(sc.scope_id)}" data-cand="${esc(st.c.id)}" data-which="${st.which}">一回だけ実験する（${st.which === "now" ? "いま" : "次"}）</button>`).join("");
    if (btns) h += `<div class="row" style="margin-top:var(--s2)">${btns}</div><p class="caption">「このプランで始める」は、何回かの練習をまたいで確かめます（練習の画面に組み方が出ます）。「一回だけ実験する」は、実験の画面に仮説・指標・目標・クラブを入れて開きます。</p>`;
    return h;
  }

  function sectionHtml(rep, sc, sec, hand, first) {
    const claims = (sec.claims || []).map((c) => { const t = fillBand(c.text, sc, hand); return { ...c, text: t === null ? null : App.textJa(t) }; }).filter((c) => c.text !== null);
    const lf = fillBand(sec.lead, sc, hand);
    const lead = lf != null ? App.textJa(lf) : (claims[0] ? claims[0].text : "");
    const nr = narrOf(sc, sec.id);
    const claimHtml = (c) => `${nr ? bridgeHtml(nr.before[c.id]) : ""}<p class="claim ${c.layer === "meta" ? "meta" : c.layer === "hint" ? "hint" : ""}" data-claim="${esc(c.id)}">${esc(c.text)}</p>${extrasFor(c, sc)}${nr ? glossHtml(rep, nr.glossAfter[c.id]) : ""}`;
    const figs = (sec.figures || []).filter((f) => sc.figures && sc.figures[f]).map((f) => `<div class="figslot" data-scope="${esc(sc.scope_id)}" data-fig="${esc(f)}"></div>`).join("");
    const extra = sec.id === "s7" ? planHtml(sc) : "";
    if (first) {
      return `<div class="sec one" data-sec="${esc(sec.id)}" id="sec-${esc(sc.scope_id)}-${esc(sec.id)}"><div class="sh"><span class="no">${esc(sec.no)}</span> <span class="ttl">${esc(sec.title)}</span></div>
        <div class="body">${nr ? bridgeHtml(nr.pre) + glossHtml(rep, nr.glossAfter[""]) : ""}${claims.map(claimHtml).join("")}${nr ? bridgeHtml(nr.post) : ""}${figs}${extra}</div></div>`;
    }
    const inTable = (c) => /\/(now|next)\.(advance|guards)$/.test(c.id);
    const rest = claims.filter((c) => c.text !== lead && !(extra && inTable(c)));
    const more = rest.length || figs || extra || (nr && nr.post.length);
    const post = nr ? bridgeHtml(nr.post) : "";
    const inner = sec.id === "s7" ? `${extra}${rest.map(claimHtml).join("")}${post}${figs}` : `${rest.map(claimHtml).join("")}${post}${figs}${extra}`;
    const leadClaim = claims.find((c) => c.text === lead);
    return `<details class="sec" data-sec="${esc(sec.id)}" id="sec-${esc(sc.scope_id)}-${esc(sec.id)}"><summary><span class="no">${esc(sec.no)}</span><span class="ttl">${esc(sec.title)}</span>
        ${nr ? bridgeHtml(nr.pre, "span") : ""}<span class="lead">${esc(lead)}${leadClaim ? extrasFor(leadClaim, sc) : ""}</span>${more ? `<span class="more">続きを開く ▾</span>` : ""}</summary>
        <div class="body">${inner}</div></details>`;
  }

  function scopeCard(rep, sc, hand, nested) {
    sc.__hand = hand;
    const excl = sc.mishit_excluded ? `・ミスヒットの候補 ${sc.mishit_excluded}球を除く（${(sc.mishit_seqs || []).map((q) => "#" + q).join(" ")}）` : "";
    const clubs = (sc.clubs || []).length > 1 ? `${sc.clubs.map((c) => esc(App.clubJa(c))).join("・")}／` : "";
    let h = `<article class="rcard" id="rc-${esc(sc.scope_id)}" data-scope="${esc(sc.scope_id)}" data-kind="${esc(sc.kind)}">
      <h2>${esc(scopeName(sc))} <span class="caption">${clubs}${sc.n}球${esc(excl)}</span></h2>`;
    h += (sc.sections || []).map((sec, i) => sectionHtml(rep, sc, sec, hand, i === 0)).join("");
    h += narrMeta(rep, sc);
    if (nested && nested.length) {
      h += `<details class="folded"><summary>1本ずつの短い版（${nested.map((x) => esc(scopeName(x))).join("・")}）</summary>${nested.map((x) => scopeCard(rep, x, hand, [])).join("")}</details>`;
    }
    return h + `</article>`;
  }

  // 数字の段（KPI と所見）
  function findingText(f, shotsList) {
    const ids = (f.shot_ids || []).length && f.shot_ids.length <= 12 ? `（${f.shot_ids.map((i) => "#" + (shotsList.find((s) => s.id === i) || {}).seq).join(" ")}）` : "";
    switch (f.kind) {
      case "curve_cause":
        if (f.cause === "strike") {
          return f.strength === "subset"
            ? `曲がった${f.evidence.of}球のうち<b>${f.evidence.count}球${ids}は打点（ギア効果）</b>が原因です。フェースの向きではなく、当たる場所が変わった球の群があります。`
            : `曲がった${f.evidence.of}球のうち<b>${f.evidence.count}球は打点（ギア効果）</b>が原因です。フェースを直す前に、当たる場所を揃えるのが先です。`;
        }
        return f.strength === "subset"
          ? `曲がった${f.evidence.of}球のうち<b>${f.evidence.count}球${ids}はフェース・トゥ・パス</b>が原因です。`
          : `曲がった${f.evidence.of}球のうち<b>${f.evidence.count}球はフェース・トゥ・パス</b>（フェースとクラブの進む向きの差）が原因です。`;
      case "dispersion_driver": {
        const r2 = f.evidence.r2_single;
        if (r2 === undefined || r2 === null) return `左右のばらつきを一番よく説明するのは<b>${esc(LABEL[f.metric] || f.metric)}</b>です（${f.evidence.n}球）。`;
        return `<b>${esc(LABEL[f.metric] || f.metric)}の向きだけで、左右のばらつき${f.outcome === "side_pct" ? "（キャリーに対する割合）" : ""}の${Math.round(r2 * 100)}%</b>を説明できます（${f.evidence.n}球・単回帰）。`;
      }
      case "extreme_strike": {
        const e = f.evidence, where = f.contact === "heel_extreme" ? "ネック寄り（ヒール側）" : "先端寄り（トゥ側）";
        const noFace = e.no_face ?? e.no_club_data ?? 0, noBoth = e.no_face_and_path || 0;
        const miss = noFace ? `（うち${noFace}球は TrackMan がフェースを取れていません${noBoth ? `。${noBoth}球はパスも` : ""}）` : "";
        return `<b>${e.count}球${ids}が極端な${where}</b>の当たりです。左右のずれは平均 <b>${fmt("side", e.mean_abs_side)}</b>（ほかの球は ${fmt("side", e.mean_abs_side_others)}）です${miss}。フェースとは別の問題です。`;
      }
      case "face_maybe_computed":
        return `フェースの値は、TrackMan が打ち出しとパスから計算している可能性があります（打ち出しの予測との差のばらつき ${f.evidence.launch_residual_sd.toFixed(2)}°・${f.evidence.n}球）。答え合わせは実際に落ちた場所で行います。`;
      case "mishit_candidates":
        return `<b>${f.evidence.count}球${ids}</b>はトップ・薄い当たりなどのミスヒットに見えます。ばらつきの分析からは外して計算しました。全体からも外すなら「一球ずつ」で除外してください。`;
      case "need_data":
        return `打点のデータが<b>${f.evidence.missing}球ぶん</b>ありません。インパクトテープの結果を「一球ずつ」で入れると、打点が原因かを確かめられます。`;
      case "insufficient":
        return `曲がりの原因を言うには球が足りません（${f.n}球 / あと${f.needed - f.n}球）。`;
    }
    return esc(JSON.stringify(f));
  }
  const START_W = { R: { push: "右に出る", straight: "まっすぐ出る", pull: "左に出る" }, L: { push: "左に出る", straight: "まっすぐ出る", pull: "右に出る" } };
  const CURVE_W = { R: { right: "右へ曲がる", none: "曲がらない", left: "左へ曲がる" }, L: { right: "左へ曲がる", none: "曲がらない", left: "右へ曲がる" } };
  const TCAUSE = { extreme_strike: "ネック・先端寄り", face_to_path: "フェース・トゥ・パス", strike: "打点", mixed: "両方", none: "—", unknown: "分からない" };
  function tendencyHtml(p, hand) {
    const t = p && p.tendency;
    if (!t || !t.cells) return "";
    const cells = t.cells.filter((c) => c.n).sort((a, b) => b.n - a.n);
    if (!cells.length) return "";
    const one = (c) => `${START_W[hand][c.start]}・${CURVE_W[hand][c.curve]} <b>${c.n}球</b>${c.breakdown.length > 1 || (c.breakdown[0] && c.breakdown[0].cause !== "none") ? `（${c.breakdown.map((b) => `${TCAUSE[b.cause] || b.cause} ${b.n}`).join("・")}）` : ""}`;
    return `<p class="caption">球筋の頻度（${t.n}球${t.mishit_included ? `・ミスヒットの候補 ${t.mishit_included}球を含む` : ""}）: ${cells.map(one).join(" ／ ")}</p>`;
  }
  function exclNote(v) {
    const parts = ["face_angle", "club_path", "face_to_path", "impact_offset"].filter((k) => v[k] && v[k].mishit_excluded).map((k) => `${esc(LABEL[k] || k)} ${v[k].mishit_excluded}球`);
    return parts.length ? `<p class="caption">平均とばらつきは、ミスヒットの候補を除いた数字です（除いた球: ${parts.join("・")}）。</p>` : "";
  }
  function analysisHtml(a, shotsList, hand) {
    let h = "";
    const fcard = (f) => { const [t, cl] = STRENGTH[f.strength] || ["", "none"]; return `<div class="finding">${t ? `<span class="chip ${cl}">${t}</span> ` : ""}${findingText(f, shotsList)}</div>`; };
    for (const g of a.groups || []) {
      const fs = a.findings.filter((f) => f.club === g.name);
      const v = g.variability;
      h += `<section class="rcard"><h2>${esc(App.textJa(g.name))} <span class="caption">${g.clubs.map((c) => esc(App.clubJa(c))).join("・")}／${g.n}球</span></h2>${exclNote(v)}
        <div class="kpi">${["face_angle", "club_path", "face_to_path", "impact_offset"].filter((k) => v[k]).map((k) =>
          `<div><div class="caption">${esc(LABEL[k])}</div><div class="v">${fmt(k, v[k].mean, true)}</div><div class="caption">ばらつき ±${v[k].sd != null ? fmt(k, v[k].sd) : "—"}</div></div>`).join("")}</div>
        ${fs.map(fcard).join("") || `<p class="sub">まとめて言えることはまだありません。</p>`}${tendencyHtml(g.profile, hand)}</section>`;
    }
    for (const c of a.clubs) {
      const fs = a.findings.filter((f) => f.club === c.club);
      const v = c.variability;
      h += `<section class="rcard"><h2>${esc(App.clubJa(c.club))} <span class="caption">${c.n}球</span></h2>${exclNote(v)}
        <div class="kpi"><div><div class="caption">Good</div><div class="v">${c.good.n_good} / ${c.n}</div></div>
          <div><div class="caption">キャリーの中央値</div><div class="v">${fmt("carry", c.carry_median)}</div></div>
          ${["face_to_path", "face_angle", "club_path"].filter((k) => v[k]).map((k) =>
            `<div><div class="caption">${esc(LABEL[k])}</div><div class="v">${fmt(k, v[k].mean, true)}</div><div class="caption">ばらつき ±${v[k].sd != null ? v[k].sd.toFixed(1) : "—"}°</div></div>`).join("")}</div>
        ${fs.length ? fs.map(fcard).join("") : `<p class="sub">このクラブについて言えることはまだありません。</p>`}
        ${tendencyHtml(c.profile, hand) || `<p class="caption">球筋のまとまり: ${c.flight_groups.map((g) => `${esc(g.miss_type)}×${esc(CAUSE[g.curve_cause] || g.curve_cause)} ${g.n}球`).join(" / ")}</p>`}</section>`;
    }
    return h || `<p class="sub">球がありません。</p>`;
  }

  // 1球の中身（図の点・練習の球の帯から）
  function openShot(sid, shot, goodInfo) {
    const m = shot.metrics, d = shot.decomposition;
    const cols = ["carry", "side", "launch_direction", "spin_axis", "face_angle", "club_path", "face_to_path", "attack_angle", "spin_loft", "impact_offset"];
    const signed = ["side", "launch_direction", "spin_axis", "face_angle", "club_path", "face_to_path", "impact_offset"];
    const sh = App.openSheet({ title: `#${shot.seq} ${App.clubJa(shot.club)}`, label: "shot", html: `
      <div class="row">${goodInfo && goodInfo.good !== null && goodInfo.good !== undefined ? `<span class="chip ${goodInfo.good ? "good" : "bad"}">${goodInfo.good ? "✓ Good" : "× Miss"}</span>` : ""}${shot.excluded ? `<span class="chip none">除外</span>` : ""}</div>
      <div class="shotgrid">${cols.map((c) => `<div><span>${esc(LABEL[c])}</span>${fmt(c, m[c], signed.includes(c))}</div>`).join("")}</div>
      <p>${esc(START[d.start_line] || "—")}・${esc(CURVE[d.curve] || "—")} ${causeChip(d.curve_cause)}
        <span class="chip ${/extreme/.test(d.contact) ? "bad" : d.contact === "center" ? "good" : "none"}">${esc(CONTACT[d.contact] || "—")}</span>
        ${(d.flags || []).includes("thin") ? `<span class="chip warn">薄い</span>` : ""}</p>
      ${(d.notes || []).map((n) => `<p class="caption">⚠︎ ${esc(n)}</p>`).join("")}
      <a class="btn block" data-act="goto" href="#/session/${sid}/shots?sel=${shot.id}">一球ずつで見る</a>` });
    sh.el.dataset.id = String(shot.id);
    return sh;
  }

  function nearestShot(ev) {
    const svg = ev.target.closest && ev.target.closest("figure.fig svg");
    if (!svg) return null;
    let best = null, bd = 24 * 24;
    for (const g of svg.querySelectorAll("g[data-shot]")) {
      const hit = g.querySelector(".g-hit") || g;
      const b = hit.getBoundingClientRect(), dx = b.left + b.width / 2 - ev.clientX, dy = b.top + b.height / 2 - ev.clientY;
      if (dx * dx + dy * dy < bd) { bd = dx * dx + dy * dy; best = g; }
    }
    return best;
  }

  async function renderDetail({ el, params, query, alive }) {
    const sid = Number(params.id);
    const tab = query.tab === "num" ? "num" : "rep";
    const q = (t) => `#/session/${sid}/detail?${new URLSearchParams({ ...(query.scope ? { scope: query.scope } : {}), ...(t === "num" ? { tab: "num" } : {}) })}`;
    el.innerHTML = `<div class="pagehead">${App.backBtn(`#/session/${sid}${query.scope ? "?scope=" + encodeURIComponent(query.scope) : ""}`, "診断へ戻る")}<h1>くわしい解説</h1></div>
      <div class="seg" role="group" aria-label="見るもの" data-detail-tabs><a href="${q("rep")}" ${tab === "rep" ? 'aria-current="page"' : ""} data-sub="rep">解説と図</a><a href="${q("num")}" ${tab === "num" ? 'aria-current="page"' : ""} data-sub="num">数字</a></div>
      <div data-body class="block"></div>`;
    const body = $("[data-body]", el);
    const stop = App.loading(body, "解説を作っています…");
    let r;
    try { r = await App.report(sid); } finally { stop(); }
    if (!alive()) return;
    const shotsList = r.shots || [];
    if (tab === "num") {
      const stop2 = App.loading(body);
      try {
        const a = S.cache.analysis[sid] || (S.cache.analysis[sid] = await api("GET", `/v1/sessions/${sid}/analysis`));
        stop2();
        if (!alive()) return;
        body.innerHTML = `<div data-analysis>${analysisHtml(a, shotsList, r.handedness === "L" ? "L" : "R")}</div>`;
      } catch (e) {
        stop2();
        body.innerHTML = App.errOf(e, { what: "数字を出せませんでした", saved: "記録は消えていません。", next: e.status === 503 ? "分析のサービスが動いていません。少し待って開き直してください。" : "少し待って開き直してください。" });
      }
      return;
    }
    if (!r.available || !r.report) {
      body.innerHTML = `<div class="note warn">解説は、分析のサービスが動いているときだけ出ます${r.reason ? `（${esc(r.reason)}）` : ""}。</div>`;
      return;
    }
    let figErr = "";
    try { await App.loadFigures(); } catch (e) { figErr = e.message; }
    const rep = r.report, hand = rep.handedness === "L" ? "L" : "R";
    const scopes = rep.scopes || [];
    const isMain = (x) => x.kind === "main";
    const parentOf = (x) => (!isMain(x) && x.folded_into ? scopes.find((m) => isMain(m) && m.scope_id === x.folded_into) : null);
    let h = figErr ? `<p class="fielderr">${esc(figErr)}</p>` : "";
    if (!scopes.length) h += `<div class="note">${rep.n_shots ? `この記録の${rep.n_shots}球は全部「除外」になっているので、解説は作れません。` : "この記録には球がありません。"}</div>`;
    for (const sc of scopes) {
      if (parentOf(sc)) continue;
      h += scopeCard(rep, sc, hand, scopes.filter((x) => parentOf(x) === sc));
    }
    if ((rep.session_unknowns || []).length) {
      h += `<details class="rcard folded" data-scope="session"><summary><b>まだ言えないこと（全部の範囲に共通）</b></summary>${rep.session_unknowns.map((c) => `<p class="claim meta" data-claim="${esc(c.id)}">${esc(c.text)}</p>`).join("")}</details>`;
    }
    if ((rep.glossary || []).length) {
      h += `<details class="rcard folded"><summary>用語</summary><dl>${rep.glossary.map((g) => `<dt><b>${esc(g.term)}</b></dt><dd class="sub" style="margin:0 0 var(--s2)">${esc(g.text)}</dd>`).join("")}</dl></details>`;
    }
    const mains = scopes.filter((x) => isMain(x) && (x.sections || []).length && x.sections[0].id !== "error");
    const narrated = scopes.filter((x) => x.narrative && x.narrative.result && x.narrative.result.generated_by === "narrative");
    const rejected = scopes.filter((x) => x.narrative && x.narrative.result && x.narrative.result.reason === "validation");
    const paid = narrated.concat(rejected);
    const costs = paid.map((x) => x.narrative.cost_usd);
    const costText = !paid.length ? "" : costs.every((c) => typeof c === "number") ? `・Claude の料金 $${costs.reduce((a, b) => a + b, 0).toFixed(4)}（${paid.length}範囲）` : "・Claude の料金は一部不明";
    const genText = narrated.length ? "定型文＋Claude のつなぎ（検証済み）"
      : rejected.length ? "文章はすべて定型文です（Claude のつなぎは検証を通らなかったので使っていません）" : "文章はすべて定型文です（Claude は使っていません）";
    h += `<p class="repmeta" data-gen="${narrated.length ? "narrative" : "template"}">${genText}${esc(costText)}・${esc(rep.report_version)}・${esc(rep.engine_version)}・${esc(r.physics_version || "")}</p>`;
    const lacking = mains.filter((x) => x.narrative && !(x.narrative.result && (x.narrative.result.generated_by === "narrative" || x.narrative.result.reason === "validation")));
    if (r.narrative_enabled && lacking.length) {
      h += `<div class="row"><button class="btn" data-act="narrate">Claude のつなぎの文を付ける（${lacking.length}範囲・1範囲 約 $0.02〜0.05）</button></div>
        <p class="caption" data-narr-note>事実の文は定型文のままです。Claude は節の頭に案内の文を足すだけで、検証を通らなかった節は定型文だけで出します。</p>`;
    }
    body.innerHTML = h;
    if (window.Figures) {
      for (const slot of body.querySelectorAll(".figslot")) {
        const sc = scopes.find((x) => x.scope_id === slot.dataset.scope);
        const node = sc && window.Figures.render(sc.figures[slot.dataset.fig], { hand, bandShape: sc.band_shape });
        if (node) slot.replaceWith(node); else slot.remove();
      }
    }
    // 開いてきた節（「なぜ？」の「図と数字をくわしく見る」）まで動かして開く
    const target = query.scope && query.sec ? document.getElementById(`sec-${query.scope}-${query.sec}`) : (query.scope ? document.getElementById(`rc-${query.scope}`) : null);
    if (target) { if (target.tagName === "DETAILS") target.open = true; target.scrollIntoView({ block: "start" }); }

    const goodOf = (shot) => (shot.good_override !== undefined && shot.good_override !== null ? { good: shot.good_override } : null);
    body.addEventListener("click", async (ev) => {
      const pt = nearestShot(ev) || ev.target.closest("[data-shot]");
      if (pt) { const s = shotsList.find((x) => x.id === Number(pt.dataset.shot)); if (s) openShot(sid, s, goodOf(s)); return; }
      const j = ev.target.closest("[data-jump]");
      if (j) { ev.preventDefault(); const s = shotsList.find((x) => x.seq === Number(j.dataset.jump)); if (s) App.go(`/session/${sid}/shots?sel=${s.id}`); return; }
      const nb = ev.target.closest("button[data-act=narrate]");
      if (nb) { startNarrative(sid, nb, body); return; }
      const sp = ev.target.closest("button[data-act=startplan]");
      if (sp) {
        const sc = scopes.find((x) => x.scope_id === sp.dataset.scope);
        const cand = sc && sc.candidates.candidates.find((c) => c.id === sp.dataset.cand);
        if (cand) startPlan(sid, rep, sc, cand, sp.dataset.which);
        return;
      }
      const b = ev.target.closest("button[data-act=startexp]");
      if (b) {
        const sc = scopes.find((x) => x.scope_id === b.dataset.scope);
        const cand = sc && sc.candidates.candidates.find((c) => c.id === b.dataset.cand);
        if (cand) startExperiment(sid, sc, cand, b.dataset.which);
      }
    });
    body.addEventListener("keydown", (ev) => {
      const pt = ev.target.closest && ev.target.closest("[data-shot]");
      if (pt && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); const s = shotsList.find((x) => x.id === Number(pt.dataset.shot)); if (s) openShot(sid, s, goodOf(s)); }
    });
  }

  // Claude のつなぎの文を頼む（ジョブ。数秒おきに見て、終わったら解説を取り直す）
  async function startNarrative(sid, btn, body) {
    const note = $("[data-narr-note]", body);
    btn.disabled = true;
    const sayN = (t) => { if (note) note.textContent = t; };
    try {
      const r = await api("POST", `/v1/sessions/${sid}/report/narrative`);
      if (!r.enabled) { sayN(r.reason || "Claude は使えません（定型文だけ）"); return; }
      const limited = (r.jobs || []).filter((j) => j.limit);
      let wait = (r.jobs || []).filter((j) => j.job_id && j.status !== "done" && j.status !== "failed");
      sayN(`Claude に頼んでいます（${wait.length}範囲）…${limited.length ? ` ${limited[0].reason}` : ""}`);
      const failed = [];
      for (let i = 0; wait.length && i < 120; i++) {
        await new Promise((ok) => setTimeout(ok, 2000));
        if (!body.isConnected) return;
        const next = [];
        for (const j of wait) {
          const got = await api("GET", `/v1/jobs/${j.job_id}`);
          if (got.status === "failed") failed.push(got.error || "失敗");
          else if (got.status !== "done") next.push(j);
        }
        wait = next;
      }
      await App.report(sid, true);
      await App.render();
      const msg = [failed.length ? `${failed.length}範囲は Claude を使えなかったので定型文だけです（${failed[0]}）` : "", limited.length ? limited[0].reason : ""].filter(Boolean).join("。");
      if (msg) App.toast(msg);
    } catch (e) { sayN(e.message); }
    finally { btn.disabled = false; }
  }

  // ⑦ の「一回だけ実験する」: 実験の画面に値を入れて開く
  const confirmedBlocked = new Set();
  async function startExperiment(sid, sc, cand, which) {
    const key = `${sc.scope_id}/${cand.id}`;
    if ((cand.blocked_by || []).length && !confirmedBlocked.has(key)) {
      const first = cand.blocked_by[0].first;
      const firstT = (claimOf(sc, "next.now") || "").replace(/^いま:\s*/, "").split("。")[0] || first || "ほかの候補";
      if (!(await App.ask({ title: "先にする候補があります", text: `この候補は「${firstT}」が先、としたものです（理由は⑦のとおり）。先にこちらを始めると、結果を読み分けにくくなります。それでも始めますか？`, ok: "それでも始める", cancel: "やめる" }))) return;
      confirmedBlocked.add(key);
    }
    const d = cand.design || {};
    const club = cand.club && cand.club.club;
    S.expPrefill = {
      sid, hyp: claimOf(sc, which === "now" ? "now.hypothesis" : "next.hypothesis") || "",
      metric: cand.kpi && cand.kpi.metric, goal: cand.kpi && cand.kpi.goal, club: club || "",
      note: `仮説・指標・目標・クラブを入れました。意識する1点（介入）を書いて「作る」を押してください。`
        + (d.per_block ? `組み方の目安: ${club ? club + "で" : ""}「いつも通り」→「本番」→「本番」→「いつも通り」（各${d.per_block}球）。作ったあと、打った順の範囲をブロックに入れます。` : ""),
    };
    App.go(`/session/${sid}/experiments`);
  }

  // ==== 一球ずつ ====
  async function renderShots({ el, params, query, alive }) {
    const sid = Number(params.id);
    // 操作（良い球の印・当たった場所・除外）は番号とクラブのすぐ右に置く（右端だと、スマホでは表を大きく横に動かさないと見つからない）
    el.innerHTML = `<div class="pagehead">${App.backBtn(`#/session/${sid}`, "診断へ戻る")}<h1>一球ずつ</h1></div>
      <p class="sub">球ごとに、良い球の印を付け直す・当たった場所を入れる・計算から外すことができます。</p>
      <details class="folded"><summary>表の見方</summary>
        <p class="caption">「曲がりの原因」は、当たる瞬間のクラブの向きと動きから計算で分けたものです。</p>
        <p class="caption">「当たった場所」には、フェースに貼るシールなどで見た位置を、芯からの mm で入れます（先の側がプラス、ネックの側がマイナス）。</p>
        <p class="caption">「良い球」を押すたびに、自動 → 良い球 → 良くない球 → 自動 の順に替わります。</p></details>
      <div data-err></div><div class="scroll card" style="padding:0"><table data-shots></table></div>`;
    const table = $("[data-shots]", el);
    const stop = App.loading(table);
    let list;
    try { list = await App.shots(sid, true); } finally { stop(); }
    if (!alive()) return;
    let analysis = S.cache.analysis[sid] || null;
    const goodOf = (shot) => {
      if (shot.good_override !== undefined && shot.good_override !== null) return { good: shot.good_override, by: "override" };
      if (!analysis) return null;
      for (const c of analysis.clubs) { const j = c.good.shots.find((x) => x.id === shot.id); if (j) return j; }
      return null;
    };
    const candidateOf = (shot) => {
      if (!analysis) return null;
      for (const c of analysis.clubs) { const x = (c.mishit_candidates || []).find((m) => m.id === shot.id); if (x) return x; }
      return null;
    };
    const cols = ["carry", "side", "launch_direction", "spin_axis", "face_angle", "club_path", "face_to_path", "attack_angle", "impact_offset"];
    const signed = ["side", "launch_direction", "spin_axis", "face_angle", "club_path", "face_to_path", "impact_offset"];
    const draw = () => {
      const head = `<tr><th>#</th><th class="l">クラブ</th><th class="l">良い球</th><th class="l">当たった場所（mm）</th><th class="l">計算</th>${cols.map((c) => `<th>${esc(LABEL[c])}</th>`).join("")}<th class="l">球筋</th><th class="l">曲がりの原因</th><th class="l">当たり</th></tr>`;
      const rows = list.map((s) => {
        const m = s.metrics, d = s.decomposition, g = goodOf(s);
        const gChip = !g || g.good === null ? `<span class="chip none">—</span>`
          : `<span class="chip ${g.good ? "good" : "bad"}" title="${esc((g.failed || []).join(", "))}">${g.good ? "✓ 良い" : "× 良くない"}${g.by === "override" ? "✎" : ""}</span>`;
        const manual = (s.manual || []).includes("impact_offset");
        const cand = candidateOf(s);
        return `<tr class="${s.excluded ? "excluded" : ""} ${String(s.id) === query.sel ? "sel" : ""}" data-id="${s.id}">
          <td>${s.seq}</td><td class="l">${esc(App.clubJa(s.club))}</td>
          <td class="l"><button class="btn small" data-act="good" aria-label="#${s.seq} の良い球の印を切り替える（いま: ${!g || g.good === null ? "決まっていない" : g.good ? "良い" : "良くない"}）">${gChip}</button></td>
          <td class="l"><input type="number" step="1" min="-60" max="60" data-act="offset" aria-label="#${s.seq} の当たった場所（mm）" value="${m.impact_offset != null && manual ? Math.round(m.impact_offset * 1000) : ""}"></td>
          <td class="l"><button class="btn small" data-act="excl">${s.excluded ? "戻す" : "除外"}</button></td>
          ${cols.map((c) => `<td>${fmt(c, m[c], signed.includes(c))}${c === "impact_offset" && manual ? "✎" : ""}</td>`).join("")}
          <td class="l">${esc(START[d.start_line])}・${esc(CURVE[d.curve])}</td>
          <td class="l">${causeChip(d.curve_cause)}${(d.notes || []).length ? ` <span title="${esc(d.notes.join("\n"))}">⚠︎</span>` : ""}</td>
          <td class="l"><span class="chip ${/extreme/.test(d.contact) ? "bad" : d.contact === "center" ? "good" : "none"}">${esc(CONTACT[d.contact] || "—")}</span>${(d.flags || []).includes("thin") ? ` <span class="chip warn">薄い</span>` : ""}${cand ? ` <span class="chip warn" title="${esc(cand.reasons.map((x) => REASON[x] || x).join("・"))}">除外候補</span>` : ""}</td></tr>`;
      }).join("");
      table.innerHTML = head + rows;
    };
    draw();
    const sel = table.querySelector("tr.sel");
    if (sel) sel.scrollIntoView({ block: "center" });
    if (!analysis) {
      api("GET", `/v1/sessions/${sid}/analysis`).then((a) => { analysis = S.cache.analysis[sid] = a; if (alive()) draw(); }).catch(() => {});
    }
    const patch = async (id, body) => {
      $("[data-err]", el).innerHTML = "";
      try {
        const out = await api("PATCH", `/v1/shots/${id}`, body);
        list = list.map((x) => (x.id === id ? out : x));
        App.invalidate(sid);
        S.cache.shots[sid] = list;
        analysis = null;
        draw();
        api("GET", `/v1/sessions/${sid}/analysis`).then((a) => { analysis = S.cache.analysis[sid] = a; if (alive()) draw(); }).catch(() => {});
      } catch (e) { $("[data-err]", el).innerHTML = App.errOf(e, { what: "変えられませんでした", saved: "この球は前のままです。" }); }
    };
    table.addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-act]");
      if (!b) return;
      const id = Number(b.closest("tr").dataset.id), s = list.find((x) => x.id === id);
      if (b.dataset.act === "excl") patch(id, { excluded: !s.excluded });
      if (b.dataset.act === "good") {
        const cur = s.good_override;
        patch(id, { good_override: cur === undefined || cur === null ? true : cur === true ? false : null });
      }
    });
    table.addEventListener("change", (ev) => {
      const i = ev.target.closest("input[data-act=offset]");
      if (!i || i.value === "") return;
      patch(Number(i.closest("tr").dataset.id), { impact_offset_mm: Number(i.value) });
    });
  }

  // ==== 一回だけの実験 ====
  const TARGETS = ["face_to_path", "face_angle", "club_path", "attack_angle", "dynamic_loft", "spin_loft", "low_point", "impact_offset", "launch_direction", "spin_axis", "side", "carry", "club_speed", "smash_factor"];
  const EXB = { baseline: "基準", intervention: "介入", retention: "定着" };
  function evalText(label, c, metric) {
    const [t, cl] = GRADE[c.grade] || [c.grade, "none"];
    if (c.grade === "insufficient") return `<div class="finding"><span class="chip ${cl}">${t}</span> ${label}: 各ブロック${c.needed}球以上が要ります（いま ${c.n.join(" / ")}球）。</div>`;
    const u = (x) => fmt(metric, x).replace(/^\+/, "");
    return `<div class="finding"><span class="chip ${cl}">${t}</span> ${label}: 改善 <b>${u(c.improvement)}</b>（95%区間 ${u(c.ci95[0])}〜${u(c.ci95[1])}・意味のある差 ${c.mmd != null ? u(c.mmd) : "未設定"}・${c.n.join(" / ")}球）</div>`;
  }
  async function renderExperiments({ el, params, alive }) {
    const sid = Number(params.id);
    const pre = S.expPrefill && S.expPrefill.sid === sid ? S.expPrefill : null;
    S.expPrefill = null;
    el.innerHTML = `<div class="pagehead">${App.backBtn(`#/session/${sid}`, "診断へ戻る")}<h1>一回だけの実験</h1></div>
      <p class="sub">仮説と、意識する1点を決めて、打った順の範囲をブロックに入れて評価します。何日もかけて確かめるなら、診断の「このプランで始める」を使います。</p>
      <section class="card block"><h2>新しい実験</h2>
        ${pre ? `<div class="note" data-ex-note>${esc(pre.note)}</div>` : ""}
        <label class="field"><span>仮説</span><input data-f="hyp" placeholder="例: フェースが開いてプッシュフェードになる"></label>
        <label class="field"><span>介入（意識する1点）</span><input data-f="int" placeholder="例: 左手甲を目標に向けたまま"></label>
        <div class="row"><label class="grow1"><span class="label">指標</span><select data-f="metric" style="width:100%">${TARGETS.map((k) => `<option value="${k}">${esc(LABEL[k])}</option>`).join("")}</select></label>
          <label class="grow1"><span class="label">目標</span><select data-f="goal" style="width:100%">${Object.entries(GOAL_L).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></label>
          <label class="grow1"><span class="label">クラブ</span><select data-f="club" style="width:100%"><option value="">全クラブ</option></select></label></div>
        <div class="block"><button class="btn primary" data-f="save">作る</button><div data-f="err"></div></div></section>
      <section class="block"><h2>この記録の実験</h2><div data-f="list"></div></section>`;
    const f = (k) => $(`[data-f=${k}]`, el);
    const list = await App.shots(sid).catch(() => []);
    if (!alive()) return;
    const clubs = [...new Set(list.map((s) => s.club).filter(Boolean))];
    f("club").insertAdjacentHTML("beforeend", clubs.map((c) => `<option>${esc(c)}</option>`).join(""));
    if (pre) {
      f("hyp").value = pre.hyp || "";
      if (pre.metric && [...f("metric").options].some((o) => o.value === pre.metric)) f("metric").value = pre.metric;
      if (pre.goal && [...f("goal").options].some((o) => o.value === pre.goal)) f("goal").value = pre.goal;
      if (pre.club && [...f("club").options].some((o) => o.value === pre.club)) f("club").value = pre.club;
      f("int").focus();
    }
    const load = async () => {
      const es = await api("GET", `/v1/sessions/${sid}/experiments`);
      f("list").innerHTML = es.map((e) => `
        <div class="card" data-ex="${e.id}">
          <h3>${esc(e.hypothesis)}</h3>
          <p class="sub">介入: ${esc(e.intervention || "—")} ／ 目標: ${esc(LABEL[e.target_metric] || e.target_metric)} を ${esc(GOAL_L[e.goal] || e.goal)}${e.club ? ` ／ ${esc(e.club)}` : ""}</p>
          <p class="sub">ブロック: ${e.blocks.length ? e.blocks.map((b) => `${EXB[b.kind]} #${b.seq_from}〜${b.seq_to}`).join("、") : "まだありません"}</p>
          <div class="row"><select data-x="kind" aria-label="ブロックの種類"><option value="baseline">基準</option><option value="intervention">介入</option><option value="retention">定着</option></select>
            <input type="number" min="1" data-x="from" placeholder="何球目から" style="width:8rem" aria-label="何球目から">
            <input type="number" min="1" data-x="to" placeholder="何球目まで" style="width:8rem" aria-label="何球目まで">
            <button class="btn small" data-act="block">ブロックを足す</button><button class="btn small" data-act="eval">評価する</button></div>
          <div data-x="err"></div><div data-x="out"></div></div>`).join("") || `<p class="sub">まだ実験がありません。</p>`;
    };
    await load().catch((e) => { f("list").innerHTML = App.errOf(e); });
    f("save").addEventListener("click", async () => {
      f("err").innerHTML = "";
      try {
        await api("POST", `/v1/sessions/${sid}/experiments`, { hypothesis: f("hyp").value, intervention: f("int").value, target_metric: f("metric").value, goal: f("goal").value, club: f("club").value });
        f("hyp").value = ""; f("int").value = "";
        await load();
      } catch (e) { f("err").innerHTML = App.errOf(e, { what: "実験を作れませんでした", next: "仮説を書いたか確かめてください。" }); }
    });
    f("list").addEventListener("click", async (ev) => {
      const b = ev.target.closest("button[data-act]");
      if (!b) return;
      const box = b.closest("[data-ex]"), id = box.dataset.ex, x = (k) => box.querySelector(`[data-x=${k}]`);
      x("err").innerHTML = "";
      try {
        if (b.dataset.act === "block") {
          await api("POST", `/v1/experiments/${id}/blocks`, { kind: x("kind").value, seq_from: Number(x("from").value), seq_to: Number(x("to").value) });
          await load();
          return;
        }
        const r = await api("GET", `/v1/experiments/${id}/evaluation`);
        const bl = r.blocks, m = r.target_metric;
        let h = `<p class="caption">${Object.entries(bl).map(([k, v]) => `${EXB[k]}: 平均 ${fmt(m, v.mean, true)}・ばらつき ±${v.sd != null ? fmt(m, v.sd) : "—"}（${v.n}球）`).join(" ／ ")}</p>`;
        h += evalText("介入の効果", r.intervention_vs_baseline, m);
        if (r.retention_vs_baseline) h += evalText("意識を外しても残ったか", r.retention_vs_baseline, m);
        h += (r.notes || []).map((n) => `<p class="caption">※ ${esc(n)}</p>`).join("");
        x("out").innerHTML = h;
      } catch (e) { x("err").innerHTML = App.errOf(e, { what: "できませんでした" }); }
    });
  }

  // ==== プランを始めるシート ====
  const checked = (d) => !!(d && typeof d.checked_by === "string" && d.checked_by.trim());
  const confirmed = new Set();
  async function startPlan(sid, rep, sc, cand, which) {
    const key = `${sc.scope_id}/${cand.id}`;
    // 候補の口（記録つき。開くたびに取り直す）。読めなければ解説の候補のまま
    let cc = null;
    if (S.player) {
      try {
        const body = await api("GET", `/v1/players/${S.player.id}/plan-candidates?session=${sid}`);
        cc = (body.scopes || []).find((x) => x.scope_id === sc.scope_id) || null;
      } catch { /* 解説の候補のまま */ }
    }
    const cx = cc ? (cc.candidates.candidates || []).find((x) => x.id === cand.id) : null;
    if (cx && cx.history && cx.history.blocked && !confirmed.has(key + "/blocked")) {
      if (!(await App.ask({ title: "前に詰まった直しです", text: "この直しは、前のプランで「詰まった」ものです（計測器の数字で見える範囲ではここまで、としました）。それでももう一度始めますか？", ok: "もう一度始める", cancel: "やめる" }))) return;
      confirmed.add(key + "/blocked");
    }
    if ((cand.blocked_by || []).length && !confirmed.has(key)) {
      const g0 = sc.gist && sc.gist.candidates && sc.candidates && sc.gist.candidates[sc.candidates.now];
      const firstT = (g0 && g0.title) || (claimOf(sc, "next.now") || "").replace(/^いま:\s*/, "").split("。")[0] || "ほかの候補";
      if (!(await App.ask({ title: "先にする候補があります", text: `この候補は「${firstT}」が先、としたものです（理由は要点のとおり）。先にこちらを始めると、結果を読み分けにくくなります。それでも始めますか？`, ok: "それでも始める", cancel: "やめる" }))) return;
      confirmed.add(key);
    }
    const gc = sc.gist && sc.gist.candidates && sc.gist.candidates[cand.id];
    const what = (gc && gc.title) || (claimOf(sc, `next.${which}`) || "").replace(/^(いま|次):\s*/, "").split("。")[0];
    const d = cand.design || {};
    const sh = App.openSheet({ title: "このプランで始める", label: "plan", size: "full", html: `
      <p class="t-headline" data-ps="what">${esc(what || cand.id)}</p>
      ${gc && gc.why ? `<p class="sub">${esc(gc.why)}</p>` : ""}
      <p class="caption">${esc(scopeName(sc))}${cand.club && cand.club.club ? `・${esc(App.clubJa(cand.club.club))}で打つ` : ""}<span data-ps="total"></span></p>
      <div data-ps="drills" class="sub">ドリル集を読み込み中…</div>
      <label class="field"><span>本番で意識する1点</span><textarea class="plain" data-ps="cue" rows="3" maxlength="120" placeholder="例: 向こうにボールがあるつもりで打つ"></textarea></label>
      <div class="note caption">1回の練習は「いつも通り → 本番 → 本番 → いつも通り」の順に打ちます。別の日にもう一回同じ結果が出たら「効いた」と言います。飛ぶ距離か振りの速さが続けて落ちたら知らせます。</div>
      <div data-ps="advance"></div>
      <div class="block"><button class="btn primary block" data-ps="go">プランを作る</button><div data-ps="err"></div></div>` });
    const q = (k) => $(`[data-ps=${k}]`, sh.el);
    // 次に進む条件（定型文。数字はここ＝シートの中で出してよい層）
    const adv = fillBand(claimOf(sc, `${which}.advance`), sc, rep.handedness === "L" ? "L" : "R");
    if (adv) q("advance").innerHTML = `<p class="label" style="margin:var(--s3) 0 0">次に進む条件</p><p class="caption" style="margin-top:var(--s1)">${esc(App.textJa(adv.replace(/^次に進む条件:\s*/, "")))}</p>`;
    // 意識する1点の初期値: 要点が「意識する一点」と言った文（数字・専門用語の検査を通ったもの）。直せる
    const g = sc.gist || {};
    if (g.focus && g.focus.candidate_id === cand.id) {
      const act = (g.blocks || []).find((b) => b.id === "action");
      const ln = act && (act.lines || []).find((x) => /^意識する一点: /.test(x.text || ""));
      if (ln) q("cue").value = ln.text.replace(/^意識する一点: /, "").replace(/^「(.*)」$/, "$1").replace(/。$/, "");
    }
    let list;
    if (cx && cx.drills) list = (cx.drills.drills || []).filter(checked);
    else {
      let cat = null;
      try { cat = await api("GET", `/v1/drills?handedness=${rep.handedness === "L" ? "L" : "R"}`); } catch { cat = App.LS.get(`golf.drills.${rep.handedness === "L" ? "L" : "R"}`); }
      const names = new Set(cand.issue_names || (cand.issues || []).map((i) => i.issue));
      list = ((cat && cat.drills) || []).filter((x) => checked(x) && (x.issues || []).some((i) => names.has(i)) && (!(x.levers || []).length || x.levers.includes(cand.lever))
        && (!(x.categories || []).length || !sc.category || x.categories.includes(sc.category)));
    }
    if (sh.closed) return;
    const tp = d.template || [];
    const tot = tp.filter((x) => list.length || x.kind !== "drill").reduce((a, x) => a + (x.n || 0), 0);
    if (tot) q("total").textContent = `・1回 約${tot}球${list.length ? "（ドリルを使うとき）" : ""}`;
    const box = q("drills");
    if (!list.length) box.innerHTML = `<span data-t="nodrill">確かめ済みのドリルはまだありません。本番で意識する1点を自分で書いてください。</span>`;
    else {
      const REC = { worked_before: ["前回は効いた", "good"], not_worked_before: ["前回は効かなかった", "none"] };
      box.innerHTML = `<fieldset style="border:0;padding:0;margin:0"><legend class="label">ドリル（人が向きと安全を確かめたもの）</legend>` + list.map((x, i) => {
        const rc = REC[x.record_note];
        const when = x.record && x.record.last_date ? `（${esc(x.record.last_date)}）` : "";
        return `<label class="radio drl"><input type="radio" name="psdrill" value="${esc(x.id)}" ${i === 0 ? "checked" : ""}> <span>${esc(x.title)}${rc ? ` <span class="chip ${rc[1]}">${rc[0]}${when}</span>` : ""}${x.coach_reviewed ? "" : ` <span class="chip none">コーチ未確認</span>`}</span></label>`;
      }).join("") + `<label class="radio drl"><input type="radio" name="psdrill" value=""> <span>使わない（意識する1点を自分で書く）</span></label></fieldset>`;
    }
    q("go").addEventListener("click", () => createPlan(sid, sc, cand, sh, false));
  }

  async function createPlan(sid, sc, cand, sh, replace) {
    const q = (k) => $(`[data-ps=${k}]`, sh.el);
    q("err").innerHTML = "";
    const drill = (sh.el.querySelector("input[name=psdrill]:checked") || {}).value || "";
    const cue = q("cue").value.trim();
    if (!drill && !cue) { q("err").innerHTML = `<p class="fielderr">本番で意識する1点を書いてください</p>`; q("cue").focus(); return; }
    const w = sc && sc.band_shape && sc.band_shape.ok ? sc.band_shape.window : null;
    const body = { from_session: sid, scope_id: sc.scope_id, candidate_id: cand.id, drill_id: drill, cue, window: w || null };
    const go = q("go");
    go.disabled = true; go.textContent = "作っています…";
    const r = await App.request("POST", `/v1/players/${S.player.id}/plans${replace ? "?replace=1" : ""}`, { body }).catch((e) => ({ ok: false, status: 0, body: { error: String(e) } }));
    go.disabled = false; go.textContent = "プランを作る";
    if (r.status === 409 && !replace) {
      if (await App.ask({ title: "動いているプランがもう1つあります", text: "動かせるのは1つだけです。前のプランを「替えた」にして、こちらに切り替えますか？", ok: "切り替える", cancel: "やめる" })) return createPlan(sid, sc, cand, sh, true);
      return;
    }
    if (!r.ok) {
      q("err").innerHTML = r.status === 0 ? App.errOf({ offline: true }) : App.errorHtml({ what: "プランを作れませんでした", next: "内容を確かめて、もう一度押してください。", detail: (r.body && r.body.error) || r.text });
      return;
    }
    sh.close("done");
    App.toast("プランを作りました。練習の画面に組み方が出ます。");
    App.go("/practice");
  }

  App.route("/session/:id", renderD, { tab: "record" });
  App.route("/session/:id/detail", renderDetail, { tab: "record", wide: true });
  App.route("/session/:id/shots", renderShots, { tab: "record", wide: true });
  App.route("/session/:id/experiments", renderExperiments, { tab: "record" });
  return { startPlan, openShot, claimOf, fillBand, scopeName };
})();
