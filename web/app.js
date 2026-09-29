"use strict";
/*
  殻（docs/DESIGN_v2.md §4.2・§11・§12）。ほかのファイルは App.route(...) で画面を足し、App の部品を使う。
  - ルーティングはハッシュ（#/home・#/record/{date}・#/session/{id}/… ）。再読み込みしても同じ画面に戻る。
  - サーバーから来る文字列（CSV のクラブ名・定型文）は必ず esc() を通してから HTML に入れる。
  - alert / confirm は使わない。確認はシート（App.confirm）、失敗はその場の文（App.errorHtml）、知らせはトースト。
  - 起動して最初の API が3秒を超えたら「サーバーを起こしています」を出す（無料の仕組みは寝ている）。
    起きたあとの遅い処理（スクショの読み取り・解説の作成）では出さない（寝起きではないので、案内が嘘になる）。
  - 使う人とホームの写しがあれば、サーバーを待たずに先に描き、届いたら描き直す。
  - 圏外でもホームと練習は描く（使う人・ホーム・今日の練習の写しを localStorage に持つ）。
    「圏外」と言うのは端末が電波を失っているとき（navigator.onLine が false）だけ。電波はあってサーバーに
    届かないときは「サーバーにつながりません」と分ける（偽の確信を出さない）。
    判定の文はサーバーから届いたものだけを出す（古い写しの数字で新しい判定を作らない）。
*/
const App = (() => {
  const APP_VERSION = "web/2.0-stage1";
  const APP_UPDATED = "2026-09-29"; // 画面を最後に直した日（設定の「このアプリについて」）
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icon = (id, cls = "") => `<svg class="icon ${cls}" aria-hidden="true"><use href="icons.svg#${id}"/></svg>`;
  // 構造のラベル（日付・件数・P の番号・料金・時間・クラブ）。最初の面で数字を出してよいのはこの印の中だけ（§3 R1）
  const lab = (kind, text) => `<span data-label="${kind}">${esc(text)}</span>`;

  const LS = {
    get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* 容量・プライベートモード */ } },
    del(k) { try { localStorage.removeItem(k); } catch { /* 読めない */ } },
  };

  // ---- 状態 ----
  const S = { player: null, offline: false, health: null, cache: { report: {}, analysis: {}, shots: {}, sessions: null } };

  // ---- API ----
  // 端末が電波を失っているか（false のときだけ「圏外」と言う。分からなければ電波はあるとみなす）
  const netDown = () => navigator.onLine === false;
  let waking = 0;
  let awake = false; // サーバーから一度でも答えが返ったか（返ったあとは寝起きではない）
  function wakeBanner(on) {
    waking = Math.max(0, waking + (on ? 1 : -1));
    const el = $("#banners");
    let b = el && $("[data-banner=wake]", el);
    if (waking && !b && el) {
      const copy = S.shownCopy ? "前に開いたときの内容を先に出しています。" : "";
      el.insertAdjacentHTML("beforeend", `<div class="banner" data-banner="wake"><div class="info">サーバーを起こしています（無料の仕組みなので、最大1分ほど）。${copy}</div></div>`);
    } else if (!waking && b) b.remove();
  }
  async function request(method, path, { body, raw, ctype } = {}) {
    const opt = { method, headers: {} };
    if (body instanceof FormData) opt.body = body;
    else if (raw !== undefined) { opt.body = raw; opt.headers["Content-Type"] = ctype || "text/plain; charset=utf-8"; }
    else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
    let slow = false;
    // 寝起きの案内は、サーバーからまだ一度も答えが返っていないあいだだけ（§11.4・§12）
    const t = awake ? null : setTimeout(() => { if (!awake) { slow = true; wakeBanner(true); } }, 3000);
    try {
      const r = await fetch(path, opt);
      awake = true;
      const text = await r.text();
      let j = null;
      try { j = text ? JSON.parse(text) : null; } catch { /* JSON でない応答 */ }
      if (S.offline && r.ok) setOffline(false);
      return { status: r.status, ok: r.ok, body: j, text };
    } finally {
      clearTimeout(t);
      if (slow) wakeBanner(false);
    }
  }
  // 失敗は例外（status と本文つき）。圏外は status 0
  async function api(method, path, body) {
    let r;
    try { r = await request(method, path, { body }); } catch (e) {
      const err = new Error(netDown() ? "圏外なので届きません" : "サーバーにつながりません");
      err.status = 0; err.offline = true; err.detail = String(e && e.message || e);
      throw err;
    }
    if (!r.ok) {
      const err = new Error((r.body && r.body.error) || r.text || String(r.status));
      err.status = r.status; err.body = r.body;
      throw err;
    }
    return r.body;
  }

  // ---- 書式（保存は SI。表示の単位だけここで変える） ----
  const LABEL = {
    face_angle: "フェース角", club_path: "クラブパス", face_to_path: "フェース・トゥ・パス",
    attack_angle: "入射角", dynamic_loft: "ダイナミックロフト", spin_loft: "スピンロフト",
    low_point: "最下点", impact_offset: "打点（左右）", impact_height: "打点（上下）",
    launch_direction: "打ち出し方向", launch_angle: "打ち出し角", spin_axis: "スピン軸",
    spin_rate: "スピン量", side: "着地の左右", carry: "キャリー", club_speed: "ヘッドスピード",
    ball_speed: "ボール初速", smash_factor: "ミート率",
  };
  const KIND = {
    face_angle: "deg", club_path: "deg", face_to_path: "deg", attack_angle: "deg", dynamic_loft: "deg",
    spin_loft: "deg", launch_direction: "deg", launch_angle: "deg", spin_axis: "deg",
    low_point: "mm", impact_offset: "mm", impact_height: "mm", side: "dist", carry: "dist",
    spin_rate: "rpm", club_speed: "mps", ball_speed: "mps", smash_factor: "ratio",
  };
  const distUnit = () => {
    const p = S.player && S.player.prefs;
    const v = (p && p.dist_unit) || LS.get("golf.distUnit");
    return v === "m" ? "m" : "yd";
  };
  function fmt(metric, v, signed = false) {
    if (v === null || v === undefined) return "—";
    const k = KIND[metric];
    let x = v, u = "", d = 1;
    if (k === "deg") u = "°";
    else if (k === "mm") { x = v * 1000; u = "mm"; d = 0; }
    else if (k === "dist") { x = distUnit() === "yd" ? v / 0.9144 : v; u = distUnit(); }
    else if (k === "rpm") { u = "rpm"; d = 0; }
    else if (k === "mps") { u = "m/s"; }
    else if (k === "ratio") { d = 2; }
    const s = x.toFixed(d);
    return (signed && x > 0 ? "+" : "") + s + u;
  }
  // 番手の表示（保存はそのまま。画面だけ日本語にする）
  const CLUB_KIND = { iron: "番アイアン", wood: "番ウッド", hybrid: "番ユーティリティ" };
  const CLUB_WORD = { driver: "ドライバー", pw: "ピッチングウェッジ", aw: "アプローチウェッジ", gw: "ギャップウェッジ", sw: "サンドウェッジ", lw: "ロブウェッジ", putter: "パター" };
  function clubJa(c) {
    const s = String(c || "").trim();
    const m = /^(\d+)\s*(Iron|Wood|Hybrid)$/i.exec(s);
    if (m) return `${m[1]}${CLUB_KIND[m[2].toLowerCase()]}`;
    return CLUB_WORD[s.toLowerCase().replace(/[\s.]/g, "")] || s;
  }
  const pad2 = (n) => String(n).padStart(2, "0");
  const localDate = (d = new Date()) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  const WD = "日月火水木金土";
  // "2026-09-29" → "9月29日"（今日・昨日は添える）
  function dateJa(iso, withDay = true) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ""));
    if (!m) return String(iso || "");
    const s = `${Number(m[2])}月${Number(m[3])}日`;
    if (!withDay) return s;
    const today = localDate();
    const y = new Date(); y.setDate(y.getDate() - 1);
    if (iso === today) return `${s}（今日）`;
    if (iso === localDate(y)) return `${s}（昨日）`;
    return `${s}（${WD[new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).getDay()]}）`;
  }
  // 定型文の中のクラブ名（"6 Iron"）と距離（"105.3m"）を、画面の言葉と設定の単位にそろえる。
  // 文を作り直すのではなく、書き方だけを置き換える（数字の中身は変えない）
  function textJa(t) {
    let s = String(t ?? "").replace(/\b(\d+)\s?(Iron|Wood|Hybrid)\b/gi, (m) => clubJa(m.replace(/(\d+)\s?/, "$1 ")));
    if (distUnit() === "yd") {
      s = s.replace(/(\d+(?:\.\d+)?)m(?![a-zA-Z/²])/g, (m, v) => `${(Number(v) / 0.9144).toFixed(v.includes(".") ? 1 : 0)}yd`);
    }
    return s;
  }
  const localTime = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : `${dateJa(localDate(d), false)} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`; };

  // ---- 部品: トースト・読み上げ・エラー・読み込み ----
  let toastTimer = null;
  function toast(text) {
    const t = $("#toast");
    t.textContent = text; t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 4000);
  }
  function say(text) { const l = $("#live"); if (l) { l.textContent = ""; setTimeout(() => { l.textContent = text; }, 30); } }
  // 失敗の文（NN/g #9）: 何が起きたか・何が保存されたか・次に何ができるか。技術の文言は「くわしく」の中
  // retry: true なら「もう一度読み込む」を主ボタンで添える（画面を出せなかったとき。NN/g #9・Carbon の action）
  function errorHtml({ what, saved, next, detail, retry }) {
    return `<div class="errbox" role="alert"><p class="what">${esc(what)}</p>${saved ? `<p>${esc(saved)}</p>` : ""}${next ? `<p>${esc(next)}</p>` : ""}${retry ? `<button class="btn primary" data-retry>もう一度読み込む</button>` : ""}${detail ? `<details><summary>くわしく</summary><p class="caption">${esc(detail)}</p></details>` : ""}</div>`;
  }
  // 例外 → 失敗の文。圏外（端末に電波が無い）・サーバーに届かない・サーバーの断りを分ける
  function errOf(e, { what, saved, next, retry } = {}) {
    if (e && e.offline) {
      const down = netDown();
      return errorHtml({ what: what || (down ? "圏外なので届きませんでした" : "サーバーにつながりませんでした"), saved: saved || "この画面で入れたものは、まだ送っていません。",
        next: next || (down ? "電波のあるところで、もう一度押してください。" : "サーバーが止まっているか、起きるところです。少し待ってから、もう一度押してください。"), detail: e.detail, retry });
    }
    return errorHtml({ what: what || "うまくいきませんでした", saved, next: next || "内容を確かめて、もう一度押してください。", detail: e && e.message, retry });
  }
  document.addEventListener("click", (ev) => { if (ev.target.closest("[data-retry]")) { ev.preventDefault(); render(); } });
  // 1秒未満は何も出さない・1〜3秒は骨組み・3秒超は言葉（Carbon）。止める関数を返す
  function loading(el, text = "読み込んでいます…") {
    let t2 = null;
    const t1 = setTimeout(() => {
      el.innerHTML = `<div aria-hidden="true"><span class="skel" style="width:60%"></span><span class="skel l"></span><span class="skel" style="width:80%"></span></div>`;
      t2 = setTimeout(() => { el.innerHTML = `<p class="loading-text" role="status">${esc(text)}</p>`; }, 2000);
    }, 1000);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }

  // ---- シート（下から出る。なぜそう言える？・確認・プランを始める・1球の中身） ----
  const sheets = [];
  function openSheet({ title, html = "", size = "half", label, onClose } = {}) {
    const opener = document.activeElement;
    const scrim = document.createElement("div");
    scrim.className = "scrim";
    const el = document.createElement("section");
    const hid = `sh${Date.now()}${sheets.length}`;
    el.className = `sheet ${size}`;
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.setAttribute("aria-labelledby", hid);
    if (label) el.dataset.sheet = label;
    el.innerHTML = `<div class="grab" aria-hidden="true"><span></span></div>
      <header><h2 id="${hid}" tabindex="-1">${esc(title || "")}</h2><button class="iconbtn" data-sheet-close aria-label="閉じる">${icon("x")}</button></header>
      <div class="body">${html}</div>`;
    document.body.append(scrim, el);
    requestAnimationFrame(() => { scrim.classList.add("on"); el.classList.add("on"); });
    const sh = { el, body: $(".body", el), closed: false };
    sh.close = (why) => {
      if (sh.closed) return;
      sh.closed = true;
      const i = sheets.indexOf(sh);
      if (i >= 0) sheets.splice(i, 1);
      el.classList.remove("on"); scrim.classList.remove("on");
      setTimeout(() => { el.remove(); scrim.remove(); }, 300);
      if (opener && opener.isConnected && opener.focus) opener.focus();
      if (onClose) onClose(why);
    };
    scrim.addEventListener("click", () => sh.close("scrim"));
    $("[data-sheet-close]", el).addEventListener("click", () => sh.close("x"));
    // 引っぱって閉じる（ボタンでも閉じられる。WCAG 2.5.7）
    let y0 = null;
    const grabs = [$(".grab", el), $("header", el)];
    for (const g of grabs) {
      g.addEventListener("pointerdown", (ev) => { if (ev.target.closest("button")) return; y0 = ev.clientY; g.setPointerCapture(ev.pointerId); });
      g.addEventListener("pointermove", (ev) => { if (y0 === null) return; const dy = Math.max(0, ev.clientY - y0); el.style.transform = `translateY(${dy}px)`; });
      g.addEventListener("pointerup", (ev) => { if (y0 === null) return; const dy = ev.clientY - y0; y0 = null; el.style.transform = ""; if (dy > 80) sh.close("swipe"); });
    }
    sheets.push(sh);
    // 最初のフォーカスは見出し（中の最初のボタンにすると、そこまで自動で動いて文が途中から見える。
    // 入力欄にするとスマホのキーボードが勝手に開いてシートを隠す）。読み始めは本文の頭から
    setTimeout(() => { sh.body.scrollTop = 0; $("h2", el).focus({ preventScroll: true }); }, 30);
    return sh;
  }
  // シートの中にフォーカスを閉じ込める（aria-modal の約束。Tab で背面へ抜けない。WCAG 2.4.3）
  const FOCUSABLE = "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex='-1'])";
  document.addEventListener("keydown", (ev) => {
    if (!sheets.length) return;
    const top = sheets[sheets.length - 1];
    if (ev.key === "Escape") { ev.preventDefault(); top.close("esc"); return; }
    if (ev.key !== "Tab") return;
    const fs = $$(FOCUSABLE, top.el).filter((x) => x.offsetParent !== null || x === document.activeElement);
    if (!fs.length) { ev.preventDefault(); return; }
    const first = fs[0], last = fs[fs.length - 1], a = document.activeElement;
    if (!top.el.contains(a)) { ev.preventDefault(); first.focus(); }
    else if (ev.shiftKey && (a === first || a === $("h2", top.el))) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && a === last) { ev.preventDefault(); first.focus(); }
  });
  // 確認（confirm の代わり）。true / false で返す
  function confirmSheet({ title, text, ok = "続ける", cancel = "やめる", danger = false }) {
    return new Promise((resolve) => {
      let done = false;
      const sh = openSheet({ title, label: "confirm", html: `<p>${esc(text)}</p><div class="stack"><button class="btn block ${danger ? "danger" : "primary"}" data-ok>${esc(ok)}</button><button class="btn block" data-cancel>${esc(cancel)}</button></div>`,
        onClose: () => { if (!done) { done = true; resolve(false); } } });
      $("[data-ok]", sh.el).addEventListener("click", () => { done = true; resolve(true); sh.close("ok"); });
      $("[data-cancel]", sh.el).addEventListener("click", () => sh.close("cancel"));
    });
  }

  // ---- 図の部品（figures.js）は要るときだけ読む ----
  let figuresLoading = null;
  function loadFigures() {
    if (window.Figures) return Promise.resolve();
    if (!figuresLoading) {
      figuresLoading = new Promise((ok, ng) => {
        const sc = document.createElement("script");
        sc.src = "figures.js";
        sc.onload = ok;
        sc.onerror = () => { figuresLoading = null; ng(new Error("図の部品（figures.js）を読めませんでした")); };
        document.head.appendChild(sc);
      });
    }
    return figuresLoading;
  }

  // ---- ルーター ----
  const routes = [];
  // pattern: "/session/:id/shots"。opt: { tab, wide, noTabbar }
  function route(pattern, render, opt = {}) {
    const keys = [];
    const re = new RegExp("^" + pattern.replace(/:(\w+)/g, (_, k) => { keys.push(k); return "([^/]+)"; }) + "$");
    routes.push({ pattern, re, keys, render, opt });
  }
  const current = { path: "", token: 0 };
  function parseHash() {
    const raw = (location.hash || "").replace(/^#/, "");
    let h;
    try { h = decodeURIComponent(raw); } catch { h = raw; } // 壊れた % でも画面を止めない（知らない道ならホームへ）
    h = h || "/home";
    const [path, qs] = h.split("?");
    return { path: path || "/home", query: Object.fromEntries(new URLSearchParams(qs || "")) };
  }
  function go(path, { replace = false } = {}) {
    const h = "#" + path;
    if (replace) { history.replaceState(null, "", h); render(); } else if (location.hash === h) render(); else location.hash = h;
  }
  async function render() {
    const { path, query } = parseHash();
    let hit = null, params = {};
    for (const r of routes) {
      const m = r.re.exec(path);
      if (m) { hit = r; r.keys.forEach((k, i) => { params[k] = m[i + 1]; }); break; }
    }
    if (!hit) { go("/home", { replace: true }); return; }
    const token = ++current.token;
    const moved = current.path !== path;
    current.path = path;
    while (sheets.length) sheets[sheets.length - 1].close("route");
    const view = $("#view");
    const el = document.createElement("div");
    el.dataset.route = hit.pattern;
    view.replaceChildren(el);
    view.dataset.route = hit.pattern;
    view.classList.toggle("wide", !!hit.opt.wide);
    document.body.classList.toggle("no-tabbar", !!hit.opt.noTabbar);
    for (const a of $$("#tabbar a")) {
      if (a.dataset.tab === hit.opt.tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    }
    if (moved) window.scrollTo(0, 0);
    const ctx = { el, params, query, alive: () => token === current.token };
    try {
      await hit.render(ctx);
    } catch (e) {
      if (!ctx.alive()) return;
      el.innerHTML = errOf(e, { what: "この画面を出せませんでした", saved: "入れた記録は消えていません。", next: "少し待ってから、もう一度読み込んでください。", retry: true });
      console.warn(e);
    }
    if (ctx.alive()) {
      for (const sg of $$(".seg", el)) watchSeg(sg);
      const h1 = $("h1", el);
      if (h1 && moved) { h1.tabIndex = -1; h1.focus({ preventScroll: true }); }
      document.title = (h1 ? h1.textContent.trim() + " - " : "") + "Swing Lab";
    }
  }
  window.addEventListener("hashchange", render);
  // 横に動かせるセグメント（診断のクラブなど）: 続きがある側の端を薄くし、選んでいるものを見える所へ動かす
  function watchSeg(sg) {
    if (sg.dataset.watched) return;
    sg.dataset.watched = "1";
    const update = () => {
      const more = sg.scrollWidth - sg.clientWidth;
      sg.classList.toggle("more-r", more > 2 && sg.scrollLeft < more - 2);
      sg.classList.toggle("more-l", more > 2 && sg.scrollLeft > 2);
    };
    const cur = sg.querySelector("[aria-current=page],[aria-pressed=true]");
    if (cur && sg.scrollWidth > sg.clientWidth) sg.scrollLeft = Math.max(0, cur.offsetLeft - sg.offsetLeft - 48);
    sg.addEventListener("scroll", update, { passive: true });
    update();
  }

  // ---- 使う人（画面に「選手」を出さない。§2.2） ----
  function setPlayer(p) {
    S.player = p;
    if (p) { LS.set("golf.player", p.id); LS.set("golf.playerObj", p); if (p.name) LS.set("golf.playerName", p.name); }
  }
  async function loadPlayer() {
    const last = LS.get("golf.player");
    try {
      const ps = await api("GET", "/v1/players");
      setPlayer(ps.find((p) => p.id === last) || ps[0] || null);
      if (S.player && S.player.prefs && S.player.prefs.dist_unit) LS.set("golf.distUnit", S.player.prefs.dist_unit);
    } catch (e) {
      if (!e.offline) throw e;
      S.player = LS.get("golf.playerObj");
      setOffline(true);
    }
  }
  // 使う人がまだいないときに選んだ利き手（ようこそ・設定で選ぶ）。どの道から最初の記録を入れても、これで作る
  // （タブ・ブックマーク・#/record を直に開いたときに、右打ちで作ってしまわないように）
  const pendingHand = () => (LS.get("golf.handPending") === "L" ? "L" : "R");
  const setPendingHand = (v) => LS.set("golf.handPending", v === "L" ? "L" : "R");
  // 最初の記録の前に1人作る。hand を渡さなければ、使う人がいないうちに選んだ利き手
  async function ensurePlayer(hand) {
    if (S.player) return S.player;
    const r = await api("POST", "/v1/me", { handedness: (hand || pendingHand()) === "L" ? "L" : "R" });
    setPlayer(r.player);
    LS.del("golf.handPending");
    return S.player;
  }
  function setOffline(on) {
    S.offline = on;
    const el = $("#banners");
    const b = el && $("[data-banner=offline]", el);
    const text = netDown()
      ? "圏外です。最後に写した内容で動いています。練習のブロックは進められます（送るのは電波が戻ってから）。"
      : "サーバーにつながりません。最後に写した内容で動いています。練習のブロックは進められます（送るのはつながってから）。";
    if (on && el) {
      if (b) b.firstElementChild.textContent = text;
      else el.insertAdjacentHTML("afterbegin", `<div class="banner" data-banner="offline"><div>${esc(text)}</div></div>`);
    }
    if (!on && b) b.remove();
  }

  // セッションの一覧（記録・経過・練習で使う）。圏外なら写し
  async function sessions(fresh = false) {
    if (!S.player) return [];
    if (!fresh && S.cache.sessions) return S.cache.sessions;
    try {
      S.cache.sessions = await api("GET", `/v1/players/${S.player.id}/sessions`);
      LS.set(`golf.sessions.${S.player.id}`, S.cache.sessions);
    } catch (e) {
      if (!e.offline) throw e;
      S.cache.sessions = LS.get(`golf.sessions.${S.player.id}`) || [];
    }
    return S.cache.sessions;
  }
  // 球が変わったら、そのセッションの写しを捨てる（古い解説を出さない）
  function invalidate(sid) {
    delete S.cache.report[sid]; delete S.cache.analysis[sid]; delete S.cache.shots[sid];
    S.cache.sessions = null;
  }
  async function report(sid, fresh = false) {
    if (!fresh && S.cache.report[sid]) return S.cache.report[sid];
    const r = await api("GET", `/v1/sessions/${sid}/report`);
    S.cache.report[sid] = r;
    if (r && Array.isArray(r.shots)) S.cache.shots[sid] = r.shots;
    return r;
  }
  async function shots(sid, fresh = false) {
    if (!fresh && S.cache.shots[sid]) return S.cache.shots[sid];
    S.cache.shots[sid] = await api("GET", `/v1/sessions/${sid}/shots`);
    return S.cache.shots[sid];
  }

  // 戻るボタン（戻る先が分かるときはそこ、無ければ履歴）
  const backBtn = (href, label = "戻る") => `<a class="iconbtn" href="${esc(href)}" aria-label="${esc(label)}">${icon("chevron-left")}</a>`;
  const navItem = (href, title, sub, attrs = "") => `<li><a href="${esc(href)}" ${attrs}><span class="grow1">${title}${sub ? `<small>${sub}</small>` : ""}</span>${icon("chevron-right", "chev")}</a></li>`;

  // ---- 起動 ----
  async function start() {
    // 動作の状態（/healthz）。保存先が一時的・スクショが読めないときは、記録の画面と設定で知らせる
    fetch("/healthz").then((r) => r.json()).then((h) => { S.health = h; }).catch(() => {});
    // 使う人の写しがあれば、サーバーを待たずに先に描く（寝起きに白い画面で待たせない）。届いたら、変わっていれば描き直す
    const copy = LS.get("golf.playerObj");
    if (copy && copy.id != null && copy.id === LS.get("golf.player")) {
      S.player = copy;
      S.shownCopy = true;
      render();
      loadPlayer().then(() => {
        const p = S.player;
        if (!p || p.id !== copy.id || p.handedness !== copy.handedness) render();
      }).catch((e) => console.warn(e));
    } else {
      try { await loadPlayer(); } catch (e) {
        $("#view").innerHTML = errOf(e, { what: "サーバーで失敗しました", next: "少し待ってから、もう一度読み込んでください。" });
        return;
      }
      render();
    }
    // 圏外でも画面を開き直せるよう、画面のファイルだけを手元に置く（API は置かない。sw.js）
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  }
  document.addEventListener("DOMContentLoaded", start);

  return {
    APP_VERSION, APP_UPDATED, $, $$, esc, icon, lab, LS, S, api, request, fmt, LABEL, KIND, distUnit, clubJa, textJa, localDate, dateJa, localTime,
    toast, say, errorHtml, errOf, loading, openSheet, ask: confirmSheet, loadFigures, route, go, render, parseHash,
    ensurePlayer, pendingHand, setPendingHand, setPlayer, sessions, invalidate, report, shots, backBtn, navItem, setOffline, netDown,
  };
})();
