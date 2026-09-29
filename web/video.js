"use strict";
/*
  動画から P1〜P7 を手で選ぶ（docs/DESIGN_v2.md §6・§15 段2a）。開いたときだけ読む（index.html が <script defer> で置くが、
  MediaPipe と mp4box は使うときに import() する）。
  - **動画は端末の中だけで使う。** サーバーへ送るのは、選んだ P のコマの時刻・体の点（33点）・本人のタップ・
    長辺 360px のサムネイルだけ。長辺 1024px の JPEG は端末（IndexedDB golf-local の swingFrames）に置く。
  - 手順: ① 向きと番手 → ② コマ送りで P1〜P7 を選ぶ（見本の線画と定義の一文。写っていない P は押せる）
          → ③ ボールの両端・P2 のクラブ（握りの端と先）をタップ（任意で P1〜P7 全部のクラブ）
          → ④ 選んだコマでだけ体の点を取る → 送って測る → チェック一覧へ。
  - fps は ①ファイルの中の記録（mp4 / mov。mp4box.js）②再生して測る（requestVideoFrameCallback）の順。分からなければ 0（不明）。
  - 体の点は MediaPipe Pose Landmarker（lite・同梱）。window.__FAKE_POSE があればそれを使う（画面の確認で棒人間を使うため）。
  - タップ: ピンチ・ボタンで拡大、押した所の上に2倍の拡大鏡（利き手の側を避けて出す）、1画素ずつ動かすボタン（長押しで続けて）、
    点の形で区別（握りの端＝四角、先・ボール＝丸）、［このコマは飛ばす］はいつでも押せる（WCAG 2.5.7）。
*/
const Video = (() => {
  const { $, $$, esc, icon, lab, S, api, LS } = App;
  const V_MP = "vendor/mediapipe-tasks-vision-1.0.1";
  const MODEL = "vendor/mediapipe-models/pose_landmarker_lite-float16-v1.task";
  const V_MP4BOX = "vendor/mp4box-2.4.1/mp4box.all.mjs";
  const PS = ["P1", "P2", "P3", "P4", "P5", "P6", "P7"];
  const PS_OPT = ["P8", "P9", "P10"];
  const THUMB_EDGE = 360, KEEP_EDGE = 1024;
  const CLUBS = [
    ["Driver", "ドライバー"], ["3 Wood", "3番ウッド"], ["5 Wood", "5番ウッド"], ["4 Hybrid", "4番ユーティリティ"],
    ["4 Iron", "4番アイアン"], ["5 Iron", "5番アイアン"], ["6 Iron", "6番アイアン"], ["7 Iron", "7番アイアン"], ["8 Iron", "8番アイアン"], ["9 Iron", "9番アイアン"],
    ["PW", "ピッチングウェッジ"], ["GW", "ギャップウェッジ"], ["SW", "サンドウェッジ"], ["LW", "ロブウェッジ"],
  ];
  const classOf = (c) => /driver/i.test(c) ? "driver" : /wood/i.test(c) ? "wood" : /hybrid/i.test(c) ? "hybrid" : /^(GW|SW|LW|AW)$/i.test(c) ? "wedge" : "iron";

  // ---- カタログ（P の名前・定義・見本の線画）。写しを localStorage に持つ（圏外でも前回の版で描ける。§5.1） ----
  let catalogP = null;
  async function catalog() {
    const hand = (S.player && S.player.handedness) || "R";
    if (catalogP && catalogP.hand === hand) return catalogP.data;
    const key = `golf.cpCatalog.${hand}`;
    let data = null;
    try { data = await api("GET", `/v1/checkpoints?handedness=${hand}`); LS.set(key, data); } catch (e) {
      data = LS.get(key);
      if (!data) throw e;
    }
    catalogP = { hand, data };
    return data;
  }

  // ---- 端末の置き場（IndexedDB golf-local。§13.3）。失敗しても処理は止めない ----
  function idb() {
    return new Promise((ok, ng) => {
      if (!("indexedDB" in window)) { ng(new Error("IndexedDB がありません")); return; }
      const r = indexedDB.open("golf-local", 1);
      r.onupgradeneeded = () => { const db = r.result; for (const n of ["swingFrames", "pose", "refs", "strips"]) if (!db.objectStoreNames.contains(n)) db.createObjectStore(n); };
      r.onsuccess = () => ok(r.result);
      r.onerror = () => ng(r.error);
    });
  }
  async function keepFrame(key, blob) {
    try {
      if (navigator.storage && navigator.storage.persist && !LS.get("golf.persistAsked")) {
        LS.set("golf.persistAsked", true);
        navigator.storage.persist().then((v) => LS.set("golf.persisted", !!v)).catch(() => {});
      }
      const db = await idb();
      await new Promise((ok, ng) => { const tx = db.transaction("swingFrames", "readwrite"); tx.objectStore("swingFrames").put(blob, key); tx.oncomplete = ok; tx.onerror = () => ng(tx.error); });
      db.close();
      return true;
    } catch { return false; }
  }

  // ---- fps: ファイルの中の記録（mp4 / mov） ----
  // 上の階層の箱をたどって ftyp と moov だけを読み、mp4box に渡す（mdat は読まない。moov が末尾でも大きな動画を丸ごと読まない）
  async function containerInfo(file) {
    if (!/\.(mp4|mov|m4v)$/i.test(file.name || "") && !/mp4|quicktime/.test(file.type || "")) return null;
    const parts = [];
    let pos = 0;
    for (let n = 0; n < 64 && pos + 8 <= file.size; n++) {
      const hb = new DataView(await file.slice(pos, pos + 16).arrayBuffer());
      let size = hb.getUint32(0);
      const type = String.fromCharCode(hb.getUint8(4), hb.getUint8(5), hb.getUint8(6), hb.getUint8(7));
      if (size === 1) size = Number(hb.getBigUint64(8));
      else if (size === 0) size = file.size - pos;
      if (size < 8) break;
      if (type === "ftyp" || type === "moov") {
        if (size > 64 * 1024 * 1024) return null;
        parts.push(await file.slice(pos, pos + size).arrayBuffer());
      }
      if (type === "moov") break;
      pos += size;
    }
    if (!parts.some((b) => new DataView(b).getUint32(4) === 0x6d6f6f76)) return null;
    const buf = new Uint8Array(parts.reduce((s, b) => s + b.byteLength, 0));
    let o = 0;
    for (const b of parts) { buf.set(new Uint8Array(b), o); o += b.byteLength; }
    const mp4 = await import("./" + V_MP4BOX);
    return await new Promise((ok) => {
      const f = mp4.createFile();
      f.onError = () => ok(null);
      f.onReady = (info) => {
        const t = (info.videoTracks && info.videoTracks[0]) || (info.tracks || []).find((x) => x.video);
        if (!t || !t.nb_samples || !t.duration || !t.timescale) { ok(null); return; }
        const dur = t.duration / t.timescale;
        ok({ fps: Math.round((t.nb_samples / dur) * 100) / 100, samples: t.nb_samples, media_duration: Math.round(dur * 1000) / 1000, codec: t.codec || "",
          width: (t.video && t.video.width) || t.track_width || 0, height: (t.video && t.video.height) || t.track_height || 0 });
      };
      try {
        const ab = buf.buffer;
        const mb = mp4.MP4BoxBuffer ? mp4.MP4BoxBuffer.fromArrayBuffer(ab, 0) : Object.assign(ab, { fileStart: 0 });
        f.appendBuffer(mb);
        f.flush();
        setTimeout(() => ok(null), 1500);
      } catch { ok(null); }
    });
  }

  // ---- fps: 再生して測る（コンテナから読めないとき） ----
  const COMMON_FPS = [24, 25, 30, 48, 50, 60, 90, 100, 120, 180, 240, 480];
  function playbackFps(video) {
    return new Promise((ok) => {
      if (!("requestVideoFrameCallback" in HTMLVideoElement.prototype)) { ok(0); return; }
      const ts = [];
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        video.pause();
        const d = [];
        for (let i = 1; i < ts.length; i++) { const x = ts[i] - ts[i - 1]; if (x > 0.0005) d.push(x); }
        d.sort((a, b) => a - b);
        const med = d.length >= 5 ? d[Math.floor(d.length / 2)] : 0;
        if (!med) { ok(0); return; }
        // 測った値はコマの間隔の揺れで少しずれる。よくあるコマの速さに4%以内なら、そこへ寄せる
        const raw = 1 / med;
        const near = COMMON_FPS.reduce((a, b) => (Math.abs(b - raw) < Math.abs(a - raw) ? b : a));
        ok(Math.abs(near - raw) / near < 0.04 ? near : Math.round(raw * 10) / 10);
      };
      const cb = (_now, meta) => { ts.push(meta.mediaTime); if (ts.length >= 24) finish(); else video.requestVideoFrameCallback(cb); };
      video.requestVideoFrameCallback(cb);
      video.muted = true;
      video.play().catch(() => finish());
      setTimeout(finish, 2500);
    });
  }

  // ---- 体の点（MediaPipe・同梱。無ければ棒人間の偽物） ----
  let detector = null;
  async function poseDetector() {
    if (typeof window.__FAKE_POSE === "function") return { backend: "fake", detect: (_c, meta) => window.__FAKE_POSE(meta) };
    if (detector) return detector;
    const vision = await import("./" + V_MP + "/vision_bundle.mjs");
    const fileset = await vision.FilesetResolver.forVisionTasks(new URL(V_MP + "/wasm", location.href).href);
    let lm = null, backend = "gpu";
    for (const delegate of ["GPU", "CPU"]) {
      try {
        lm = await vision.PoseLandmarker.createFromOptions(fileset, { baseOptions: { modelAssetPath: new URL(MODEL, location.href).href, delegate }, runningMode: "IMAGE", numPoses: 1 });
        backend = delegate.toLowerCase();
        break;
      } catch (e) { if (delegate === "CPU") throw e; }
    }
    detector = {
      backend,
      detect: (canvas) => {
        const r = lm.detect(canvas);
        const p = r && r.landmarks && r.landmarks[0];
        return p ? p.map((q) => ({ x: +q.x.toFixed(5), y: +q.y.toFixed(5), visibility: +(q.visibility ?? 1).toFixed(3) })) : null;
      },
    };
    return detector;
  }
  // 同梱の MediaPipe が読めて動くか（画面の確認で呼ぶ。白いコマでは人が見つからないので null が返れば成功）
  async function selfTest() {
    const t0 = performance.now();
    const d = await poseDetector();
    const c = document.createElement("canvas");
    c.width = 256; c.height = 256;
    const g = c.getContext("2d");
    g.fillStyle = "white"; g.fillRect(0, 0, 256, 256);
    const r = d.detect(c, {});
    return { backend: d.backend, found: !!r, ms: Math.round(performance.now() - t0) };
  }

  // ---- コマ ----
  const seekTo = (video, t) => new Promise((ok) => {
    const tt = Math.max(0, Math.min(t, (video.duration || t) - 0.0005));
    if (Math.abs(video.currentTime - tt) < 1e-4 && video.readyState >= 2) { ok(); return; }
    const on = () => { video.removeEventListener("seeked", on); ok(); };
    video.addEventListener("seeked", on);
    video.currentTime = tt;
  });
  function frameCanvas(video, maxEdge = 0) {
    const w = video.videoWidth, h = video.videoHeight;
    const k = maxEdge ? Math.min(1, maxEdge / Math.max(w, h)) : 1;
    const c = document.createElement("canvas");
    c.width = Math.round(w * k); c.height = Math.round(h * k);
    c.getContext("2d").drawImage(video, 0, 0, c.width, c.height);
    return c;
  }
  const toBlob = (c, q = 0.8) => new Promise((ok) => c.toBlob((b) => ok(b), "image/jpeg", q));
  const b64 = (blob) => new Promise((ok, ng) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(",")[1] || ""); r.onerror = () => ng(r.error); r.readAsDataURL(blob); });

  // ---- 画面 ----
  const pTitle = (cat, p) => `${lab("p", p)} ${esc((cat.p_names || {})[p] || "")}`;
  const svgOf = (cat, p, view) => (cat.svg || {})[`${p.toLowerCase()}.${view}`] || "";

  async function render({ el, params, query, alive }) {
    const date = /^\d{4}-\d{2}-\d{2}$/.test(params.date || "") ? params.date : App.localDate();
    const st = {
      date, session: /^\d+$/.test(query.session || "") ? Number(query.session) : null,
      view: LS.get("golf.cpView") === "fo" ? "fo" : "dtl", club: LS.get("golf.cpClub") || "7 Iron",
      file: null, url: null, video: null, fps: 0, fpsSource: "", fpsStep: 30, container: null,
      cur: "P1", frames: {}, missing: new Set(), ball: null, taps: {}, perf: {},
    };
    el.innerHTML = `<div class="pagehead"><a class="iconbtn" href="#/record/${esc(date)}" aria-label="記録へ戻る">${icon("chevron-left")}</a><h1>動画から P を選ぶ</h1></div>
      <p class="caption" data-step aria-live="polite"></p><div data-body></div>`;
    let cat;
    try { cat = await catalog(); } catch (e) {
      if (!alive()) return;
      $("[data-body]", el).innerHTML = App.errOf(e, { what: "チェックポイントの基準を読めませんでした", saved: "まだ何も送っていません。", next: "つながる所で、もう一度開いてください。", retry: true });
      return;
    }
    if (!alive()) return;
    const cleanup = () => { if (st.url) URL.revokeObjectURL(st.url); };
    window.addEventListener("hashchange", cleanup, { once: true });
    stepSetup(el, st, cat);
  }

  function setStep(el, n, text) { $("[data-step]", el).innerHTML = `${lab("count", n + " / 4")}　${esc(text)}`; }

  // ① 向きと番手・動画を選ぶ
  function stepSetup(el, st, cat) {
    setStep(el, 1, "向きと番手を選び、動画を選びます");
    const body = $("[data-body]", el);
    body.innerHTML = `
      <section class="card" aria-labelledby="h-view"><h2 id="h-view" class="label">撮った向き</h2>
        <div class="seg" data-view role="group" aria-label="撮った向き">
          <button type="button" data-v="dtl" aria-pressed="${st.view === "dtl"}">${icon("view-dtl")}後ろから</button>
          <button type="button" data-v="fo" aria-pressed="${st.view === "fo"}">${icon("view-face")}正面から</button></div>
        <p class="caption">一本の動画は一つの向きです。同じ一球を両方から見ることはできません。</p>
        <label class="field"><span>番手</span><select data-club>${CLUBS.map(([v, t]) => `<option value="${esc(v)}" ${v === st.club ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label>
      </section>
      <label class="drop block" data-drop>${icon("video")}
        <input type="file" data-file accept="video/*,.mov,.mp4,.m4v,.webm" class="visually-hidden">
        <span><b>動画を選ぶ</b><span class="caption">端末の中で処理します。送るのは選んだコマの小さな写真と体の点だけです。</span></span></label>
      <div data-err></div>
      <ul class="navlist block">${App.navItem(`#/guide/${st.view}`, "撮り方ガイド", "カメラの置き方と、試し撮りの確かめ方", "data-guide")}</ul>`;
    $("[data-view]", body).addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-v]");
      if (!b) return;
      st.view = b.dataset.v;
      LS.set("golf.cpView", st.view);
      $$("[data-view] button", body).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      $("[data-guide]", body).setAttribute("href", `#/guide/${st.view}`);
    });
    $("[data-club]", body).addEventListener("change", (ev) => { st.club = ev.target.value; LS.set("golf.cpClub", st.club); });
    $("[data-file]", body).addEventListener("change", async (ev) => {
      const f = ev.target.files[0];
      if (!f) return;
      $("[data-err]", body).innerHTML = `<p class="loading-text" role="status">動画を読み込んでいます…</p>`;
      try { await loadVideo(st, f); stepChoose(el, st, cat); } catch (e) {
        $("[data-err]", body).innerHTML = App.errorHtml({ what: "この動画を読めませんでした", saved: "何も送っていません。",
          next: "iPhone ならカメラの設定の「フォーマット」を「互換性優先」にして撮り直すと読めることがあります。", detail: e && e.message });
      }
    });
  }

  async function loadVideo(st, f) {
    const t0 = performance.now();
    st.file = f;
    st.container = await containerInfo(f).catch(() => null);
    st.url = URL.createObjectURL(f);
    const v = document.createElement("video");
    v.muted = true; v.playsInline = true; v.preload = "auto";
    v.setAttribute("playsinline", ""); v.setAttribute("muted", "");
    v.src = st.url;
    await new Promise((ok, ng) => {
      v.addEventListener("loadeddata", ok, { once: true });
      v.addEventListener("error", () => ng(new Error("この形式の動画はこのブラウザで読めません")), { once: true });
      setTimeout(() => ng(new Error("動画の読み込みが終わりません")), 20000);
    });
    if (!v.videoWidth || !v.videoHeight) throw new Error("動画の大きさが分かりません");
    st.video = v;
    const elDur = isFinite(v.duration) ? v.duration : 0;
    if (st.container && st.container.fps > 0) {
      st.fps = st.container.fps; st.fpsSource = "container";
      // 画面の上の時間で1コマ進む幅（スローの動画は、記録の fps と再生の時間がずれる）
      st.fpsStep = elDur ? st.container.samples / elDur : st.container.fps;
    } else {
      const f2 = await playbackFps(v);
      await seekTo(v, 0);
      st.fps = f2; st.fpsSource = f2 ? "playback" : ""; st.fpsStep = f2 || 30;
    }
    st.duration = elDur;
    st.perf.load_ms = Math.round(performance.now() - t0);
    await seekTo(v, 0.5 / st.fpsStep);
  }

  const frameNo = (st) => Math.round(st.video.currentTime * st.fpsStep - 0.5);
  const toFrame = (st, n) => seekTo(st.video, (Math.max(0, n) + 0.5) / st.fpsStep);

  function chipsHtml(st) {
    const one = (p, opt) => {
      const done = st.frames[p], miss = st.missing.has(p), cur = st.cur === p;
      const mark = done ? "✓" : miss ? "―" : opt ? "" : "△";
      const word = done ? "選んだ" : miss ? "写っていない" : opt ? "任意" : "まだ";
      return `<button type="button" class="pchip ${done ? "done" : miss ? "miss" : ""}" data-p="${p}" ${cur ? `aria-current="step"` : ""} aria-label="${p} ${word}"><span aria-hidden="true">${mark}</span>${lab("p", p)}</button>`;
    };
    return `<div class="pchips" data-pchips role="group" aria-label="P を選ぶ">${PS.map((p) => one(p, false)).join("")}<span class="pchips-sep" aria-hidden="true"></span>${PS_OPT.map((p) => one(p, true)).join("")}</div>`;
  }

  // ② P1〜P7 を手で選ぶ
  function stepChoose(el, st, cat) {
    setStep(el, 2, "コマ送りで P を選びます");
    const body = $("[data-body]", el);
    body.innerHTML = `
      <div class="vframe" data-vframe></div>
      <div class="scrub"><input type="range" data-scrub min="0" max="${Math.max(1, Math.round(st.duration * st.fpsStep) - 1)}" step="1" value="${frameNo(st)}" aria-label="コマの位置"></div>
      <div class="stepbtns" role="group" aria-label="コマ送り">
        <button type="button" class="btn small" data-mv="-0.5s" aria-label="半秒戻る">−半秒</button>
        <button type="button" class="btn small" data-mv="-1" aria-label="一コマ戻る">−1コマ</button>
        <button type="button" class="btn small" data-mv="+1" aria-label="一コマ進む">＋1コマ</button>
        <button type="button" class="btn small" data-mv="+0.5s" aria-label="半秒進む">＋半秒</button></div>
      <section class="card pinfo" aria-live="polite" data-pinfo></section>
      <div class="stack block"><button type="button" class="btn primary block" data-ok>このコマでよい</button>
        <button type="button" class="btn block" data-miss>このPは写っていない</button></div>
      <div data-chips></div>
      <div class="block" data-next-wrap></div>`;
    const vf = $("[data-vframe]", body);
    st.video.className = "vid";
    st.video.setAttribute("aria-label", "選んでいる動画のコマ");
    vf.append(st.video);
    const scrub = $("[data-scrub]", body);
    const paint = () => {
      $("[data-pinfo]", body).innerHTML = `<div class="prow"><div class="pdef"><p class="t-headline" style="margin:0">${pTitle(cat, st.cur)}${PS_OPT.includes(st.cur) ? `<span class="chip none">任意</span>` : ""}</p>
          <p class="sub" style="margin:var(--s1) 0 0">${esc(((cat.p_define || {})[st.cur]) || "")}</p></div>
          <figure class="psample" aria-label="${esc(st.cur)} の見本の線画">${svgOf(cat, st.cur, st.view)}</figure></div>`;
      $("[data-chips]", body).innerHTML = chipsHtml(st);
      const ready = PS.every((p) => st.frames[p] || st.missing.has(p));
      $("[data-next-wrap]", body).innerHTML = ready
        ? `<button type="button" class="btn primary block" data-go-taps>次へ（ボールとクラブの点を押す）</button>`
        : `<p class="caption">P1〜P7 を全部選ぶか、「写っていない」を押すと次へ進めます（P8〜P10 は任意）。</p>`;
      const gb = $("[data-go-taps]", body);
      if (gb) gb.addEventListener("click", () => stepTaps(el, st, cat));
      scrub.value = String(frameNo(st));
    };
    const move = async (how) => {
      const n = frameNo(st);
      if (how === "+1") await toFrame(st, n + 1);
      else if (how === "-1") await toFrame(st, n - 1);
      else if (how === "+0.5s") await toFrame(st, n + Math.round(st.fpsStep / 2));
      else if (how === "-0.5s") await toFrame(st, n - Math.round(st.fpsStep / 2));
      scrub.value = String(frameNo(st));
    };
    body.addEventListener("click", async (ev) => {
      const mv = ev.target.closest("[data-mv]");
      if (mv) { await move(mv.dataset.mv); return; }
      const pc = ev.target.closest("[data-p]");
      if (pc) {
        st.cur = pc.dataset.p;
        if (st.frames[st.cur]) await toFrame(st, st.frames[st.cur].frame);
        paint();
        return;
      }
      if (ev.target.closest("[data-ok]")) {
        st.frames[st.cur] = { t: +st.video.currentTime.toFixed(4), frame: frameNo(st) };
        st.missing.delete(st.cur);
        advance();
        return;
      }
      if (ev.target.closest("[data-miss]")) {
        delete st.frames[st.cur];
        st.missing.add(st.cur);
        advance();
      }
    });
    const advance = () => {
      const all = [...PS, ...PS_OPT];
      const i = all.indexOf(st.cur);
      const next = all.slice(i + 1).find((p) => !st.frames[p] && !st.missing.has(p) && PS.includes(p)) || all[Math.min(i + 1, all.length - 1)];
      st.cur = next;
      paint();
      App.say(`${next} を選びます`);
    };
    scrub.addEventListener("input", () => toFrame(st, Number(scrub.value)));
    paint();
  }

  // ---- タップの部品（§6.5） ----
  // points: [{key, label, shape: "square" | "circle"}]。決定で {key: [x, y]}（コマの画素）、飛ばすで null を返す
  function tapEditor(host, { canvas, title, hint, points, initial = {}, hand = "R" }) {
    return new Promise((resolve) => {
      const W = canvas.width, H = canvas.height;
      const pts = {};
      for (const p of points) if (initial[p.key]) pts[p.key] = [...initial[p.key]];
      let cur = points.findIndex((p) => !pts[p.key]);
      if (cur < 0) cur = 0;
      let zoom = 1;
      host.innerHTML = `
        <h2 class="t-headline" style="margin:0 0 var(--s1)">${esc(title)}</h2>
        <p class="sub" style="margin:0 0 var(--s2)">${esc(hint)}</p>
        <p class="tapnow" data-now aria-live="polite"></p>
        <div class="tapwrap" data-wrap><div class="tapstage" data-stage><canvas data-over aria-hidden="true"></canvas></div><canvas class="loupe" data-loupe width="160" height="160" aria-hidden="true" hidden></canvas></div>
        <div class="row between"><div class="seg" data-zoom role="group" aria-label="拡大">${[1, 2, 3].map((z) => `<button type="button" data-z="${z}" aria-pressed="${z === 1}">${z}倍</button>`).join("")}</div>
          <div class="nudge" role="group" aria-label="点を一画素ずつ動かす">
            <button type="button" class="iconbtn" data-nd="0,-1" aria-label="上へ">↑</button><button type="button" class="iconbtn" data-nd="-1,0" aria-label="左へ">←</button>
            <button type="button" class="iconbtn" data-nd="1,0" aria-label="右へ">→</button><button type="button" class="iconbtn" data-nd="0,1" aria-label="下へ">↓</button></div></div>
        <div class="row pointsel" data-sel role="group" aria-label="置く点">${points.map((p, i) => `<button type="button" class="btn small" data-pi="${i}">${p.shape === "square" ? "■" : "●"} ${esc(p.label)}</button>`).join("")}</div>
        <div class="stack block"><button type="button" class="btn primary block" data-done>決定</button>
          <div class="row"><button type="button" class="btn grow1" data-redo>やり直す</button><button type="button" class="btn grow1" data-skip>このコマは飛ばす</button></div></div>`;
      const stage = $("[data-stage]", host), over = $("[data-over]", host), loupe = $("[data-loupe]", host), wrap = $("[data-wrap]", host);
      canvas.classList.add("tapimg");
      stage.prepend(canvas);
      over.width = W; over.height = H;
      const g = over.getContext("2d");
      const css = getComputedStyle(document.documentElement);
      const draw = () => {
        g.clearRect(0, 0, W, H);
        const r = Math.max(6, Math.round(Math.min(W, H) * 0.012));
        for (const [i, p] of points.entries()) {
          const q = pts[p.key];
          if (!q) continue;
          g.lineWidth = Math.max(2, r / 3);
          for (const [stroke, lw] of [[css.getPropertyValue("--ov-edge").trim() || "black", g.lineWidth + 3], [css.getPropertyValue(i === cur ? "--ov-gap" : "--ov-me").trim() || "white", g.lineWidth]]) {
            g.strokeStyle = stroke; g.lineWidth = lw;
            g.beginPath();
            if (p.shape === "square") g.rect(q[0] - r, q[1] - r, 2 * r, 2 * r); else g.arc(q[0], q[1], r, 0, Math.PI * 2);
            g.stroke();
          }
        }
        host.dataset.pts = JSON.stringify(pts); // 画面の確認が読む（押した点・コマの画素）
        const p = points[cur];
        $("[data-now]", host).innerHTML = `いま置いている点: <b>${p.shape === "square" ? "■" : "●"} ${esc(p.label)}</b>${pts[p.key] ? "（置きました。ボタンで一画素ずつ直せます）" : "（まだ）"}`;
        $$("[data-pi]", host).forEach((b, i) => b.setAttribute("aria-pressed", String(i === cur)));
        $("[data-done]", host).toggleAttribute("disabled", !points.every((q) => pts[q.key]));
      };
      const setZoom = (z) => {
        zoom = z;
        stage.style.width = `${z * 100}%`;
        $$("[data-z]", host).forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.z) === z)));
      };
      const toPx = (ev) => {
        const r = canvas.getBoundingClientRect();
        return [Math.round(((ev.clientX - r.left) / r.width) * W), Math.round(((ev.clientY - r.top) / r.height) * H)];
      };
      const showLoupe = (ev, q) => {
        const lr = wrap.getBoundingClientRect();
        const lg = loupe.getContext("2d");
        const s = 40;
        lg.imageSmoothingEnabled = false;
        lg.clearRect(0, 0, 160, 160);
        lg.drawImage(canvas, q[0] - s, q[1] - s, 2 * s, 2 * s, 0, 0, 160, 160);
        lg.strokeStyle = "white"; lg.lineWidth = 3; lg.strokeRect(78, 78, 4, 4);
        lg.strokeStyle = "black"; lg.lineWidth = 1; lg.strokeRect(76, 76, 8, 8);
        loupe.hidden = false;
        // 指で隠れない側（右手で押すなら左上、左手なら右上）
        const x = ev.clientX - lr.left, y = ev.clientY - lr.top;
        const left = hand === "L" ? Math.min(lr.width - 164, x + 40) : Math.max(4, x - 200);
        loupe.style.left = `${left}px`;
        loupe.style.top = `${Math.max(4, y - 200)}px`;
      };
      // ピンチで拡大（ボタンでも同じことができる）
      const active = new Map();
      let pinch0 = null;
      stage.addEventListener("pointerdown", (ev) => {
        active.set(ev.pointerId, ev);
        if (active.size === 2) { const [a, b] = [...active.values()]; pinch0 = { d: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY), z: zoom }; loupe.hidden = true; return; }
        stage.setPointerCapture(ev.pointerId);
        const q = toPx(ev);
        pts[points[cur].key] = q;
        showLoupe(ev, q);
        draw();
      });
      stage.addEventListener("pointermove", (ev) => {
        if (!active.has(ev.pointerId)) return;
        active.set(ev.pointerId, ev);
        if (pinch0 && active.size === 2) {
          // 二本指: 広げると拡大、動かすと拡大した絵をずらす
          const [a, b] = [...active.values()];
          const z = Math.max(1, Math.min(4, (pinch0.z * Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY)) / pinch0.d));
          const mx = (a.clientX + b.clientX) / 2, my = (a.clientY + b.clientY) / 2;
          if (pinch0.mx !== undefined) { wrap.scrollLeft -= mx - pinch0.mx; wrap.scrollTop -= my - pinch0.my; }
          pinch0.mx = mx; pinch0.my = my;
          stage.style.width = `${z * 100}%`; zoom = z;
          return;
        }
        const q = toPx(ev);
        pts[points[cur].key] = q;
        showLoupe(ev, q);
        draw();
      });
      const up = (ev) => {
        active.delete(ev.pointerId);
        if (active.size < 2) pinch0 = null;
        if (!active.size) {
          loupe.hidden = true;
          // 置いたら次の点へ（全部置いたら、いまの点のまま）
          const nxt = points.findIndex((p) => !pts[p.key]);
          if (nxt >= 0) cur = nxt;
          else if (cur < points.length - 1) cur += 1; // 前のコマの点が入っているときも、押した順に次の点へ
          draw();
        }
      };
      stage.addEventListener("pointerup", up);
      stage.addEventListener("pointercancel", up);
      $("[data-zoom]", host).addEventListener("click", (ev) => { const b = ev.target.closest("[data-z]"); if (b) setZoom(Number(b.dataset.z)); });
      $("[data-sel]", host).addEventListener("click", (ev) => { const b = ev.target.closest("[data-pi]"); if (b) { cur = Number(b.dataset.pi); draw(); } });
      // 1画素ずつ（長押しで続けて動く）
      let rep = null;
      const nudge = (dx, dy) => {
        const k = points[cur].key;
        const q = pts[k] || [Math.round(W / 2), Math.round(H / 2)];
        pts[k] = [Math.max(0, Math.min(W - 1, q[0] + dx)), Math.max(0, Math.min(H - 1, q[1] + dy))];
        draw();
      };
      const stopRep = () => { clearInterval(rep); clearTimeout(rep); rep = null; };
      for (const b of $$("[data-nd]", host)) {
        const [dx, dy] = b.dataset.nd.split(",").map(Number);
        b.addEventListener("click", (ev) => { if (ev.detail === 0) nudge(dx, dy); });
        b.addEventListener("pointerdown", () => { nudge(dx, dy); rep = setTimeout(() => { rep = setInterval(() => nudge(dx, dy), 60); }, 400); });
        for (const e of ["pointerup", "pointerleave", "pointercancel"]) b.addEventListener(e, stopRep);
      }
      host.addEventListener("keydown", (ev) => {
        const m = { ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0] }[ev.key];
        if (m && ev.target.closest("[data-nd],[data-stage]")) { ev.preventDefault(); nudge(...m); }
      });
      $("[data-redo]", host).addEventListener("click", () => { for (const p of points) delete pts[p.key]; cur = 0; draw(); });
      $("[data-skip]", host).addEventListener("click", () => { stopRep(); resolve(null); });
      $("[data-done]", host).addEventListener("click", () => { stopRep(); if (points.every((q) => pts[q.key])) resolve({ ...pts }); });
      setZoom(1);
      draw();
    });
  }

  // ③ ボールの両端・P2 のクラブ・（任意で）P1〜P7 のクラブ
  async function stepTaps(el, st, cat) {
    const body = $("[data-body]", el);
    const hand = (S.player && S.player.handedness) || App.pendingHand();
    const shot = async (p) => { await toFrame(st, st.frames[p].frame); return frameCanvas(st.video, KEEP_EDGE * 2); };
    const scaleOf = (c) => c.width / st.video.videoWidth; // タップはコマの画素（元の大きさ）で持つ
    const back = (q, k) => [Math.round(q[0] / k), Math.round(q[1] / k)];
    const fwd = (q, k) => q && [q[0] * k, q[1] * k];
    if (st.frames.P1) {
      setStep(el, 3, "構えのコマでボールの両端を押します");
      const c = await shot("P1"), k = scaleOf(c);
      const r = await tapEditor(body, { canvas: c, hand, title: `${"P1"} 構え: ボールの両端`, hint: "ボールの左の端と右の端を押します。ボール一個の大きさを物差しにします（手の位置・横のずれを測る）。",
        points: [{ key: "b1", label: "ボールの端（一つ目）", shape: "circle" }, { key: "b2", label: "ボールの端（二つ目）", shape: "circle" }],
        initial: st.ball ? { b1: fwd(st.ball[0], k), b2: fwd(st.ball[1], k) } : {} });
      st.ball = r ? [back(r.b1, k), back(r.b2, k)] : null;
    }
    if (st.frames.P2) {
      setStep(el, 3, "P2 でクラブの握りの端と先を押します");
      const c = await shot("P2"), k = scaleOf(c);
      const r = await tapEditor(body, { canvas: c, hand, title: "P2 クラブが水平（上げ）: クラブ", hint: "クラブの握りの端と、クラブの先（真ん中）を押します。上げ始めのクラブの先の位置を測ります（最初に見る項目）。",
        points: [{ key: "grip", label: "握りの端", shape: "square" }, { key: "head", label: "クラブの先", shape: "circle" }], initial: mapTaps(st.taps.P2, (q) => fwd(q, k)) });
      if (r) st.taps.P2 = { grip: back(r.grip, k), head: back(r.head, k) };
    }
    askMoreTaps(el, st, cat, shot, scaleOf, back, fwd, hand);
  }
  const mapTaps = (t, f) => { const o = {}; for (const [k, v] of Object.entries(t || {})) o[k] = f(v); return o; };

  function askMoreTaps(el, st, cat, shot, scaleOf, back, fwd, hand) {
    setStep(el, 3, "ほかのコマのクラブ（任意）");
    const body = $("[data-body]", el);
    const rest = PS.filter((p) => p !== "P2" && st.frames[p]);
    const clubItems = (cat.items || []).filter((it) => it.view === st.view && rest.includes(it.p) && /tap/.test((it.measure || {}).how || "") && it.definition_status === "ok" && !it.same_as && (it.clubs || []).includes(classOf(st.club) === "driver" ? "driver" : "iron")).length;
    body.innerHTML = `<section class="card"><h2 class="t-headline" style="margin-top:0">クラブの位置を付ける（任意）</h2>
      <p class="sub">残りのコマ（${rest.map((p) => lab("p", p)).join("・")}）でも握りの端と先を押すと、クラブの傾きの項目も測れます（${lab("count", clubItems + "件")}）。一コマ十秒ほどです。</p>
      <div class="stack"><button type="button" class="btn block" data-more>クラブの位置を付ける</button>
      <button type="button" class="btn primary block" data-send>このまま測る</button></div></section>`;
    $("[data-send]", body).addEventListener("click", () => stepProcess(el, st, cat));
    $("[data-more]", body).addEventListener("click", async () => {
      let last = st.taps.P2;
      for (const p of rest) {
        const c = await shot(p), k = scaleOf(c);
        setStep(el, 3, `${p} のクラブ`);
        const r = await tapEditor(body, { canvas: c, hand, title: `${p} ${(cat.p_names || {})[p] || ""}: クラブ`, hint: "クラブの握りの端と、クラブの先を押します。ぶれて見えないときは「このコマは飛ばす」。",
          points: [{ key: "grip", label: "握りの端", shape: "square" }, { key: "head", label: "クラブの先", shape: "circle" }],
          // 前のコマの点を初めの位置に置く（§6.5）。押し直せば動く
          initial: mapTaps(st.taps[p] || last, (q) => fwd(q, k)) });
        if (r) { st.taps[p] = { grip: back(r.grip, k), head: back(r.head, k) }; last = st.taps[p]; }
      }
      stepProcess(el, st, cat);
    });
  }

  // ④ 選んだコマでだけ体の点を取る → 送って測る
  async function stepProcess(el, st, cat) {
    setStep(el, 4, "体の点を取って測ります");
    const body = $("[data-body]", el);
    const stages = ["コマを切り出す", "体の点を取る", "送って測る"];
    body.innerHTML = `<section class="card"><ol class="stages" data-stages>${stages.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
      <p class="caption">このあいだは画面を閉じないでください。</p><div data-err></div></section>`;
    const mark = (i) => { $$("[data-stages] li", body).forEach((li, j) => { li.className = j < i ? "done" : j === i ? "cur" : ""; }); App.say(stages[i] || ""); };
    let wake = null;
    try { if (navigator.wakeLock) wake = await navigator.wakeLock.request("screen"); } catch { /* 使えなくても続ける */ }
    const t0 = performance.now();
    try {
      mark(0);
      const chosen = [...PS, ...PS_OPT].filter((p) => st.frames[p]);
      const shots = {};
      for (const p of chosen) {
        await toFrame(st, st.frames[p].frame);
        shots[p] = { full: frameCanvas(st.video), thumb: frameCanvas(st.video, THUMB_EDGE), keep: frameCanvas(st.video, KEEP_EDGE) };
      }
      mark(1);
      let det = null, poseErr = "";
      try { det = await poseDetector(); } catch (e) { poseErr = String(e && e.message || e); }
      const tp = performance.now();
      const frames = [];
      for (const p of chosen) {
        let lms = null;
        if (det) {
          try { lms = det.detect(shots[p].full, { t: st.frames[p].t, frame: st.frames[p].frame, p, view: st.view, width: st.video.videoWidth, height: st.video.videoHeight }); } catch (e) { poseErr = String(e && e.message || e); }
        }
        const thumb = await b64(await toBlob(shots[p].thumb, 0.8));
        frames.push({ checkpoint: p, t: st.frames[p].t, frame: st.frames[p].frame, source: "manual", landmarks: lms && lms.length === 33 ? lms : [], taps: st.taps[p] || {}, thumb });
      }
      st.perf.pose_ms_per_frame = chosen.length ? Math.round((performance.now() - tp) / chosen.length) : 0;
      st.perf.pose_backend = det ? det.backend : "none";
      mark(2);
      const p = await App.ensurePlayer();
      let sid = st.session;
      if (!sid) {
        const s = await api("POST", "/v1/sessions", { player_id: p.id, date: st.date, location: "" });
        sid = s.id;
      }
      const cap = { container: st.container, fps_step: Math.round(st.fpsStep * 100) / 100, element_duration: Math.round((st.duration || 0) * 1000) / 1000,
        file_type: st.file.type || "", size_mb: Math.round((st.file.size / 1048576) * 10) / 10, ua: (navigator.userAgent || "").slice(0, 160), perf: { ...st.perf, total_ms: Math.round(performance.now() - t0) } };
      const sw = await api("POST", `/v1/sessions/${sid}/swings`, { view: st.view, club: st.club, club_class: classOf(st.club), fps: st.fps || 0, fps_source: st.fpsSource,
        width: st.video.videoWidth, height: st.video.videoHeight, duration: st.duration || 0, ball: st.ball || [], capture: cap });
      // 長辺 1024px の JPEG は端末にだけ置く（サーバーには送らない）
      for (const q of chosen) keepFrame(`${sw.id}:${q}`, await toBlob(shots[q].keep, 0.85));
      await api("PUT", `/v1/swings/${sw.id}/frames`, { frames, missing: [...st.missing], ball: st.ball || [] });
      App.invalidate(sid);
      if (wake) wake.release().catch(() => {});
      App.toast(poseErr ? "体の点を取れなかったコマがあります（その項目は判断できないになります）" : "測りました");
      App.go(`/session/${sid}/check`);
    } catch (e) {
      if (wake) wake.release().catch(() => {});
      $("[data-err]", body).innerHTML = App.errOf(e, { what: "測れませんでした", saved: "選んだコマはこの画面に残っています。", next: "もう一度押すか、つながる所で試してください。" }) + `<button type="button" class="btn block" data-again>もう一度送る</button>`;
      const b = $("[data-again]", body);
      if (b) b.addEventListener("click", () => stepProcess(el, st, cat));
    }
  }

  App.route("/video", render, { tab: "record", noTabbar: true });
  App.route("/video/:date", render, { tab: "record", noTabbar: true });
  return { catalog, containerInfo, selfTest, poseDetector };
})();
