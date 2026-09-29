"use strict";
/*
  ホーム（docs/DESIGN_v2.md §10 H）。状態の見出し・今日の1点（プラン中）／いまの課題・主ボタン1つ・次に見る1行だけ。
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
    if (local.videoPending) return { key: "video_resume", heading: "動画の処理が途中です", primary: { label: "続きから処理する", href: "#/record" } };
    if (local.setupMismatch) return { key: "setup", heading: "撮り方を確かめたい", primary: { label: "撮り方を合わせる", href: "#/record" } };
    if (!h || !h.sessions_with_shots) return { key: "first", heading: "ようこそ", primary: { label: "最初の記録を入れる", href: "#/record" } };
    const p = h.plan;
    if (p) {
      const pr = p.last && p.last.progress;
      // 判定と選ぶボタンが最初から出るように、最後に判定した回を開く（?show=last）
      if (pr && pr.next_action === "ask_continue") return { key: "stop", heading: "止める", primary: { label: "続けるか選ぶ", href: "#/practice/result?show=last" } };
      if (pr && ((pr.advance && pr.advance.ok) || pr.next_action === "recompute_candidates")) {
        return { key: "passed", heading: "合格しました", primary: { label: "次の項目を見る", href: "#/practice/result?show=last" } };
      }
      return { key: "plan", heading: "プラン中", nth: (p.next_index || 0) + 1, primary: { label: "練習を始める", href: "#/practice/run", count: p.total } };
    }
    if (h.focus_state === "found" && h.focus && h.focus.startable) {
      return { key: "found", heading: "課題が見つかりました", primary: { label: "この一点で練習を組む", href: `#/session/${h.focus.session_id}?scope=${encodeURIComponent(h.focus.scope_id)}&start=1` } };
    }
    // 課題は出ているが、直し方を組めない（先に測れるようにする候補）。「まだ見つかっていない」とは言わない
    if (h.focus_state === "found" && h.focus) {
      return { key: "measure", heading: "先に測れるようにします", primary: { label: "診断を見る", href: `#/session/${h.focus.session_id}?scope=${encodeURIComponent(h.focus.scope_id)}` } };
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

  // 並び: 状態 → 今日の一点／いまの課題 → （合格の条件・前回）→ 主ボタン → 次に見る → 理想との差（小さな図）。
  // 主ボタンは320px でもスクロールせずに見える位置に置く（図が大きく、ボタンを画面の外へ押し出していた）。
  function draw(el, h, fromCopy) {
    const st = homeState(h);
    const f = h && h.focus, p = h && h.plan;
    let body = `${appbar()}<h1 class="visually-hidden">ホーム</h1>${headHtml(st)}`;
    if (st.key === "first") {
      body += `<div class="empty">${ART}
        <p class="t-headline">スイングの記録から、いま一番の課題を一つ見つけ、直ったかを別の日に確かめます。</p>
        <p class="sub">計測器の画面のスクショか、表の貼り付けで入れられます。</p></div>`;
    }
    if (p) {
      // プラン中: 「今日の一点」は打つときに意識すること。その下に、それで直す課題
      body += `<section class="block" aria-labelledby="h-one"><h2 id="h-one" class="label">今日の一点</h2>
        <div class="focuscard t-display" data-home="one">${esc(p.cue)}</div>
        ${p.title ? `<p data-home="first"><span class="label">いまの課題</span><br><b>${esc(p.title)}</b></p>` : ""}
        ${p.advance ? `<button class="textbtn" data-home="advance">合格の条件を見る</button>` : ""}</section>`;
      body += lastLine(p);
    } else if (f && st.key !== "first") {
      // プランの前: 課題（何を直すか）を先に、意識すること（どう打つか）を2番目に。「今日の一点」とは呼ばない
      body += `<section class="block" aria-labelledby="h-issue"><h2 id="h-issue" class="label">いまの課題</h2>
        <p class="t-headline" data-home="first" style="margin:var(--s1) 0 var(--s3)">${esc(f.title)}</p>
        ${f.cue ? `<div class="focuscard"><span class="label">打つときに意識すること</span><p class="t-headline" data-home="one" style="margin:var(--s1) 0 0">${esc(f.cue)}</p></div>` : ""}</section>`;
    }
    if (st.key === "measure") {
      body += `<p class="sub" data-home="finding">この課題は、いまの記録だけでは直し方を決められません。診断で、先に測れるようにする方法を見られます。</p>`;
    } else if (st.key === "finding") {
      body += `<p class="sub" data-home="finding">${h.focus_state === "unavailable" ? "分析のサービスに届かないので、まだ課題を出せません。記録と練習は使えます。" : "いまの記録からは、課題をまだ一つに決められません。診断で様子を見られます。"}</p>`;
    }
    body += `<div class="block">${primaryHtml(st)}</div>`;
    const next = f && f.next_title;
    if (next && st.key !== "first") body += `<p class="sub" data-home="next">次に見る: ${esc(next)}</p>`;
    if (f && (f.gap || f.figure)) {
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
  }

  App.route("/home", render, { tab: "home" });
  return { homeState };
})();
App.homeState = Home.homeState;
