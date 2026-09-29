"use strict";
/*
  今日の練習とプラン（docs/DESIGN_coaching.md §8・§11 Phase 1b）。index.html の本体より先に読み、
  本体の部品（$・esc・api・S・fmt・LABEL・GRADE・BLK・fillBand・claimOf・openSheet）は呼ぶときに使う。

  - 圏外でも動く。プラン・型・ドリルは開いたときに localStorage に写し、ブロックの進み具合も
    localStorage にだけ置く（送るのは取り込んだあと、練習を記録するとき）。
  - 事実の文は作らない。判定・状態・次の手の文は分析サービスの定型文（claims・progress.title/text）をそのまま出す。
  - 1球ごとには押させない。押すのはブロックを変えたときと打ち直したときだけ（§8.4）。
  - ドリルは人が向きと安全を確かめたもの（checked_by）だけ。サーバーが絞ったうえで、ここでももう一度見る。
*/
const Today = (() => {
  const LS = {
    get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* 容量・プライベートモード */ } },
  };
  const K_PLAYER = "golf.player";
  const kToday = (pid) => `golf.today.${pid}`;
  const kPrac = (planId) => `golf.practice.${planId}`;
  const kDrills = (hand) => `golf.drills.${hand}`;
  const NO = ["⓪", "①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩", "⑪", "⑫"];
  const no = (i) => NO[i] || `(${i})`;

  // T.data は /v1/players/{id}/today の応答（圏外のときは写しておいたもの）
  const T = { pid: null, data: null, offline: false, savedAt: null, drill: null, run: null, eval: null, busy: false };

  const pad2 = (n) => String(n).padStart(2, "0");
  const localDate = () => { const d = new Date(); return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; };
  // 写した時刻は手元の時計で出す（toISOString のまま切ると UTC で、日本では9時間ずれる）
  const localTime = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`; };
  // 番手の表示（保存はそのまま。画面だけ日本語にする）
  const CLUB_KIND = { iron: "番アイアン", wood: "番ウッド", hybrid: "番ユーティリティ" };
  const clubJa = (c) => { const m = /^(\d+)\s*(Iron|Wood|Hybrid)$/i.exec(String(c || "").trim()); return m ? `${m[1]}${CLUB_KIND[m[2].toLowerCase()]}` : String(c || ""); };
  const plan = () => (T.data && T.data.plan) || null;
  const hand = () => (T.data && T.data.handedness === "L" ? "L" : "R");

  // 409 の existing_id を読むために、状態の番号と本文を両方返す取り方
  async function call(method, path, body) {
    const opt = { method, headers: {} };
    if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
    const r = await fetch(path, opt);
    const t = await r.text();
    let j = null;
    try { j = t ? JSON.parse(t) : null; } catch { /* JSON でない */ }
    return { status: r.status, ok: r.ok, body: j, text: t };
  }

  // ---- 練習の進み具合（localStorage だけ） ----
  function practice() {
    const p = plan();
    if (!p) return null;
    const tpl = p.template || [];
    let st = LS.get(kPrac(p.id));
    // 日が変われば新しい練習（型の段の数が変わっていても作り直す）
    if (!st || st.date !== localDate() || !Array.isArray(st.counts) || st.counts.length !== tpl.length) {
      // 前回が「足りない」なら、サーバーが出した次の型の球数で組む（§8.6「次の型の球数を増やす」）
      const nc = T.data && Array.isArray(T.data.next_counts) && T.data.next_counts.length === tpl.length ? T.data.next_counts : null;
      st = { plan_id: p.id, date: localDate(), idx: 0, done: false, counts: nc ? nc.slice() : tpl.map((b) => b.n) };
      LS.set(kPrac(p.id), st);
    }
    return st;
  }
  const savePractice = (st) => LS.set(kPrac(st.plan_id), st);

  // ブロックごとの「このクラブで何球目から何球目か」（打った順。TrackMan の球の番号と見比べる）
  function ranges(counts) {
    let at = 0;
    return counts.map((n) => { const r = n > 0 ? [at + 1, at + n] : null; at += n; return r; });
  }
  const rangeText = (r) => (r ? `${r[0]}〜${r[1]}球目` : "打たない");

  // ---- 読み込み ----
  async function load(player) {
    T.pid = player.id;
    LS.set(K_PLAYER, player.id);
    if (player.name) LS.set("golf.playerName", player.name);
    T.run = null; T.eval = null;
    try {
      const r = await call("GET", `/v1/players/${player.id}/today`);
      if (!r.ok) throw new Error((r.body && r.body.error) || r.statusText);
      T.data = r.body; T.offline = false; T.savedAt = new Date().toISOString();
      LS.set(kToday(player.id), { data: T.data, saved_at: T.savedAt });
    } catch {
      const c = LS.get(kToday(player.id));
      T.data = c ? c.data : null; T.savedAt = c ? c.saved_at : null; T.offline = true;
    }
    await loadDrill();
    return !!plan();
  }

  // 圏外で開き直したとき（選手の一覧も取れない）: 最後に開いた選手の写しで動く
  function offlineBoot() {
    const pid = LS.get(K_PLAYER);
    const c = pid != null ? LS.get(kToday(pid)) : null;
    if (!c || !c.data) return false;
    T.pid = pid; T.data = c.data; T.savedAt = c.saved_at; T.offline = true;
    const nm = LS.get("golf.playerName");
    if (nm) $("player").innerHTML = `<option value="${Number(pid)}">${esc(nm)}</option>`;
    const cd = LS.get(kDrills(hand()));
    T.drill = cd && plan() && plan().drill_id ? (cd.drills || []).find((d) => d.id === plan().drill_id && checked(d)) || null : null;
    return true;
  }

  const checked = (d) => !!(d && typeof d.checked_by === "string" && d.checked_by.trim());

  async function drills() {
    const h = hand();
    try {
      const r = await call("GET", `/v1/drills?handedness=${h}`);
      if (!r.ok) throw new Error();
      LS.set(kDrills(h), r.body);
      return r.body;
    } catch { return LS.get(kDrills(h)); }
  }

  async function loadDrill() {
    T.drill = null;
    const p = plan();
    if (!p || !p.drill_id) return;
    const cat = await drills();
    T.drill = ((cat && cat.drills) || []).find((d) => d.id === p.drill_id && checked(d)) || null;
  }

  // ドリルの置き方の図（分析サービスのファイル。形の検査はしてあるが、script・イベント・リンクは念のため外す）
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

  // ---- 描く ----
  // 窓の文の {band:lo} / {band:hi} は、作った時点で固定した窓（params.window）で埋める
  function fill(text) {
    const p = plan();
    const w = p && p.params && p.params.window;
    return fillBand(text, { band_shape: w ? { ok: true, window: { ok: true, ...w } } : null }, hand());
  }
  const planClaim = (suffix) => {
    const cs = (plan() && plan().rationale && plan().rationale.claims) || [];
    const c = cs.find((x) => x && typeof x.id === "string" && x.id.endsWith(suffix));
    return c ? c.text : null;
  };

  function drillHtml(p) {
    if (!p.drill_id) {
      return `<div class="muted" data-t="nodrill">確かめ済みのドリルはまだありません。自分で書いた「意識する1点」で進めます。</div>`;
    }
    const d = T.drill;
    if (!d) return `<div class="muted" data-t="nodrill">ドリル（${esc(p.drill_id)}）の中身を読めませんでした。「意識する1点」で進めます。</div>`;
    return `<details class="tdrill" data-t="drill"><summary><b>${esc(d.title)}</b>${d.coach_reviewed ? "" : ` <span class="chip c-none">コーチ未確認のドリル</span>`}${d.kind === "compensation" ? ` <span class="chip c-warn">補正（根本の直しではない）</span>` : ""} <span class="muted">やり方と置き方の図 ▾</span></summary>
      <div class="fig" data-t="drillfig"></div>
      <ol>${(d.steps || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ol>
      ${d.cue_drill ? `<div class="kv"><span>ドリル中</span>「${esc(d.cue_drill)}」</div>` : ""}
      ${d.safety ? `<div class="muted">⚠︎ ${esc(d.safety)}</div>` : ""}
      ${(d.video_checks || []).length ? `<details class="folded"><summary>動画で確かめること</summary><ul>${d.video_checks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}
    </details>`;
  }

  function lastHtml() {
    const ev = T.data.last_evaluation;
    if (!ev) return "";
    const pr = ev.progress || {};
    const [rl] = plainOf(ev);
    const prim = (ev.claims || []).find((c) => c.id === "run/primary");
    return `<div class="tlast" data-t="last"><span class="muted">前回</span> <b>${esc(rl)}</b>${pr.title ? `（${esc(pr.title)}）` : ""}
      ${prim ? `<details class="why"><summary>なぜそう言える？</summary><p class="claim">${ev.run && ev.run.date ? `${esc(ev.run.date)}: ` : ""}${esc(prim.text)}</p></details>` : ""}</div>`;
  }

  // 1回の練習の判定を、数字も専門用語も使わずに言う。言葉は分析サービスが決まった対応で作る（evaluation.plain）。
  // 良い判定でも「効いた」に数えない回は「良くなった」と言わない（R7）。plain の無い古い保存だけ、ここで同じ対応を引く
  const TONE = { good: "c-good", none: "c-none", bad: "c-bad", warn: "c-warn" };
  const RUN_PLAIN = {
    strong: ["今日ははっきり差が出ました", "c-good"], moderate: ["今日は差が出ました", "c-good"],
    weak: ["向きは良いですが、はっきりしません", "c-none"], none: ["今日は変わりませんでした", "c-none"],
    worse: ["今日は悪い向きでした", "c-bad"], insufficient: ["球が足りず、決められませんでした", "c-warn"],
  };
  const GOODG = (g) => g === "strong" || g === "moderate";
  function plainOf(ev) {
    if (ev && ev.plain && ev.plain.label) return [ev.plain.label, TONE[ev.plain.tone] || "c-none", ev.plain.why || ""];
    if (ev && GOODG(ev.grade) && ev.counts_as_worked === false) return ["良く見えましたが、今日は数えません", "c-warn", ""];
    const [l, c] = RUN_PLAIN[ev && ev.grade] || ["—", "c-none"];
    return [l, c, ""];
  }
  // 何をするかの言葉: 分析サービスが作った時点の言葉（trigger.plain）。無い古いプランは見出しのまま
  const planTitle = (p) => (p.trigger && p.trigger.plain && p.trigger.plain.title) || (p.trigger && p.trigger.title) || p.issue;

  function noPlanHtml() {
    let h = `<section class="tcard"><h2>今日の練習</h2><p>動いているプランはありません。セッションの「診断」→ ⑦「次にやること」の候補から「このプランで始める」を押すと、ここに今日の練習が出ます。</p>`;
    const r = S.report && S.report.report;
    const mains = r ? (r.scopes || []).filter((x) => x.kind === "main" && x.candidates && (x.candidates.candidates || []).length) : [];
    const items = [];
    for (const sc of mains) {
      // 候補の口（/plan-candidates）が読めていれば、その「いま」「次」を使う（「詰まった」で閉じた直しは後ろへ回る）
      const cc = candsOf(sc.scope_id);
      for (const which of ["now", "next"]) {
        const id = cc ? cc.candidates[which] : sc.candidates[which];
        const c = id && sc.candidates.candidates.find((x) => x.id === id);
        if (!c || c.kind === "measure" || items.length >= 2) continue;
        const gc = sc.gist && sc.gist.candidates && sc.gist.candidates[c.id];
        const what = (gc && gc.title) || (claimOf(sc, `next.${which}`) || "").replace(/^(いま|次):\s*/, "").split("。")[0];
        items.push(`<div class="cand"><div><b>${esc(sc.label)}</b> <span class="chip ${which === "now" ? "c-good" : "c-none"}">${which === "now" ? "いま" : "次"}</span> ${esc(what)}</div>
          <div class="row" style="margin-top:6px"><button data-act="startplan" data-scope="${esc(sc.scope_id)}" data-cand="${esc(c.id)}" data-which="${which}">このプランで始める</button></div></div>`);
      }
    }
    h += items.length ? `<h3>候補（診断したセッションから）</h3>${items.join("")}` : `<div class="row"><button class="ghost" data-t="godiag">診断を開く</button></div>`;
    return h + `</section>`;
  }

  // 候補の口（GET /v1/players/{id}/plan-candidates?session=）。この選手のプランと練習の記録を見て、
  // 詰まった直しを後ろへ回し、記録のあるドリルを先に置いたもの。読めなければ解説の候補のまま
  function candsOf(scopeId) {
    const c = T.cands;
    if (!c || !S.session || c.session !== S.session.id) return null;
    return (c.body.scopes || []).find((x) => x.scope_id === scopeId) || null;
  }
  async function loadCands(fresh) {
    if (!S.session || T.pid == null || T.offline) return null;
    if (!fresh && T.cands && T.cands.session === S.session.id) return T.cands;
    try {
      const r = await call("GET", `/v1/players/${T.pid}/plan-candidates?session=${S.session.id}`);
      if (r.ok && r.body) T.cands = { session: S.session.id, body: r.body };
    } catch { /* 読めなければ解説の候補のまま */ }
    return T.cands;
  }

  function render() {
    const out = $("todayOut");
    if (!out) return;
    document.body.classList.toggle("has-tbar", !!plan() && isOn() && !((practice() || {}).recorded));
    if (!T.data) {
      out.innerHTML = `<section class="tcard"><h2>今日の練習</h2><p class="muted">まだ一度も開いていないので、圏外では出せません。電波のあるところで一度開くと、練習場でも使えます。</p></section>`;
      $("tbar").hidden = true;
      return;
    }
    const off = T.offline ? `<div class="finding c-warn" data-t="offline">圏外です。${T.savedAt && localTime(T.savedAt) ? `${esc(localTime(T.savedAt))} に写した内容で動いています。` : ""}ブロックは進められます（送るのは取り込むときです）。</div>` : "";
    const p = plan();
    if (!p) {
      out.innerHTML = off + noPlanHtml(); $("tbar").hidden = true;
      if (!T.cands || !S.session || T.cands.session !== S.session.id) loadCands().then((c) => { if (c && isOn() && !plan()) render(); });
      return;
    }
    const st = practice();
    const tpl = p.template || [];
    const rg = ranges(st.counts);
    const title = planTitle(p);
    const why = p.trigger && p.trigger.plain && p.trigger.plain.why;
    // 今日の練習をもう記録していれば、その回（記録したあと「次の回」に進んで見えないように）。
    // 前の晩の練習を翌朝取り込むとセッションの日付は今日ではないので、記録したセッションでも探す
    const runsAll = (T.data.runs) || [];
    const todayRun = runsAll.find((r) => st.recorded && r.session_id === st.recorded) || runsAll.find((r) => r.date === localDate());
    const nth = todayRun ? todayRun.index + 1 : (T.data.next_index || 0) + 1;
    const blocks = tpl.map((b, i) => {
      const cls = st.done || i < st.idx ? "done" : i === st.idx ? "cur" : "";
      const extra = b.kind === "intervention" ? `<div class="bcue">「${esc(p.cue)}」</div>`
        : b.kind === "drill" && T.drill ? `<div class="bcue muted">${esc(T.drill.title)}</div>` : "";
      return `<li class="${cls} k-${esc(b.kind)}" data-i="${i}"><span class="bno">${no(i)}</span><span class="bk">${esc(BLK[b.kind] || b.kind)}</span><span class="bn">${st.counts[i]}球</span><span class="br muted">${rangeText(rg[i])}</span>${extra}</li>`;
    }).join("");
    const total = st.counts.reduce((a, b) => a + b, 0);
    const adv = planClaim(".advance"), guards = planClaim(".guards");
    out.innerHTML = `${off}
      <section class="tcard" data-t="head">
        <div class="row"><b>今日の練習</b><span class="muted" style="margin-left:auto">${esc(clubJa(p.club))}・${nth}回目</span></div>
        <div class="ttitle">${esc(title)}</div>
        ${lastHtml()}
        <div class="tcue"><span class="muted">本番で意識する1点</span><br>「${esc(p.cue)}」</div>
        ${drillHtml(p)}
        <details class="why" data-t="planwhy"><summary>なぜこれをやる？</summary>
          ${why ? `<p>${esc(why)}</p>` : ""}
          <p class="claim">${esc(p.hypothesis)}</p>
          ${adv ? `<p class="claim">${esc(adv)}</p>` : ""}
          ${guards && fill(guards) ? `<p class="claim">${esc(fill(guards))}</p>` : ""}
          <p class="muted">最後の「いつも通り」には効果が残って混ざるので、効いたぶんは小さく見えます。</p>
        </details>
      </section>
      <section class="tcard">
        <h3 style="margin-top:0">組み方（${esc(clubJa(p.club))}だけで ${total}球）</h3>
        ${T.data.next_counts_reason === "more_shots" && !todayRun ? `<p class="muted" data-t="more">前回は球が足りなかったので、いつも通りと本番の球を増やしています。</p>` : ""}
        <ol class="tblocks" id="tBlocks">${blocks}</ol>
        <p class="muted">球の番号は、このクラブで打った順です。打ち直したら下の「1球足す」を押します（1球ごとには押しません）。</p>
      </section>
      <section class="tcard" id="tAfter">
        <h3 style="margin-top:0">練習が終わったら</h3>
        <ol class="steps">
          <li><label class="muted tsess">この練習のセッションを選ぶ <select id="tSess"></select></label>
            <button class="ghost tbig" data-t="newsess" hidden>今日のセッションを作る</button></li>
          <li>計測器の球を、そのセッションに取り込む <button class="ghost tbig" data-t="goimport">取り込む画面へ</button></li>
          <li>球の並びでブロックの区切りを確かめて、判定する</li>
        </ol>
        <div id="tRun"></div>
      </section>
      <details class="tcard folded" id="tProg"><summary><b>進捗</b>（最初の「いつも通り」の推移）</summary><div id="tProgOut" class="muted">開くと読み込みます。</div></details>
      <details class="tcard folded" id="tRec"><summary><b>あなたの記録</b></summary><div id="tRecOut" class="muted">開くと読み込みます。</div></details>`;
    const fig = out.querySelector("[data-t=drillfig]");
    if (fig && T.drill) { const n = safeSvg(T.drill.diagram_svg); if (n) fig.appendChild(n); else fig.remove(); }
    renderBar(st);
    renderSessions();
  }

  function renderBar(st) {
    const p = plan(), bar = $("tbar");
    // 今日の練習を記録したら、下の大きな段は片づける（終わった作業の案内で画面を埋めない。
    // 「1つ戻る」で記録したあとの練習を未完了に戻せないようにもなる）
    if (!p || !isOn() || st.recorded) { bar.hidden = true; document.body.classList.remove("has-tbar"); return; }
    bar.hidden = false;
    document.body.classList.add("has-tbar");
    const tpl = p.template, rg = ranges(st.counts);
    const b = tpl[st.idx] || tpl[tpl.length - 1];
    if (st.done) {
      bar.querySelector(".now").innerHTML = `<b>練習はここまで</b> <span class="muted">取り込んで、ブロックを確かめます</span>`;
      bar.querySelector(".cueline").textContent = "";
      $("tNext").textContent = "取り込んで確かめる ↓";
    } else {
      bar.querySelector(".now").innerHTML = `<span class="muted">いま</span> <b>${no(st.idx)} ${esc(BLK[b.kind] || b.kind)} ${st.counts[st.idx]}球</b> <span class="muted">${rangeText(rg[st.idx])}</span>`;
      bar.querySelector(".cueline").textContent = b.kind === "intervention" ? `「${p.cue}」` : b.kind === "drill" && T.drill ? `ドリル中: 「${T.drill.cue_drill || T.drill.title}」` : b.kind === "baseline" ? "いつも通り（何も意識しない）" : "";
      const nx = tpl[st.idx + 1];
      $("tNext").textContent = nx ? `次のブロックへ（${no(st.idx + 1)} ${BLK[nx.kind] || nx.kind}）` : "最後のブロックを打ち終えた";
    }
    bar.querySelector("[data-t=minus]").disabled = st.done || st.counts[st.idx] <= 0;
    bar.querySelector("[data-t=plus]").disabled = st.done;
    bar.querySelector("[data-t=back]").disabled = !st.done && st.idx === 0;
  }

  // 色と音で知らせる（iOS では振動が使えない）
  let actx = null;
  function signal() {
    const bar = $("tbar");
    bar.classList.remove("flash"); void bar.offsetWidth; bar.classList.add("flash");
    try {
      actx = actx || new (window.AudioContext || window.webkitAudioContext)();
      const o = actx.createOscillator(), g = actx.createGain();
      o.frequency.value = 880; g.gain.value = 0.08;
      o.connect(g); g.connect(actx.destination); o.start(); o.stop(actx.currentTime + 0.12);
    } catch { /* 音が出せなくても進める */ }
  }

  // 画面が消えないように（使えないブラウザでは何もしない）
  let wake = null;
  async function keepAwake() {
    try { if (navigator.wakeLock && !wake) { wake = await navigator.wakeLock.request("screen"); wake.addEventListener("release", () => { wake = null; }); } } catch { wake = null; }
  }
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && isOn() && plan()) keepAwake(); });

  function barAction(act) {
    const st = practice();
    if (!st) return;
    const n = plan().template.length;
    if (act === "next") {
      if (st.done) { $("tAfter").scrollIntoView({ block: "start" }); return; }
      if (st.idx < n - 1) st.idx += 1; else st.done = true;
      signal();
    } else if (act === "plus") st.counts[st.idx] = Math.min(60, st.counts[st.idx] + 1);
    else if (act === "minus") st.counts[st.idx] = Math.max(0, st.counts[st.idx] - 1);
    else if (act === "back") { if (st.done) st.done = false; else if (st.idx > 0) st.idx -= 1; }
    savePractice(st);
    keepAwake();
    // 描き直すのはブロックの一覧と下の段だけ（練習が終わったあとの面は触らない）
    const tpl = plan().template, rg = ranges(st.counts);
    for (const li of document.querySelectorAll("#tBlocks li")) {
      const i = Number(li.dataset.i);
      li.className = `${st.done || i < st.idx ? "done" : i === st.idx ? "cur" : ""} k-${tpl[i].kind}`;
      li.querySelector(".bn").textContent = `${st.counts[i]}球`;
      li.querySelector(".br").textContent = rangeText(rg[i]);
    }
    renderBar(st);
    // いまのブロックが下の固定の段の裏に隠れないように
    const cur = document.querySelector("#tBlocks li.cur");
    if (act === "next" && cur && cur.scrollIntoView) cur.scrollIntoView({ block: "center" });
  }

  // ---- 練習が終わったら: 記録・割り当て・判定 ----
  function renderSessions_(pick) { T.sessPick = pick; renderSessions(); if (typeof window.renderSessions === "function") window.renderSessions(S.session && S.session.id); }
  function renderSessions() {
    const sel = $("tSess");
    if (!sel) return;
    const ss = (S.sessions && S.sessions.length ? S.sessions : []);
    const runs = (T.data && T.data.runs) || [];
    if (!ss.length) { sel.innerHTML = `<option value="">（${T.offline ? "圏外なので読めません" : "セッションがありません"}）</option>`; $("tRun").innerHTML = ""; return; }
    // きっかけの診断のセッションと、プランを作る前の日付で球の入ったセッションは練習にできない（サーバーも断る）
    const p0 = plan();
    const trig0 = p0 && p0.trigger && p0.trigger.session_id;
    const usable = (s) => s.id !== trig0;
    sel.innerHTML = ss.filter(usable).map((s) => `<option value="${s.id}">${esc(s.date)}${s.location ? "・" + esc(s.location) : ""}${runs.some((r) => r.session_id === s.id) ? " ✓記録済み" : ""}</option>`).join("");
    // 既定: 記録した練習のうち今日のもの → 今日の日付のセッション → プランを作ったあとのいちばん新しいもの。
    // きっかけの診断のセッション（プランを作る前の球）は既定にしない
    const today = ss.find((s) => s.date === localDate());
    const p = plan();
    const trig = p.trigger && p.trigger.session_id;
    const since = (p.created_at || "").slice(0, 10);
    const after = ss.find((s) => s.id !== trig && s.date >= since);
    const recorded = runs.length ? ss.find((s) => s.id === runs[runs.length - 1].session_id && s.date === localDate()) : null;
    const nb = $("todayOut").querySelector("[data-t=newsess]");
    if (nb) nb.hidden = !!today;
    const pickS = T.sessPick && ss.some((s) => s.id === T.sessPick) ? { id: T.sessPick } : (recorded || today || after);
    const gi = $("todayOut").querySelector("[data-t=goimport]");
    if (!pickS) {
      sel.insertAdjacentHTML("afterbegin", `<option value="">（選んでください）</option>`);
      sel.value = "";
      if (gi) gi.disabled = true; // 選ぶ前に取り込むと、前に選んでいた（診断の）セッションに入ってしまう
      $("tRun").innerHTML = `<p class="muted">この練習のセッションがまだありません。「今日のセッションを作る」を押してから、球を取り込みます。</p>`;
      return;
    }
    if (gi) gi.disabled = false;
    sel.value = String(pickS.id);
    showRunFor(Number(sel.value));
  }

  async function showRunFor(sid) {
    T.sessPick = sid;
    const box = $("tRun");
    if (!box) return;
    const r0 = ((T.data && T.data.runs) || []).find((r) => r.session_id === sid);
    T.eval = null;
    if (!r0) {
      const st = practice();
      box.innerHTML = `<p class="muted">このセッションには、まだこのプランの練習を記録していません。いまの組み方（${st.counts.map((n, i) => `${no(i)}${n}`).join("・")}）で区切ります。あとで球の並びで直せます。</p>
        <div class="row"><button data-t="record">この練習を記録する</button></div><div class="err" data-t="err"></div>`;
      return;
    }
    box.innerHTML = `<p class="muted">読み込み中…</p>`;
    const r = await call("GET", `/v1/plan-runs/${r0.id}`);
    if (!r.ok) { box.innerHTML = `<p class="err">${esc((r.body && r.body.error) || "読み込めませんでした")}</p>`; return; }
    T.run = r.body;
    renderRun();
  }

  // 球の帯（1球＝1マス、ブロックごとに色）と、境目を1球ずつ動かすボタン
  function renderRun() {
    const v = T.run, box = $("tRun");
    if (!v || !box) return;
    const kindOf = {};
    (v.blocks || []).forEach((b, bi) => { for (const q of v.club_seqs) if (q >= b.seq_from && q <= b.seq_to) kindOf[q] = [b.kind, bi]; });
    const cells = v.club_seqs.map((q) => { const k = kindOf[q]; return `<button class="cell ${k ? "k-" + esc(k[0]) : "k-none"}" data-seq="${q}" title="#${q}">${q}</button>`; }).join("");
    const tpl = v.template || [];
    const counts = v.counts || [];
    // 段ごとの行（段 j の「終わりの境目」を動かす。◀ は最後の1球を次の段へ、▶ は次の段の最初の1球をもらう）
    const rows = tpl.map((b, j) => {
      const blk = (v.blocks || []).find((x, bi) => blockIndex(v, bi) === j);
      const last = j === tpl.length - 1;
      const canR = last ? (v.unassigned_seqs || []).length > 0 : (counts[j + 1] || 0) > 0;
      return `<div class="brow k-${esc(b.kind)}"><span class="sw"></span><span>${no(j)} ${esc(BLK[b.kind] || b.kind)}</span>
        <span class="muted">${blk ? `#${blk.seq_from}〜#${blk.seq_to}` : "—"}（${counts[j] ?? 0}球）</span>
        <span class="mv"><button class="ghost" data-mv="${j}" data-d="-1" ${counts[j] > 0 ? "" : "disabled"} aria-label="${no(j)}の終わりを1球前へ">◀</button><button class="ghost" data-mv="${j}" data-d="1" ${canR ? "" : "disabled"} aria-label="${no(j)}の終わりを1球後ろへ">▶</button></span></div>`;
    }).join("");
    const warn = !v.club_seqs.length ? `<div class="finding c-warn">このセッションに ${esc(clubJa(v.plan.club))} の球がまだありません。取り込んでから開き直してください。</div>`
      : v.mismatch ? `<div class="finding c-warn" data-t="mismatch">予定は ${v.planned}球、${esc(clubJa(v.plan.club))}で打った球は ${v.club_seqs.length}球です。${(v.unassigned_seqs || []).length ? `余った ${v.unassigned_seqs.length}球（${v.unassigned_seqs.map((q) => "#" + q).join(" ")}）はどのブロックにも入っていません。` : ""}区切りを確かめてください。</div>` : "";
    // 区切りが仮のまま（予定と打った数が合わない）なら、先に区切りを確かめるほうを主のボタンにする
    const prov = v.blocks_source !== "saved";
    const acts = !prov ? `<button data-t="judge" class="big2">判定する</button>`
      : v.mismatch ? `<button data-t="confirm" class="big2">この区切りで確定する</button><button class="ghost" data-t="judge">このまま判定する</button>`
        : `<button data-t="judge" class="big2">判定する</button><button class="ghost" data-t="confirm">この区切りで確定する</button>`;
    box.innerHTML = `<div class="row"><b>球の並び</b> <span class="chip ${prov ? "c-warn" : "c-good"}" data-t="src">${prov ? "予定の球数で区切った仮の区切り" : "確かめて保存した区切り"}</span></div>
      ${warn}
      <div class="strip" data-t="strip">${cells}</div>
      <p class="muted">マスを押すと、その球の中身が開きます。</p>
      <div class="brows">${rows}</div>
      <div class="row" style="margin-top:8px">${acts}</div>
      <div class="err" data-t="err"></div>
      <div id="tJudge"></div>`;
    if (T.eval) renderJudge();
  }

  // 保存したブロックの i 番目が、型の何段目か（0球の段は飛ばして並ぶ）
  function blockIndex(v, bi) {
    const counts = v.counts || [];
    let seen = -1;
    for (let j = 0; j < counts.length; j++) {
      if (counts[j] > 0) seen += 1;
      if (seen === bi) return j;
    }
    return -1;
  }

  async function putCounts(counts) {
    const box = $("tRun");
    const r = await call("PUT", `/v1/plan-runs/${T.run.id}/blocks`, { counts });
    if (!r.ok) { box.querySelector("[data-t=err]").textContent = (r.body && r.body.error) || r.statusText; return; }
    T.run = r.body; T.eval = null;
    renderRun();
  }

  async function moveBoundary(j, d) {
    const v = T.run, c = [...v.counts];
    const last = j === c.length - 1;
    if (d < 0) { if (c[j] <= 0) return; c[j] -= 1; if (!last) c[j + 1] += 1; }
    else if (last) { if (!(v.unassigned_seqs || []).length) return; c[j] += 1; }
    else { if (c[j + 1] <= 0) return; c[j] += 1; c[j + 1] -= 1; }
    await putCounts(c);
  }

  async function recordRun() {
    const box = $("tRun"), st = practice(), p = plan();
    const sid = Number($("tSess").value);
    const r = await call("POST", `/v1/plans/${p.id}/runs`, { session_id: sid, counts: st.counts });
    if (!r.ok) { box.querySelector("[data-t=err]").textContent = (r.body && r.body.error) || r.statusText; return; }
    T.run = r.body;
    // 予定と打った球の数が合っていれば、その区切りをそのまま保存する（仮の区切りのまま判定させない）。
    // 合わないときだけ、球の並びで確かめてもらう
    const v0 = r.body;
    if (v0 && v0.blocks_source !== "saved" && !v0.mismatch && (v0.club_seqs || []).length) {
      const c = await call("PUT", `/v1/plan-runs/${v0.id}/blocks`, { counts: [...v0.counts] });
      if (c.ok) T.run = c.body;
    }
    // 記録したら、その日の練習は終わり（下の「次のブロックへ」を片づける）
    st.done = true; st.recorded = sid; savePractice(st);
    await load({ id: T.pid });
    T.sessPick = sid;
    render();
  }

  // 次の手の1行。分析サービスの progress.next_text をそのまま出す（無い古い保存だけここで引く）
  const NEXT = {
    same_template: "次の練習も同じ組み方で打ちます。",
    check_retention: "次は、何もしていない最初の球に残るかを見ます。",
    more_shots: "次は球を増やして打ちます。",
    confirm_blocks: "球の並びで区切りを確かめてください。",
  };

  async function judge(refresh) {
    const box = $("tRun");
    const btn = box.querySelector("[data-t=judge]");
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = "判定しています…";
    const r = await call("GET", `/v1/plan-runs/${T.run.id}/evaluation${refresh ? "?refresh=1" : ""}`);
    btn.disabled = false; btn.textContent = label;
    if (!r.ok) { box.querySelector("[data-t=err]").textContent = r.status === 503 ? "分析サービスに届きません。電波のあるところでもう一度押してください。" : (r.body && r.body.error) || r.statusText; return; }
    T.eval = r.body.evaluation;
    renderJudge();
    loadProgress();
  }

  // 判定の最初の面: 1回ぶんの言葉・状態の見出し・次の手の1行だけ（同じことを2回言わない）。
  // 数字は球数（あと◯球）だけ。細かい文と数字は「なぜそう言える？」の中
  function renderJudge() {
    const ev = T.eval, box = $("tJudge");
    if (!ev || !box) return;
    const pr = ev.progress || {};
    const [rl, rc, rwhy] = plainOf(ev);
    const nextLine = pr.next_text || NEXT[pr.next_action] || "";
    const more = pr.state === "insufficient" && pr.more_shots ? `あと ${Number(pr.more_shots)}球要ります。` : "";
    const provNote = ev.planned_mismatch && ev.blocks_provisional && pr.state !== "pending"
      ? `<p class="muted" data-t="provnote">※ 区切りをまだ確かめていません。上の並びを見て「この区切りで確定する」を押すと、判定し直します。</p>` : "";
    box.innerHTML = `<div class="judge" data-t="judgeout" data-state="${esc(pr.state || "")}">
        <div class="jplain"><span class="chip ${rc}">${esc(rl)}</span>${rwhy ? ` <span class="muted" data-t="jwhy">${esc(rwhy)}</span>` : ""}</div>
        <div class="row"><b class="jt">${esc(pr.title || "")}</b></div>
        ${more ? `<p class="kv" data-t="more">${esc(more)}</p>` : ""}
        ${nextLine ? `<p class="kv" data-t="next">次の手: ${esc(nextLine)}</p>` : ""}
        ${provNote}
        ${actionsHtml(pr)}
        <details class="why" data-t="judgewhy"><summary>なぜそう言える？</summary>
          ${pr.text ? `<p>${esc(pr.text)}</p>` : ""}
          ${(ev.claims || []).map((c) => `<p class="claim" data-claim="${esc(c.id)}">${esc(c.text)}</p>`).join("")}
          ${(pr.notes || (pr.note ? [pr.note] : [])).map((n) => `<p class="claim">${esc(n)}</p>`).join("")}
          ${ev.blocks_provisional ? `<p class="muted">※ 区切りは予定の球数で区切ったままです${ev.planned_mismatch ? "（予定と打った球の数が違います）" : ""}。</p>` : ""}
          <p class="muted">「差が出た」は、同じ日に何も意識しない球と比べたものです。一回では偶然でも出るので、別の日にもう一度同じ結果が出てから「効いた」と言います。</p>
        </details>
        ${(pr.video_checks || []).length ? `<details class="why"><summary>動画で確かめること</summary><ul>${pr.video_checks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}
      </div>`;
  }

  // 次の手。プランの状態（plans.status）を変えるのは本人が押したときだけ（§8.6「続けるか本人に選ばせる」）
  function actionsHtml(pr) {
    const a = pr.next_action, adv = pr.advance && pr.advance.ok;
    const btn = (status, label, ghost) => `<button class="${ghost ? "ghost" : ""}" data-close="${status}">${esc(label)}</button>`;
    const bs = [];
    if (adv || a === "recompute_candidates") bs.push(btn("done", "次に進む（このプランを閉じて、候補を計算し直す）"));
    // 「移せていない」: 同じ直し・同じドリル・同じ番手で、ドリルと本番を1球ずつ交互に打つ組み方に作り直す
    if (a === "alternate_template") bs.push(`<button data-alt="1">交互に打つ組み方で作り直す</button>`);
    if (a === "switch_drill" || a === "alternate_template") bs.push(btn("switched", "やり方を替える（このプランを閉じて、候補から選び直す）", a === "alternate_template"));
    if (a === "video_or_coach") bs.push(btn("blocked", "動画かコーチへ（このプランを「詰まった」で閉じる）"));
    if (a === "ask_continue") { bs.push(`<button data-cont="1">続ける（次の練習も同じ組み方で打つ）</button>`); bs.push(btn("abandoned", "やめる（このプランを閉じる）", true)); }
    return bs.length ? `<div class="row">${bs.join("")}</div>` : "";
  }

  // 「止める」のあと「続ける」: その時点の最後の練習より後の回だけで、崩れを数え直す
  async function continuePlan() {
    const r = await call("POST", `/v1/plans/${plan().id}/continue`);
    if (!r.ok) { alert((r.body && r.body.error) || r.statusText); return; }
    await load({ id: T.pid });
    render();
  }

  // 「移せていない」の次の型（§8.6）: きっかけの診断・同じ直し・同じドリル・同じ番手で作り直す（前のプランは「替えた」）
  async function alternatePlan() {
    const p = plan(), tr = p.trigger || {};
    if (!tr.session_id || !tr.scope_id) { alert("きっかけの診断が分からないので作り直せません。候補から選び直してください。"); return; }
    const body = { from_session: tr.session_id, scope_id: tr.scope_id, candidate_id: p.issue, drill_id: p.drill_id || "", club: p.club,
      cue: p.cue, variant: "alternate", window: p.params && p.params.window ? { ok: true, ...p.params.window } : null };
    const r = await call("POST", `/v1/players/${T.pid}/plans?replace=1`, body);
    if (!r.ok) { alert((r.body && r.body.error) || r.statusText); return; }
    await load({ id: T.pid });
    render();
  }

  async function closePlan(status) {
    const p = plan();
    const reason = { done: "次に進む条件を満たした", switched: "やり方を替える", blocked: "計測器の数字で見える範囲では詰まった", abandoned: "本人がやめた" }[status] || "";
    const r = await call("PATCH", `/v1/plans/${p.id}`, { status, close_reason: reason });
    if (!r.ok) { alert((r.body && r.body.error) || r.statusText); return; }
    await load({ id: T.pid });
    render();
  }

  // ---- 進捗（最初の「いつも通り」の推移。物差しはプランの1回目の A） ----
  const VAL = { reduce_abs: ["mean_abs", "ずれの大きさの平均"], reduce_sd: ["sd", "ばらつき"], increase: ["mean", "平均"], decrease: ["mean", "平均"] };

  async function loadProgress() {
    const out = $("tProgOut");
    if (!out || !plan()) return;
    const r = await call("GET", `/v1/plans/${plan().id}/progress`);
    if (!r.ok) { out.innerHTML = `<p class="err">${esc((r.body && r.body.error) || "読み込めませんでした")}</p>`; return; }
    const p = r.body.plan, runs = r.body.runs || [];
    const [key, label] = VAL[p.goal] || ["mean", "平均"];
    const latest = r.body.latest && r.body.latest.progress;
    // 物差しはサーバーが決めた回（最初の「いつも通り」が5球以上測れた最初の回。1回目とは限らない）
    const refRun = latest && latest.reference_run != null ? latest.reference_run : null;
    const pts = runs.map((x, i) => {
      const e = x.evaluation || {}, fa = e.first_a || {}, b = (e.stats && e.stats.B) || {};
      return { i, date: x.date, a: (fa.measured ?? fa.n ?? 0) >= 5 ? fa[key] : null, an: fa.measured ?? fa.n ?? 0, b: (b.n || 0) >= 5 ? b[key] : null, bn: b.n || 0,
        ev: e, fresh: x.fresh, ref: refRun != null && x.run_id === refRun };
    });
    // 進捗の面でだけ言う文（判定の面と同じ文を繰り返さない）
    let h = latest ? `<p data-t="progplain"><b>${esc(latest.title || "")}</b> ${esc(latest.text || "")}</p>` : `<p class="muted">まだ判定した練習がありません。</p>`;
    h += `<details class="why" data-t="progwhy"><summary>なぜそう言える？（推移の図と数字）</summary>`;
    h += progressSvg(pts, p.target_metric, label);
    h += `<div class="scroll"><table data-t="trend"><tr><th class="l">練習</th><th>最初の「いつも通り」</th><th>本番</th><th class="l">その日</th></tr>${pts.map((x) => {
      const [gl, gc] = plainOf(x.ev);
      return `<tr><td class="l">${x.i + 1}回目 ${esc(x.date)}${x.ref ? "（物差し）" : ""}</td><td>${x.a == null ? "—" : fmt(p.target_metric, x.a)}（${x.an}球）</td><td>${x.b == null ? "—" : fmt(p.target_metric, x.b)}（${x.bn}球）</td><td class="l"><span class="chip ${gc}">${esc(gl)}</span>${x.fresh === false ? " <span class=\"muted\">保存した判定</span>" : ""}</td></tr>`;
    }).join("")}</table></div>`;
    // 週ごとの数字（いつも通りの球だけ。5球未満の週は出さない。§8.6）
    const wk = (latest && latest.weekly) || [];
    if (wk.length) {
      h += `<div class="scroll"><table data-t="weekly"><tr><th class="l">週</th><th>いつも通りの${esc(label)}</th></tr>${wk.map((w) =>
        `<tr><td class="l">${esc(w.from || "")}〜${esc(w.to || "")}</td><td>${w.shown && w.value != null ? `${fmt(p.target_metric, w.value)}（${Number(w.n)}球）` : `—（${Number(w.n)}球。5球未満）`}</td></tr>`).join("")}</table></div>
        <p class="muted">週ごとの数字は、練習の「いつも通り」の球だけで数えます（意識して打った本番の球は良く出るので入れません。プランの外で打った球はまだ数えていません）。</p>`;
    }
    h += `<p class="muted">点は、その日の最初の「いつも通り」の${esc(LABEL[p.target_metric] || p.target_metric)}（${esc(label)}）。点線が物差しの回${refRun != null ? "" : "（まだありません）"}。薄い点は意識して打った本番で、良く出るので物差しにはしません。5球未満の回は点を打ちません。</p></details>`;
    out.innerHTML = h;
  }

  function progressSvg(pts, metric, label) {
    const vals = pts.flatMap((x) => [x.a, x.b]).filter((v) => v != null);
    if (!vals.length) return `<p class="muted">まだ点を打てる練習がありません。</p>`;
    const W = 360, H = 200, L = 52, R = 12, Tp = 14, B = 34;
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi - lo < 1e-9) { lo -= Math.abs(lo) * 0.2 + 1e-3; hi += Math.abs(hi) * 0.2 + 1e-3; }
    const pad = (hi - lo) * 0.15; lo -= pad; hi += pad;
    const n = pts.length;
    const x = (i) => (n === 1 ? (L + W - R) / 2 : L + (i * (W - L - R)) / (n - 1));
    const y = (v) => Tp + ((hi - v) * (H - Tp - B)) / (hi - lo);
    const ns = "http://www.w3.org/2000/svg";
    let s = `<svg xmlns="${ns}" viewBox="0 0 ${W} ${H}" role="img"><title>最初の「いつも通り」の推移</title><desc>${esc(label)}を練習の回ごとに打った図</desc>`;
    s += `<line class="g-axis" x1="${L}" y1="${H - B}" x2="${W - R}" y2="${H - B}"/><line class="g-axis" x1="${L}" y1="${Tp}" x2="${L}" y2="${H - B}"/>`;
    for (const v of [lo + pad, (lo + hi) / 2, hi - pad]) s += `<text class="g-sub t11" x="${L - 4}" y="${y(v) + 4}" text-anchor="end">${esc(fmt(metric, v))}</text>`;
    const ref = pts.find((p) => p.a != null && p.ref);
    if (ref) s += `<line class="g-diag" x1="${L}" y1="${y(ref.a)}" x2="${W - R}" y2="${y(ref.a)}"/>`;
    const line = pts.filter((p) => p.a != null).map((p) => `${x(p.i)},${y(p.a)}`).join(" ");
    if (line.includes(" ")) s += `<polyline class="g-flight g-good" style="fill:none" points="${line}"/>`;
    for (const p of pts) {
      s += `<text class="g-sub t11" x="${x(p.i)}" y="${H - B + 16}" text-anchor="middle">${p.i + 1}回目</text>`;
      if (p.b != null) s += `<circle class="g-miss hollow" data-t="bpt" cx="${x(p.i)}" cy="${y(p.b)}" r="4"/>`;
      if (p.a != null) s += `<circle class="g-good" data-t="apt" cx="${x(p.i)}" cy="${y(p.a)}" r="5"/>`;
    }
    return `<figure class="fig" data-t="progfig">${s}</svg></figure>`;
  }

  // ---- あなたの記録（件数だけ。割合は出さない） ----
  const STATUS = { active: ["動いている", "c-good"], done: ["次に進んだ", "c-info"], switched: ["替えた", "c-none"], blocked: ["詰まった", "c-warn"], abandoned: ["やめた", "c-none"] };
  async function loadRecord() {
    const out = $("tRecOut");
    if (!out || T.pid == null) return;
    const r = await call("GET", `/v1/players/${T.pid}/record`);
    if (!r.ok) { out.innerHTML = `<p class="err">${esc((r.body && r.body.error) || "読み込めませんでした")}</p>`; return; }
    const rows = r.body || [];
    if (!rows.length) { out.innerHTML = `<p class="muted">まだ記録がありません。</p>`; return; }
    out.innerHTML = rows.map((x) => {
      const p = x.plan, [sl, sc] = STATUS[p.status] || [p.status, "c-none"];
      const ev = x.runs.filter((y) => y.evaluated);
      // 「良くなった回」は効いたに数えた回だけ（良い判定でも数えなかった回は入れない）
      const good = ev.filter((y) => y.counts_as_worked).length;
      return `<div class="cand" data-t="rec"><div><b>${esc(planTitle(p))}</b> <span class="chip ${sc}">${esc(sl)}</span>${x.state_title ? ` <span class="chip c-none">${esc(x.state_title)}</span>` : ""}</div>
        <div class="kv"><span>やり方</span>${p.drill_id ? esc(p.drill_id) : `自分で書いた1点「${esc(p.cue)}」`}・${esc(clubJa(p.club))}</div>
        <div class="kv"><span>練習</span>${x.runs.length}回（判定した ${ev.length}回のうち、良くなった回 ${good}）</div>
        <details class="why"><summary>日ごとの判定</summary>${ev.map((y) => { const [rl, rc] = plainOf(y); return `<div>${esc(y.date)} <span class="chip ${rc}">${esc(rl)}</span></div>`; }).join("") || "—"}</details></div>`;
    }).join("") + `<p class="muted">同じ直しで次のやり方を選ぶときは、記録のあるやり方を先に試します。</p>`;
  }

  // ---- 候補からプランを作る（⑦「このプランで始める」） ----
  const confirmed = new Set();
  async function startPlan(sc, cand, which) {
    const key = `${sc.scope_id}/${cand.id}`;
    // 候補の口（記録つき。開くたびに取り直す ―― 前のプランの記録が変わっているかもしれない）。読めなければ解説の候補のまま
    await loadCands(true);
    const cc = candsOf(sc.scope_id);
    const cx = cc ? (cc.candidates.candidates || []).find((x) => x.id === cand.id) : null;
    if (cx && cx.history && cx.history.blocked && !confirmed.has(key + "/blocked")) {
      if (!confirm("この直しは、前のプランで「詰まった」ものです（計測器の数字で見える範囲ではここまで、としました）。それでももう一度始めますか？")) return;
      confirmed.add(key + "/blocked");
    }
    if ((cand.blocked_by || []).length && !confirmed.has(key)) {
      const g0 = sc.gist && sc.gist.candidates && sc.candidates && sc.gist.candidates[sc.candidates.now];
      const firstT = (g0 && g0.title) || (claimOf(sc, "next.now") || "").replace(/^いま:\s*/, "").split("。")[0] || "ほかの候補";
      if (!confirm(`この候補は「${firstT}」が先、としたものです（理由は要点のとおり）。先にこちらを始めると、結果を読み分けにくくなります。それでも始めますか？`)) return;
      confirmed.add(key);
    }
    const gc = sc.gist && sc.gist.candidates && sc.gist.candidates[cand.id];
    const what = (gc && gc.title) || (claimOf(sc, `next.${which}`) || "").replace(/^(いま|次):\s*/, "").split("。")[0];
    const sheet = $("planSheet");
    const d = cand.design || {};
    sheet.innerHTML = `<div class="row"><b>このプランで始める</b><span style="margin-left:auto"></span><button class="ghost small" data-ps="close">閉じる</button></div>
      <p style="margin:6px 0" data-ps="what"><b>${esc(what || cand.id)}</b></p>
      ${gc && gc.why ? `<p class="muted" style="margin:4px 0">${esc(gc.why)}</p>` : ""}
      <div class="muted">${esc(sc.label)}${cand.club && cand.club.club ? `・${esc(clubJa(cand.club.club))}で打つ` : ""}<span data-ps="total"></span></div>
      <div data-ps="drills" style="margin-top:8px" class="muted">ドリル集を読み込み中…</div>
      <label class="muted" style="display:block;margin-top:8px">本番で意識する1点
        <input data-ps="cue" maxlength="120" placeholder="例: 向こうにボールがあるつもりで打つ" style="width:100%"></label>
      <div class="row" style="margin-top:10px"><button data-ps="go" class="big2">プランを作る</button></div>
      <div class="err" data-ps="err"></div>`;
    sheet.dataset.scope = sc.scope_id; sheet.dataset.cand = cand.id;
    sheet.hidden = false;
    // 意識する1点の初期値: 要点が「意識する一点」と言った文（数字・専門用語の検査を通ったもの）。直せる
    const cueIn = sheet.querySelector("[data-ps=cue]");
    const g = sc.gist || {};
    if (g.focus && g.focus.candidate_id === cand.id) {
      const act = (g.blocks || []).find((b) => b.id === "action");
      const ln = act && (act.lines || []).find((x) => /^意識する一点: /.test(x.text || ""));
      if (ln) cueIn.value = ln.text.replace(/^意識する一点: /, "").replace(/^「(.*)」$/, "$1").replace(/。$/, "");
    }
    // ドリル: 候補の口が選んだもの（確かめ済み・この範囲のクラブの種類に当てられる・記録のあるものが先）。
    // 読めなければドリル集から絞る（圏外）
    let list;
    if (cx && cx.drills) {
      list = (cx.drills.drills || []).filter(checked);
    } else {
      const cat = await drills();
      const names = new Set(cand.issue_names || (cand.issues || []).map((i) => i.issue));
      list = ((cat && cat.drills) || []).filter((x) => checked(x) && (x.issues || []).some((i) => names.has(i)) && (!(x.levers || []).length || x.levers.includes(cand.lever))
        && (!(x.categories || []).length || !sc.category || x.categories.includes(sc.category)));
    }
    const box = sheet.querySelector("[data-ps=drills]");
    // 1回の球数: ドリルを使わないときは、型からドリルのブロックが抜ける
    const tp = d.template || [];
    const tot = tp.filter((x) => list.length || x.kind !== "drill").reduce((a, x) => a + (x.n || 0), 0);
    if (tot) sheet.querySelector("[data-ps=total]").textContent = `・1回 約${tot}球${list.length ? "（ドリルを使うとき）" : ""}`;
    if (!list.length) {
      box.innerHTML = `<span data-t="nodrill">確かめ済みのドリルはまだありません。本番で意識する1点を自分で書いてください。</span>`;
    } else {
      const REC = { worked_before: ["前回は効いた", "c-good"], not_worked_before: ["前回は効かなかった", "c-none"] };
      box.innerHTML = `<div class="kv">ドリル（人が向きと安全を確かめたもの）</div>` + list.map((x, i) => {
        const rc = REC[x.record_note];
        const when = x.record && x.record.last_date ? `（${esc(x.record.last_date)}）` : "";
        return `<label class="drl"><input type="radio" name="psdrill" value="${esc(x.id)}" ${i === 0 ? "checked" : ""}> ${esc(x.title)}${rc ? ` <span class="chip ${rc[1]}">${rc[0]}${when}</span>` : ""}${x.coach_reviewed ? "" : ` <span class="chip c-none">コーチ未確認</span>`}</label>`;
      }).join("")
        + `<label class="drl"><input type="radio" name="psdrill" value=""> 使わない（意識する1点を自分で書く）</label>`;
    }
  }

  async function createPlan(replace) {
    const sheet = $("planSheet");
    const sc = S.report.report.scopes.find((x) => x.scope_id === sheet.dataset.scope);
    const err = sheet.querySelector("[data-ps=err]");
    err.textContent = "";
    const drill = (sheet.querySelector("input[name=psdrill]:checked") || {}).value || "";
    const cue = sheet.querySelector("[data-ps=cue]").value.trim();
    if (!drill && !cue) { err.textContent = "本番で意識する1点を書いてください"; return; }
    const w = sc && sc.band_shape && sc.band_shape.ok ? sc.band_shape.window : null;
    const body = { from_session: S.session.id, scope_id: sheet.dataset.scope, candidate_id: sheet.dataset.cand, drill_id: drill, cue, window: w || null };
    const go = sheet.querySelector("[data-ps=go]");
    go.disabled = true; go.textContent = "作っています…";
    const r = await call("POST", `/v1/players/${S.player.id}/plans${replace ? "?replace=1" : ""}`, body);
    go.disabled = false; go.textContent = "プランを作る";
    if (r.status === 409 && !replace) {
      if (confirm("動いているプランがもう1つあります（動かせるのは1つだけです）。前のプランを「替えた」にして、こちらに切り替えますか？")) return createPlan(true);
      return;
    }
    if (!r.ok) { err.textContent = (r.body && r.body.error) || r.statusText; return; }
    sheet.hidden = true;
    await load(S.player);
    showPane("today");
  }

  // ---- 画面の切り替え（上の2つ: 今日の練習 / セッションと診断） ----
  const isOn = () => { const p = document.querySelector("[data-top-pane=today]"); return !!p && !p.hidden; };
  function showPane(name) {
    document.querySelectorAll("#topNav button").forEach((b) => b.classList.toggle("on", b.dataset.top === name));
    document.querySelectorAll("[data-top-pane]").forEach((p) => { p.hidden = p.dataset.topPane !== name; });
    // サーバーの状態の知らせ（スクショの読み取りが使えない など）は取り込む側だけに出す（練習場の画面を狭めない）
    const sb = $("statusBanner");
    if (sb) sb.style.display = name === "today" ? "none" : "";
    if (name === "today") render();
    else { $("tbar").hidden = true; document.body.classList.remove("has-tbar"); }
  }

  function init() {
    $("topNav").addEventListener("click", (ev) => { const b = ev.target.closest("button[data-top]"); if (b) showPane(b.dataset.top); });
    $("tbar").addEventListener("click", (ev) => {
      const b = ev.target.closest("button"); if (!b) return;
      barAction(b.id === "tNext" ? "next" : b.dataset.t);
    });
    $("todayOut").addEventListener("change", (ev) => {
      if (ev.target.id !== "tSess") return;
      const gi = $("todayOut").querySelector("[data-t=goimport]");
      if (gi) gi.disabled = !ev.target.value;
      if (ev.target.value) showRunFor(Number(ev.target.value));
    });
    $("todayOut").addEventListener("toggle", (ev) => {
      if (!ev.target.open) return;
      if (ev.target.id === "tProg") loadProgress();
      if (ev.target.id === "tRec") loadRecord();
    }, true);
    $("todayOut").addEventListener("click", async (ev) => {
      const b = ev.target.closest("button");
      if (!b) return;
      const err = () => $("tRun") && $("tRun").querySelector("[data-t=err]");
      try {
        if (b.dataset.t === "godiag" || b.dataset.t === "goimport") {
          showPane("study");
          if (b.dataset.t === "goimport" && $("tSess") && $("tSess").value && $("session").value !== $("tSess").value) { $("session").value = $("tSess").value; await pickSession(); }
          (b.dataset.t === "goimport" ? $("shotFlow") : $("analyzeBtn")).scrollIntoView({ block: "start" });
          return;
        }
        if (b.dataset.t === "record") return await recordRun();
        if (b.dataset.t === "newsess") {
          const ns = await api("POST", "/v1/sessions", { player_id: T.pid, date: localDate(), location: "練習場" });
          S.sessions = await api("GET", `/v1/players/${T.pid}/sessions`);
          renderSessions_(ns.id);
          return;
        }
        if (b.dataset.mv) return await moveBoundary(Number(b.dataset.mv), Number(b.dataset.d));
        if (b.dataset.t === "confirm") return await putCounts([...T.run.counts]);
        if (b.dataset.t === "judge") return await judge(false);
        if (b.dataset.close) return await closePlan(b.dataset.close);
        if (b.dataset.cont) return await continuePlan();
        if (b.dataset.alt) return await alternatePlan();
        if (b.dataset.seq) {
          const q = Number(b.dataset.seq);
          const sid = T.run && T.run.session_id;
          if (!S.session || S.session.id !== sid) { $("session").value = String(sid); await pickSession(); }
          const s = S.shots.find((x) => x.seq === q);
          if (s) openSheet(s.id);
          return;
        }
        if (b.dataset.act === "startplan" && S.report && S.report.report) {
          const sc = S.report.report.scopes.find((x) => x.scope_id === b.dataset.scope);
          const cand = sc && sc.candidates.candidates.find((c) => c.id === b.dataset.cand);
          if (cand) startPlan(sc, cand, b.dataset.which);
        }
      } catch (e) { const x = err(); if (x) x.textContent = e.message; else alert(e.message); }
    });
    $("planSheet").addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-ps]");
      if (!b) return;
      if (b.dataset.ps === "close") $("planSheet").hidden = true;
      if (b.dataset.ps === "go") createPlan(false);
    });
  }

  return { init, load, offlineBoot, render, showPane, startPlan, hasPlan: () => !!plan(), isOn };
})();
