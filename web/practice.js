"use strict";
/*
  練習（docs/DESIGN_v2.md §10 P・P2）。前の today.js の中身を移したもの（docs/DESIGN_coaching.md §8）。
  - 圏外でも動く。プラン・型・ドリルは開いたときに localStorage に写し、ブロックの進み具合も
    localStorage にだけ置く（送るのは取り込んだあと、練習を記録するとき）。
  - 練習中（#/practice/run）は今のブロックだけを大きく出す。下のタブは隠し、出口は「✕ 中断」1つ。
    「次のブロックへ」は 72px、±1球と1つ戻るは 48px。1球ごとには押させない。
  - 事実の文は作らない。判定・状態・次の手の文は分析サービスの定型文をそのまま出す。
  - ドリルは人が向きと安全を確かめたもの（checked_by）だけ。
  - 確認は全部シート（confirm を使わない）。失敗はその場に出す（alert を使わない）。
*/
const Practice = (() => {
  const { $, esc, lab, icon, LS, S } = App;
  const kToday = (pid) => `golf.today.${pid}`;
  const kPrac = (planId) => `golf.practice.${planId}`;
  const kDrills = (hand) => `golf.drills.${hand}`;
  const NO = ["⓪", "①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩", "⑪", "⑫"];
  const no = (i) => NO[i] || `(${i})`;
  const BLK = { warmup: "準備", baseline: "いつも通り", drill: "ドリル", intervention: "本番", retention: "定着" };

  // T.data は /v1/players/{id}/today の応答（圏外のときは写しておいたもの）
  const T = { pid: null, data: null, offline: false, savedAt: null, drill: null, run: null, eval: null, sessPick: null };
  const plan = () => (T.data && T.data.plan) || null;
  const hand = () => (T.data && T.data.handedness === "L" ? "L" : "R");
  // 動きのプラン（docs/DESIGN_v2.md §8.3）: ⑥は10球テスト、①と⑥で「撮る」
  const isMotion = (p) => !!(p && p.kind === "motion");
  const testBlock = (p) => (p && p.params && Number.isInteger(p.params.test_block) ? p.params.test_block : 6);
  const blkName = (p, i, kind) => (isMotion(p) && i === testBlock(p) ? "10球テスト" : BLK[kind] || kind);
  const films = (p, i) => isMotion(p) && (i === testBlock(p) || i === 1);
  const planTitle = (p) => (p.trigger && p.trigger.plain && p.trigger.plain.title) || (p.trigger && p.trigger.title) || p.issue;

  // 409 の existing_id などを読むために、状態の番号と本文を両方返す取り方（圏外は status 0）
  async function call(method, path, body) {
    try { return await App.request(method, path, { body }); } catch (e) { return { status: 0, ok: false, body: null, text: String(e) }; }
  }
  const errText = (r) => (r.status === 0 ? "サーバーに届きません。電波のあるところでもう一度押してください。" : (r.body && r.body.error) || r.text || String(r.status));

  // ---- 練習の進み具合（localStorage だけ） ----
  function practice() {
    const p = plan();
    if (!p) return null;
    const tpl = p.template || [];
    let st = LS.get(kPrac(p.id));
    if (!st || st.date !== App.localDate() || !Array.isArray(st.counts) || st.counts.length !== tpl.length) {
      // 前回が「足りない」なら、サーバーが出した次の型の球数で組む
      const nc = T.data && Array.isArray(T.data.next_counts) && T.data.next_counts.length === tpl.length ? T.data.next_counts : null;
      st = { plan_id: p.id, date: App.localDate(), idx: 0, done: false, counts: nc ? nc.slice() : tpl.map((b) => b.n) };
      LS.set(kPrac(p.id), st);
    }
    return st;
  }
  const savePractice = (st) => LS.set(kPrac(st.plan_id), st);
  function ranges(counts) {
    let at = 0;
    return counts.map((n) => { const r = n > 0 ? [at + 1, at + n] : null; at += n; return r; });
  }
  const rangeText = (r) => (r ? `${r[0]}〜${r[1]}球目` : "打たない");

  // ---- 読み込み ----
  const checked = (d) => !!(d && typeof d.checked_by === "string" && d.checked_by.trim());
  async function drills() {
    const h = hand();
    const r = await call("GET", `/v1/drills?handedness=${h}`);
    if (r.ok) { LS.set(kDrills(h), r.body); return r.body; }
    return LS.get(kDrills(h));
  }
  async function load() {
    const pid = S.player ? S.player.id : LS.get("golf.player");
    T.pid = pid; T.run = null; T.eval = null; T.noPlayer = false;
    if (pid == null) { T.data = null; T.noPlayer = true; return false; }
    const r = await call("GET", `/v1/players/${pid}/today`);
    if (r.ok) {
      T.data = r.body; T.offline = false; T.savedAt = new Date().toISOString();
      LS.set(kToday(pid), { data: T.data, saved_at: T.savedAt });
    } else if (r.status === 0) {
      const c = LS.get(kToday(pid));
      T.data = c ? c.data : null; T.savedAt = c ? c.saved_at : null; T.offline = true;
      App.setOffline(true);
    } else throw new Error(errText(r));
    T.drill = null;
    const p = plan();
    if (p && p.drill_id) {
      const cat = T.offline ? LS.get(kDrills(hand())) : await drills();
      T.drill = ((cat && cat.drills) || []).find((d) => d.id === p.drill_id && checked(d)) || null;
    }
    return !!p;
  }

  // ドリルの置き方の図（分析サービスのファイル。script・イベント・リンクは念のため外す）
  function safeSvg(text) {
    try {
      const doc = new DOMParser().parseFromString(String(text || ""), "image/svg+xml");
      const svg = doc.documentElement;
      if (!svg || svg.nodeName !== "svg") return null;
      svg.querySelectorAll("script,foreignObject,a,use,image").forEach((x) => x.remove());
      for (const el of [svg, ...svg.querySelectorAll("*")]) {
        for (const a of [...el.attributes]) if (/^on/i.test(a.name) || /href$/i.test(a.name)) el.removeAttribute(a.name);
      }
      return document.importNode(svg, true);
    } catch { return null; }
  }
  function fill(text) {
    const p = plan();
    const w = p && p.params && p.params.window;
    return Report.fillBand(text, { band_shape: w ? { ok: true, window: { ok: true, ...w } } : null }, hand());
  }
  const planClaim = (suffix) => {
    const cs = (plan() && plan().rationale && plan().rationale.claims) || [];
    const c = cs.find((x) => x && typeof x.id === "string" && x.id.endsWith(suffix));
    return c ? c.text : null;
  };

  // 1回の練習の判定を、数字も専門用語も使わずに言う（evaluation.plain）。無い古い保存だけ同じ対応を引く
  const TONE = { good: "good", none: "none", bad: "bad", warn: "warn" };
  const RUN_PLAIN = {
    strong: ["今日ははっきり差が出ました", "good"], moderate: ["今日は差が出ました", "good"],
    weak: ["向きは良いですが、はっきりしません", "none"], none: ["今日は変わりませんでした", "none"],
    worse: ["今日は悪い向きでした", "bad"], insufficient: ["球が足りず、決められませんでした", "warn"],
  };
  const GOODG = (g) => g === "strong" || g === "moderate";
  function plainOf(ev) {
    if (ev && ev.plain && ev.plain.label) return [ev.plain.label, TONE[ev.plain.tone] || "none", ev.plain.why || ""];
    if (ev && GOODG(ev.grade) && ev.counts_as_worked === false) return ["良く見えましたが、今日は数えません", "warn", ""];
    const [l, c] = RUN_PLAIN[ev && ev.grade] || ["—", "none"];
    return [l, c, ""];
  }

  function drillHtml(p) {
    if (!p.drill_id) return `<p class="sub" data-t="nodrill">確かめ済みのドリルはまだありません。自分で書いた「意識する1点」で進めます。</p>`;
    const d = T.drill;
    if (!d) return `<p class="sub" data-t="nodrill">ドリル（${esc(p.drill_id)}）の中身を読めませんでした。「意識する1点」で進めます。</p>`;
    return `<details class="card" data-t="drill"><summary class="textbtn"><b>${esc(d.title)}</b>${d.coach_reviewed ? "" : ` <span class="chip none">コーチ未確認のドリル</span>`}${d.kind === "compensation" ? ` <span class="chip warn">補正（根本の直しではない）</span>` : ""} やり方と置き方の図</summary>
      <div class="fig" data-t="drillfig"></div>
      <ol>${(d.steps || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ol>
      ${d.cue_drill ? `<p>ドリル中: 「${esc(d.cue_drill)}」</p>` : ""}
      ${d.safety ? `<p class="caption">⚠︎ ${esc(d.safety)}</p>` : ""}
      ${(d.video_checks || []).length ? `<details class="folded"><summary>動画で確かめること</summary><ul>${d.video_checks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}
    </details>`;
  }

  function lastHtml() {
    const ev = T.data.last_evaluation;
    if (!ev) return "";
    const pr = ev.progress || {};
    const [rl, rc] = plainOf(ev);
    return `<p data-t="last"><span class="label">前回</span> <span class="chip ${rc}">${esc(rl)}</span>${pr.title ? ` ${esc(pr.title)}` : ""}</p>`;
  }

  function offlineNote() {
    const where = App.netDown() ? "圏外です。" : "サーバーにつながりません。";
    return T.offline ? `<div class="note warn" data-t="offline">${where}${T.savedAt && App.localTime(T.savedAt) ? `${esc(App.localTime(T.savedAt))} に写した内容で動いています。` : ""}ブロックは進められます（送るのは取り込むときです）。</div>` : "";
  }
  // 使う人がまだいない（最初の記録の前）。圏外の文ではなく、空の状態を出す
  const noPlayerHtml = () => `<div class="empty" data-t="noplayer"><svg class="art" viewBox="0 0 96 96" aria-hidden="true"><circle cx="48" cy="48" r="34"/><path d="M40 34v28l22-14z"/></svg>
      <p class="t-headline">まだ記録がありません。</p><p class="sub">最初の記録を入れて診断すると、課題の練習をここで組めます。</p></div>
      <a class="btn primary block" data-primary href="#/record">最初の記録を入れる</a>`;
  const noCopyHtml = () => `<div class="note">まだ一度も開いていないので、${App.netDown() ? "圏外" : "サーバーにつながらない"}あいだは出せません。つながるところで一度開くと、練習場でも使えます。</div>`;

  // ==== P 練習（組み方） ====
  async function renderOverview({ el, alive }) {
    el.innerHTML = `<div class="pagehead"><h1>練習</h1></div><div data-body></div>`;
    const body = $("[data-body]", el);
    const stop = App.loading(body);
    try { await load(); } finally { stop(); }
    if (!alive()) return;
    if (T.noPlayer) { body.innerHTML = noPlayerHtml(); return; }
    if (!T.data) { body.innerHTML = noCopyHtml(); return; }
    const p = plan();
    if (!p) {
      const ss = await App.sessions().catch(() => []);
      const last = ss.find((s) => s.n_shots);
      body.innerHTML = `${offlineNote()}<div class="empty" data-t="noplan"><svg class="art" viewBox="0 0 96 96" aria-hidden="true"><circle cx="48" cy="48" r="34"/><path d="M40 34v28l22-14z"/></svg>
        <p class="t-headline">動いているプランはありません。</p><p class="sub">診断で一点を選んで「このプランで始める」を押すと、ここに今日の練習が出ます。</p></div>
        ${last ? `<a class="btn primary block" data-primary href="#/session/${last.id}">診断を見る</a>` : `<a class="btn primary block" data-primary href="#/record">最初の記録を入れる</a>`}`;
      return;
    }
    const st = practice();
    const tpl = p.template || [];
    const rg = ranges(st.counts);
    const runsAll = T.data.runs || [];
    const todayRun = runsAll.find((r) => st.recorded && r.session_id === st.recorded) || runsAll.find((r) => r.date === App.localDate());
    const nth = todayRun ? todayRun.index + 1 : (T.data.next_index || 0) + 1;
    const total = st.counts.reduce((a, b) => a + b, 0);
    const blocks = tpl.map((b, i) => {
      const cls = st.done || i < st.idx ? "done" : i === st.idx ? "cur" : "";
      let extra = b.kind === "intervention" ? `<span class="bcue">「${esc(p.cue)}」</span>` : b.kind === "drill" && T.drill ? `<span class="bcue sub">${esc(T.drill.title)}</span>` : "";
      if (isMotion(p) && i === testBlock(p)) extra = `<span class="bcue" data-t="testcue">撮る・課題以外は意識しない</span>`;
      else if (films(p, i)) extra += `<span class="bcue sub" data-t="film">撮る</span>`;
      return `<li class="${cls} k-${esc(b.kind)}" data-i="${i}"><span class="bno">${no(i)}</span><span class="bk">${esc(blkName(p, i, b.kind))}</span><span class="bn">${st.counts[i]}球</span><span class="br sub">${rangeText(rg[i])}</span>${extra}</li>`;
    }).join("");
    const started = st.idx > 0 || st.done;
    const primary = st.recorded || st.done ? `<a class="btn primary block" data-primary href="#/practice/result">${isMotion(p) ? "練習の結果へ（動画で数える）" : "練習の結果へ（取り込んで判定する）"}</a>`
      : `<a class="btn primary block" data-primary href="#/practice/run"><span>${started ? "続きから" : "練習を始める"}（${lab("count", total + "球")}）</span></a>`;
    body.innerHTML = `${offlineNote()}
      <section data-t="head">
        <p class="status">${lab("club", App.clubJa(p.club))}・${lab("count", nth + "回目")}</p>
        <p class="t-title ttitle">${esc(planTitle(p))}</p>
        ${lastHtml()}
        <div class="focuscard block"><span class="label">本番で意識する一点</span><p class="t-headline" style="margin:var(--s1) 0 0">「${esc(p.cue)}」</p></div>
        ${drillHtml(p)}
        <button class="textbtn" data-t="planwhy">なぜこれをやる？</button>
      </section>
      <section class="block"><h2>組み方 <span class="caption">${esc(App.clubJa(p.club))}だけで ${total}球</span></h2>
        ${T.data.next_counts_reason === "more_shots" && !todayRun ? `<p class="sub" data-t="more">前回は球が足りなかったので、いつも通りと本番の球を増やしています。</p>` : ""}
        <ol class="tblocks" id="tBlocks">${blocks}</ol>
        <p class="caption">球の番号は、このクラブで打った順です。打ち直したら練習中の画面で「1球足す」を押します（1球ごとには押しません）。</p>
        ${isMotion(p) ? `<p class="caption" data-t="motionnote">最後の十球を撮って、課題の場所が範囲に入った回を数えます。十回中八回で合格です（ガイドの練習の合格ラインで、確率ではありません）。</p>` : ""}</section>
      <div class="block">${primary}</div>
      <ul class="navlist block">${App.navItem("#/practice/result", "練習が終わったら", "記録を選ぶ・取り込む・判定する")}${App.navItem("#/progress", "経過", "これまでの練習の進み")}</ul>`;
    const fig = body.querySelector("[data-t=drillfig]");
    if (fig && T.drill) { const n = safeSvg(T.drill.diagram_svg); if (n) fig.appendChild(n); else fig.remove(); }
    body.querySelector("[data-t=planwhy]").addEventListener("click", () => {
      const why = p.trigger && p.trigger.plain && p.trigger.plain.why;
      const adv = planClaim(".advance"), guards = planClaim(".guards");
      App.openSheet({ title: "なぜこれをやる？", label: "planwhy", size: "full", html: `${why ? `<p>${esc(why)}</p>` : ""}
        <p class="claim">${esc(p.hypothesis)}</p>${adv ? `<p class="claim">${esc(adv)}</p>` : ""}${guards && fill(guards) ? `<p class="claim">${esc(fill(guards))}</p>` : ""}
        <p class="caption">最後の「いつも通り」には効果が残って混ざるので、効いたぶんは小さく見えます。</p>` });
    });
  }

  // ==== 練習中（今のブロックだけを大きく） ====
  let actx = null;
  function signal(root) {
    root.classList.remove("flash"); void root.offsetWidth; root.classList.add("flash");
    try {
      actx = actx || new (window.AudioContext || window.webkitAudioContext)();
      const o = actx.createOscillator(), g = actx.createGain();
      o.frequency.value = 880; g.gain.value = 0.08;
      o.connect(g); g.connect(actx.destination); o.start(); o.stop(actx.currentTime + 0.12);
    } catch { /* 音が出せなくても進める */ }
  }
  let wake = null;
  async function keepAwake() {
    try { if (navigator.wakeLock && !wake) { wake = await navigator.wakeLock.request("screen"); wake.addEventListener("release", () => { wake = null; }); } } catch { wake = null; }
  }
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && location.hash.startsWith("#/practice/run")) keepAwake(); });

  async function renderRunning({ el, alive }) {
    el.innerHTML = `<div data-body></div>`;
    const body = $("[data-body]", el);
    if (!T.data || !plan()) {
      const stop = App.loading(body);
      try { await load(); } finally { stop(); }
    }
    if (!alive()) return;
    const p = plan();
    if (!p) { App.go("/practice", { replace: true }); return; }
    body.innerHTML = `<div class="run" data-t="run">
      <div class="runtop"><button class="btn small" data-t="quit" aria-label="中断して組み方へ戻る">${icon("x")}中断</button><span class="grow1 caption" style="text-align:right">${esc(App.clubJa(p.club))}</span></div>
      <h1 class="visually-hidden">練習中</h1>
      <div class="dots" data-t="dots" aria-hidden="true"></div>
      <div class="now" data-t="now" aria-live="polite"></div>
      <div class="ctl">
        <button class="btn primary next" data-primary data-t="next" id="tNext"></button>
        <div class="row" style="margin-top:var(--s2)"><button class="btn" data-t="plus" aria-label="このブロックに1球足す">＋1球</button><button class="btn" data-t="minus" aria-label="このブロックから1球引く">−1球</button></div>
        <div class="row" style="margin-top:var(--s2)"><button class="btn" data-t="back">1つ前のブロックへ</button></div>
        <p class="caption why" data-t="why" aria-live="polite"></p>
      </div></div>`;
    const root = $("[data-t=run]", body);
    const draw = () => {
      const st = practice(), tpl = p.template, rg = ranges(st.counts);
      const b = tpl[st.idx] || tpl[tpl.length - 1];
      $("[data-t=dots]", root).innerHTML = tpl.map((_, i) => `<span class="${st.done || i < st.idx ? "done" : i === st.idx ? "cur" : ""}"></span>`).join("");
      const now = $("[data-t=now]", root), next = $("[data-t=next]", root);
      if (st.done) {
        now.innerHTML = isMotion(p) ? `<p class="kind">練習はここまで</p><p class="sub">撮った動画を入れて、十球テストを数えます。</p>`
          : `<p class="kind">練習はここまで</p><p class="sub">球を取り込んで、ブロックの区切りを確かめます。</p>`;
        next.textContent = isMotion(p) ? "動画で数える" : "取り込んで確かめる";
      } else {
        let cue = b.kind === "intervention" ? `「${p.cue}」` : b.kind === "drill" && T.drill ? `ドリル中: 「${T.drill.cue_drill || T.drill.title}」` : b.kind === "baseline" ? "いつも通り（何も意識しない）" : "";
        if (isMotion(p) && st.idx === testBlock(p)) cue = "撮ります。課題以外は意識しません。当たり方も行方も問いません。打ち終えてから、できたと思った回数を一回だけ答えます。";
        else if (films(p, st.idx)) cue += "（撮ります）";
        now.innerHTML = `<p class="caption">いま ${no(st.idx)}（${st.idx + 1} / ${tpl.length}）</p><p class="kind" data-t="kind">${esc(blkName(p, st.idx, b.kind))}</p>
          <p class="count" data-t="count">${st.counts[st.idx]}球</p><p class="sub">${rangeText(rg[st.idx])}</p><p class="cueline">${esc(cue)}</p>`;
        const nx = tpl[st.idx + 1];
        next.textContent = nx ? `次のブロックへ（${no(st.idx + 1)} ${blkName(p, st.idx + 1, nx.kind)}）` : "最後のブロックを打ち終えた";
      }
      // 押せないボタンは aria-disabled にして、理由を1行で書く（disabled だけだと、なぜ押せないかが分からない。§11.4）
      const why = [];
      const off = (k, on) => { const b = $(`[data-t=${k}]`, root); if (on) b.setAttribute("aria-disabled", "true"); else b.removeAttribute("aria-disabled"); };
      off("minus", st.done || st.counts[st.idx] <= 0);
      off("plus", st.done);
      off("back", !st.done && st.idx === 0);
      if (st.done) why.push("打ち終えたので、球の数はもう変えられません。直すときは「1つ前のブロックへ」で戻ります。");
      else {
        if (st.counts[st.idx] <= 0) why.push("このブロックは0球なので、これ以上引けません。");
        if (st.idx === 0) why.push("最初のブロックなので、前には戻れません。");
        else why.push("打ち直した球があれば「＋1球」を押します（1球ごとには押しません）。");
      }
      $("[data-t=why]", root).textContent = why.join(" ");
    };
    draw();
    keepAwake();
    root.addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-t]");
      if (!b) return;
      const act = b.dataset.t;
      if (act === "quit") { App.go("/practice"); return; }
      if (b.getAttribute("aria-disabled") === "true") return;
      const st = practice();
      const n = p.template.length;
      if (act === "next") {
        if (st.done) { App.go("/practice/result"); return; }
        if (st.idx < n - 1) st.idx += 1; else st.done = true;
        signal(root);
      } else if (act === "plus") st.counts[st.idx] = Math.min(60, st.counts[st.idx] + 1);
      else if (act === "minus") st.counts[st.idx] = Math.max(0, st.counts[st.idx] - 1);
      else if (act === "back") { if (st.done) st.done = false; else if (st.idx > 0) st.idx -= 1; }
      savePractice(st);
      keepAwake();
      draw();
    });
  }

  // ==== P2 練習の結果（記録・区切り・判定・次の手） ====
  async function renderResult({ el, query, alive }) {
    el.innerHTML = `<div class="pagehead">${App.backBtn("#/practice", "練習へ戻る")}<h1>練習の結果</h1></div><div data-body></div>`;
    const body = $("[data-body]", el);
    const stop = App.loading(body);
    try { await load(); } finally { stop(); }
    if (!alive()) return;
    if (T.noPlayer) { body.innerHTML = noPlayerHtml(); return; }
    // ホームの「合格しました／止める」から来たら、最後に判定した回を開いて、その判定と選ぶボタンを最初から出す
    if (query.show === "last") {
      const lastRun = [...((T.data && T.data.runs) || [])].reverse().find((r) => r.evaluated);
      if (lastRun) T.sessPick = lastRun.session_id;
      history.replaceState(null, "", "#/practice/result");
    }
    if (!T.data) { body.innerHTML = noCopyHtml(); return; }
    const p = plan();
    if (!p) {
      body.innerHTML = `${offlineNote()}<p class="sub">動いているプランはありません。</p><a class="btn primary block" data-primary href="#/practice">練習へ</a>`;
      return;
    }
    if (isMotion(p)) { await renderMotionResult(body, p, alive); return; }
    body.innerHTML = `${offlineNote()}
      <p class="sub">${esc(planTitle(p))}</p>
      <div id="tJudge" class="block"></div>
      <section class="card" id="tAfter"><h2>練習が終わったら</h2>
        <ol class="stack" style="padding-left:1.4em">
          <li><label class="field"><span>この練習の記録を選ぶ</span><select id="tSess"></select></label>
            <button class="btn" data-t="newsess" hidden>今日の記録を作る</button></li>
          <li>計測器の球を、その記録に取り込む <a class="btn" data-t="goimport" href="#/record">取り込む画面へ</a></li>
          <li>球の並びでブロックの区切りを確かめて、判定する</li>
        </ol>
        <div id="tRun"></div></section>`;
    await renderSessions(body);
    body.addEventListener("change", (ev) => {
      if (ev.target.id !== "tSess") return;
      updateImportLink(body);
      if (ev.target.value) showRunFor(body, Number(ev.target.value));
    });
    body.addEventListener("click", async (ev) => {
      const b = ev.target.closest("button");
      if (!b) return;
      const errBox = () => body.querySelector("#tRun [data-t=err]");
      const fail = (msg) => { const x = errBox(); if (x) x.innerHTML = `<p class="fielderr">${esc(msg)}</p>`; else App.toast(msg); };
      if (b.dataset.t === "record") return recordRun(body);
      if (b.dataset.t === "newsess") {
        const r = await call("POST", "/v1/sessions", { player_id: T.pid, date: App.localDate(), location: "練習場" });
        if (!r.ok) { fail(errText(r)); return; }
        await App.sessions(true).catch(() => []);
        T.sessPick = r.body.id;
        await renderSessions(body);
        return;
      }
      if (b.dataset.mv) return moveBoundary(body, Number(b.dataset.mv), Number(b.dataset.d));
      if (b.dataset.t === "confirm") return putCounts(body, [...T.run.counts]);
      if (b.dataset.t === "judge") return judge(body);
      if (b.dataset.close) return closePlan(body, b.dataset.close);
      if (b.dataset.cont) return continuePlan(body);
      if (b.dataset.alt) return alternatePlan(body);
      if (b.dataset.t === "whyjudge") return openJudgeWhy();
      if (b.dataset.seq) {
        const q = Number(b.dataset.seq), sid = T.run && T.run.session_id;
        const list = await App.shots(sid, true).catch(() => []);
        const s = list.find((x) => x.seq === q);
        if (s) Report.openShot(sid, s, null);
      }
    });
  }

  function updateImportLink(body) {
    const sel = body.querySelector("#tSess"), gi = body.querySelector("[data-t=goimport]");
    const s = (App.S.cache.sessions || []).find((x) => String(x.id) === sel.value);
    if (!gi) return;
    if (s) { gi.href = `#/record/${s.date}?target=${s.id}&from=practice`; gi.removeAttribute("aria-disabled"); }
    else { gi.href = "#/practice/result"; gi.setAttribute("aria-disabled", "true"); }
  }

  async function renderSessions(body) {
    const sel = body.querySelector("#tSess");
    if (!sel) return;
    const ss = await App.sessions().catch(() => []);
    const runs = (T.data && T.data.runs) || [];
    const box = body.querySelector("#tRun");
    if (!ss.length) { sel.innerHTML = `<option value="">（${T.offline ? "圏外なので読めません" : "記録がありません"}）</option>`; box.innerHTML = ""; }
    // きっかけの診断の記録（プランを作る前の球）は練習にできない（サーバーも断る）
    const p = plan();
    const trig = p.trigger && p.trigger.session_id;
    const usable = ss.filter((s) => s.id !== trig);
    if (ss.length) sel.innerHTML = usable.map((s) => `<option value="${s.id}">${esc(App.dateJa(s.date))}${s.location ? "・" + esc(s.location) : ""}（${s.n_shots || 0}球）${runs.some((r) => r.session_id === s.id) ? " ✓記録済み" : ""}</option>`).join("");
    const today = ss.find((s) => s.date === App.localDate() && s.id !== trig);
    const since = (p.created_at || "").slice(0, 10);
    const after = ss.find((s) => s.id !== trig && s.date >= since);
    const recorded = runs.length ? ss.find((s) => s.id === runs[runs.length - 1].session_id && s.date === App.localDate()) : null;
    const nb = body.querySelector("[data-t=newsess]");
    if (nb) nb.hidden = !!today || T.offline;
    const pickS = T.sessPick && usable.some((s) => s.id === T.sessPick) ? { id: T.sessPick } : (recorded || today || after);
    if (!pickS) {
      sel.insertAdjacentHTML("afterbegin", `<option value="">（選んでください）</option>`);
      sel.value = "";
      updateImportLink(body);
      box.innerHTML = `<p class="sub">この練習の記録がまだありません。「今日の記録を作る」を押してから、球を取り込みます。</p>`;
      return;
    }
    sel.value = String(pickS.id);
    updateImportLink(body);
    await showRunFor(body, Number(sel.value));
  }

  async function showRunFor(body, sid) {
    T.sessPick = sid;
    const box = body.querySelector("#tRun");
    const r0 = ((T.data && T.data.runs) || []).find((r) => r.session_id === sid);
    T.eval = null;
    const jb = body.querySelector("#tJudge");
    if (jb) jb.innerHTML = "";
    if (!r0) {
      const st = practice();
      box.innerHTML = `<p class="sub">この記録には、まだこのプランの練習を記録していません。いまの組み方（${st.counts.map((n, i) => `${no(i)}${n}`).join("・")}）で区切ります。あとで球の並びで直せます。</p>
        <button class="btn primary block" data-primary data-t="record">この練習を記録する</button><div data-t="err"></div>`;
      return;
    }
    box.innerHTML = `<p class="loading-text">読み込んでいます…</p>`;
    const r = await call("GET", `/v1/plan-runs/${r0.id}`);
    if (!r.ok) { box.innerHTML = App.errorHtml({ what: "練習の記録を読めませんでした", next: errText(r) }); return; }
    T.run = r.body;
    // 最後に判定した回なら、保存してある判定を最初から出す（もう一度「判定する」を押させない）
    const runs = (T.data && T.data.runs) || [];
    const lastEv = [...runs].reverse().find((x) => x.evaluated);
    if (lastEv && lastEv.id === r0.id && T.data.last_evaluation) T.eval = T.data.last_evaluation;
    renderRun(body);
  }

  function blockIndex(v, bi) {
    const counts = v.counts || [];
    let seen = -1;
    for (let j = 0; j < counts.length; j++) {
      if (counts[j] > 0) seen += 1;
      if (seen === bi) return j;
    }
    return -1;
  }

  // 球の帯（1球＝1マス、ブロックごとに色と下線）と、境目を1球ずつ動かすボタン
  function renderRun(body) {
    const v = T.run, box = body.querySelector("#tRun");
    if (!v || !box) return;
    const kindOf = {};
    (v.blocks || []).forEach((b, bi) => { for (const q of v.club_seqs) if (q >= b.seq_from && q <= b.seq_to) kindOf[q] = [b.kind, bi]; });
    const cells = v.club_seqs.map((q) => { const k = kindOf[q]; return `<button class="cell ${k ? "k-" + esc(k[0]) : "k-none"}" data-seq="${q}" aria-label="${q}球目の中身">${q}</button>`; }).join("");
    const tpl = v.template || [];
    const counts = v.counts || [];
    const rows = tpl.map((b, j) => {
      const blk = (v.blocks || []).find((x, bi) => blockIndex(v, bi) === j);
      const last = j === tpl.length - 1;
      const canR = last ? (v.unassigned_seqs || []).length > 0 : (counts[j + 1] || 0) > 0;
      return `<div class="brow k-${esc(b.kind)}"><span>${no(j)} ${esc(BLK[b.kind] || b.kind)}</span>
        <span class="caption">${blk ? `#${blk.seq_from}〜#${blk.seq_to}` : "—"}（${counts[j] ?? 0}球）</span>
        <span class="mv"><button class="iconbtn" data-mv="${j}" data-d="-1" ${counts[j] > 0 ? "" : "disabled"} aria-label="${no(j)}の終わりを1球前へ">${icon("chevron-left")}</button><button class="iconbtn" data-mv="${j}" data-d="1" ${canR ? "" : "disabled"} aria-label="${no(j)}の終わりを1球後ろへ">${icon("chevron-right")}</button></span></div>`;
    }).join("");
    const clubW = esc(App.clubJa(v.plan.club));
    const warn = !v.club_seqs.length ? `<div class="note warn">この記録に ${clubW} の球がまだありません。取り込んでから開き直してください。</div>`
      : v.mismatch ? `<div class="note warn" data-t="mismatch">予定は ${v.planned}球、${clubW}で打った球は ${v.club_seqs.length}球です。${(v.unassigned_seqs || []).length ? `余った ${v.unassigned_seqs.length}球（${v.unassigned_seqs.map((q) => "#" + q).join(" ")}）はどのブロックにも入っていません。` : ""}区切りを確かめてください。</div>` : "";
    const prov = v.blocks_source !== "saved";
    const acts = !prov ? `<button class="btn primary block" data-primary data-t="judge">判定する</button>`
      : v.mismatch ? `<button class="btn primary block" data-primary data-t="confirm">この区切りで確定する</button><button class="btn block" data-t="judge">このまま判定する</button>`
        : `<button class="btn primary block" data-primary data-t="judge">判定する</button><button class="btn block" data-t="confirm">この区切りで確定する</button>`;
    box.innerHTML = `<details class="block" data-t="blocksbox" ${T.eval ? "" : "open"}><summary class="textbtn">球の並びと区切り <span class="chip ${prov ? "warn" : "good"}" data-t="src">${prov ? "△ 予定の球数で区切った仮の区切り" : "✓ 確かめて保存した区切り"}</span></summary>
      ${warn}
      <div class="strip" data-t="strip">${cells}</div>
      <p class="caption">マスを押すと、その球の中身が開きます。</p>
      <div class="brows">${rows}</div></details>
      <div class="stack block" data-t="acts">${acts}</div>
      <div data-t="err"></div>`;
    if (T.eval) renderJudge(body); else body.querySelector("#tJudge").innerHTML = "";
  }

  async function putCounts(body, counts) {
    const r = await call("PUT", `/v1/plan-runs/${T.run.id}/blocks`, { counts });
    if (!r.ok) { body.querySelector("#tRun [data-t=err]").innerHTML = `<p class="fielderr">${esc(errText(r))}</p>`; return; }
    T.run = r.body; T.eval = null;
    renderRun(body);
  }
  async function moveBoundary(body, j, d) {
    const v = T.run, c = [...v.counts];
    const last = j === c.length - 1;
    if (d < 0) { if (c[j] <= 0) return; c[j] -= 1; if (!last) c[j + 1] += 1; }
    else if (last) { if (!(v.unassigned_seqs || []).length) return; c[j] += 1; }
    else { if (c[j + 1] <= 0) return; c[j] += 1; c[j + 1] -= 1; }
    await putCounts(body, c);
  }

  async function recordRun(body) {
    const st = practice(), p = plan();
    const sid = Number(body.querySelector("#tSess").value);
    const err = body.querySelector("#tRun [data-t=err]");
    if (!sid) { err.innerHTML = `<p class="fielderr">この練習の記録を選んでください</p>`; return; }
    const r = await call("POST", `/v1/plans/${p.id}/runs`, { session_id: sid, counts: st.counts });
    if (!r.ok) { err.innerHTML = `<p class="fielderr">${esc(errText(r))}</p>`; return; }
    T.run = r.body;
    // 予定と打った球の数が合っていれば、その区切りをそのまま保存する（仮の区切りのまま判定させない）
    const v0 = r.body;
    if (v0 && v0.blocks_source !== "saved" && !v0.mismatch && (v0.club_seqs || []).length) {
      const c = await call("PUT", `/v1/plan-runs/${v0.id}/blocks`, { counts: [...v0.counts] });
      if (c.ok) T.run = c.body;
    }
    st.done = true; st.recorded = sid; savePractice(st);
    await load();
    T.sessPick = sid;
    await renderSessions(body);
  }

  const NEXT = {
    same_template: "次の練習も同じ組み方で打ちます。",
    check_retention: "次は、何もしていない最初の球に残るかを見ます。",
    more_shots: "次は球を増やして打ちます。",
    confirm_blocks: "球の並びで区切りを確かめてください。",
  };

  async function judge(body) {
    const btn = body.querySelector("#tRun [data-t=judge]");
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = "判定しています…";
    const r = await call("GET", `/v1/plan-runs/${T.run.id}/evaluation`);
    btn.disabled = false; btn.textContent = label;
    if (!r.ok) {
      body.querySelector("#tRun [data-t=err]").innerHTML = App.errorHtml({ what: "判定できませんでした", saved: "練習の記録と区切りは保存してあります。",
        next: r.status === 503 || r.status === 0 ? "分析のサービスに届きません。電波のあるところでもう一度押してください。" : errText(r) });
      return;
    }
    T.eval = r.body.evaluation;
    renderRun(body);
  }

  // 判定の最初の面: 1回ぶんの言葉・状態の見出し・次の手の1行だけ。細かい文と数字は「なぜそう言える？」の中
  function renderJudge(body) {
    const ev = T.eval, box = body.querySelector("#tJudge");
    if (!ev || !box) return;
    const pr = ev.progress || {};
    const [rl, rc, rwhy] = plainOf(ev);
    const nextLine = pr.next_text || NEXT[pr.next_action] || "";
    const more = pr.state === "insufficient" && pr.more_shots ? `あと${lab("count", Number(pr.more_shots) + "球")}要ります。` : "";
    const provNote = ev.planned_mismatch && ev.blocks_provisional && pr.state !== "pending"
      ? `<p class="caption" data-t="provnote">※ 区切りをまだ確かめていません。下の並びを見て「この区切りで確定する」を押すと、判定し直します。</p>` : "";
    box.innerHTML = `<section class="card judge" data-t="judgeout" data-state="${esc(pr.state || "")}">
        <div class="jplain"><span class="chip ${rc}">${esc(rl)}</span>${rwhy ? ` <span class="sub" data-t="jwhy">${esc(rwhy)}</span>` : ""}</div>
        <p class="t-title" style="margin:var(--s2) 0">${esc(pr.title || "")}</p>
        ${more ? `<p data-t="more">${more}</p>` : ""}
        ${nextLine ? `<p data-t="next">次の手: ${esc(nextLine)}</p>` : ""}
        ${provNote}
        ${actionsHtml(pr)}
        <button class="textbtn" data-t="whyjudge">なぜそう言える？</button>
        ${(pr.video_checks || []).length ? `<details class="folded"><summary>動画で確かめること</summary><ul>${pr.video_checks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}
      </section>`;
    const acts = body.querySelector("#tRun [data-t=acts]");
    if (acts) acts.hidden = true;
  }
  function openJudgeWhy() {
    const ev = T.eval;
    if (!ev) return;
    const pr = ev.progress || {};
    App.openSheet({ title: "なぜそう言える？", label: "judgewhy", size: "full", html: `
      ${pr.text ? `<p>${esc(pr.text)}</p>` : ""}
      ${(ev.claims || []).map((c) => `<p class="claim" data-claim="${esc(c.id)}">${esc(c.text)}</p>`).join("")}
      ${(pr.notes || (pr.note ? [pr.note] : [])).map((n) => `<p class="claim">${esc(n)}</p>`).join("")}
      ${ev.blocks_provisional ? `<p class="caption">※ 区切りは予定の球数で区切ったままです${ev.planned_mismatch ? "（予定と打った球の数が違います）" : ""}。</p>` : ""}
      <p class="caption">「差が出た」は、同じ日に何も意識しない球と比べたものです。一回では偶然でも出るので、別の日にもう一度同じ結果が出てから「効いた」と言います。</p>
      <a class="btn block" href="#/progress">経過を見る</a>` });
  }

  // 次の手。プランの状態を変えるのは本人が押したときだけ
  function actionsHtml(pr) {
    const a = pr.next_action, adv = pr.advance && pr.advance.ok;
    const btn = (status, label, primary) => `<button class="btn block ${primary ? "primary" : ""}" data-close="${status}">${esc(label)}</button>`;
    const bs = [];
    if (adv || a === "recompute_candidates") bs.push(btn("done", "次に進む（このプランを閉じて、候補を計算し直す）", true));
    if (a === "alternate_template") bs.push(`<button class="btn primary block" data-alt="1">交互に打つ組み方で作り直す</button>`);
    if (a === "switch_drill" || a === "alternate_template") bs.push(btn("switched", "やり方を替える（このプランを閉じて、候補から選び直す）", a !== "alternate_template"));
    if (a === "video_or_coach") bs.push(btn("blocked", "動画かコーチへ（このプランを「詰まった」で閉じる）", true));
    if (a === "ask_continue") { bs.push(`<button class="btn primary block" data-cont="1">続ける（次の練習も同じ組み方で打つ）</button>`); bs.push(btn("abandoned", "やめる（このプランを閉じる）", false)); }
    return bs.length ? `<div class="stack block" data-t="nextacts">${bs.join("")}</div>` : "";
  }
  async function afterChange(body, r) {
    if (!r.ok) { const x = body.querySelector("#tRun [data-t=err]"); if (x) x.innerHTML = `<p class="fielderr">${esc(errText(r))}</p>`; return; }
    await App.render();
  }
  async function continuePlan(body) { afterChange(body, await call("POST", `/v1/plans/${plan().id}/continue`)); }
  async function alternatePlan(body) {
    const p = plan(), tr = p.trigger || {};
    if (!tr.session_id || !tr.scope_id) { afterChange(body, { ok: false, status: 400, body: { error: "きっかけの診断が分からないので作り直せません。候補から選び直してください。" } }); return; }
    const req = { from_session: tr.session_id, scope_id: tr.scope_id, candidate_id: p.issue, drill_id: p.drill_id || "", club: p.club,
      cue: p.cue, variant: "alternate", window: p.params && p.params.window ? { ok: true, ...p.params.window } : null };
    afterChange(body, await call("POST", `/v1/players/${T.pid}/plans?replace=1`, req));
  }
  async function closePlan(body, status) {
    const p = plan();
    const reason = { done: "次に進む条件を満たした", switched: "やり方を替える", blocked: "計測器の数字で見える範囲では詰まった", abandoned: "本人がやめた" }[status] || "";
    if (status === "abandoned" && !(await App.ask({ title: "このプランをやめますか？", text: "やめたプランは経過の「あなたの記録」に残ります。", ok: "やめる", cancel: "続けるか考える", danger: true }))) return;
    afterChange(body, await call("PATCH", `/v1/plans/${p.id}`, { status, close_reason: reason }));
  }


  // ==== 動きのプランの練習の結果（10球テスト。docs/DESIGN_v2.md §8.2・§10 P2） ====
  const FEEL = [["too_much", "やりすぎ気味"], ["too_little", "足りない気味"], ["just", "ちょうど"]];
  const PASS_HEAD = (ps) => (ps === true ? ["合格", "good"] : ps === false ? ["まだ", "none"] : ["判定できません", "warn"]);
  const MARK = { in_range: ["範囲の中", "in"], out_range: ["範囲の外", "out"], unknown: ["判定できない", "unk"] };

  function selfHtml(st) {
    const sr = st.self || {};
    const c = Number.isInteger(sr.count) ? sr.count : null;
    return `<section class="card block" data-t="self"><h2>打ち終えたら（動画を見る前に一回だけ）</h2>
      <p class="label" id="selfc">できたと思った回数</p>
      <div class="stepper" role="group" aria-labelledby="selfc"><button type="button" class="btn" data-self="-1" aria-label="一回減らす">−</button>
        <output data-t="selfcount" aria-live="polite">${c == null ? "—" : lab("count", c + "回")}</output>
        <button type="button" class="btn" data-self="1" aria-label="一回増やす">＋</button></div>
      <fieldset style="border:0;padding:0;margin:var(--s3) 0 0"><legend class="label">感じ</legend>
        ${FEEL.map(([k, w]) => `<label class="radio"><input type="radio" name="feel" value="${k}" ${sr.feel === k ? "checked" : ""}> <span>${w}</span></label>`).join("")}</fieldset>
      <p class="caption">答えなくても数えられます。答えると、感覚と実際のずれが分かります。</p></section>`;
  }

  function ballsHtml(res) {
    const marks = (res && res.marks) || [];
    const cells = [];
    for (let i = 0; i < 10; i++) {
      const m = marks[i];
      const [w, c] = m ? MARK[m.state] || MARK.unknown : ["撮っていない", "none"];
      cells.push(`<li class="ball ${c}" data-mark="${c}"><span class="visually-hidden">${w}</span></li>`);
    }
    return `<ol class="tenballs" data-t="balls" aria-label="十回の結果">${cells.join("")}</ol>
      <p class="caption" data-t="passline">塗った丸が範囲の中、×が範囲の外、点線は判定できなかった回。塗った丸が八つで合格です。</p>`;
  }

  function focusResultHtml(out) {
    const t = out.test || {}, res = t.result || {};
    const [head, tone] = PASS_HEAD(t.passed);
    const prev = out.previous;
    const pr = out.progress;
    const th = out.thumbs || {};
    const cmp = th.before && th.after ? `<div class="pair" data-t="beforeafter"><figure><img src="${esc(th.before)}" alt="前の同じコマ" loading="lazy"><figcaption class="caption">前 ${lab("p", th.p)}</figcaption></figure>
      <figure><img src="${esc(th.after)}" alt="今日の範囲に入ったコマ" loading="lazy"><figcaption class="caption">今日 ${lab("p", th.p)}</figcaption></figure></div>` : "";
    return `<section class="card block" data-focus-result="${t.passed === true ? "passed" : t.passed === false ? "not_yet" : "unknown"}">
      <p class="status"><b data-t="fhead">${esc(head)}</b> <span class="chip ${tone}">${t.block === "baseline" ? "いつも通りの撮影" : "練習の最後の撮影"}</span>${res.basis_label ? ` <span class="chip none" data-t="visual">${esc(res.basis_label)}</span>` : ""}</p>
      ${ballsHtml(res)}
      <p class="t-headline" data-t="fnext">${esc(res.next_text || "")}</p>
      ${prev ? `<p data-t="prev"><span class="label">前回</span> ${lab("date", App.dateJa(prev.date || "", false))} <span class="chip ${PASS_HEAD(prev.passed)[1]}">${esc(PASS_HEAD(prev.passed)[0])}</span></p>` : ""}
      ${cmp}
      ${pr ? `<div class="focuscard" data-t="mstate"><p style="margin:0"><b>${esc(pr.head || "")}</b> ${esc(pr.text || "")}</p><p class="sub" style="margin:var(--s1) 0 0">${esc(pr.next_text || "")}</p></div>` : ""}
      <button type="button" class="textbtn" data-t="fwhy">${icon("info")}なぜそう言える？</button></section>`;
  }

  function openFocusWhy(out) {
    const t = out.test || {}, res = t.result || {};
    const rows = [`<li>${esc(res.label || "")}</li>`];
    if (res.self_compare) rows.push(`<li>${esc(res.self_compare)}（一本ずつは突き合わせていません）</li>`);
    if (res.need_more) rows.push(`<li>合格か決めるには、判定できたスイングがあと${res.need_more}本要ります。合格に要る数は下げません。</li>`);
    if (res.same_side || res.opposite_side) rows.push(`<li>範囲の外の回のうち、前と同じ向き ${res.same_side}回・反対の向き ${res.opposite_side}回</li>`);
    rows.push(`<li>${esc(res.why || "")}</li>`);
    if (res.basis_label) rows.push(`<li>見た目の項目は AI が写真から選んだ答えで数えています。正しさはまだ測っていません。</li>`);
    if (out.progress && out.progress.why) rows.push(`<li>${esc(out.progress.why)}</li>`);
    rows.push(`<li>前と後の写真は、同じ向き・同じカタログの版のスイングだけを並べています。</li>`);
    App.openSheet({ title: "なぜそう言える？", label: "focuswhy", size: "half", html: `<ul class="parts">${rows.join("")}</ul>` });
  }

  async function renderMotionResult(body, p, alive) {
    const st = practice();
    const m = p.motion || {};
    body.innerHTML = `${offlineNote()}
      <p class="sub">${esc(planTitle(p))}</p>
      <div data-t="focusout"></div>
      ${selfHtml(st)}
      <section class="card block" data-t="count"><h2>動画で数える</h2>
        <label class="field"><span>この練習の記録</span><select id="tFSess"></select></label>
        <a class="btn block" data-t="addvideo" href="#/record">動画を入れる（${esc(m.view === "fo" ? "正面から" : "後ろから")}）</a>
        <button type="button" class="btn primary block" data-primary data-t="counttest">十球テストを数える</button>
        <button type="button" class="btn block" data-t="countbase">いつも通りの撮影として数える</button>
        <div data-t="ferr"></div>
        <p class="caption">いちばん新しい十本（同じ向き）を数えます。見た目でしか判断できない項目は、先にチェックの画面で見た目を評価してから数えます。</p></section>`;
    const sel = $("#tFSess", body);
    const ss = await App.sessions().catch(() => []);
    if (!alive()) return;
    const trig = p.trigger && p.trigger.session_id;
    const usable = ss.filter((x) => x.id !== trig);
    sel.innerHTML = usable.map((x) => `<option value="${x.id}">${esc(App.dateJa(x.date))}${x.location ? "・" + esc(x.location) : ""}</option>`).join("") || `<option value="">（記録がありません）</option>`;
    const today = usable.find((x) => x.date === App.localDate());
    if (today) sel.value = String(today.id);
    const link = () => {
      const x = usable.find((y) => String(y.id) === sel.value);
      $("[data-t=addvideo]", body).href = x ? `#/video/${x.date}?session=${x.id}` : "#/record";
    };
    link();
    sel.addEventListener("change", link);
    const outBox = $("[data-t=focusout]", body);
    const show = (out) => {
      outBox.innerHTML = focusResultHtml(out);
      $("[data-t=fwhy]", outBox).addEventListener("click", () => openFocusWhy(out));
    };
    // 今日もう数えていれば、その結果を最初から出す
    const tests = (T.data && T.data.motion && T.data.motion.tests) || [];
    const last = [...tests].reverse().find((x) => x.date === App.localDate());
    if (last) { const r = await call("GET", `/v1/focus-tests/${last.id}`); if (alive() && r.ok) show(r.body); }
    body.addEventListener("click", async (ev) => {
      const b = ev.target.closest("button");
      if (!b) return;
      if (b.dataset.self) {
        const cur = practice();
        const c = Number.isInteger((cur.self || {}).count) ? cur.self.count : 5;
        cur.self = { ...(cur.self || {}), count: Math.max(0, Math.min(10, c + Number(b.dataset.self))) };
        savePractice(cur);
        $("[data-t=selfcount]", body).innerHTML = lab("count", cur.self.count + "回");
        return;
      }
      if (b.dataset.t !== "counttest" && b.dataset.t !== "countbase") return;
      if (b.getAttribute("aria-disabled") === "true") return;
      const err = $("[data-t=ferr]", body);
      err.innerHTML = "";
      if (!sel.value) { err.innerHTML = `<p class="fielderr">記録を選んでください（動画を入れた日の記録）</p>`; return; }
      const cur = practice();
      const feel = (body.querySelector("input[name=feel]:checked") || {}).value;
      if (feel) { cur.self = { ...(cur.self || {}), feel }; savePractice(cur); }
      b.setAttribute("aria-disabled", "true");
      const r = await call("POST", "/v1/focus-tests", { plan_id: p.id, session_id: Number(sel.value), block: b.dataset.t === "countbase" ? "baseline" : "test", self_rating: cur.self || null });
      b.removeAttribute("aria-disabled");
      if (!alive()) return;
      if (!r.ok) { err.innerHTML = App.errorHtml({ what: "数えられませんでした", next: errText(r) }); return; }
      show(r.body);
      outBox.scrollIntoView({ block: "start", behavior: "smooth" });
    });
  }

  App.route("/practice", renderOverview, { tab: "practice" });
  App.route("/practice/run", renderRunning, { tab: "practice", noTabbar: true });
  App.route("/practice/result", renderResult, { tab: "practice" });
  return { load, plan: () => plan(), plainOf, planTitle, T, isMotion };
})();
