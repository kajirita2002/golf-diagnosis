"use strict";
/*
  チェック一覧（C）・項目1つ（C-1）・撮り方ガイド（R0）。docs/DESIGN_v2.md §10 C・C-1・R0、§5.5、§11.1。
  - 最初の面は言葉と印だけ（数字・角度・ページは「なぜそう言える？」の中。R1）。件数・P の番号はラベル（data-label）。
  - 状態は3つ（範囲の中 ✓・範囲の外 △・判断できない ―）＋「まずここ」×（赤は1つだけ）。形・色・文字で分け、色だけに頼らない。
  - 札（字）: 測れた／測れた（目安）／測れた（あなたが示した点から）／見た目／判断できない。
  - 範囲の中・判断できない・参考・フォロー（任意）は畳む。判断できない理由は束ねて1行、どう撮れば見られるかを添える。
  - 判定の文は全部サーバーの定型文（カタログ）。ここで事実の文を作らない。
  - 理想との比較（段3。ideal.js）: 範囲の外の項目は、写真の上に範囲の面と「範囲に入れた目安」の線を重ね、［理想と比べる］（I）へ。
  - 見た目の項目（段2c）: ［見た目を評価する（料金）］で、端末に置いたコマの写真（長辺 1024px）を送り、AI（Claude）の答えで埋める。
    写真はサーバーにも残さない。鍵が無い・上限・失敗でも、測れる項目の一覧はそのまま出す（押せない理由を1行で出す）。
  - 球の課題とつながる候補（§9.1）は印だけ。「まだ確かめていません」まで言う。TrackMan の球とは本人が確かめて結ぶ。
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
  const GROUP_ICON = { setup: "info", power: "power", tempo: "clock", path: "layers" };
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

  // 参考の項目（テンポ）の言葉: 外れの言葉／目安の近く（幅が丸ごと近くにあるときだけ）／どちらとも言い切れない
  const refSay = (it) => (it.n_ref ? it.fault_label || (it.ref_near ? it.ok_text : "目安と比べて、どちらとも言い切れません") : "");
  const refFold = (it) => {
    if (!it.n_ref) return it.ref_reason === "fps" ? "コマの少ない動画では判断できません" : "まだ数えていません";
    const w = it.fault_label ? it.fault_label.replace(/です$/, "") : it.ref_near ? it.ok_text.replace(/です$/, "") : "どちらとも言い切れない";
    return it.single ? `${w}・一本だけの見立て` : w;
  };
  const isTempo = (it) => it.group === "tempo";

  function rowHtml(sid, it) {
    const sub = it.state === "out_range" ? it.fault_label || it.look_at : it.state === "in_range" ? it.ok_text
      : it.state === "reference" && it.n_ref ? refSay(it) : it.look_at;
    const also = (it.also || []).length && it.state === "out_range" ? `<span class="caption">（${it.also.map((a) => esc(a.title)).join("・")}）</span>` : "";
    const one = it.single && (it.state === "in_range" || it.state === "out_range" || (it.state === "reference" && it.n_ref)) ? `<span class="caption">一本だけの見立て</span>` : "";
    return `<li><a class="cprow" href="#/session/${sid}/check/${encodeURIComponent(it.id)}" data-item="${esc(it.id)}" data-state="${esc(it.focus ? "focus" : it.state)}">
      ${badge(it)}<span class="grow1"><span class="cpt">${esc(it.title)}</span><small>${esc(sub)}</small>
      <span class="cpstate">${stateChip(it)} <span class="caption">${esc(basisText(it))}</span> ${one}${also}${linkChip(it)}</span></span>${icon("chevron-right", "chev")}</a></li>`;
  }

  function bigCard(sid, data, it, kind) {
    const src = thumbOf(data, it);
    return `<a class="cpcard ${kind}" href="#/session/${sid}/check/${encodeURIComponent(it.id)}" data-card="${kind}" data-item="${esc(it.id)}">
      ${src ? `<img src="${esc(src)}" alt="${esc(pLabel(it.p))} のあなたのコマ" loading="lazy">` : `<span class="noimg" aria-hidden="true">${icon("video")}</span>`}
      <span class="grow1"><span class="cph">${/^P\d/.test(it.p || "") ? lab("p", pLabel(it.p)) + " " : ""}${esc(it.title)}</span>
        <span class="cpf">${esc(it.fault_label || it.look_at)}</span>
        <span class="cpstate">${kind === "focus" ? "" : stateChip(it)} <span class="caption">${esc(basisText(it))}</span>${linkChip(it)}</span></span>${icon("chevron-right", "chev")}</a>`;
  }
  // 球の課題とつながる候補の印（事前の表から。§9.1）。確かめていないことは印の中で言う
  const linkChip = (it) => it.linked && (it.state === "out_range" || it.focus) ? ` <span class="chip brand" data-linked>球の課題とつながる候補</span>` : "";

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
    no_series: "動画を「自動で取り出す」で入れると見られます", fps: "スローモーションで撮ると見られます", fps_unknown: "スローモーションで撮ると見られます", low_visibility: "体全体が明るく写るように撮ると見られます",
    club_short: "この向きではクラブが短く写り、向きを測れません", border: "範囲の境目なので、どちらとも言えません。もう何本か選ぶと決まることがあります",
    split: "スイングによって分かれています。もう何本か選ぶと決まることがあります", vision_pending: "「見た目を評価する」で見られます",
    vision_unclear: "写真では見えにくかった項目です。明るく、全身が入るように撮ると見られます",
    vision_dropped: "見た目の答えが検証を通らなかったので出していません。もう一回評価すると見られることがあります",
    vision_reselect: "このコマを選び直すと見られます",
    scale: "ボールの両端を押し直すと見られます", unclear: "ガイドの読み方を確かめているところです", ref_club: "この番手の基準はガイドにありません",
  };
  const howto = (it) => it.needs_note && it.reason === "not_in_2d" ? `見るには、${it.needs_note}が要ります` : HOWTO[it.reason] || "";

  // コマの取り出し: 自動のまま使ったコマと、手で直したコマの数（段2b の完了条件。数は畳みの中だけ）
  function framesLine(sws) {
    let a = 0, m = 0;
    for (const s of sws) {
      for (const f of s.frames || []) { if (f.source === "auto") a += 1; else m += 1; }
      m += ((s.capture || {}).auto || {}).missing_marked || 0; // 「写っていない」を押した P も手で決めたコマに数える
    }
    if (!a) return "";
    return `<p class="caption" data-frames-src>コマの選び方: 自動のまま ${lab("count", a + "コマ")}・手で直した ${lab("count", m + "コマ")}</p>`;
  }

  function pStrip(data) {
    const sw = (data.swings || []).find((s) => (s.frames || []).length) || null;
    const ps = ["P1", "P2", "P3", "P4", "P5", "P6", "P7"];
    return `<div class="pstrip" data-pstrip role="list" aria-label="選んだコマ">${ps.map((p) => {
      const f = sw && (sw.frames || []).find((x) => x.checkpoint === p);
      const miss = sw && (sw.missing || []).includes(p);
      const est = f && f.status === "estimated"; // 代わりの規則で決めた「目安」のコマ（§6.4）
      return `<div class="pcell ${f ? "" : "miss"}" role="listitem" data-pchip="${p}" ${est ? `data-est aria-label="${p} 目安"` : ""}>${f && f.has_thumb ? `<img src="/v1/swings/${sw.id}/thumbs/${p}" alt="" loading="lazy">` : `<span class="noimg" aria-hidden="true">${miss ? "―" : ""}</span>`}${lab("p", p)}${est ? `<span class="caption">目安</span>` : ""}</div>`;
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
    const refs = main.filter((x) => x.state === "reference" && x.group !== "tempo");
    const tempo = main.find((x) => x.group === "tempo");
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
      <details class="folded" data-counts><summary class="textbtn">${icon("chevron-right")}件数を見る</summary><p class="caption">見られた${lab("count", c.counts.judged + "件")}のうち、範囲の外${lab("count", outN + "件")}。判断できない${lab("count", c.counts.unknown + "件")}（${rs.slice(0, 4).map(([t, n]) => `${esc(t)} ${lab("count", n + "件")}`).join("・")}）。</p>${framesLine(judgedSw)}</details>`;
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
    // 課題がまだ決まらないときの次の一手は、コマの帯より上に置く（最初の画面で見えるように）
    if (!focus) {
      const byView = {};
      for (const s of judgedSw) byView[s.view] = (byView[s.view] || 0) + 1;
      const most = Math.max(0, ...Object.values(byView));
      const need = Math.max(0, 3 - most);
      const word = ["", "一本", "二本"][need] || "";
      const one = judgedSw.length === 1 ? "まだ一本だけの見立てなので、課題は選んでいません。" : "";
      h += `<div class="note" data-nofocus><p style="margin:0">${need ? `${one}同じ向きのスイングがあと${word}あると、課題を一つ選べます。` : "いま課題にできる項目はありません。"}</p>
        ${need ? `<a class="btn primary block" style="margin-top:var(--s2)" data-add-swing href="#/video/${esc(se.date)}?session=${sid}">${icon("video")}動画をもう一本入れる</a>` : ""}</div>`;
    }
    h += pStrip(data);
    if (focus) {
      h += `<section class="block" aria-labelledby="h-first"><h2 id="h-first" class="label">まずここ</h2>${bigCard(sid, data, focus, "focus")}</section>`;
      if (next) h += `<section class="block" aria-labelledby="h-next"><h2 id="h-next" class="label">次に見る</h2>${bigCard(sid, data, next, "next")}</section>`;
    }
    h += visionHtml(data) + matchHtml(data);
    const unkLead = rs.length ? `<p class="caption" data-unk-why>${rs.map(([t, n]) => `${esc(t)} ${lab("count", n + "件")}`).join("・")}</p>` : "";
    h += `<div class="block">
      ${fold(`ほかに範囲の外 ${lab("count", outs.length + "件")}`, outs, sid, "data-fold=out")}
      ${fold(`飛ぶ力（見られた ${lab("count", dist.filter((x) => x.state === "in_range" || x.state === "out_range").length + "件")}／判断できない ${lab("count", dist.filter((x) => x.state === "unknown").length + "件")}）`, dist, sid, "data-fold=power")}
      ${fold(`範囲の中 ${lab("count", ins.length + "件")}`, ins, sid, "data-fold=in")}
      ${fold(`判断できない ${lab("count", unk.length + "件")}`, unk, sid, "data-fold=unknown", unkLead)}
      ${tempo ? fold(`テンポ（${esc(refFold(tempo))}）`, [tempo], sid, "data-fold=tempo") : ""}
      ${fold(`参考 ${lab("count", refs.length + "件")}`, refs, sid, "data-fold=ref")}
      ${fold(`フォロー（任意）${lab("count", opt.length + "件")}`, opt, sid, "data-fold=opt")}</div>
      ${focus ? `<div class="stack block"><a class="btn block" data-more-swing href="#/video/${esc(se.date)}?session=${sid}">${icon("video")}もう一本選ぶ</a></div>` : ""}
      <ul class="navlist block">${App.navItem(`#/guide/${esc(views[0] || "dtl")}`, "撮り方ガイド", "カメラの置き方")}</ul>`;
    body.innerHTML = h;
    const vl = data.vision && data.vision.last;
    if (vl && (vl.status === "queued" || vl.status === "running")) pollVision(sid, vl.job_id, alive);
    body.addEventListener("click", async (ev) => {
      // 送っているあいだ（aria-disabled）は二度押しを受けない（二重に料金がかからないように）
      const vr = ev.target.closest("[data-vision-run]");
      if (vr) { if (vr.getAttribute("aria-disabled") !== "true") runVision(sid, data, vr, alive); return; }
      const mr = ev.target.closest("[data-match-run]");
      if (mr) { if (mr.getAttribute("aria-disabled") !== "true") runMatch(sid, data, mr); return; }
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

  // ---- 見た目の評価（段2c。§6.7） ----
  const VIS_PS = ["P1", "P2", "P3", "P4", "P5", "P5_5", "P6", "P6_5", "P7", "P8", "P9", "P10"];
  const costText = (v) => "約$" + (Math.round((v.cost_usd_hi || 0) * 100) / 100).toFixed(2);
  const costLab = (v) => lab("cost", costText(v));
  function visionHtml(data) {
    const v = data.vision, c = data.checks;
    if (!v || !c) return "";
    const pend = (c.items || []).filter((x) => x.reason === "vision_pending" && !x.optional).length;
    const last = v.last;
    const running = last && (last.status === "queued" || last.status === "running");
    const done = last && last.status === "done";
    if (!pend && !last) return "";
    let h = `<section class="card block" data-vision aria-labelledby="h-vis"><h2 id="h-vis" class="label">見た目の項目</h2>`;
    if (running) h += `<p data-vision-state="running" role="status">写真を読んでいます（一分ほど）。この画面を離れても続きます。</p>`;
    else if (last && last.status === "failed") h += `<div class="note warn" data-vision-state="failed"><p style="margin:0">見た目の評価ができませんでした。もう一回押すと頼み直せます。</p>
      ${last.error ? `<details><summary class="textbtn">くわしく</summary><p class="caption">${esc(last.error)}</p></details>` : ""}</div>`;
    if (done) {
      h += `<p data-vision-state="done">見た目の項目は、写真を読んで選んだ答えで埋めています（札は「見た目」）。</p>`;
      if (last.n_dropped) h += `<p class="caption" data-vision-dropped>検証を通らなかったので出していない項目 ${lab("count", last.n_dropped + "件")}</p>`;
      const rs = [...new Set((last.reselect || []).flatMap((x) => x.ps || []))];
      if (rs.length) h += `<p class="caption" data-vision-reselect>${rs.map((p) => lab("p", pLabel(p))).join("・")} のコマは、決まった瞬間に見えないと答えました。選び直すと、そのコマの項目も見られます。</p>`;
      if ((last.extra || []).length) h += `<details class="folded" data-vision-extra><summary>そのほかの気づき（判定には使っていません）</summary><ul class="parts">${last.extra.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>`;
    }
    if (pend && !running) {
      if (!v.ready) h += `<p data-vision-off>見た目の評価は、いまは使えません。測れる項目はそのまま見られます。</p>${v.reason ? `<details><summary class="textbtn">くわしく</summary><p class="caption">${esc(v.reason)}</p></details>` : ""}`;
      else if (v.n_target) {
        h += `<p>まだ見ていない見た目の項目が ${lab("count", pend + "件")}あります。</p>
          <button type="button" class="btn primary block" data-vision-run>見た目を評価する（${costLab(v)}まで）</button>
          <p class="caption" data-vision-about>スイング${lab("count", v.n_target + "本")}の選んだコマの写真（長辺を小さくしたもの）だけを送ります。サーバーにも残しません。料金はまだ実測していない見積もりです。</p>`;
      }
    }
    return h + `</section>`;
  }

  // 写真の角に P の番号を焼き込む（AI にどのコマかを渡す。§6.7）。描けない端末ではそのまま送る
  async function stampP(blob, p) {
    try {
      const bm = await createImageBitmap(blob);
      const k = Math.min(1, 1024 / Math.max(bm.width, bm.height));
      const cv = document.createElement("canvas");
      cv.width = Math.round(bm.width * k); cv.height = Math.round(bm.height * k);
      const g = cv.getContext("2d");
      g.drawImage(bm, 0, 0, cv.width, cv.height);
      const fs = Math.max(18, Math.round(cv.height / 18));
      g.font = `bold ${fs}px sans-serif`;
      const t = pLabel(p);
      g.fillStyle = "rgba(0,0,0,.7)"; g.fillRect(0, 0, g.measureText(t).width + fs, fs * 1.5);
      g.fillStyle = "rgba(255,255,255,1)"; g.fillText(t, fs / 2, fs * 1.15);
      return await new Promise((ok) => cv.toBlob((b) => ok(b || blob), "image/jpeg", 0.85));
    } catch { return blob; }
  }

  async function runVision(sid, data, btn, alive) {
    const v = data.vision;
    const box = btn.closest("[data-vision]");
    const say = (html) => { const old = box.querySelector("[data-vision-msg]"); if (old) old.remove(); box.insertAdjacentHTML("beforeend", `<div data-vision-msg>${html}</div>`); };
    const ok = await App.ask({ title: "見た目を評価しますか", text: `料金は ${costText(v)} までの見積もりです。選んだコマの写真を AI（Claude）に送ります。写真はサーバーにも残しません。`, ok: "評価する", cancel: "やめる" });
    if (!ok || btn.getAttribute("aria-disabled") === "true") return;
    btn.setAttribute("aria-disabled", "true");
    try {
      // 同じ向きが多いほうの、判定したスイングから（見た目の答えがまだのものを先に）
      const sws = (data.swings || []).filter((s) => (s.frames || []).length);
      const byView = {};
      for (const s of sws) byView[s.view] = (byView[s.view] || 0) + 1;
      const view = Object.keys(byView).sort((a, b) => byView[b] - byView[a])[0];
      const pick = sws.filter((s) => s.view === view).sort((a, b) => (a.has_vision ? 1 : 0) - (b.has_vision ? 1 : 0));
      const fd = new FormData();
      const used = [];
      let n = 0;
      for (const s of pick) {
        if (used.length >= v.n_target) break;
        let got = 0;
        for (const f of s.frames) {
          if (!VIS_PS.includes(f.checkpoint) || n >= (v.max_images || 32)) continue;
          const b = await Video.keptFrame(`${s.id}:${f.checkpoint}`);
          if (!b) continue;
          fd.append(`f.${s.id}.${f.checkpoint}`, await stampP(b, f.checkpoint), `${f.checkpoint}.jpg`);
          n += 1; got += 1;
        }
        if (got) used.push(s.id);
      }
      if (!used.length) {
        say(`<p class="note warn">この端末に、コマの写真が残っていません。動画を選んだ端末で開くと評価できます（写真は端末の中だけに置いています）。</p>`);
        btn.removeAttribute("aria-disabled");
        return;
      }
      fd.append("meta", JSON.stringify({ session_id: sid, swings: used.map((id) => ({ swing_id: id })) }));
      const r = await api("POST", "/v1/swings/checks", fd);
      if (!r.job_id) { say(`<p class="note">${esc(r.reason || "いまは評価できません")}</p>`); btn.removeAttribute("aria-disabled"); return; }
      if (r.cached) { App.toast("前に評価した答えを使いました"); invalidate(sid); App.invalidate(sid); App.render(); return; }
      btn.remove();
      say(`<p role="status" data-vision-state="running">写真を読んでいます（一分ほど）。この画面を離れても続きます。</p>`);
      pollVision(sid, r.job_id, alive);
    } catch (e) {
      btn.removeAttribute("aria-disabled");
      say(App.errOf(e, { what: "評価を頼めませんでした", saved: "判定は前のままです。" }));
    }
  }

  async function pollVision(sid, jobId, alive) {
    for (let i = 0; i < 150; i++) {
      await new Promise((ok) => setTimeout(ok, i < 5 ? 1000 : 2000));
      if (!alive()) return;
      let j;
      try { j = await api("GET", `/v1/jobs/${jobId}`); } catch { continue; }
      if (j.status === "done" || j.status === "failed") {
        if (!alive()) return;
        if (j.status === "done") App.toast("見た目の項目を埋めました");
        invalidate(sid); App.invalidate(sid); App.render();
        return;
      }
    }
    // 待ちきれなかった（5分）: 続いているかもしれないので、開き直しを案内する（黙って「読んでいます」のままにしない）
    if (!alive()) return;
    const st = document.querySelector("[data-vision] [data-vision-state=running]");
    if (st) st.textContent = "まだ終わっていません。少ししてから開き直すと、結果が見られます。";
  }

  // ---- 球との対応づけ（§9.1） ----
  function matchHtml(data) {
    const m = data.match;
    if (!m || !m.n_shots || !data.checks) return "";
    const body = m.mismatch
      ? `<div class="note warn" data-match-mismatch><p style="margin:0">本数が合いません（動画 ${lab("count", m.n_swings + "本")}・球 ${lab("count", m.n_shots + "球")}）。素振りや打ち直しが入っているかもしれません。合わないあいだは結びません。</p></div>`
      : `<p>動画のスイングを、撮った順に球へ当てます。順番が合っていれば結んでください。</p>
         <button type="button" class="btn block" data-match-run>順番に結ぶ（${lab("count", m.n_swings + "本")}）</button>`;
    return `<details class="folded block" data-match><summary>球との対応づけ（${m.matched ? `結んだ ${lab("count", m.matched + "本")}` : "まだ結んでいません"}）</summary>${body}
      <p class="caption">結ぶと、その球だけに出た球の課題も、動きの課題とつながる候補に数えます。</p></details>`;
  }

  async function runMatch(sid, data, btn) {
    btn.setAttribute("aria-disabled", "true");
    const old = btn.parentNode.querySelector("[data-match-err]");
    if (old) old.remove();
    try {
      const plan = await api("POST", `/v1/sessions/${sid}/swings/match-suggest`);
      if (plan.mismatch) throw new Error("本数が合わなくなりました。開き直してください");
      for (const pr of plan.pairs) await api("PUT", `/v1/swings/${pr.swing_id}/match`, { seq: pr.seq });
      App.toast("球と結びました");
      invalidate(sid); App.invalidate(sid); App.render();
    } catch (e) {
      btn.removeAttribute("aria-disabled");
      btn.insertAdjacentHTML("afterend", `<div data-match-err>${App.errOf(e, { what: "結べませんでした", saved: "前のままです。" })}</div>`);
    }
  }

  // 練習の一例（チェックポイント版のドリル。ガイドの考え方の要約。確かめ中の札つき）
  function drillHtml(it) {
    const d = (it.drills || [])[0];
    if (!d) return "";
    return `<section class="card block" data-drill="${esc(d.id)}" aria-labelledby="h-drill"><h2 id="h-drill" class="label">練習の一例</h2>
      <p class="t-headline" style="margin:0 0 var(--s2)">${esc(d.title)}</p><p>${esc(d.what_changes)}</p>
      <ol class="plain">${(d.steps || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ol>
      <p><span class="label">意識する一点</span><br>${esc(d.cue)}</p><p class="caption">${esc(d.adjust)}</p>
      ${d.checked ? "" : `<p><span class="chip none">確かめ中（人がガイドと突き合わせる前の要約）</span></p>`}</section>`;
  }

  // 診断（D）とホームに出す「動きの課題」のカード（チェックの「まずここ」。無ければ空）
  async function motionCard(sid) {
    let d;
    try { d = await load(sid); } catch { return ""; }
    const c = d && d.checks;
    const f = c && (c.items || []).find((x) => x.focus);
    if (!f) return "";
    const fig = typeof Ideal !== "undefined" ? await Ideal.thumbHtml(d, f) : "";
    return `<a class="card block cpcard focus" data-motion-card href="#/session/${sid}/check/${encodeURIComponent(f.id)}">
      ${fig || (thumbOf(d, f) ? `<img src="${esc(thumbOf(d, f))}" alt="${esc(pLabel(f.p))} のあなたのコマ" loading="lazy">` : `<span class="noimg" aria-hidden="true">${icon("video")}</span>`)}
      <span class="grow1"><span class="label">動きの課題</span><span class="cph">${esc(f.fault_label || f.title)}</span>
      <span class="cpf">${/^P\d/.test(f.p || "") ? lab("p", pLabel(f.p)) + " " : ""}${esc(f.title)}</span>${linkChip(f)}</span>${icon("chevron-right", "chev")}</a>`;
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
    const tempo = isTempo(it); // テンポは区間の項目なので、P の印と一コマの写真を出さない
    const src = tempo ? "" : thumbOf(data, it);
    const unknown = it.state === "unknown";
    const say = it.state === "out_range" ? it.fault_label : it.state === "in_range" ? it.ok_text : unknown ? `判断できません（${it.reason_text || ""}）`
      : it.state === "reference" && it.n_ref ? refSay(it) : tempo ? refFold(it) : it.look_at;
    const how = unknown ? howto(it) : "";
    const est = !tempo && estimatedFrame(data, it);
    const range = tempo ? `<div class="cprange" data-range><span class="label">ガイドの目安</span>上げがゆったり長く、下ろしがそれより短いこと（目安の比は「なぜそう言える？」の中）</div>`
      : `<div class="cprange" data-range><span class="label">目安（こうなら範囲の中）</span>${esc(it.ok_text)}</div>`;
    body.innerHTML = `
      ${!tempo && /^P\d/.test(it.p || "") ? `<p class="label" data-pname>${lab("p", pLabel(it.p))}</p>` : ""}
      <h1 data-title>${esc(it.title)}</h1>
      ${tempo ? "" : `<figure class="cpfig">${src ? `<img src="${esc(src)}" alt="${esc(say)}">` : `<div class="noimg" role="img" aria-label="コマの写真はありません">${icon("video")}</div>`}</figure>`}
      ${est ? `<p class="caption" data-estimated>このコマは目安です（代わりの決め方で選んだコマ）。</p>` : ""}
      <p class="cpstate">${stateChip(it)} <span class="caption">${esc(basisText(it))}</span>${it.single && it.state === "reference" && it.n_ref ? ` <span class="caption">一本だけの見立て</span>` : ""}</p>
      <p class="t-headline" data-say>${esc(say)}</p>
      ${how ? `<p data-howto>${esc(how)}</p>` : ""}
      <p class="sub">${esc(it.look_at)}</p>
      ${range}
      ${it.linked && (it.state === "out_range" || it.focus) ? `<p data-linked-text><span class="chip brand">${esc(it.linked_text || "球の課題とつながる候補です（まだ確かめていません）")}</span></p>` : ""}
      ${drillHtml(it)}
      ${!tempo && it.state === "out_range" && testP(it) ? `<button type="button" class="btn block" data-motion-plan>この動きで練習を組む（十球テスト）</button>` : ""}
      <button type="button" class="textbtn" data-why>${icon("info")}なぜそう言える？</button>
      <nav class="itemnav" aria-label="${esc(GROUP_WORD[g])}の項目">
        ${prev ? `<a class="btn" data-prev href="#/session/${sid}/check/${encodeURIComponent(prev.id)}">${icon("chevron-left")}前の項目</a>` : "<span></span>"}
        ${nxt ? `<a class="btn" data-next href="#/session/${sid}/check/${encodeURIComponent(nxt.id)}">次の項目${icon("chevron-right")}</a>` : "<span></span>"}</nav>
      ${prev || nxt ? `<p class="caption" style="text-align:center">${esc(GROUP_WORD[g])}の項目だけを順に見ます（${lab("count", (k + 1) + " / " + same.length)}）</p>` : ""}`;
    $("[data-why]", body).addEventListener("click", () => openWhy(data, it));
    if (!tempo && src && typeof Ideal !== "undefined") Ideal.enhance($(".cpfig", body), data, it, sid);
    const mp = $("[data-motion-plan]", body);
    if (mp) mp.addEventListener("click", () => startMotionPlan(sid, data, it));
  }

  // ---- 動きのプランを始める（段4。docs/DESIGN_v2.md §8.3）: 合格の条件は十球テスト 八/十 ----
  // 項目の P が区間（P1-P4）なら、終わりの P のコマで数える
  const testP = (it) => { const m = /^(P\d+(?:_5)?)(?:-(P\d+))?$/.exec(it.p || ""); return m ? m[2] || m[1] : null; };
  async function startMotionPlan(sid, data, it) {
    if (!S.player) return;
    const d = (it.drills || [])[0];
    const club = ((data.swings || []).find((x) => x.view === it.view) || (data.swings || [])[0] || {}).club || "";
    const sh = App.openSheet({ title: "この動きで練習を組む", label: "motionplan", size: "full", html: `
      <p class="t-headline" data-mp="what">${esc(it.fault_label || it.title)}</p>
      <p class="sub">練習の最後の十球を撮って、課題の場所が範囲に入った回を数えます。十回中八回で合格です。別の日にもう一回合格すると「効いた」です。</p>
      ${club ? `<p class="caption">${lab("club", App.clubJa(club))}で打つ</p>` : ""}
      <label class="field"><span>意識する一点</span><textarea class="plain" data-mp="cue" rows="3" maxlength="120">${esc(d ? d.cue : "")}</textarea></label>
      ${d ? `<p class="caption">練習の一例「${esc(d.title)}」を使います${d.checked ? "" : "（確かめ中）"}。</p>` : ""}
      <p class="caption">いま動いているプランがあれば、置き換えるかを聞きます。</p>
      <div class="block"><button type="button" class="btn primary block" data-mp="go">プランを作る</button><div data-mp="err"></div></div>` });
    const q = (k) => $(`[data-mp=${k}]`, sh.el);
    if (!club) q("err").innerHTML = `<p class="fielderr">番手が分からないので作れません（動画の番手を選び直してください）</p>`;
    const go = async (replace) => {
      q("err").innerHTML = "";
      const cue = q("cue").value.trim();
      if (!cue && !d) { q("err").innerHTML = `<p class="fielderr">意識する一点を書いてください</p>`; return; }
      const btn = q("go");
      if (btn.getAttribute("aria-disabled") === "true") return;
      btn.setAttribute("aria-disabled", "true");
      const body = { kind: "motion", cp_item_id: it.id, view: it.view, checkpoint: testP(it), club, fault: it.fault || "", drill_id: d ? d.id : "",
        cue: cue || (d && d.cue) || "", title: it.fault_label || it.title, from_session: sid };
      const r = await App.request("POST", `/v1/players/${S.player.id}/plans${replace ? "?replace=1" : ""}`, { body }).catch((e) => ({ ok: false, status: 0, body: { error: String(e) } }));
      btn.removeAttribute("aria-disabled");
      if (r.status === 409 && !replace) {
        if (await App.ask({ title: "動いているプランがもう1つあります", text: "動かせるのは1つだけです。いまのプランを「替えた」にして、この動きのプランに置き換えますか？", ok: "置き換える", cancel: "やめる" })) return go(true);
        return;
      }
      if (!r.ok) { q("err").innerHTML = App.errorHtml({ what: "プランを作れませんでした", next: "内容を確かめて、もう一度押してください。", detail: (r.body && r.body.error) || r.text }); return; }
      sh.close("done");
      App.toast("動きのプランを作りました。練習の画面に組み方が出ます。");
      App.go("/practice");
    };
    q("go").addEventListener("click", () => { if (club) go(false); });
  }

  // 写真に使ったコマが「目安」（代わりの規則で決めた）か（§6.4）
  function estimatedFrame(data, it) {
    const fr = (sid, p) => ((data.swings || []).find((s) => s.id === sid) || { frames: [] }).frames.find((f) => f.checkpoint === p && f.has_thumb);
    const f = (it.frames || []).map((x) => fr(x.swing_id, x.p)).find(Boolean);
    return !!(f && f.status === "estimated");
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
    if (v.guide !== undefined) return `<li>ガイドの目安（上げ：下ろし）: ${v.guide == null ? "この番手の目安はありません" : "約" + num(v.guide, "ratio") + "対1"}</li><li>あなた: ${num(v.v, "ratio")}対1（一コマのずれを考えると ${num(v.band_lo, "ratio")}〜${num(v.band_hi, "ratio")}）・上げ ${num(v.back_s)} 秒・下ろし ${num(v.down_s)} 秒</li>`;
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
    if (it.state === "reference" && it.n_ref) rows.push(`<li><b>何本から</b>: ${it.n_ref}本${it.single ? "（一本だけの見立てです）" : ""}</li>`);
    if (estimatedFrame(data, it) && !isTempo(it)) rows.push(`<li><b>コマの選び方</b>: このコマは、ガイドの定義を画像から直接は見られないので、代わりの決め方で選んだ目安です（後ろからのクラブが水平のコマなど）</li>`);
    if (BASIS[it.basis]) rows.push(`<li><b>根拠</b>: ${esc(BASIS[it.basis])}${it.proxy ? `（代わりの点: ${esc(it.proxy)}）` : ""}</li>`);
    if ((it.visions || []).length) {
      const sw = (data.swings || []).map((x) => x.id);
      rows.push(`<li><b>見た目（AI）の答え</b>: ${it.visions.map((v) => `スイング ${sw.indexOf(v.swing_id) + 1}「${esc(v.option)}」${v.visual ? `（${esc(v.visual)}）` : ""}`).join("／")}。AI が写真から選んだ答えで、正しさはまだ測っていません</li>`);
    }
    if (it.linked) rows.push(`<li><b>球の課題とつながる候補</b>: この日の球に出た課題と、事前に決めた表（ガイドも動きを「可能性」として扱っています）で結びついています。本当に効いているかは、練習で確かめます</li>`);
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
      <section class="card block" data-overlay><h2>前回の構えに合わせる</h2>
        <p>別の日と比べるときは、前回と同じ置き方で撮ります。前回の構えのコマを薄く重ねて、体の位置が重なるようにカメラを動かします。</p>
        <button type="button" class="btn block" data-cam>カメラで合わせる</button><div data-camview></div>
        <p class="caption">カメラの映像はこの端末の中だけで使い、どこにも送りません。</p></section>
      <p class="sub block" data-other></p>`;
    $("[data-cam]", el).addEventListener("click", (ev) => openOverlay(el, view, ev.currentTarget, alive));
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

  // 前回の P1（構え）を、カメラのプレビューに薄く重ねる（§8.2・§10 R0）。映像はどこにも送らない
  async function prevP1(view) {
    const ss = (await App.sessions().catch(() => [])).slice(0, 6);
    const ids = [];
    for (const s of ss) {
      try { for (const sw of await api("GET", `/v1/sessions/${s.id}/swings`)) if (sw.view === view) ids.push(sw.id); } catch { /* 次の日へ */ }
      if (ids.length >= 4) break;
    }
    return ids;
  }
  async function openOverlay(el, view, btn, alive) {
    const box = $("[data-camview]", el);
    if (btn.getAttribute("aria-disabled") === "true") return;
    btn.setAttribute("aria-disabled", "true");
    let stream = null;
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error("この端末ではカメラを開けません");
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
    } catch (e) {
      btn.removeAttribute("aria-disabled");
      box.innerHTML = `<p class="note warn">カメラを開けませんでした（${esc(e && e.message ? e.message : "許可されていません")}）。前回の構えのコマは、チェックの画面の写真で見られます。</p>`;
      return;
    }
    if (!alive()) { stream.getTracks().forEach((t) => t.stop()); return; }
    box.innerHTML = `<div class="camov" data-camov><video autoplay playsinline muted></video></div><p class="caption" data-camnote>前回の構えのコマを探しています…</p>
      <button type="button" class="btn block" data-camstop>カメラを閉じる</button>`;
    const v = $("video", box);
    v.srcObject = stream;
    const stop = () => { stream.getTracks().forEach((t) => t.stop()); box.innerHTML = ""; btn.removeAttribute("aria-disabled"); window.removeEventListener("hashchange", stop); };
    $("[data-camstop]", box).addEventListener("click", stop);
    window.addEventListener("hashchange", stop);
    const ids = await prevP1(view);
    const note = $("[data-camnote]", box);
    if (!note) return;
    const tryNext = (i) => {
      if (i >= ids.length) { note.textContent = "前回の構えのコマがまだありません。今回の撮影が次回の基準になります。"; return; }
      const img = new Image();
      img.alt = "前回の構えのコマ（薄く重ねています）";
      img.onload = () => { const ov = $("[data-camov]", box); if (ov) { ov.appendChild(img); note.textContent = "薄い姿が前回の構えです。体と足元が重なるようにカメラを動かします。"; } };
      img.onerror = () => tryNext(i + 1);
      img.src = `/v1/swings/${ids[i]}/thumbs/P1`;
    };
    tryNext(0);
  }

  App.route("/session/:id/check", renderC, { tab: "record" });
  App.route("/session/:id/check/:item", renderItem, { tab: "record" });
  App.route("/guide/:view", renderGuide, { tab: "record" });
  return { load, invalidate, STATE, motionCard };
})();
