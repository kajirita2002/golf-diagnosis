"use strict";
/*
  設定（docs/DESIGN_v2.md §10 S）。利き手・距離の単位・テーマ・サーバーの状態・このアプリについて。
  - 利き手と距離の単位はサーバーの選手に持つ（端末をまたいで同じにする）。テーマは端末ごと（localStorage）。
  - 段2以降の設定（優先・比べる相手の既定・撮り方・端末の動画）は、その機能が入ってから足す
    （効かない設定を並べない）。
*/
(() => {
  const { $, esc, S, LS, api } = App;

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

  async function render({ el }) {
    const p = S.player;
    const h = S.health || await fetch("/healthz").then((r) => r.json()).catch(() => null);
    el.innerHTML = `<div class="pagehead">${App.backBtn("#/home", "ホームへ戻る")}<h1>設定</h1></div>
      <section class="card">
        ${seg("hand", "利き手", [["R", "右打ち"], ["L", "左打ち"]], p ? p.handedness : "R")}
        ${seg("unit", "距離の単位", [["yd", "ヤード"], ["m", "メートル"]], App.distUnit())}
        ${seg("theme", "表示", [["auto", "自動"], ["light", "ライト"], ["dark", "ダーク"]], themeNow())}
        <div data-err></div>
      </section>
      <section class="block"><h2>サーバーの状態</h2><div class="card" data-health>${h ? `
        <p>保存先: ${h.db_persistent === false ? `<span class="chip warn">△ 一時的</span> 再起動すると入れた記録が消えます` : `<span class="chip good">✓ 残る</span>`}</p>
        <p>スクショの読み取り: ${h.anthropic_key ? `<span class="chip good">✓ 使える</span>` : `<span class="chip none">― 使えない</span>（表の貼り付けと CSV は使えます）`}</p>
        <p>分析のサービス: ${h.analysis === "up" ? `<span class="chip good">✓ 動いている</span>` : h.analysis === "down" ? `<span class="chip warn">△ 止まっている</span>（解説と判定は出せません）` : `<span class="chip none">― 分からない</span>`}</p>` : `<p class="sub">サーバーに届きません。</p>`}</div></section>
      <section class="block"><h2>このアプリについて</h2><div class="card">
        <p>画面の版: ${esc(App.APP_VERSION)}${h ? `・計算の版: ${esc(h.physics || "")}${h.commit ? `・${esc(String(h.commit).slice(0, 7))}` : ""}` : ""}</p>
        <p>いま見ているのは計測器（TrackMan）の球だけです。動画のチェックポイント（PGA スイングガイドの基準）は次の段で入ります。</p>
        <p>文章は、計測の数字から決まった型で作った定型文です。Claude を使うのは、スクショの読み取り（1枚ごとに料金を表示）と、頼んだときだけの「つなぎの文」（1範囲 約 $0.02〜0.05）です。</p>
        <p class="caption">データは、このアプリのサーバーにだけ置いています。</p></div></section>`;
    el.addEventListener("click", async (ev) => {
      const b = ev.target.closest(".seg[data-set] button[data-v]");
      if (!b) return;
      const wrap = b.closest("[data-set]"), name = wrap.dataset.set, v = b.dataset.v;
      const mark = () => wrap.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      $("[data-err]", el).innerHTML = "";
      if (name === "theme") { applyTheme(v); mark(); return; }
      if (name === "unit") LS.set("golf.distUnit", v);
      if (!S.player) { mark(); if (name === "hand") App.toast("最初の記録を入れるときに使います"); return; }
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
