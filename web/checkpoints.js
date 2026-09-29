"use strict";
/*
  チェック一覧（C）・項目1つ（C-1）・撮り方ガイド（R0）。docs/DESIGN_v2.md §10 C・C-1・R0、§5.5、§11.1。
  - 最初の面は言葉と印だけ（数字・角度・ページは「なぜそう言える？」の中。R1）。件数・P の番号はラベル（data-label）。
  - 状態は3つ（範囲の中 ✓・範囲の外 △・判断できない ―）＋「まずここ」×（赤は1つだけ）。形・色・文字で分け、色だけに頼らない。
  - 札（字）: 測れた／測れた（目安）／測れた（あなたが示した点から）／見た目／判断できない。
  - 範囲の中・判断できない・参考・フォロー（任意）は畳む。判断できない理由は束ねて1行、どう撮れば見られるかを添える。
  - 判定の文は全部サーバーの定型文（カタログ）。ここで事実の文を作らない。
  - 理想の帯と線（段3）はまだ描かない。
*/
const Checks = (() => {
  const { $, $$, esc, icon, lab, S, api, LS } = App;

  const STATE = {
    in_range: { cls: "good", mark: "✓", text: "ガイドの範囲の中" },
    out_range: { cls: "warn", mark: "△", text: "範囲の外" },
    focus: { cls: "bad", mark: "×", text: "まずここ" },
    unknown: { cls: "none", mark: "―", text: "判断できない" },
    reference: { cls: "none", mark: "", text: "参考" },
  };
  const BASIS = { measured: "測れた", measured_approx: "測れた（目安）", measured_tap: "測れた（あなたが示した点から）", visual: "見た目", conflict: "", none: "" };
  const GROUP_ICON = { setup: "info", power: "power", tempo: "play", path: "layers" };
  const VIEW = { dtl: "後ろから", fo: "正面から" };
  const pLabel = (p) => String(p || "").split("-")[0].replace("_5", ".5");

  const cache = {};
  async function load(sid, fresh = false) {
    if (!fresh && cache[sid]) return cache[sid];
    const r = await api("GET", `/v1/sessions/${sid}/checks`);
    cache[sid] = r;
    return r;
  }
  function invalidate(sid) { delete cache[sid]; }

  function thumbOf(data, it) {
    const has = (sid, p) => (data.swings || []).some((s) => s.id === sid && (s.frames || []).some((f) => f.checkpoint === p && f.has_thumb));
    const f = (it.frames || []).find((x) => has(x.swing_id, x.p));
    return f ? `/v1/swings/${f.swing_id}/thumbs/${encodeURIComponent(f.p)}` : "";
  }

  function stateChip(it) {
    const s = it.focus ? STATE.focus : STATE[it.state] || STATE.unknown;
    const why = it.state === "unknown" && it.reason_text ? `（${esc(it.reason_text)}）` : "";
    return `<span class="chip ${s.cls} ${it.focus ? "strong" : ""} ${it.state === "unknown" ? "dashed" : ""}">${s.mark ? `<span aria-hidden="true">${s.mark}</span>` : ""}${esc(s.text)}${why}</span>`;
  }
  const basisText = (it) => (it.state === "in_range" || it.state === "out_range") ? BASIS[it.basis] || "" : "";

  function badge(it) {
    const gi = GROUP_ICON[it.group];
    if (it.group === "setup" || it.group === "power" || it.group === "tempo" || !/^P\d/.test(it.p || "")) return `<span class="pbadge icon">${icon(gi || "info")}</span>`;
    return `<span class="pbadge">${lab("p", pLabel(it.p))}</span>`;
  }

  function rowHtml(sid, it) {
    const sub = it.state === "out_range" ? it.fault_label || it.look_at : it.state === "in_range" ? it.ok_text : it.look_at;
    const also = (it.also || []).length && it.state === "out_range" ? `<span class="caption">（${it.also.map((a) => esc(a.title)).join("・")}）</span>` : "";
    const one = it.single && (it.state === "in_range" || it.state === "out_range") ? `<span class="caption">一本だけの見立て</span>` : "";
    return `<li><a class="cprow" href="#/session/${sid}/check/${encodeURIComponent(it.id)}" data-item="${esc(it.id)}" data-state="${esc(it.focus ? "focus" : it.state)}">
      ${badge(it)}<span class="grow1"><span class="cpt">${esc(it.title)}</span><small>${esc(sub)}</small>
      <span class="cpstate">${stateChip(it)} <span class="caption">${esc(basisText(it))}</span> ${one}${also}</span></span>${icon("chevron-right", "chev")}</a></li>`;
  }

  function bigCard(sid, data, it, kind) {
    const src = thumbOf(data, it);
    return `<a class="cpcard ${kind}" href="#/session/${sid}/check/${encodeURIComponent(it.id)}" data-card="${kind}" data-item="${esc(it.id)}">
      ${src ? `<img src="${esc(src)}" alt="${esc(pLabel(it.p))} のあなたのコマ" loading="lazy">` : `<span class="noimg" aria-hidden="true">${icon("video")}</span>`}
      <span class="grow1"><span class="cph">${/^P\d/.test(it.p || "") ? lab("p", pLabel(it.p)) + " " : ""}${esc(it.title)}</span>
        <span class="cpf">${esc(it.fault_label || it.look_at)}</span>
        <span class="cpstate">${kind === "focus" ? "" : stateChip(it)} <span class="caption">${esc(basisText(it))}</span></span></span>${icon("chevron-right", "chev")}</a>`;
  }

  function fold(title, items, sid, attrs = "", lead = "") {
    if (!items.length) return "";
    return `<details class="folded cpfold" ${attrs}><summary>${title}</summary>${lead}<ul class="navlist cplist">${items.map((it) => rowHtml(sid, it)).join("")}</ul></details>`;
  }

  // 項目の畳み（項目1つの画面の「前の項目／次の項目」は、同じ畳みの中だけを回る）
  function groupOf(it) {
    if (it.optional) return "opt";
    if (it.derived) return "power";
    if (it.state === "out_range") return "out";
    if (it.state === "in_range") return "in";
    if (it.state === "reference") return "ref";
    return "unk";
  }
  const GROUP_WORD = { out: "範囲の外", in: "範囲の中", unk: "判断できない", ref: "参考", opt: "フォロー（任意）", power: "飛ぶ力" };
  // 判断できない理由ごとの「こうすれば見られる」（最初の面は言葉だけ）
  const HOWTO = {
    view_dtl: "後ろから撮ると見られます", view_fo: "正面から撮ると見られます", view_mismatch: "撮った向きを直すと見られます",
    no_tap: "このコマでクラブの握りの端と先を押すと見られます", no_frame: "このコマを選ぶと見られます", no_ball: "構えのコマでボールの両端を押すと見られます",
    no_head_width: "クラブの先の両端を押すと見られます", camera: "撮り方ガイドの置き方で撮ると見られます", camera_unknown: "構えのコマを選ぶと、撮り方を確かめて見られます",
    fps: "スローモーションで撮ると見られます", fps_unknown: "スローモーションで撮ると見られます", low_visibility: "体全体が明るく写るように撮ると見られます",
    club_short: "この向きではクラブが短く写り、向きを測れません", border: "範囲の境目なので、どちらとも言えません。もう何本か選ぶと決まることがあります",
    split: "スイングによって分かれています。もう何本か選ぶと決まることがあります", vision_pending: "見た目の評価（これから）で見られます",
    scale: "ボールの両端を押し直すと見られます", unclear: "ガイドの読み方を確かめているところです", ref_club: "この番手の基準はガイドにありません",
  };
  const howto = (it) => it.needs_note && it.reason === "not_in_2d" ? `見るには、${it.needs_note}が要ります` : HOWTO[it.reason] || "";

  function pStrip(data) {
    const sw = (data.swings || []).find((s) => (s.frames || []).length) || null;
    const ps = ["P1", "P2", "P3", "P4", "P5", "P6", "P7"];
    return `<div class="pstrip" data-pstrip role="list" aria-label="選んだコマ">${ps.map((p) => {
      const f = sw && (sw.frames || []).find((x) => x.checkpoint === p);
      const miss = sw && (sw.missing || []).includes(p);
      return `<div class="pcell ${f ? "" : "miss"}" role="listitem" data-pchip="${p}">${f && f.has_thumb ? `<img src="/v1/swings/${sw.id}/thumbs/${p}" alt="" loading="lazy">` : `<span class="noimg" aria-hidden="true">${miss ? "―" : ""}</span>`}${lab("p", p)}</div>`;
    }).join("")}</div>`;
  }

  // ---- C チェック一覧 ----
  async function renderC({ el, params, alive }) {
    const sid = Number(params.id);
    el.innerHTML = `<div class="pagehead"><a class="iconbtn" href="#/session/${sid}" aria-label="診断へ戻る">${icon("chevron-left")}</a><h1>チェック</h1></div><div data-body></div>`;
    const body = $("[data-body]", el);
    const stop = App.loading(body);
    let data, se;
    try { [data, se] = await Promise.all([load(sid, true), api("GET", `/v1/sessions/${sid}`)]); } finally { stop(); }
    if (!alive()) return;
    const c = data.checks;
    if (!c) {
      body.innerHTML = `<div class="empty">${icon("video", "art")}<p class="t-headline">後ろか正面から一本撮ると、P1〜P7 をガイドの範囲と比べます。</p></div>
        <a class="btn primary block" data-primary href="#/video/${esc(se.date)}?session=${sid}">動画を選ぶ</a>
        <ul class="navlist block">${App.navItem("#/guide/dtl", "撮り方ガイド", "カメラの置き方")}</ul>`;
      return;
    }
    const items = c.items || [];
    const main = items.filter((x) => !x.optional && !x.derived);
    const focus = items.find((x) => x.focus), next = items.find((x) => x.next);
    const outs = main.filter((x) => x.state === "out_range" && !x.focus && !x.next);
    const ins = main.filter((x) => x.state === "in_range");
    const unk = main.filter((x) => x.state === "unknown");
    const refs = main.filter((x) => x.state === "reference");
    const dist = items.filter((x) => !x.optional && (x.tags || []).includes("distance"));
    const opt = items.filter((x) => x.optional);
    const sw = data.swings || [];
    const judgedSw = sw.filter((s) => (s.frames || []).length);
    const views = [...new Set(judgedSw.map((s) => s.view))];
    const clubs = [...new Set(judgedSw.map((s) => s.club).filter(Boolean))];
    const reasons = {};
    for (const x of unk) reasons[x.reason_text || x.reason] = (reasons[x.reason_text || x.reason] || 0) + 1;
    const rs = Object.entries(reasons).sort((a, b) => b[1] - a[1]);
    const cams = sw.map((s) => s.measure && s.measure.camera).filter((m) => m && m.ok === false);
    // 見出しは言葉で（件数・内訳は畳みの中。判断できない数を一番大きく見せない）
    const outN = c.counts.out_range;
    const summary = focus ? "気になる所が一つあります" : outN ? "範囲の外の所があります（まだ課題は決めていません）" : c.counts.judged ? "いまは大きな外れはありません" : "まだ判断できた所がありません";
    let h = `<p class="sub" data-cond>${lab("date", App.dateJa(se.date, false))}・${views.map((v) => esc(VIEW[v] || v)).join("と")}${clubs.length ? "・" + clubs.map((x) => lab("club", App.clubJa(x))).join("・") : ""}・スイング${lab("count", judgedSw.length + "本")}</p>
      <p class="t-headline" data-summary>${esc(summary)}</p>
      <details class="folded" data-counts><summary class="textbtn">件数を見る</summary><p class="caption">見られた${lab("count", c.counts.judged + "件")}のうち、範囲の外${lab("count", outN + "件")}。判断できない${lab("count", c.counts.unknown + "件")}（${rs.slice(0, 4).map(([t, n]) => `${esc(t)} ${lab("count", n + "件")}`).join("・")}）。</p></details>`;
    if (c.unchecked) h += `<p class="caption" data-unchecked>基準の読み取りは、まだ人がガイドと突き合わせていません。</p>`;
    if (cams.length) {
      const hints = [...new Set(cams.flatMap((m) => (m.checks || []).filter((x) => !x.ok).map((x) => x.hint)))];
      h += `<div class="note warn" data-camera>撮り方がガイドの置き方から外れています（${hints.map(esc).join("・")}）。傾きの項目は判断できないにしています。<a href="#/guide/${esc(views[0] || "dtl")}">撮り方ガイド</a></div>`;
    }
    // 向き（構えの形と、選んだ向きの食い違い。§6.1）: そのスイングの向きのある項目は判断できないにしてある。直すか消すかを選べる
    const vmis = judgedSw.filter((s) => s.measure && s.measure.view_check && s.measure.view_check.ok === false);
    if (vmis.length) {
      h += `<div class="note warn" data-viewcheck><p style="margin:0">構えの形から見ると、選んだ向きと違う向きで撮った動画かもしれません。向きが違うと項目がずれるので、そのスイングの項目は判断できないにしています。</p>
        ${vmis.map((s) => { const g = s.measure.view_check.guess; const n = judgedSw.indexOf(s) + 1;
          return `<div class="stack" style="margin-top:var(--s2)" data-vm="${s.id}"><p style="margin:0"><b>スイング${lab("count", n + "本")}目</b>（${esc(VIEW[s.view] || s.view)}として選んだもの）</p>
          ${g && g !== s.view ? `<button type="button" class="btn primary block" data-fix-view="${s.id}" data-to="${esc(g)}">向きを「${esc(VIEW[g])}」に直す</button>` : ""}
          <button type="button" class="btn block danger" data-del-swing="${s.id}">このスイングを消す</button></div>`; }).join("")}</div>`;
    }
    if (judgedSw.some((s) => s.fps > 0 && s.fps < 50)) h += `<div class="note" data-fps>コマの少ない動画です。下ろしから当たる瞬間のクラブの項目は判断できません（スローモーションで撮ると見られます）。</div>`;
    else if (judgedSw.some((s) => !s.fps)) h += `<div class="note" data-fps>コマの速さが分からない動画です。下ろしから当たる瞬間のクラブの項目は判断できないにしています。</div>`;
    h += pStrip(data);
    if (focus) {
      h += `<section class="block" aria-labelledby="h-first"><h2 id="h-first" class="label">まずここ</h2>${bigCard(sid, data, focus, "focus")}</section>`;
      if (next) h += `<section class="block" aria-labelledby="h-next"><h2 id="h-next" class="label">次に見る</h2>${bigCard(sid, data, next, "next")}</section>`;
    } else {
      const singles = main.some((x) => x.single && x.state === "out_range");
      const byView = {};
      for (const s of judgedSw) byView[s.view] = (byView[s.view] || 0) + 1;
      const most = Math.max(0, ...Object.values(byView));
      const need = Math.max(0, 3 - most);
      const word = ["", "一本", "二本"][need] || "";
      h += `<div class="note" data-nofocus><p style="margin:0">${need ? `範囲の外の項目は、まだ${singles ? "一本だけの見立て" : "本数が足りない見立て"}なので課題にしていません。同じ向きで、もう${word}選ぶと、課題を一つ選べます。` : "いま課題にできる項目はありません。"}</p>
        ${need ? `<a class="btn primary block" style="margin-top:var(--s2)" data-add-swing href="#/video/${esc(se.date)}?session=${sid}">${icon("video")}あと${word}選ぶ</a>` : ""}</div>`;
    }
    const unkLead = rs.length ? `<p class="caption" data-unk-why>${rs.map(([t, n]) => `${esc(t)} ${lab("count", n + "件")}`).join("・")}</p>` : "";
    h += `<div class="block">
      ${fold(`ほかに範囲の外 ${lab("count", outs.length + "件")}`, outs, sid, "data-fold=out")}
      ${fold(`飛ぶ力（見られた ${lab("count", dist.filter((x) => x.state === "in_range" || x.state === "out_range").length + "件")}／判断できない ${lab("count", dist.filter((x) => x.state === "unknown").length + "件")}）`, dist, sid, "data-fold=power")}
      ${fold(`範囲の中 ${lab("count", ins.length + "件")}`, ins, sid, "data-fold=in")}
      ${fold(`判断できない ${lab("count", unk.length + "件")}`, unk, sid, "data-fold=unknown", unkLead)}
      ${fold(`参考 ${lab("count", refs.length + "件")}`, refs, sid, "data-fold=ref")}
      ${fold(`フォロー（任意）${lab("count", opt.length + "件")}`, opt, sid, "data-fold=opt")}</div>
      ${focus ? `<div class="stack block"><a class="btn block" data-more-swing href="#/video/${esc(se.date)}?session=${sid}">${icon("video")}もう一本選ぶ</a></div>` : ""}
      <ul class="navlist block">${App.navItem(`#/guide/${esc(views[0] || "dtl")}`, "撮り方ガイド", "カメラの置き方")}</ul>`;
    body.innerHTML = h;
    body.addEventListener("click", async (ev) => {
      const fx = ev.target.closest("[data-fix-view]");
      const del = ev.target.closest("[data-del-swing]");
      if (!fx && !del) return;
      const id = Number((fx || del).dataset.fixView || (fx || del).dataset.delSwing);
      try {
        if (fx) {
          await api("PATCH", `/v1/swings/${id}`, { view: fx.dataset.to });
          App.toast("向きを直して、測り直しました");
        } else {
          const ok = await App.ask({ title: "このスイングを消しますか", text: "このスイングの判定は、課題の選び方に数えなくなります。", ok: "消す", cancel: "やめる", danger: true });
          if (!ok) return;
          await api("DELETE", `/v1/swings/${id}`);
          App.toast("スイングを消しました");
        }
        invalidate(sid);
        App.invalidate(sid);
        App.render();
      } catch (e) {
        (fx || del).insertAdjacentHTML("afterend", App.errOf(e, { what: fx ? "向きを直せませんでした" : "消せませんでした", saved: "スイングは前のままです。" }));
      }
    });
  }

  // ---- C-1 項目1つ ----
  async function renderItem({ el, params, alive }) {
    const sid = Number(params.id), iid = params.item;
    el.innerHTML = `<div class="pagehead"><a class="iconbtn" href="#/session/${sid}/check" aria-label="チェックへ戻る">${icon("chevron-left")}</a><span class="grow1"></span></div><div data-body></div>`;
    const body = $("[data-body]", el);
    const stop = App.loading(body);
    let data;
    try { data = await load(sid); } finally { stop(); }
    if (!alive()) return;
    const items = (data.checks && data.checks.items) || [];
    const i = items.findIndex((x) => x.id === iid);
    if (i < 0) { body.innerHTML = App.errorHtml({ what: "この項目は見つかりませんでした", next: "チェックの一覧から開き直してください。" }); return; }
    const it = items[i];
    // 前後は同じ畳みの中だけを回る（範囲の外を見ているときに、判断できない項目へ飛ばない）
    const g = groupOf(it), same = items.filter((x) => groupOf(x) === g), k = same.indexOf(it);
    const prev = same[k - 1], nxt = same[k + 1];
    const src = thumbOf(data, it);
    const unknown = it.state === "unknown";
    const say = it.state === "out_range" ? it.fault_label : it.state === "in_range" ? it.ok_text : unknown ? `判断できません（${it.reason_text || ""}）` : it.look_at;
    const how = unknown ? howto(it) : "";
    body.innerHTML = `
      ${/^P\d/.test(it.p || "") ? `<p class="label" data-pname>${lab("p", pLabel(it.p))}</p>` : ""}
      <h1 data-title>${esc(it.title)}</h1>
      <figure class="cpfig">${src ? `<img src="${esc(src)}" alt="${esc(say)}">` : `<div class="noimg" role="img" aria-label="コマの写真はありません">${icon("video")}</div>`}</figure>
      <p class="cpstate">${stateChip(it)} <span class="caption">${esc(basisText(it))}</span></p>
      <p class="t-headline" data-say>${esc(say)}</p>
      ${how ? `<p data-howto>${esc(how)}</p>` : ""}
      <p class="sub">${esc(it.look_at)}</p>
      <div class="cprange" data-range><span class="label">目安（こうなら範囲の中）</span>${esc(it.ok_text)}</div>
      <button type="button" class="textbtn" data-why>${icon("info")}なぜそう言える？</button>
      <nav class="itemnav" aria-label="${esc(GROUP_WORD[g])}の項目">
        ${prev ? `<a class="btn" data-prev href="#/session/${sid}/check/${encodeURIComponent(prev.id)}">${icon("chevron-left")}前の項目</a>` : "<span></span>"}
        ${nxt ? `<a class="btn" data-next href="#/session/${sid}/check/${encodeURIComponent(nxt.id)}">次の項目${icon("chevron-right")}</a>` : "<span></span>"}</nav>
      ${prev || nxt ? `<p class="caption" style="text-align:center">${esc(GROUP_WORD[g])}の項目だけを順に見ます（${lab("count", (k + 1) + " / " + same.length)}）</p>` : ""}`;
    $("[data-why]", body).addEventListener("click", () => openWhy(data, it));
  }

  const num = (v, unit) => {
    if (v === null || v === undefined) return "—";
    const u = { deg: "°", ball: "個（ボール）", head: "個（クラブの先）", ratio: "", label: "" }[unit] || "";
    return `${Math.round(v * 100) / 100}${u}`;
  };
  function rangeText(r) {
    if (!r || r.lo === undefined) return "—";
    const u = r.unit;
    if (u === "ratio" && r.lo === 0 && r.hi === 1) return "決まった二つの線のあいだ（0〜1 に読み替えた位置）";
    if (r.side === "lo_only") return `${num(r.lo, u)} 以上`;
    if (r.side === "hi_only") return `${num(r.hi, u)} 以下`;
    return `${num(r.lo, u)}〜${num(r.hi, u)}`;
  }
  function valueHtml(v) {
    if (!v) return "<li>値はありません</li>";
    if (v.parts) return v.parts.map((p, i) => `<li>部分 ${i + 1}: あなた ${num(p.v, p.unit)}（誤差 ±${num(p.err, p.unit)}）／範囲 ${rangeText(p)}</li>`).join("");
    return `<li>範囲: ${rangeText(v)}</li><li>あなた: ${num(v.v, v.unit)}（誤差 ±${num(v.err, v.unit)}・初期の値で、まだ実測していません）${v.edge_ball !== undefined ? `／範囲の端からボール ${v.edge_ball} 個` : ""}</li>`;
  }

  function openWhy(data, it) {
    const c = data.checks || {};
    const g = it.guide || {};
    const rows = [];
    rows.push(`<li><b>基準</b>: PGA スイングガイド p${esc(g.pages)}（${esc(g.section)}${g.page_exact ? "" : "。ページはノートの節の範囲"}）</li>`);
    if (it.focus && c.rationale) rows.push(`<li><b>先に直す理由</b>: ${esc(c.rationale)}</li>`);
    if (it.next && c.next_why) rows.push(`<li><b>次に見る理由</b>: ${esc(c.next_why)}</li>`);
    if (it.state === "in_range" || it.state === "out_range") rows.push(`<li><b>何本から</b>: ${it.n_judged}本中${it.n_out}本が範囲の外${it.single ? "（一本だけの見立てなので、課題にはしていません）" : ""}</li>`);
    if (it.state === "unknown") rows.push(`<li><b>判断できない理由</b>: ${esc(it.reason_text)}${it.detail ? `（${esc(it.detail)}）` : ""}${it.needs_note ? `。見るには: ${esc(it.needs_note)}` : ""}</li>`);
    if (it.tight) rows.push(`<li><b>この項目の見え方</b>: 範囲の幅が、いまの誤差の見積もり（初期の値）と同じくらいなので、「範囲の中」と言えることはほとんどありません。外れていることは言えます。誤差を実測して決め直すまで、このままです</li>`);
    if ((it.also || []).length) rows.push(`<li><b>ガイドの別の節でも</b>: ${it.also.map((a) => esc(a.title)).join("・")}（同じ外れを一回だけ数えています）</li>`);
    if (it.state === "reference") rows.push(`<li><b>参考</b>: 合否は出していません${it.note ? `。${esc(it.note)}` : ""}</li>`);
    if (BASIS[it.basis]) rows.push(`<li><b>根拠</b>: ${esc(BASIS[it.basis])}${it.proxy ? `（代わりの点: ${esc(it.proxy)}）` : ""}</li>`);
    if (it.conflict) rows.push(`<li><b>食い違い</b>: 見た目の答えと向きが逆でした。あなたが押した点を採っています</li>`);
    if (it.note && it.state !== "reference") rows.push(`<li><b>読み方</b>: ${esc(it.note)}</li>`);
    if (!it.checked_by) rows.push(`<li><b>未確認の項目</b>: この項目の基準の読み取りは、まだ人がガイドと突き合わせていません</li>`);
    if (c.neutral_note) rows.push(`<li>${esc(c.neutral_note)}</li>`);
    const vals = (it.values || []).filter((x) => x.value).map((x, i) => `<li>スイング ${i + 1}: ${esc(STATE[x.state] ? STATE[x.state].text : x.state)}・${num(x.value.v, x.value.unit)}</li>`).join("");
    const sh = App.openSheet({ title: "なぜそう言える？", size: "half", label: "why", html: `<ul class="parts">${rows.join("")}</ul>
      <details class="folded" data-numbers><summary>数字を見る</summary><ul class="parts">${valueHtml(it.value)}${vals}
      <li>画面の上で測った傾きと位置です（体の三次元の角度ではありません）</li>
      <li class="caption">カタログ ${esc(c.catalog_version)}・判定 ${esc(c.judge_version)}</li></ul></details>` });
    return sh;
  }

  // ---- R0 撮り方ガイド ----
  function guideArt(view) {
    // 上から見た置き方（自前の図。ガイドの画像は使わない）
    const top = view === "dtl"
      ? `<svg viewBox="0 0 320 170" class="gart" role="img" aria-labelledby="gt1"><title id="gt1">上から見た置き方（後ろから）: カメラはあなたの真後ろ、手元の線の延長に置く</title>
          <g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><line x1="20" y1="60" x2="300" y2="60" stroke-dasharray="6 6"/><rect x="140" y="80" width="40" height="24" rx="10"/><circle cx="160" cy="48" r="6" fill="currentColor"/><line x1="80" y1="92" x2="130" y2="92"/></g>
          <g fill="currentColor"><rect x="36" y="80" width="26" height="24" rx="4"/></g><text x="30" y="130" class="gl">カメラ</text><text x="136" y="130" class="gl">あなた</text><text x="220" y="52" class="gl">目標の線</text></svg>`
      : `<svg viewBox="0 0 320 170" class="gart" role="img" aria-labelledby="gt1"><title id="gt1">上から見た置き方（正面から）: カメラはあなたの正面、スタンスの真ん中に向ける</title>
          <g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><line x1="20" y1="50" x2="300" y2="50" stroke-dasharray="6 6"/><rect x="140" y="60" width="40" height="24" rx="10"/><circle cx="160" cy="42" r="6" fill="currentColor"/><line x1="160" y1="96" x2="160" y2="130"/></g>
          <g fill="currentColor"><rect x="147" y="134" width="26" height="24" rx="4"/></g><text x="186" y="152" class="gl">カメラ</text><text x="186" y="76" class="gl">あなた</text></svg>`;
    const grid = `<svg viewBox="0 0 320 180" class="gart" role="img" aria-labelledby="gt2"><title id="gt2">画面の格子: 横の真ん中の線を腰骨の高さ、縦の真ん中の線を${view === "dtl" ? "握りの端と手の真ん中のあいだ" : "スタンスの真ん中"}に合わせる</title>
        <rect x="4" y="4" width="312" height="172" rx="8" fill="none" stroke="currentColor" stroke-width="2"/>
        <g stroke="currentColor" stroke-width="1" stroke-dasharray="4 4" opacity=".6"><line x1="108" y1="4" x2="108" y2="176"/><line x1="212" y1="4" x2="212" y2="176"/><line x1="4" y1="61" x2="316" y2="61"/><line x1="4" y1="119" x2="316" y2="119"/></g>
        <g stroke="currentColor" stroke-width="3"><line x1="160" y1="4" x2="160" y2="176"/><line x1="4" y1="90" x2="316" y2="90"/></g>
        <text x="166" y="20" class="gl">縦の真ん中</text><text x="10" y="84" class="gl">横の真ん中＝腰骨</text></svg>`;
    return top + grid;
  }
  async function renderGuide({ el, params, alive }) {
    const view = params.view === "fo" ? "fo" : "dtl";
    el.innerHTML = `<div class="pagehead"><a class="iconbtn" data-gback href="#/record" aria-label="戻る">${icon("chevron-left")}</a><h1>撮り方ガイド</h1></div>
      <div class="seg" role="group" aria-label="向き"><a href="#/guide/dtl" ${view === "dtl" ? `aria-current="page"` : ""}>後ろから</a><a href="#/guide/fo" ${view === "fo" ? `aria-current="page"` : ""}>正面から</a></div>
      <div class="block">${guideArt(view)}</div>
      <section class="card block"><h2>置き方</h2><ul class="plain">
        ${view === "dtl" ? `<li>カメラはあなたの真後ろ。左右に斜めにしない</li><li>縦の真ん中の線を、握りの端と手の真ん中のあいだに（ボールと体の真ん中に合わせない）</li>`
          : `<li>カメラはあなたの正面。見上げたり見下ろしたりしない（レンズはお腹の高さ）</li><li>縦の真ん中の線を、スタンスの真ん中に</li>`}
        <li>横の真ん中の線を、腰骨の高さに（格子の表示を入れる）</li>
        <li>三脚を使い、スマホは横向き。レンズは一倍（広角は端がゆがむ）</li>
        <li>頭の上からクラブの先まで全部入れる。ボールの横に、目標の線と平行な棒を置く</li>
        <li>できればスローモーション（一秒に多くのコマ）で。コマが少ないと、下ろしから当たる瞬間のクラブが見えません</li></ul></section>
      <section class="card block"><h2>試し撮りの確かめ方</h2>
        <p>一本目の構えのコマで、腰の高さと手元（正面ならスタンスの真ん中）が画面の真ん中の線から離れていないかを自動で確かめます。外れていれば、チェックの画面に「カメラをもう少し右へ」のように直す向きが出ます。外れたままだと、傾きの項目は判断できないにします。</p></section>
      <p class="sub block" data-other></p>`;
    // 戻るは来た画面へ（動画の段から開いたら、動画の段へ）。履歴が無いときだけ記録の画面へ
    $("[data-gback]", el).addEventListener("click", (ev) => {
      if (App.cameInApp()) { ev.preventDefault(); history.back(); }
    });
    try {
      const cat = await Video.catalog();
      if (!alive()) return;
      const other = view === "dtl" ? "fo" : "dtl";
      const n = (cat.items || []).filter((it) => it.view === other && !it.optional).length;
      $("[data-other]", el).innerHTML = `この向きでは、${esc(VIEW[other])}の項目 ${lab("count", n + "件")}は判断できません（${esc(VIEW[other])}撮ると見られます）。`;
    } catch { /* 圏外でも置き方は読める */ }
  }

  App.route("/session/:id/check", renderC, { tab: "record" });
  App.route("/session/:id/check/:item", renderItem, { tab: "record" });
  App.route("/guide/:view", renderGuide, { tab: "record" });
  return { load, invalidate, STATE };
})();
