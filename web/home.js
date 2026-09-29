"use strict";
/*
  ホーム（docs/DESIGN_v2.md §10 H）。今日の1点・状態の見出し・主ボタン1つ・次に見る1行だけ。
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
      if (pr && pr.next_action === "ask_continue") return { key: "stop", heading: "止める", primary: { label: "続けるか選ぶ", href: "#/practice/result" } };
      if (pr && ((pr.advance && pr.advance.ok) || pr.next_action === "recompute_candidates")) {
        return { key: "passed", heading: "合格しました", primary: { label: "次の項目を見る", href: "#/practice/result" } };
      }
      return { key: "plan", heading: "プラン中", nth: (p.next_index || 0) + 1, primary: { label: "練習を始める", href: "#/practice/run", count: p.total } };
    }
    if (h.focus_state === "found" && h.focus && h.focus.startable) {
      return { key: "found", heading: "課題が見つかりました", primary: { label: "この一点で練習を組む", href: `#/session/${h.focus.session_id}?scope=${encodeURIComponent(h.focus.scope_id)}&start=1` } };
    }
    const sid = (h.focus && h.focus.session_id) || (h.latest && h.latest.session_id);
    return { key: "finding", heading: "課題を見つけています", primary: { label: "診断を見る", href: sid ? `#/session/${sid}` : "#/record" } };
  }

  // 主ボタンの文（練習の球数だけはラベルとして数字を出す）
  const primaryHtml = (st) => `<a class="btn primary block" data-primary href="${esc(st.primary.href)}"><span>${esc(st.primary.label)}${st.primary.count ? `（${lab("count", st.primary.count + "球")}）` : ""}</span></a>`;

  function headHtml(st, h) {
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

  async function render({ el, alive }) {
    const appbar = `<div class="appbar" style="padding:0;margin:0 0 var(--s2)"><span class="brandname">${lab("brand", "Swing Lab")}</span><a class="iconbtn" href="#/settings" aria-label="設定">${icon("settings")}</a></div>`;
    if (!S.player) {
      // 初回: 名前は聞かない。利き手だけ選ぶ（あとで設定で直せる）
      el.innerHTML = `${appbar}<h1 class="visually-hidden">ホーム</h1>${headHtml({ key: "first", heading: "ようこそ" })}
        <div class="empty">
          <svg class="art" viewBox="0 0 96 96" aria-hidden="true"><path d="M30 84V16l32 12-32 12"/><path d="M14 84h40"/><circle cx="70" cy="74" r="10"/></svg>
          <p class="t-headline">スイングの記録から、いま一番の課題を一つ見つけ、直ったかを別の日に確かめます。</p>
        </div>
        <fieldset class="card" style="border:0"><legend class="label">利き手</legend>
          <div class="seg" role="group" aria-label="利き手" data-hand>
            <button type="button" data-v="R" aria-pressed="true">右打ち</button><button type="button" data-v="L" aria-pressed="false">左打ち</button>
          </div></fieldset>
        <div class="block"><button class="btn primary block" data-primary data-first>最初の記録を入れる</button><div data-err></div></div>`;
      let hand = "R";
      el.querySelector("[data-hand]").addEventListener("click", (ev) => {
        const b = ev.target.closest("button[data-v]");
        if (!b) return;
        hand = b.dataset.v;
        el.querySelectorAll("[data-hand] button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      });
      el.querySelector("[data-first]").addEventListener("click", async (ev) => {
        ev.currentTarget.setAttribute("aria-disabled", "true");
        try { await App.ensurePlayer(hand); App.go("/record"); } catch (e) {
          el.querySelector("[data-err]").innerHTML = App.errOf(e, { what: "はじめられませんでした" });
          ev.currentTarget.removeAttribute("aria-disabled");
        }
      });
      return;
    }
    const key = `golf.home.${S.player.id}`;
    const stop = App.loading(el);
    let h = null, fromCopy = false;
    try {
      h = await api("GET", `/v1/players/${S.player.id}/home`);
      LS.set(key, { data: h, saved_at: new Date().toISOString() });
      if (h.player) App.setPlayer(h.player);
    } catch (e) {
      if (!e.offline) { stop(); throw e; }
      const c = LS.get(key);
      h = c ? c.data : null; fromCopy = true;
      App.setOffline(true);
    }
    stop();
    if (!alive()) return;
    const st = homeState(h);
    const f = h && h.focus, p = h && h.plan;
    const oneThing = p ? p.cue : f ? (f.cue || f.title) : "";
    let body = `${appbar}<h1 class="visually-hidden">ホーム</h1>${headHtml(st, h)}`;
    if (st.key === "first") {
      body += `<div class="empty"><svg class="art" viewBox="0 0 96 96" aria-hidden="true"><path d="M30 84V16l32 12-32 12"/><path d="M14 84h40"/><circle cx="70" cy="74" r="10"/></svg>
        <p class="t-headline">スイングの記録から、いま一番の課題を一つ見つけ、直ったかを別の日に確かめます。</p>
        <p class="sub">計測器の画面のスクショか、表の貼り付けで入れられます。</p></div>`;
    }
    if (oneThing) {
      body += `<section class="block" aria-labelledby="h-one"><h2 id="h-one" class="label">今日の一点</h2>
        <div class="focuscard t-display" data-home="one">${esc(oneThing)}</div>
        ${(p ? p.title : f && f.title) ? `<p data-home="first">まずここ: <b>${esc(p ? p.title : f.title)}</b></p>` : ""}</section>`;
    }
    if (st.key === "finding") {
      body += `<p class="sub" data-home="finding">${h.focus_state === "unavailable" ? "分析のサービスに届かないので、まだ課題を出せません。記録と練習は使えます。" : "まだ言い切れることが少ないので、診断で様子を見られます。球を足すと課題が見つかります。"}</p>`;
    }
    if (f && (f.gap || f.figure)) {
      const g = f.gap || {};
      const first = (g.lines || [])[0];
      body += `<section class="block"><a class="card" style="display:block;text-decoration:none;color:inherit" href="#/session/${f.session_id}?scope=${encodeURIComponent(f.scope_id)}" data-home="gap">
          <span class="row"><span class="grow1 t-headline">理想との差</span>${icon("chevron-right")}</span>
          <div data-home-fig></div>
          ${first ? `<p class="t-callout">${esc(first.text)}</p>` : ""}</a></section>`;
    }
    if (p) body += lastLine(p);
    body += `<div class="block">${primaryHtml(st)}</div>`;
    const next = f && f.next_title;
    if (next && st.key !== "first") body += `<p class="sub" data-home="next">次に見る: ${esc(next)}</p>`;
    if (h && h.latest && st.key !== "first") {
      body += `<p class="caption" data-home="latest">最後の記録: ${App.lab("date", App.dateJa(h.latest.date))}</p>`;
    }
    if (fromCopy && !h) body += `<p class="note">まだ一度も開いていないので、圏外では出せません。電波のあるところで一度開くと、練習場でも使えます。</p>`;
    el.innerHTML = body;
    // 比べる図（要点の「理想との差」と同じ図）。図の部品は要るときだけ読む
    const slot = el.querySelector("[data-home-fig]");
    if (slot && f && f.figure) {
      try {
        await App.loadFigures();
        const node = window.Figures.render(f.figure, { hand: S.player.handedness === "L" ? "L" : "R", bandShape: f.band_shape });
        if (node && alive()) slot.replaceWith(node);
      } catch { slot.remove(); }
    }
  }

  App.route("/home", render, { tab: "home" });
  return { homeState };
})();
App.homeState = Home.homeState;
