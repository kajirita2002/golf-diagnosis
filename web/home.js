"use strict";
/*
  ホーム（docs/DESIGN_v2.md §10 H）。状態の見出し・いまの課題・主ボタン1つ・次に取り組む課題1行。
  - 診断（diagnosis）があれば、課題のカードを「課題 → 原因（いちばん可能性の高い動き）→ 理想 → 今日やること」の4行で出し、
    ［くわしいレポートを見る］で診断レポートへ（「今日の一点」の一言だけでは、何が原因で何をすればいいか分からなかった。本人の声）。
    プラン中は「今日やること」がプランの練習になる。動画が無ければ、原因を確かめる動画を促す。
  - 数値・角度・複数の課題・球の図の細かいもの・版と料金・選手の選択は置かない。
  - 文は全部サーバーの定型文（要点 gist・プランの言葉）。ここで事実の文を作らない。
  - 状態と主ボタンは homeState() の関数1つで決める（画面のあちこちで分岐させない）。
  - 圏外でも描く（最後に取ったホームを localStorage に写してある）。
*/
const Home = (() => {
  const { esc, lab, icon, LS, S, api } = App;

  // 状態 → 見出し・主ボタン。h は GET /v1/players/{id}/home（使う人がまだいなければ null）。
  // local は端末の中だけの状態（段2以降: 動画の処理が途中・置き方が前回と違う）。
  function homeState(h, local = {}) {
    if (local.videoPending) {
      // 途中の動画の記録へ（同じ動画を選び直すと、取ってある体の点で続きから処理する）
      const vp = typeof local.videoPending === "object" ? local.videoPending : {};
      const href = vp.date ? `#/video/${vp.date}${vp.session ? `?session=${vp.session}` : ""}` : "#/record";
      // 押した先で同じ動画を選び直す（動画は端末に置かないので、ファイルを選ぶのは本人）。名前もそう言う
      return { key: "video_resume", heading: "動画の処理が途中です", primary: { label: "続きから処理する", href } };
    }
    if (local.setupMismatch) return { key: "setup", heading: "撮り方を確かめたい", primary: { label: "撮り方を合わせる", href: "#/record" } };
    // 動きの課題（段2c）: プランが無く、動画のチェックで「まずここ」が決まっていれば、それが今日の一点
    // 見た目（AI）だけの項目は「今日の一点」にしない（十球テストで合格まで行けない・まだ確かめていない）。「次に見る」止まり
    const mf = h && h.motion_focus;
    if (mf && !(h.plan) && mf.basis !== "visual") {
      return { key: "motion", heading: "課題が見つかりました", motion: mf, primary: { label: "この課題を見る", href: `#/session/${mf.session_id}/check/${encodeURIComponent(mf.item_id)}` } };
    }
    // 球は無く、動画のチェックだけがある（段2a）: 「ようこそ」のままにせず、動画のチェックへ
    if (h && !h.sessions_with_shots && h.latest_video) {
      const v = h.latest_video;
      return { key: "video", heading: "動画のチェックがあります", video: v, primary: { label: "チェックを見る", href: `#/session/${v.session_id}/check` } };
    }
    if (!h || !h.sessions_with_shots) return { key: "first", heading: "ようこそ", primary: { label: "最初の記録を入れる", href: "#/record" } };
    const p = h.plan;
    if (p) {
      const pr = p.last && p.last.progress;
      // 判定と選ぶボタンが最初から出るように、最後に判定した回を開く（?show=last）
      if (pr && pr.next_action === "ask_continue") return { key: "stop", heading: "止める", primary: { label: "続けるか選ぶ", href: "#/practice/result?show=last" } };
      if (pr && ((pr.advance && pr.advance.ok) || pr.next_action === "recompute_candidates" || pr.next_action === "next_item")) {
        return { key: "passed", heading: "合格しました", primary: { label: "次の項目を見る", href: "#/practice/result?show=last" } };
      }
      return { key: "plan", heading: "プラン中", nth: (p.next_index || 0) + 1, primary: { label: "練習を始める", href: "#/practice/run", count: p.total } };
    }
    // 課題が見つかった: まずくわしいレポート（原因・理想・直し方）を読む。練習はレポートの［この課題で練習を組む］から
    if (h.focus_state === "found" && h.focus && h.focus.startable) {
      return { key: "found", heading: "課題が見つかりました", primary: { label: "くわしいレポートを見る", href: `#/session/${h.focus.session_id}` } };
    }
    // 課題は出ているが、直し方を組めない（先に測れるようにする候補）。「まだ見つかっていない」とは言わない
    if (h.focus_state === "found" && h.focus) {
      return { key: "measure", heading: "先に測れるようにします", primary: { label: "くわしいレポートを見る", href: `#/session/${h.focus.session_id}` } };
    }
    const sid = (h.focus && h.focus.session_id) || (h.latest && h.latest.session_id);
    return { key: "finding", heading: "課題を見つけています", primary: { label: "診断を見る", href: sid ? `#/session/${sid}` : "#/record" } };
  }

  // 主ボタンの文（練習の球数だけはラベルとして数字を出す）
  const primaryHtml = (st) => `<a class="btn primary block" data-primary href="${esc(st.primary.href)}"><span>${esc(st.primary.label)}${st.primary.count ? `（${lab("count", st.primary.count + "球")}）` : ""}</span></a>`;

  function headHtml(st) {
    if (st.key === "plan") return `<p class="status" data-home-state="plan"><b>プラン中</b>・次は${lab("count", st.nth + "回目")}の練習</p>`;
    return `<p class="status" data-home-state="${esc(st.key)}"><b>${esc(st.heading)}</b></p>`;
  }

  function lastLine(p) {
    const l = p && p.last;
    if (!l) return "";
    const pl = l.plain || {};
    const pr = l.progress || {};
    const tone = { good: "good", bad: "bad", warn: "warn" }[pl.tone] || "none";
    return `<p data-home="last"><span class="label">前回</span> ${p.last_date ? lab("date", App.dateJa(p.last_date, false)) : ""} ${pl.label ? `<span class="chip ${tone}">${esc(pl.label)}</span>` : ""}${pr.title ? ` ${esc(pr.title)}` : ""}</p>`;
  }

  const ART = `<svg class="art" viewBox="0 0 96 96" aria-hidden="true"><path d="M30 84V16l32 12-32 12"/><path d="M14 84h40"/><circle cx="70" cy="74" r="10"/></svg>`;
  const appbar = () => `<div class="appbar" style="padding:0;margin:0 0 var(--s2)"><span class="brandname">${lab("brand", "Swing Lab")}</span><a class="iconbtn" href="#/settings" aria-label="設定">${icon("settings")}</a></div>`;

  // 初回（使う人がいない）: 名前は聞かない。利き手だけ選ぶ（あとで設定で直せる）。
  // 選んだ利き手はすぐ端末に置く（ここを通らずにタブから記録へ行っても、その利き手で作る）
  function renderWelcome(el) {
    const hand0 = App.pendingHand();
    el.innerHTML = `${appbar()}<h1 class="visually-hidden">ホーム</h1>${headHtml({ key: "first", heading: "ようこそ" })}
      <div class="empty">${ART}
        <p class="t-headline">スイングの記録から、いま一番の課題を一つ見つけ、直ったかを別の日に確かめます。</p>
      </div>
      <div class="card" role="group" aria-labelledby="lb-hand"><p class="label" id="lb-hand" style="margin:0 0 var(--s2)">利き手</p>
        <div class="seg" data-hand>
          <button type="button" data-v="R" aria-pressed="${hand0 === "R"}">右打ち</button><button type="button" data-v="L" aria-pressed="${hand0 === "L"}">左打ち</button>
        </div></div>
      <div class="block"><button class="btn primary block" data-primary data-first>最初の記録を入れる</button><div data-err></div></div>`;
    let hand = hand0;
    el.querySelector("[data-hand]").addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-v]");
      if (!b) return;
      hand = b.dataset.v;
      App.setPendingHand(hand);
      el.querySelectorAll("[data-hand] button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    });
    el.querySelector("[data-first]").addEventListener("click", async (ev) => {
      ev.currentTarget.setAttribute("aria-disabled", "true");
      try { await App.ensurePlayer(hand); App.go("/record"); } catch (e) {
        el.querySelector("[data-err]").innerHTML = App.errOf(e, { what: "はじめられませんでした" });
        ev.currentTarget.removeAttribute("aria-disabled");
      }
    });
  }

  // 動画の途中の印。古い印（7日より前）は捨てる（ホームの主ボタンを「続き」に固定し続けない。video.js の pending と同じ日数）
  function freshPending() {
    const vp = LS.get("golf.videoPending");
    if (vp && typeof vp === "object" && vp.at && Date.now() - vp.at > 7 * 86400000) { LS.del("golf.videoPending"); return null; }
    return vp || null;
  }

  // 課題のカード（診断があるとき）: 課題 → 原因（いちばん可能性の高い動き）→ 理想 → 今日やること の4行。
  // 文は診断の定型文だけ。数字はラベル（球数・回数）だけ。p があればプラン中（今日やることはプランの練習）
  function diagCard(d, p) {
    const is = d.issue || {}, fx = is.fix || {};
    const c0 = (is.causes || [])[0];
    const fact = String(is.club_state || "").split(/(?<=。)/)[0];
    const likely = c0 && c0.basis === "likely";
    const cause = c0 ? `${fact ? `<span data-home-fact>${esc(fact)}</span><br>` : ""}<span class="label">${likely ? "体の動きの候補" : "体の動き"}</span><br><b>${esc(c0.title)}</b>
        <span class="row dgchips" style="margin:var(--s1) 0 0">${Diagnosis.basisChip(c0.basis)}</span>${likely ? `<span class="caption" style="display:block">動画で確かめるまでは推測です。</span>` : ""}`
      : esc(fact);
    const menu = (fx.menu || []).map((x) => `<li>${esc(x.title)} ${lab("count", x.balls + "球")}</li>`).join("");
    const today = p
      ? `<span class="label">本番で意識する体の動き</span><br><b data-home="one">${esc(p.cue)}</b><br><span class="sub">次は${lab("count", ((p.next_index || 0) + 1) + "回目")}の練習${p.total ? `（${lab("count", p.total + "球")}）` : ""}。ドリルの手順は練習の画面に出ます。</span>`
      : `<span class="label">本番で意識する体の動き</span><br><b data-home="one">${esc(fx.cue_move || fx.cue || "")}</b>${menu ? `<ol class="hmenu" data-home="menu">${menu}</ol>` : ""}`;
    return `<section class="block" aria-labelledby="h-issue"><div class="row between" style="margin-bottom:var(--s2)"><h2 id="h-issue" class="label" style="margin:0">いまの課題</h2>${is.club_scope ? `<span class="chip none">${Diagnosis.scopeHtml(is.club_scope)}</span>` : ""}</div>
      <div class="card hcard" data-home="diag"><dl>
        <div class="hrow issue" data-row="issue"><dt>課題</dt><dd><b data-home="first">${esc(is.title || (p && p.title) || "")}</b></dd></div>
        <div class="hrow" data-row="cause"><dt>原因</dt><dd>${cause}</dd></div>
        <div class="hrow" data-row="ideal"><dt>理想</dt><dd>${esc((is.ideal && is.ideal.text) || "")}</dd></div>
        <div class="hrow" data-row="today"><dt>今日やること</dt><dd>${today}</dd></div>
      </dl></div></section>`;
  }

  // 並び: 状態 → 今日の一点／いまの課題 → （合格の条件・前回）→ 主ボタン → 次に見る → 理想との差（小さな図）。
  // 主ボタンは320px でもスクロールせずに見える位置に置く（図が大きく、ボタンを画面の外へ押し出していた）。
  function draw(el, h, fromCopy) {
    const st = homeState(h, { videoPending: freshPending() });
    const f = h && h.focus, p = h && h.plan;
    let body = `${appbar()}<h1 class="visually-hidden">ホーム</h1>${headHtml(st)}`;
    // 動画の続きは、見出しのすぐ下に主ボタンを置く（間に課題のカードを挟むと、練習のボタンに見える）
    if (st.key === "video_resume") body += `<div class="block">${primaryHtml(st)}</div>`;
    if (st.key === "first") {
      body += `<div class="empty">${ART}
        <p class="t-headline">スイングの記録から、いま一番の課題を一つ見つけ、直ったかを別の日に確かめます。</p>
        <p class="sub">計測器の画面のスクショか、表の貼り付けで入れられます。</p></div>`;
    }
    const d = f && f.diag && f.diag.issue ? f.diag : null;
    if (d && (p || (st.key !== "first" && st.key !== "motion" && st.key !== "video"))) {
      body += diagCard(d, p);
      if (p) body += lastLine(p);
    } else if (p) {
      // プラン中: 「今日の一点」は打つときに意識すること。その下に、それで直す課題
      body += `<section class="block" aria-labelledby="h-one"><h2 id="h-one" class="label">今日の一点</h2>
        <div class="focuscard t-display" data-home="one">${esc(p.cue)}</div>
        ${p.title ? `<p data-home="first"><span class="label">いまの課題</span><br><b>${esc(p.title)}</b></p>` : ""}
        ${p.advance ? `<button class="textbtn" data-home="advance">合格の条件を見る</button>` : ""}</section>`;
      body += lastLine(p);
    } else if (st.key === "motion") {
      // 動きの課題: 言葉だけ（数字・角度は C-1 の「なぜそう言える？」の中）。球の課題とつながる候補なら印を添える
      const m = st.motion;
      body += `<section class="block" aria-labelledby="h-one"><h2 id="h-one" class="label">今日の一点</h2>
        <div class="focuscard" data-home="one" data-motion-focus><div data-motion-fig></div><p class="t-headline" style="margin:0">${esc(m.fault_label || m.title)}</p>
          <p class="sub" style="margin:var(--s1) 0 0">${m.p && /^P\d/.test(m.p) ? lab("p", m.p.replace("_5", ".5")) + " " : ""}${esc(m.title)}</p></div>
        ${m.earlier ? `<p class="caption" data-home="earlier">前の動画（${lab("date", App.dateJa(m.date, false))}）の課題です。</p>` : ""}
        ${m.linked ? `<p data-home="linked"><span class="chip brand">${icon("layers")}${esc(m.linked_text || "球の課題とつながる候補です（まだ確かめていません）")}</span></p>` : ""}
        ${m.drill ? `<p data-home="drill"><span class="label">練習の一例</span><br>${esc(m.drill.cue || m.drill.title)}</p>` : ""}</section>`;
    } else if (f && st.key !== "first") {
      // プランの前: 課題（何を直すか）を先に、意識すること（どう打つか）を2番目に。「今日の一点」とは呼ばない
      body += `<section class="block" aria-labelledby="h-issue"><h2 id="h-issue" class="label">いまの課題</h2>
        <p class="t-headline" data-home="first" style="margin:var(--s1) 0 var(--s3)">${esc(f.title)}</p>
        ${f.earlier ? `<p class="caption" data-home="earlier">前の記録（${lab("date", App.dateJa(f.session_date, false))}）の課題です。最新の記録だけでは、まだ一つに決められません。</p>` : ""}
        ${f.cue ? `<div class="focuscard"><span class="label">打つときに意識すること</span><p class="t-headline" data-home="one" style="margin:var(--s1) 0 0">${esc(f.cue)}</p></div>` : ""}</section>`;
    }
    if (st.key === "video") {
      const need = Math.max(0, 3 - (st.video.n_same_view || 0));
      body += `<section class="block" aria-labelledby="h-issue"><h2 id="h-issue" class="label">いまの課題</h2>
        <p class="t-headline" data-home="first" data-video-focus style="margin:var(--s1) 0 var(--s2)">${need ? `同じ向きのスイングがあと${["", "一本", "二本"][need]}あると、課題が決まります` : "チェックで「まずここ」を見られます"}</p>
        <p class="sub">球の記録（計測器の表）を入れると、球の結果も並べて見られます。</p></section>`;
    }
    if (st.key === "measure") {
      body += `<p class="sub" data-home="finding">この課題は、いまの記録だけでは直し方を決められません。診断で、先に測れるようにする方法を見られます。</p>`;
    } else if (st.key === "finding") {
      body += `<p class="sub" data-home="finding">${h.focus_state === "unavailable" ? "分析のサービスに届かないので、まだ課題を出せません。記録と練習は使えます。" : "いまの記録からは、課題をまだ一つに決められません。診断で様子を見られます。"}</p>`;
    }
    // 課題のカードは読み物で長いので、主ボタンは画面の下（タブの上）に留める（スクロールしなくても押せる）
    const long = d && (p || st.key === "found" || st.key === "measure");
    if (st.key !== "video_resume") body += `<div class="block ${long ? "stickyact" : ""}">${primaryHtml(st)}</div>`;
    if (long) {
      // プラン中でもレポートへ1押しで戻れるように（主ボタンは練習）。動画が無ければ、原因を確かめる動画を促す
      if (p) body += `<a class="btn block" data-home="report" href="#/session/${f.session_id}">くわしいレポートを見る</a>`;
      const c0 = ((d.issue && d.issue.causes) || [])[0];
      if (d.video_needed && c0 && c0.basis === "likely") {
        body += `<a class="card vidcard block" data-home="video" href="#/video/${App.localDate()}">${icon("video")}<span class="grow1"><b>動画を撮って原因を確かめる</b>
          <span class="caption">${esc(d.video_hint || "")}</span></span>${icon("chevron-right", "chev")}</a>`;
      }
    }
    // 次に見る: 動きの課題を今日の一点にしたときは、球の一番の課題を回す（球の課題をホームから消さない）。
    // 見た目だけの動きの課題は、今日の一点にしないかわりにここへ札つきで出す
    const mfv = h && h.motion_focus && h.motion_focus.basis === "visual" && !p ? h.motion_focus : null;
    const next = st.key === "motion" ? f && f.title : (d && !p ? (d.next && d.next.title) : f && f.next_title);
    if (mfv && st.key !== "first" && st.key !== "video") {
      body += `<p class="sub" data-home="next"><a href="#/session/${mfv.session_id}/check/${encodeURIComponent(mfv.item_id)}">次に見る: ${esc(mfv.fault_label || mfv.title)}</a> <span class="chip none" data-visual-chip>見た目の判定（まだ確かめていません）</span></p>`;
    } else if (next && st.key !== "first") body += `<p class="sub" data-home="next">${d && !p ? "次に取り組む課題" : "次に見る"}: ${esc(next)}</p>`;
    if (f && (f.gap || f.figure) && !d) {
      const g = f.gap || {};
      const first = (g.lines || [])[0];
      body += `<section class="block"><a class="card" style="display:block;text-decoration:none;color:inherit" href="#/session/${f.session_id}?scope=${encodeURIComponent(f.scope_id)}" data-home="gap">
          <span class="row"><span class="grow1 t-headline">理想との差</span>${icon("chevron-right")}</span>
          <div data-home-fig></div>
          ${first ? `<p class="t-callout">${esc(first.text)}</p>` : ""}</a></section>`;
    }
    if (h && h.latest && st.key !== "first") {
      body += `<p class="caption" data-home="latest">最後の記録: ${App.lab("date", App.dateJa(h.latest.date))}</p>`;
    }
    if (fromCopy && !h) body += `<p class="note">まだ一度も開いていないので、${App.netDown() ? "圏外" : "サーバーにつながらない"}あいだは出せません。つながるところで一度開くと、練習場でも使えます。</p>`;
    el.innerHTML = body;
    const adv = el.querySelector("[data-home=advance]");
    if (adv) {
      adv.addEventListener("click", () => App.openSheet({ title: "合格の条件", label: "advance", html: `<p class="claim">${esc(App.textJa(p.advance))}</p>
        <p class="caption">条件を満たすと、ホームに「合格しました」と出ます。</p>` }));
    }
    return f;
  }

  async function drawFigure(el, f, alive) {
    // 比べる図（要点の「理想との差」と同じ図の小さな版。球の散らばりだけ・件数の文は出さない）
    const slot = el.querySelector("[data-home-fig]");
    if (!slot || !f || !f.figure) return;
    try {
      await App.loadFigures();
      const node = window.Figures.render(f.figure, { hand: S.player.handedness === "L" ? "L" : "R", bandShape: f.band_shape, compact: true });
      if (node && alive() && slot.isConnected) slot.replaceWith(node);
    } catch { slot.remove(); }
  }

  async function render({ el, alive }) {
    if (!S.player) { renderWelcome(el); return; }
    const key = `golf.home.${S.player.id}`;
    // 前に開いたときの写しがあれば、先に描く（寝起きのサーバーを待たせない）。届いたら描き直す
    const copy = LS.get(key);
    let shown = null;
    if (copy && copy.data) {
      shown = JSON.stringify(copy.data);
      drawFigure(el, draw(el, copy.data, false), alive);
    }
    const stop = shown ? () => {} : App.loading(el);
    let h = null, fromCopy = false;
    try {
      h = await api("GET", `/v1/players/${S.player.id}/home`);
      LS.set(key, { data: h, saved_at: new Date().toISOString() });
      if (h.player) App.setPlayer(h.player);
    } catch (e) {
      stop();
      if (!e.offline) { if (shown) { console.warn(e); return; } throw e; }
      App.setOffline(true);
      if (shown) return;
      h = null; fromCopy = true;
    }
    stop();
    if (!alive()) return;
    if (shown && shown === JSON.stringify(h)) return; // 写しと同じなら描き直さない（ちらつかせない）
    await drawFigure(el, draw(el, h, fromCopy), alive);
    videoFocus(el, h, alive);
    motionFig(el, h, alive);
  }

  // 動きの課題の写真に、範囲の面と「範囲に入れた目安」の線を重ねる（段3。描けなければ何も出さない）
  async function motionFig(el, h, alive) {
    const slot = el.querySelector("[data-motion-fig]");
    const mf = h && h.motion_focus;
    if (!slot || !mf || typeof Checks === "undefined" || typeof Ideal === "undefined") return;
    try {
      const d = await Checks.load(mf.session_id);
      const it = ((d && d.checks && d.checks.items) || []).find((x) => x.id === mf.item_id);
      const html = it ? await Ideal.thumbHtml(d, it) : "";
      if (html && alive() && slot.isConnected) slot.outerHTML = `<div class="row" data-motion-fig>${html}</div>`;
    } catch (e) { console.warn(e); }
  }

  // 動画だけの日は、チェックの「まずここ」の文を後から入れる（ホームの API では課題を選ばない。選ぶのはチェックと同じ関数）
  async function videoFocus(el, h, alive) {
    const slot = el.querySelector("[data-video-focus]");
    if (!slot || !h || !h.latest_video || typeof Checks === "undefined") return;
    try {
      const d = await Checks.load(h.latest_video.session_id);
      const c = d && d.checks;
      const f = c && (c.items || []).find((x) => x.focus);
      if (f && alive() && slot.isConnected) {
        slot.textContent = f.fault_label || f.title;
        if (f.basis === "visual") slot.insertAdjacentHTML("afterend", `<p><span class="chip none" data-visual-chip>${esc(Checks.VISUAL_CHIP)}</span></p>`);
      }
    } catch (e) { console.warn(e); }
  }

  App.route("/home", render, { tab: "home" });
  return { homeState };
})();
App.homeState = Home.homeState;
