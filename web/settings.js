"use strict";
/*
  設定（docs/DESIGN_v2.md §10 S）。利き手・距離の単位・テーマ・サーバーの状態・このアプリについて。
  - 利き手と距離の単位はサーバーの選手に持つ（端末をまたいで同じにする）。テーマは端末ごと（localStorage）。
  - 段2以降の設定（優先・比べる相手の既定・撮り方・端末の動画）は、その機能が入ってから足す
    （効かない設定を並べない）。
*/
(() => {
  const { $, esc, lab, S, LS, api } = App;

  function applyTheme(v) {
    // index.html の頭の小さなスクリプトが素の文字列で読むので、JSON にせずに入れる
    try {
      if (v === "light" || v === "dark") localStorage.setItem("golf.theme", v); else localStorage.removeItem("golf.theme");
    } catch { /* 入れられなくても、この画面の間は効かせる */ }
    if (v === "light" || v === "dark") document.documentElement.dataset.theme = v; else delete document.documentElement.dataset.theme;
  }
  const themeNow = () => { try { const t = localStorage.getItem("golf.theme"); return t === "light" || t === "dark" ? t : "auto"; } catch { return "auto"; } };

  const seg = (name, label, opts, cur) => `<div class="field"><span id="lb-${name}">${esc(label)}</span><div class="seg" role="group" aria-labelledby="lb-${name}" data-set="${name}">
    ${opts.map(([v, t]) => `<button type="button" data-v="${v}" aria-pressed="${String(v === cur)}">${esc(t)}</button>`).join("")}</div></div>`;

  // 開発向けの状態は「くわしい状態」に畳む。困ること（記録が消える・解説が出ない）があるときだけ上に1行出す
  function problems(h) {
    if (!h) return `<div class="note warn" data-problem>サーバーに届きません。少し待ってから開き直してください。</div>`;
    const out = [];
    if (h.db_persistent === false) out.push("保存先が一時的です。サーバーを再起動すると、入れた記録が消えます。");
    if (h.analysis === "down") out.push("分析のサービスが止まっています。解説と判定は、動き出すまで出せません。記録と練習は使えます。");
    return out.map((t) => `<div class="note warn" data-problem>${esc(t)}</div>`).join("");
  }

  async function render({ el }) {
    const p = S.player;
    const h = S.health || await fetch("/healthz").then((r) => r.json()).catch(() => null);
    el.innerHTML = `<div class="pagehead">${App.backBtn("#/home", "ホームへ戻る")}<h1>設定</h1></div>
      <section class="card">
        ${seg("hand", "利き手", [["R", "右打ち"], ["L", "左打ち"]], p ? p.handedness : App.pendingHand())}
        ${seg("unit", "距離の単位", [["yd", "ヤード"], ["m", "メートル"]], App.distUnit())}
        ${seg("theme", "表示", [["auto", "自動"], ["light", "ライト"], ["dark", "ダーク"]], themeNow())}
        <div data-err></div>
      </section>
      ${problems(h)}
      <section class="block"><h2>このアプリについて</h2><div class="card">
        <p>画面の版: ${esc(App.APP_VERSION)}${h ? `・計算の版: ${esc(h.physics || "")}${h.commit ? `・${esc(String(h.commit).slice(0, 7))}` : ""}` : ""}</p>
        <p data-updated>最終更新日: ${lab("date", App.APP_UPDATED)}</p>
        <p>いま見ているのは計測器（TrackMan）の球だけです。動画のチェックポイント（PGA スイングガイドの基準）は次の段で入ります。</p>
        <p>文章は、計測の数字から決まった型で作った定型文です。Claude を使うのは、スクショの読み取り（1枚ごとに料金を表示）と、頼んだときだけの「つなぎの文」（1範囲 約 $0.02〜0.05）です。</p>
        <p class="caption">データは、このアプリのサーバーにだけ置いています。</p>
        <details class="folded" data-health-more><summary>くわしい状態</summary><div data-health>${h ? `
          <p>保存先: ${h.db_persistent === false ? `<span class="chip warn">△ 一時的</span> 再起動すると入れた記録が消えます` : `<span class="chip good">✓ 残る</span>`}</p>
          <p>スクショの読み取り: ${h.anthropic_key ? `<span class="chip good">✓ 使える</span>` : `<span class="chip none">― 使えない</span>（表の貼り付けと CSV は使えます）`}</p>
          <p>分析のサービス: ${h.analysis === "up" ? `<span class="chip good">✓ 動いている</span>` : h.analysis === "down" ? `<span class="chip warn">△ 止まっている</span>（解説と判定は出せません）` : `<span class="chip none">― 分からない</span>`}</p>` : `<p class="sub">サーバーに届きません。</p>`}</div></details></div></section>`;
    el.addEventListener("click", async (ev) => {
      const b = ev.target.closest(".seg[data-set] button[data-v]");
      if (!b) return;
      const wrap = b.closest("[data-set]"), name = wrap.dataset.set, v = b.dataset.v;
      const mark = () => wrap.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      $("[data-err]", el).innerHTML = "";
      if (name === "theme") { applyTheme(v); mark(); return; }
      if (name === "unit") LS.set("golf.distUnit", v);
      if (!S.player) {
        // 使う人がまだいない: 端末に置き、最初の記録を入れるとき（どの画面から入れても）この利き手で作る
        if (name === "hand") { App.setPendingHand(v); App.toast("最初の記録を入れるとき、この利き手で始めます"); }
        mark();
        return;
      }
      try {
        const body = name === "hand" ? { handedness: v } : { prefs: { dist_unit: v } };
        const np = await api("PATCH", `/v1/players/${S.player.id}`, body);
        App.setPlayer(np);
        App.S.cache.report = {}; // 利き手で向きの言葉が変わる
        mark();
        App.toast("保存しました");
      } catch (e) { $("[data-err]", el).innerHTML = App.errOf(e, { what: "保存できませんでした", saved: "設定は前のままです。" }); }
    });
  }
  App.route("/settings", render, { tab: "home" });
})();
