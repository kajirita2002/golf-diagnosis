"use strict";
/*
  動画から P1〜P7 を取り出す（docs/DESIGN_v2.md §6・§15 段2a〜2b）。既定は自動（段2b）、手で選ぶ道も残す（段2a）。開いたときだけ読む（index.html が <script defer> で置くが、
  MediaPipe と mp4box は使うときに import() する）。
  - **動画は端末の中だけで使う。** サーバーへ送るのは、選んだ P のコマの時刻・体の点（33点）・本人のタップ・
    長辺 360px のサムネイルだけ。長辺 1024px の JPEG は端末（IndexedDB golf-local の swingFrames）に置く。
  - 手順: ① 向きと番手 → ② コマ送りで P1〜P7 を選ぶ（見本の線画と定義の一文。写っていない P は押せる）
          → ③ ボールの両端・P2 のクラブ（握りの端と先。ドライバーはクラブの先の幅も）をタップ（任意で P1〜P7 全部のクラブ）
          → ④ 選んだコマでだけ体の点を取る → 送って測る → チェック一覧へ。
  - **コマは「このコマでよい」を押した瞬間に一度だけ絵にして持つ**（JPEG の Blob）。タップ・体の点・サムネイルは全部この絵から作る。
    §6.3-2 の本命（WebCodecs で1コマずつデコード）はまだ入れておらず、<video> の currentTime で探したコマを使う（ずれとして記録）。
    探し直すたびに Safari の着地がずれると、押した点と体の点が別のコマのものになるので、探すのは選ぶときの一回だけにする。
  - 送り直しは同じスイングへ（作れた記録とスイングの id を覚え、コマの送信だけをやり直す。同じスイングを二本作らない＝R11）。
  - fps は ①ファイルの中の記録（mp4 / mov。mp4box.js）②再生して測る（requestVideoFrameCallback）の順。分からなければ 0（不明）。
  - 体の点は MediaPipe Pose Landmarker（lite・同梱）。window.__FAKE_POSE があればそれを使う（画面の確認で棒人間を使うため）。
  - 自動（段2b）: ⓪ 数コマを高めの解像度で見て人の範囲（体の点の外枠を、クラブが伸びるぶん上下左右に広げた枠）を決め、
            以降の体の点はその枠を切り出して拡大して取る（人が小さく写った動画のため。点は元のコマの座標に戻して送る）
          ① 粗い走査（1秒に10コマ）で体の点を取り、サーバー（video.py）がスイングの区間を返す
            （見つからなければ、サーバーが返す diag で「体の点が取れない」「振り上げが無い」などの理由と次の手を出す）
          → ② 最初のスイングの構えでボールの両端を押す（当たる瞬間の挟み込みと物差し）
          → ③ スイングの区間だけ細かく（最大1秒に120コマ）体の点とボールのまわりの変化を取り、P1〜P10 と中間を返してもらう
          → ④ 確かめるところ（自信の低いコマ・見つからなかった P）だけ本人が直す → ⑤ 代表の P2 のクラブ → 送って測る。
    処理の段・［止める］・画面を消さない（Wake Lock）・続きから処理する（取った体の点は端末の IndexedDB pose に残す）。
    処理の時間と、自動のまま使ったコマ・手で直したコマの数は capture に残す（段2b の完了条件: 手で直したコマの割合）。
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

  // 端末に置いた長辺 1024px のコマ（無ければ null）。見た目の評価（段2c）に送るときだけ読む
  async function keptFrame(key) {
    try {
      const db = await idb();
      const b = await new Promise((ok, ng) => { const tx = db.transaction("swingFrames", "readonly"); const r = tx.objectStore("swingFrames").get(key); r.onsuccess = () => ok(r.result || null); r.onerror = () => ng(r.error); });
      db.close();
      return b instanceof Blob ? b : null;
    } catch { return null; }
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
    // 偽物は元のコマの座標で点を返すので、切り出した枠があれば枠の中の座標に直して返す（本物と同じく、渡した絵の中の座標）
    if (typeof window.__FAKE_POSE === "function") return { backend: "fake", detect: (_c, meta) => {
      const r = window.__FAKE_POSE(meta);
      const b = meta && meta.box;
      if (!r || !b) return r;
      return r.map((q) => ({ ...q, x: (q.x * meta.width - b.x) / b.w, y: (q.y * meta.height - b.y) / b.h }));
    } };
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
        if (!p) return null;
        // 版によっては visibility が全部 0（値が入っていない）で返る。そのまま送ると全部「体の点が見えない」になるので、
        // 全部 0 のときは visibility を付けずに送る（分析サービスは無いとき見えているとみなす）。実機でどちらかは未確認
        const none = p.every((q) => !q.visibility);
        return p.map((q) => none ? { x: +q.x.toFixed(5), y: +q.y.toFixed(5) } : { x: +q.x.toFixed(5), y: +q.y.toFixed(5), visibility: +(q.visibility ?? 1).toFixed(3) });
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
  const GRAB_EDGE = 2048; // 選んだコマを持つ大きさの上限（4K でもタップの細かさが足りる。メモリは JPEG で持つ）
  const seekTo = (video, t) => new Promise((ok) => {
    const tt = Math.max(0, Math.min(t, (video.duration || t) - 0.0005));
    if (Math.abs(video.currentTime - tt) < 1e-4 && video.readyState >= 2) { ok(); return; }
    const on = () => { video.removeEventListener("seeked", on); ok(); };
    video.addEventListener("seeked", on);
    video.currentTime = tt;
  });
  function scaled(src, sw, sh, maxEdge = 0) {
    const k = maxEdge ? Math.min(1, maxEdge / Math.max(sw, sh)) : 1;
    const c = document.createElement("canvas");
    c.width = Math.round(sw * k); c.height = Math.round(sh * k);
    c.getContext("2d").drawImage(src, 0, 0, c.width, c.height);
    return c;
  }
  const frameCanvas = (video, maxEdge = 0) => scaled(video, video.videoWidth, video.videoHeight, maxEdge);
  const toBlob = (c, q = 0.8) => new Promise((ok) => c.toBlob((b) => ok(b), "image/jpeg", q));
  const b64 = (blob) => new Promise((ok, ng) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(",")[1] || ""); r.onerror = () => ng(r.error); r.readAsDataURL(blob); });
  // 持っている絵（Blob）→ canvas。maxEdge を渡せば縮める
  async function blobCanvas(blob, maxEdge = 0) {
    if (window.createImageBitmap) {
      const bmp = await createImageBitmap(blob);
      const c = scaled(bmp, bmp.width, bmp.height, maxEdge);
      if (bmp.close) bmp.close();
      return c;
    }
    const url = URL.createObjectURL(blob);
    try {
      const img = await new Promise((ok, ng) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => ng(new Error("コマの絵を読めません")); i.src = url; });
      return scaled(img, img.naturalWidth, img.naturalHeight, maxEdge);
    } finally { URL.revokeObjectURL(url); }
  }

  // ---- 画面 ----
  const pName = (cat, p) => esc((cat.p_names || {})[p] || "");
  const svgOf = (cat, p, view) => (cat.svg || {})[`${p.toLowerCase()}.${view}`] || "";
  const pIdx = (p) => [...PS, ...PS_OPT].indexOf(p);

  async function render({ el, params, query, alive }) {
    const date = /^\d{4}-\d{2}-\d{2}$/.test(params.date || "") ? params.date : App.localDate();
    const st = {
      date, session: /^\d+$/.test(query.session || "") ? Number(query.session) : null, sid: null, swingId: null, payload: null,
      view: LS.get("golf.cpView") === "fo" ? "fo" : "dtl", club: LS.get("golf.cpClub") || "7 Iron",
      mode: LS.get("golf.cpMode") === "manual" ? "manual" : "auto", auto: null, autoIds: {},
      file: null, url: null, video: null, fps: 0, fpsSource: "", fpsStep: 30, container: null,
      cur: "P1", frames: {}, missing: new Set(), ball: null, taps: {}, perf: {},
    };
    el.innerHTML = `<div class="pagehead"><a class="iconbtn" data-back href="#/record/${esc(date)}" aria-label="記録へ戻る">${icon("chevron-left")}</a><h1>動画から形を選ぶ</h1></div>
      <p class="caption" data-step aria-live="polite"></p><div data-body></div>`;
    // 選んだコマ・自動で取り出した形・処理中の作業があるときは、確かめずに捨てない（NN/g #3・#5）
    $("[data-back]", el).addEventListener("click", async (ev) => {
      const auto = !!st.auto || !!st.running;
      if (!Object.keys(st.frames).length && !st.missing.size && !auto) return;
      ev.preventDefault();
      const text = auto ? "取り出した形と確かめた結果は消えます。取った体の点はこの端末に残ります（同じ動画を選ぶと続きから処理します）。"
        : "選んだコマと押した点は消えます（まだ送っていません）。";
      const ok = await App.ask({ title: "記録の画面へ戻りますか", text, ok: "戻る（消す）", cancel: "続ける", danger: true });
      if (!ok) return;
      st.abort = true; st.toManual = false; // 裏の走査を止め、画面を消さない印（Wake Lock）も放す
      endRun(st);
      st.auto = null; st.frames = {}; st.missing.clear();
      App.go(`/record/${date}`);
    });
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

  // 段が変わったら、画面の頭から始め、見出しに移る（読み上げにも段が変わったことが伝わる）
  function setStep(el, n, text, sub = "") {
    $("[data-step]", el).innerHTML = `${lab("count", n + " / 4")}　${esc(text)}${sub ? `　${sub}` : ""}`;
  }
  // 段・画面ごとに入れ物を作り直す（同じ要素に付けた前の画面の押したときの処理が残ると、一回押して二回動く）
  function freshBody(el) {
    const old = $("[data-body]", el);
    const nb = old.cloneNode(false);
    old.replaceWith(nb);
    return nb;
  }
  function enter(el) {
    window.scrollTo(0, 0);
    const h = $("[data-body] h2", el) || $("h1", el);
    if (h) { h.tabIndex = -1; h.focus({ preventScroll: true }); }
  }

  // よく使う番手はボタン、残りは一覧（iOS のホイールで14個から選ばせない）
  const QUICK = [["7 Iron", "7番アイアン"], ["Driver", "ドライバー"]];

  // ① 向きと番手・動画を選ぶ
  function stepSetup(el, st, cat) {
    // 段の番号は手で選ぶときだけ（自動は処理の段を別に数えるので、二つの数え方を並べない）
    const setupStep = () => { if (st.mode === "manual") setStep(el, 1, "向きと番手を選び、動画を選びます"); else $("[data-step]", el).textContent = ""; };
    setupStep();
    const body = freshBody(el);
    const quick = QUICK.some(([v]) => v === st.club);
    body.innerHTML = `
      ${pendingNote()}
      <section class="card" aria-labelledby="h-view"><h2 id="h-view" class="label">撮った向き</h2>
        <div class="seg" data-view role="group" aria-label="撮った向き">
          <button type="button" data-v="dtl" aria-pressed="${st.view === "dtl"}">${icon("view-dtl")}後ろから</button>
          <button type="button" data-v="fo" aria-pressed="${st.view === "fo"}">${icon("view-face")}正面から</button></div>
        <p class="caption">一本の動画は一つの向きです。同じ一球を両方から見ることはできません。</p>
        <p class="label" id="h-club">番手</p>
        <div class="seg wrap" data-quick role="group" aria-labelledby="h-club">${QUICK.map(([v, t]) => `<button type="button" data-c="${esc(v)}" aria-pressed="${v === st.club}">${esc(t)}</button>`).join("")}
          <button type="button" data-c="" aria-pressed="${!quick}">ほかの番手</button></div>
        <label class="field" data-club-wrap ${quick ? "hidden" : ""}><span>ほかの番手</span><select data-club>${CLUBS.map(([v, t]) => `<option value="${esc(v)}" ${v === st.club ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label>
      </section>
      <section class="card" aria-labelledby="h-mode"><h2 id="h-mode" class="label">コマの選び方</h2>
        <div class="seg" data-mode-sel role="group" aria-labelledby="h-mode">
          <button type="button" data-md="auto" aria-pressed="${st.mode === "auto"}">自動で取り出す</button>
          <button type="button" data-md="manual" aria-pressed="${st.mode === "manual"}">手で選ぶ</button></div>
        <p class="caption" data-mode-note>${st.mode === "auto" ? "全部のスイングの形を自動で探し、自信の低いコマだけ確かめます。" : "一つのスイングの形を、コマ送りで選びます。"}</p></section>
      ${st.video ? `<button type="button" class="btn primary block" data-keep>選んだ動画で続ける</button>` : ""}
      <label class="drop block" data-drop>${icon("video")}
        <input type="file" data-file accept="video/*,.mov,.mp4,.m4v,.webm" class="visually-hidden">
        <span><b>${st.video ? "別の動画を選ぶ" : "動画を選ぶ"}</b><span class="caption">動画は送りません。送るのは体の点の数値と、選んだコマの小さな写真だけです（自動のときは、形を探すために全部のコマの体の点を一度送ります。保存するのは選んだコマの分だけです）。</span></span></label>
      <div data-err></div>
      ${pending() ? `<button type="button" class="textbtn" data-drop-pending>途中の分を消す</button>` : ""}
      <ul class="navlist block">${App.navItem(`#/guide/${st.view}`, "撮り方ガイド", "カメラの置き方と、試し撮りの確かめ方", "data-guide")}</ul>`;
    $("[data-view]", body).addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-v]");
      if (!b) return;
      st.view = b.dataset.v;
      LS.set("golf.cpView", st.view);
      $$("[data-view] button", body).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      $("[data-guide]", body).setAttribute("href", `#/guide/${st.view}`);
    });
    $("[data-quick]", body).addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-c]");
      if (!b) return;
      $$("[data-quick] button", body).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      const wrap = $("[data-club-wrap]", body);
      if (b.dataset.c) { st.club = b.dataset.c; LS.set("golf.cpClub", st.club); wrap.hidden = true; } else { wrap.hidden = false; st.club = $("[data-club]", body).value; LS.set("golf.cpClub", st.club); }
    });
    $("[data-club]", body).addEventListener("change", (ev) => { st.club = ev.target.value; LS.set("golf.cpClub", st.club); });
    $("[data-mode-sel]", body).addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-md]");
      if (!b) return;
      st.mode = b.dataset.md;
      LS.set("golf.cpMode", st.mode);
      $$("[data-mode-sel] button", body).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      $("[data-mode-note]", body).textContent = st.mode === "auto" ? "全部のスイングの形を自動で探し、自信の低いコマだけ確かめます。" : "一つのスイングの形を、コマ送りで選びます。";
      setupStep();
    });
    const pickSame = $("[data-pick-same]", body);
    if (pickSame) pickSame.addEventListener("click", () => $("[data-file]", body).click());
    const dropPending = $("[data-drop-pending]", body);
    if (dropPending) {
      dropPending.addEventListener("click", async () => {
        const ok = await App.ask({ title: "途中の分を消しますか", text: "この端末に残した体の点を消します。同じ動画を選んでも、最初から処理し直しになります。", ok: "消す", cancel: "やめる", danger: true });
        if (!ok) return;
        await clearPending(true);
        stepSetup(el, st, cat);
      });
    }
    const keep = $("[data-keep]", body);
    if (keep) keep.addEventListener("click", () => (st.mode === "auto" ? stepAuto(el, st, cat) : stepChoose(el, st, cat)));
    $("[data-file]", body).addEventListener("change", async (ev) => {
      const f = ev.target.files[0];
      if (!f) return;
      $("[data-err]", body).innerHTML = `<p class="loading-text" role="status">動画を読み込んでいます…</p>`;
      try {
        if (st.url) URL.revokeObjectURL(st.url);
        Object.assign(st, { frames: {}, missing: new Set(), ball: null, taps: {}, cur: "P1", swingId: null, payload: null, auto: null, autoIds: {}, box: undefined });
        await loadVideo(st, f);
        if (st.mode === "auto") stepAuto(el, st, cat); else stepChoose(el, st, cat);
      } catch (e) {
        $("[data-err]", body).innerHTML = App.errorHtml({ what: "この動画を読めませんでした", saved: "何も送っていません。",
          next: "iPhone ならカメラの設定の「フォーマット」を「互換性優先」にして撮り直すと読めることがあります。", detail: e && e.message });
      }
    });
    enter(el);
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
    st.perf.grab = "seek_once"; // §6.3-2 の WebCodecs ではなく currentTime で探したコマ（ずれとして残す）
    await seekTo(v, 0.5 / st.fpsStep);
  }

  const frameNo = (st) => Math.round(st.video.currentTime * st.fpsStep - 0.5);
  const toFrame = (st, n) => seekTo(st.video, (Math.max(0, n) + 0.5) / st.fpsStep);

  // 選んだ P の時刻が順番どおりか（P1＜P2＜…）。ずれている P を返す
  function orderErrors(st) {
    const bad = [];
    let prev = null;
    for (const p of [...PS, ...PS_OPT]) {
      const f = st.frames[p];
      if (!f) continue;
      if (prev && f.t <= st.frames[prev].t) bad.push([p, prev]);
      prev = p;
    }
    return bad;
  }

  function chipsHtml(st, bad) {
    const one = (p, opt) => {
      const done = st.frames[p], miss = st.missing.has(p), cur = st.cur === p, wrong = bad.includes(p);
      // 印: 選んだ ✓・写っていない ―・順番が違う !・まだ は空の丸（「△」は一覧で「範囲の外」の印なので使わない）
      const mark = wrong ? "!" : done ? "✓" : miss ? "―" : "○";
      const word = wrong ? "順番が違う" : done ? "選んだ" : miss ? "写っていない" : opt ? "任意" : "まだ";
      return `<button type="button" class="pchip ${wrong ? "wrong" : done ? "done" : miss ? "miss" : ""}" data-p="${p}" ${cur ? `aria-current="step"` : ""} aria-label="${p} ${esc((App.S.cpNames || {})[p] || "")} ${word}"><span aria-hidden="true">${mark}</span>${lab("p", p)}</button>`;
    };
    return `<div class="pchips" data-pchips role="group" aria-label="形を選ぶ">${PS.map((p) => one(p, false)).join("")}</div>
      <div class="pchips opt" role="group" aria-label="任意の形"><span class="caption">任意</span>${PS_OPT.map((p) => one(p, true)).join("")}</div>`;
  }

  // ② P1〜P7 を手で選ぶ（押した瞬間のコマを絵にして持つ）
  function stepChoose(el, st, cat) {
    setStep(el, 2, "コマ送りで、それぞれの形のコマを選びます");
    App.S.cpNames = cat.p_names || {};
    const body = freshBody(el);
    body.innerHTML = `
      <h2 class="visually-hidden">形を選ぶ</h2>
      <div class="vframe" data-vframe></div>
      <div data-chips></div>
      <div class="scrub"><input type="range" data-scrub min="0" max="${Math.max(1, Math.round(st.duration * st.fpsStep) - 1)}" step="1" value="${frameNo(st)}" aria-label="コマの位置"></div>
      <div class="stepbtns" role="group" aria-label="コマ送り">
        <button type="button" class="btn small" data-mv="-0.5s" aria-label="半秒戻る">−半秒</button>
        <button type="button" class="btn small" data-mv="-1" aria-label="一コマ戻る">−1コマ</button>
        <button type="button" class="btn small" data-mv="+1" aria-label="一コマ進む">＋1コマ</button>
        <button type="button" class="btn small" data-mv="+0.5s" aria-label="半秒進む">＋半秒</button></div>
      <div class="row choosebtns"><button type="button" class="btn primary grow1" data-ok>このコマでよい</button>
        <button type="button" class="btn" data-miss>写っていない</button></div>
      <section class="card pinfo" aria-live="polite" data-pinfo></section>
      <div class="block" data-next-wrap></div>
      <button type="button" class="btn block" data-prev-step>${icon("chevron-left")}前へ（向きと番手）</button>`;
    const vf = $("[data-vframe]", body);
    st.video.className = "vid";
    st.video.setAttribute("aria-label", "選んでいる動画のコマ");
    vf.append(st.video);
    const scrub = $("[data-scrub]", body);
    const paint = () => {
      const bad = orderErrors(st);
      const badPs = bad.map((x) => x[0]);
      $("[data-pinfo]", body).innerHTML = `<div class="prow"><div class="pdef"><p class="t-headline" style="margin:0">${pName(cat, st.cur)} <span class="caption">${lab("p", st.cur)}</span>${PS_OPT.includes(st.cur) ? `<span class="chip none">任意</span>` : ""}</p>
          <p class="sub" style="margin:var(--s1) 0 0">${esc(((cat.p_define || {})[st.cur]) || "")}</p></div>
          <figure class="psample" aria-label="${esc((cat.p_names || {})[st.cur] || "")} の見本の線画">${svgOf(cat, st.cur, st.view)}</figure></div>`;
      $("[data-chips]", body).innerHTML = chipsHtml(st, badPs);
      const ready = PS.every((p) => st.frames[p] || st.missing.has(p));
      $("[data-next-wrap]", body).innerHTML = bad.length
        ? `<div class="note warn" data-order>${bad.map(([p, q]) => `${pName(cat, p)}（${lab("p", p)}）のコマが、${pName(cat, q)}（${lab("p", q)}）より前になっています。`).join("")}選び直してください。</div>`
        : ready ? `<button type="button" class="btn primary block" data-go-taps>次へ（ボールとクラブの点を押す）</button>`
          : `<p class="caption">必須の七つの形を全部選ぶか、「写っていない」を押すと次へ進めます（フォローの三つは任意）。</p>`;
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
    // 押した順に一つずつ片づける（コマを絵にしているあいだに押されたコマ送りを、落とさず後に回す）
    let chain = Promise.resolve();
    body.addEventListener("click", (ev) => { chain = chain.then(() => onClick(ev)).catch((e) => console.warn(e)); });
    const onClick = async (ev) => {
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
        // いま画面に出ているコマを、その場で一度だけ絵にする（あとで探し直さない）
        const t = +st.video.currentTime.toFixed(4), n = frameNo(st);
        const blob = await toBlob(frameCanvas(st.video, GRAB_EDGE), 0.92);
        st.frames[st.cur] = { t, frame: n, blob };
        st.missing.delete(st.cur);
        delete st.taps[st.cur];
        st.payload = null;
        advance();
        return;
      }
      if (ev.target.closest("[data-miss]")) {
        delete st.frames[st.cur];
        delete st.taps[st.cur];
        st.missing.add(st.cur);
        st.payload = null;
        advance();
        return;
      }
      if (ev.target.closest("[data-prev-step]")) stepSetup(el, st, cat);
    };
    const advance = () => {
      const all = [...PS, ...PS_OPT];
      const i = all.indexOf(st.cur);
      const next = all.slice(i + 1).find((p) => !st.frames[p] && !st.missing.has(p) && PS.includes(p)) || all[Math.min(i + 1, all.length - 1)];
      st.cur = next;
      paint();
      App.say(`${(cat.p_names || {})[next] || next} を選びます`);
    };
    scrub.addEventListener("input", () => toFrame(st, Number(scrub.value)));
    paint();
    enter(el);
  }

  // ---- タップの部品（§6.5） ----
  // points: [{key, label, shape}]。shape: square（握りの端）・circle（クラブの先）・half-l / half-r（ボールの左右の端）・tri-l / tri-r（クラブの先の幅）
  // 決定で {key: [x, y]}（コマの画素）、飛ばすで null、前へで "back" を返す
  const SHAPE_MARK = { square: "■", circle: "●", "half-l": "◐", "half-r": "◑", "tri-l": "◀", "tri-r": "▶" };
  function tapEditor(host, { canvas, p = "", title, hint, points, initial = {}, hand = "R", progress = "", zoomOnFirst = false, canBack = true }) {
    return new Promise((resolve) => {
      const W = canvas.width, H = canvas.height;
      const pts = {};
      for (const p of points) if (initial[p.key]) pts[p.key] = [...initial[p.key]];
      let cur = points.findIndex((p) => !pts[p.key]);
      if (cur < 0) cur = 0;
      let zoom = 1, mode = "place";
      host.innerHTML = `
        <h2 class="t-headline" style="margin:0 0 var(--s1)" data-tap-p="${esc(p)}">${esc(title)}${p ? ` <span class="caption">${lab("p", p)}</span>` : ""}</h2>
        ${progress ? `<p class="caption" data-tprog>${progress}</p>` : ""}
        <p class="sub" style="margin:0 0 var(--s2)">${esc(hint)}</p>
        <p class="tapnow" data-now aria-live="polite"></p>
        <div class="tapwrap" data-wrap><div class="tapstage" data-stage><canvas data-over aria-hidden="true"></canvas></div></div>
        <canvas class="loupe" data-loupe width="160" height="160" aria-hidden="true" hidden></canvas>
        <div class="tapbar"><div class="seg" data-zoom role="group" aria-label="拡大">${[1, 2, 3].map((z) => `<button type="button" data-z="${z}" aria-pressed="${z === 1}">${z}倍</button>`).join("")}</div>
          <div class="seg" data-mode role="group" aria-label="指の動き"><button type="button" data-m="place" aria-pressed="true">点を置く</button><button type="button" data-m="pan" aria-pressed="false">絵を動かす</button></div></div>
        <p class="caption" data-panhint>拡大した絵は、二本指か「絵を動かす」で動かせます。</p>
        <div class="row between"><div class="row pointsel" data-sel role="group" aria-label="置く点">${points.map((p, i) => `<button type="button" class="btn small" data-pi="${i}">${SHAPE_MARK[p.shape] || "●"} ${esc(p.label)}</button>`).join("")}</div>
          <div class="nudge" role="group" aria-label="点を一画素ずつ動かす">
            <button type="button" class="iconbtn" data-nd="0,-1" aria-label="上へ">↑</button><button type="button" class="iconbtn" data-nd="-1,0" aria-label="左へ">←</button>
            <button type="button" class="iconbtn" data-nd="1,0" aria-label="右へ">→</button><button type="button" class="iconbtn" data-nd="0,1" aria-label="下へ">↓</button></div></div>
        <div class="stack block"><button type="button" class="btn primary block" data-done>決定</button>
          <p class="caption" data-need aria-live="polite"></p>
          <div class="row"><button type="button" class="btn grow1" data-redo>やり直す</button><button type="button" class="btn grow1" data-skip>このコマは飛ばす</button></div>
          ${canBack ? `<button type="button" class="btn block" data-tback>${icon("chevron-left")}前へ</button>` : ""}</div>`;
      const stage = $("[data-stage]", host), over = $("[data-over]", host), loupe = $("[data-loupe]", host), wrap = $("[data-wrap]", host);
      canvas.classList.add("tapimg");
      stage.prepend(canvas);
      over.width = W; over.height = H;
      const g = over.getContext("2d");
      const css = getComputedStyle(document.documentElement);
      const shapePath = (shape, x, y, r) => {
        g.beginPath();
        if (shape === "square") g.rect(x - r, y - r, 2 * r, 2 * r);
        else if (shape === "tri-l") { g.moveTo(x - r, y); g.lineTo(x + r, y - r); g.lineTo(x + r, y + r); g.closePath(); }
        else if (shape === "tri-r") { g.moveTo(x + r, y); g.lineTo(x - r, y - r); g.lineTo(x - r, y + r); g.closePath(); }
        else g.arc(x, y, r, 0, Math.PI * 2);
      };
      const draw = () => {
        g.clearRect(0, 0, W, H);
        const r = Math.max(6, Math.round(Math.min(W, H) * 0.012));
        for (const [i, p] of points.entries()) {
          const q = pts[p.key];
          if (!q) continue;
          const lw0 = Math.max(2, r / 3);
          const main = css.getPropertyValue(i === cur ? "--ov-gap" : "--ov-me").trim() || "white";
          for (const [stroke, lw] of [[css.getPropertyValue("--ov-edge").trim() || "black", lw0 + 3], [main, lw0]]) {
            g.strokeStyle = stroke; g.lineWidth = lw;
            shapePath(p.shape, q[0], q[1], r);
            g.stroke();
          }
          // ボールの端は、左右どちらの端かを塗り分けでも示す（色だけに頼らない。WCAG 1.4.1）
          if (p.shape === "half-l" || p.shape === "half-r") {
            g.fillStyle = main;
            g.beginPath();
            const a0 = p.shape === "half-l" ? Math.PI / 2 : -Math.PI / 2;
            g.arc(q[0], q[1], r, a0, a0 + Math.PI);
            g.closePath();
            g.fill();
          }
        }
        host.dataset.pts = JSON.stringify(pts); // 画面の確認が読む（押した点・コマの画素）
        const p = points[cur];
        $("[data-now]", host).innerHTML = `いま置いている点: <b>${SHAPE_MARK[p.shape] || "●"} ${esc(p.label)}</b>${pts[p.key] ? "（置きました。ボタンで一画素ずつ直せます）" : "（まだ）"}`;
        $$("[data-pi]", host).forEach((b, i) => b.setAttribute("aria-pressed", String(i === cur)));
        const left = points.filter((q) => !pts[q.key]);
        $("[data-done]", host).toggleAttribute("disabled", left.length > 0);
        $("[data-need]", host).textContent = left.length ? `あと${["", "一つ", "二つ", "三つ", "四つ"][left.length] || left.length + "つ"}、${left.map((q) => `${SHAPE_MARK[q.shape] || "●"} ${q.label}`).join("・")}を押すと決定できます。` : "";
      };
      // 1倍は絵の全体が枠に収まる大きさ（縦長の動画でもボールのある下の端が切れない）。拡大したときだけ枠の中を動かす
      const maxH = () => Math.max(220, Math.round(window.innerHeight * 0.55));
      const baseFrac = () => { const cw = wrap.clientWidth || 1; return Math.min(1, (maxH() * W / H) / cw); };
      const centerOn = (q) => {
        const sw = stage.clientWidth, sh = stage.clientHeight;
        wrap.scrollLeft = Math.max(0, (q[0] / W) * sw - wrap.clientWidth / 2);
        wrap.scrollTop = Math.max(0, (q[1] / H) * sh - wrap.clientHeight / 2);
      };
      const setZoom = (z, q) => {
        zoom = z;
        wrap.style.maxHeight = `${maxH()}px`;
        stage.style.width = `${z * baseFrac() * 100}%`;
        stage.style.margin = z === 1 ? "0 auto" : "0";
        $$("[data-z]", host).forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.z) === z)));
        // 拡大したら、いまの点（無ければ最後に置いた点、それも無ければ絵の真ん中）を真ん中に出す
        const c = q || pts[points[cur].key] || Object.values(pts).pop() || [W / 2, H / 2];
        requestAnimationFrame(() => centerOn(c));
      };
      const setMode = (m) => {
        mode = m;
        $$("[data-m]", host).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.m === m)));
        stage.classList.toggle("panning", m === "pan");
      };
      // 押した所 → コマの画素（コマの外へ指を滑らせても、点はコマの端に止める）
      const toPx = (ev) => {
        const r = canvas.getBoundingClientRect();
        const x = Math.round(((ev.clientX - r.left) / r.width) * W), y = Math.round(((ev.clientY - r.top) / r.height) * H);
        return [Math.max(0, Math.min(W - 1, x)), Math.max(0, Math.min(H - 1, y))];
      };
      // 拡大鏡は枠の外（画面に固定）に、指で隠れない側へ出す（右手で押すなら左上、左手なら右上）
      const showLoupe = (ev, q) => {
        const lg = loupe.getContext("2d");
        const s = 40;
        lg.imageSmoothingEnabled = false;
        lg.clearRect(0, 0, 160, 160);
        lg.drawImage(canvas, q[0] - s, q[1] - s, 2 * s, 2 * s, 0, 0, 160, 160);
        lg.strokeStyle = "white"; lg.lineWidth = 3; lg.strokeRect(78, 78, 4, 4);
        lg.strokeStyle = "black"; lg.lineWidth = 1; lg.strokeRect(76, 76, 8, 8);
        loupe.hidden = false;
        const vw = window.innerWidth;
        const left = hand === "L" ? Math.min(vw - 168, ev.clientX + 48) : Math.max(8, ev.clientX - 208);
        const top = ev.clientY - 208 >= 8 ? ev.clientY - 208 : Math.min(window.innerHeight - 168, ev.clientY + 48);
        loupe.style.left = `${Math.max(8, left)}px`;
        loupe.style.top = `${Math.max(8, top)}px`;
      };
      const active = new Map();
      let pinch0 = null, pan0 = null;
      stage.addEventListener("pointerdown", (ev) => {
        active.set(ev.pointerId, ev);
        if (active.size === 2) { const [a, b] = [...active.values()]; pinch0 = { d: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY), z: zoom }; loupe.hidden = true; pan0 = null; return; }
        stage.setPointerCapture(ev.pointerId);
        if (mode === "pan") { pan0 = { x: ev.clientX, y: ev.clientY, l: wrap.scrollLeft, t: wrap.scrollTop }; return; }
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
          stage.style.width = `${z * baseFrac() * 100}%`; zoom = z;
          return;
        }
        if (pan0) { wrap.scrollLeft = pan0.l - (ev.clientX - pan0.x); wrap.scrollTop = pan0.t - (ev.clientY - pan0.y); return; }
        if (mode === "pan") return;
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
          if (pan0) { pan0 = null; return; }
          if (mode === "pan") return;
          const placed = pts[points[cur].key];
          // ボールは小さいので、一つ目を置いたら、そのあたりを三倍で出す
          if (zoomOnFirst && zoom === 1 && placed) { setZoom(3, placed); zoomOnFirst = false; }
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
      $("[data-mode]", host).addEventListener("click", (ev) => { const b = ev.target.closest("[data-m]"); if (b) setMode(b.dataset.m); });
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
      const tb = $("[data-tback]", host);
      if (tb) tb.addEventListener("click", () => { stopRep(); resolve("back"); });
      $("[data-done]", host).addEventListener("click", () => { stopRep(); if (points.every((q) => pts[q.key])) resolve({ ...pts }); });
      setMode("place");
      setZoom(1);
      draw();
      window.scrollTo(0, 0);
      const h2 = $("h2", host);
      if (h2) { h2.tabIndex = -1; h2.focus({ preventScroll: true }); }
    });
  }

  const CLUB_PTS = [{ key: "grip", label: "握りの端", shape: "square" }, { key: "head", label: "クラブの先", shape: "circle" }];
  // ドライバーの基準は「クラブの先一個」（§5.3 C）。クラブの先の幅を、根元側と先端側の端の二点で測る
  const WIDTH_PTS = [{ key: "heel", label: "先の根元側の端", shape: "tri-l" }, { key: "toe", label: "先の先端側の端", shape: "tri-r" }];

  // ③ ボールの両端・P2 のクラブ・（任意で）ほかのコマのクラブ。前へで一つ前の画面に戻れる
  async function stepTaps(el, st, cat) {
    const hand = (S.player && S.player.handedness) || App.pendingHand();
    const driver = classOf(st.club) === "driver";
    const canvasOf = (p) => blobCanvas(st.frames[p].blob);
    const scaleOf = (c) => c.width / st.video.videoWidth; // タップはコマの画素（元の大きさ）で持つ
    const back = (q, k) => [Math.round(q[0] / k), Math.round(q[1] / k)];
    const fwd = (q, k) => q && [q[0] * k, q[1] * k];
    const clubPts = (p) => (driver && (p === "P2" || p === "P4") ? [...CLUB_PTS, ...WIDTH_PTS] : CLUB_PTS);
    // 画面の並び（ボール → P2 → 任意で残りのコマ）。前へで戻れるように、番号で進める
    const base = [];
    if (st.frames.P1) base.push("ball");
    if (st.frames.P2) base.push("P2");
    let screens = base.slice(), i = 0, askedMore = false;
    for (;;) {
      if (i >= screens.length) {
        if (askedMore) { stepProcess(el, st, cat); return; }
        const more = await askMoreTaps(el, st, cat);
        if (more === "back") {
          if (!screens.length) { stepChoose(el, st, cat); return; }
          i = screens.length - 1;
          continue;
        }
        if (more === "send") { stepProcess(el, st, cat); return; }
        screens = base.concat(PS.filter((q) => q !== "P2" && st.frames[q]));
        askedMore = true;
        continue;
      }
      const sc = screens[i];
      const prog = `点を押す ${lab("count", (i + 1) + " / " + screens.length)}`;
      let r;
      if (sc === "ball") {
        setStep(el, 3, "構えのコマでボールの両端を押します");
        const c = await canvasOf("P1"), k = scaleOf(c);
        r = await tapEditor(freshBody(el), { canvas: c, p: "P1", hand, progress: prog, zoomOnFirst: !st.ball, title: `${(cat.p_names || {}).P1 || "構え"}: ボールの両端`,
          hint: "ボールのあたりを一度押すと拡大します。ボールの左の端と右の端を押します（ボール一個の大きさを物差しにします）。",
          points: [{ key: "b1", label: "左の端", shape: "half-l" }, { key: "b2", label: "右の端", shape: "half-r" }],
          initial: st.ball ? { b1: fwd(st.ball[0], k), b2: fwd(st.ball[1], k) } : {} });
        if (r !== "back") st.ball = r ? [back(r.b1, k), back(r.b2, k)] : null;
      } else {
        const p = sc;
        setStep(el, 3, `${(cat.p_names || {})[p] || p}のクラブを押します`);
        const c = await canvasOf(p), k = scaleOf(c);
        const pts = clubPts(p);
        const wide = pts.length > 2 ? "。ドライバーはクラブの先の両端も押します（先の大きさを物差しにします）" : "";
        // 前のコマの点を初めの位置に置く（§6.5。握りの端と先だけ）。押し直せば動く
        const prev = st.taps[p] || (i > 0 && st.taps[screens[i - 1]]) || (p !== "P2" && st.taps.P2) || null;
        r = await tapEditor(freshBody(el), { canvas: c, p, hand, progress: prog, title: `${(cat.p_names || {})[p] || ""}: クラブ`,
          hint: p === "P2" ? `クラブの握りの端と、クラブの先（真ん中）を押します${wide}。上げ始めのクラブの先の位置を測ります（最初に見る項目）。`
            : `クラブの握りの端と、クラブの先を押します${wide}。ぶれて見えないときは「このコマは飛ばす」。`,
          points: pts,
          initial: mapTaps(st.taps[p] || (prev && { grip: prev.grip, head: prev.head }), (q) => fwd(q, k)) });
        if (r !== "back") { if (r) st.taps[p] = mapTaps(r, (q) => back(q, k)); else delete st.taps[p]; }
      }
      if (r === "back") {
        if (i === 0) { stepChoose(el, st, cat); return; }
        i -= 1;
        continue;
      }
      st.payload = null;
      i += 1;
    }
  }
  const mapTaps = (t, f) => { const o = {}; for (const [k, v] of Object.entries(t || {})) if (v) o[k] = f(v); return o; };

  // 残りのコマのクラブ（任意）。"more" / "send" / "back" を返す
  function askMoreTaps(el, st, cat) {
    return new Promise((resolve) => {
      setStep(el, 3, "ほかのコマのクラブ（任意）");
      const body = freshBody(el);
      const rest = PS.filter((p) => p !== "P2" && st.frames[p]);
      const cls = classOf(st.club) === "driver" ? "driver" : "iron";
      const clubItems = (cat.items || []).filter((it) => it.view === st.view && rest.includes(String(it.p || "").split("-").pop()) && /tap/.test((it.measure || {}).how || "")
        && it.definition_status === "ok" && it.judge === "binary" && !it.same_as && (it.clubs || []).includes(cls)).length;
      body.innerHTML = `<section class="card"><h2 class="t-headline" style="margin-top:0">クラブの位置を付ける（任意）</h2>
        <p class="sub">残りのコマ（${rest.map((p) => esc((cat.p_names || {})[p] || p)).join("・")}）でも握りの端と先を押すと、クラブの傾きの項目も測れます（${lab("count", clubItems + "件")}）。一コマ十秒ほどです。</p>
        <div class="stack"><button type="button" class="btn block" data-more ${rest.length ? "" : "disabled"}>クラブの位置を付ける</button>
        <button type="button" class="btn primary block" data-send>このまま測る</button>
        <button type="button" class="btn block" data-tback>${icon("chevron-left")}前へ</button></div></section>`;
      $("[data-send]", body).addEventListener("click", () => resolve("send"));
      $("[data-more]", body).addEventListener("click", () => resolve("more"));
      $("[data-tback]", body).addEventListener("click", () => resolve("back"));
      enter(el);
    });
  }

  // 送る前に、点がコマの中にあるかを確かめる（外なら、どの点を置き直すかを返す）
  function outsidePoints(st) {
    const W = st.video.videoWidth, H = st.video.videoHeight;
    const inside = (q) => Array.isArray(q) && q[0] >= 0 && q[0] <= W && q[1] >= 0 && q[1] <= H;
    const bad = [];
    if (st.ball && !st.ball.every(inside)) bad.push("ボールの端");
    for (const [p, t] of Object.entries(st.taps)) if (!Object.values(t).every(inside)) bad.push(`${p} のクラブ`);
    return bad;
  }

  // ④ 選んだコマでだけ体の点を取る → 送って測る。送り直しは同じスイングへ
  async function stepProcess(el, st, cat) {
    setStep(el, 4, "体の点を取って測ります");
    const body = freshBody(el);
    const stages = ["コマを用意する", "体の点を取る", "送って測る"];
    body.innerHTML = `<section class="card"><h2 class="visually-hidden">測っています</h2><ol class="stages" data-stages>${stages.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
      <p class="caption">このあいだは画面を閉じないでください。</p><div data-err></div></section>`;
    enter(el);
    const mark = (i) => { $$("[data-stages] li", body).forEach((li, j) => { li.className = j < i ? "done" : j === i ? "cur" : ""; }); App.say(stages[i] || ""); };
    const bad = outsidePoints(st);
    if (bad.length) { showSendError(el, st, cat, body, { status: 400, message: `${bad.join("・")}の点がコマの外です` }); return; }
    let wake = null;
    try { if (navigator.wakeLock) wake = await navigator.wakeLock.request("screen"); } catch { /* 使えなくても続ける */ }
    const t0 = performance.now();
    let poseErr = "";
    try {
      if (!st.payload) {
        mark(0);
        const chosen = [...PS, ...PS_OPT].filter((p) => st.frames[p]);
        const shots = {};
        for (const p of chosen) shots[p] = await blobCanvas(st.frames[p].blob);
        mark(1);
        let det = null;
        try { det = await poseDetector(); } catch (e) { poseErr = String(e && e.message || e); }
        const tp = performance.now();
        const frames = [];
        let noVis = 0;
        for (const p of chosen) {
          let lms = null;
          if (det) {
            try { lms = det.detect(shots[p], { t: st.frames[p].t, frame: st.frames[p].frame, p, view: st.view, width: st.video.videoWidth, height: st.video.videoHeight }); } catch (e) { poseErr = String(e && e.message || e); }
          }
          if (lms && lms.length === 33 && lms.every((q) => q.visibility === undefined)) noVis += 1;
          const thumb = await b64(await toBlob(scaled(shots[p], shots[p].width, shots[p].height, THUMB_EDGE), 0.8));
          frames.push({ checkpoint: p, t: st.frames[p].t, frame: st.frames[p].frame, source: "manual", landmarks: lms && lms.length === 33 ? lms : [], taps: st.taps[p] || {}, thumb });
          st.frames[p].keep = scaled(shots[p], shots[p].width, shots[p].height, KEEP_EDGE);
        }
        st.perf.pose_ms_per_frame = chosen.length ? Math.round((performance.now() - tp) / chosen.length) : 0;
        st.perf.pose_backend = det ? det.backend : "none";
        if (noVis) st.perf.visibility_missing = noVis;
        st.payload = { frames, missing: [...st.missing], ball: st.ball || [], poseErr };
      }
      poseErr = st.payload.poseErr;
      mark(2);
      const p = await App.ensurePlayer();
      if (!st.sid) {
        st.sid = st.session || (await api("POST", "/v1/sessions", { player_id: p.id, date: st.date, location: "" })).id;
      }
      if (!st.swingId) {
        const cap = { container: st.container, fps_step: Math.round(st.fpsStep * 100) / 100, element_duration: Math.round((st.duration || 0) * 1000) / 1000,
          file_type: st.file.type || "", size_mb: Math.round((st.file.size / 1048576) * 10) / 10, ua: (navigator.userAgent || "").slice(0, 160), perf: { ...st.perf, total_ms: Math.round(performance.now() - t0) } };
        const sw = await api("POST", `/v1/sessions/${st.sid}/swings`, { view: st.view, club: st.club, club_class: classOf(st.club), fps: st.fps || 0, fps_source: st.fpsSource,
          width: st.video.videoWidth, height: st.video.videoHeight, duration: st.duration || 0, ball: st.ball || [], capture: cap });
        st.swingId = sw.id;
        // 長辺 1024px の JPEG は端末にだけ置く（サーバーには送らない）
        for (const q of Object.keys(st.frames)) if (st.frames[q].keep) keepFrame(`${sw.id}:${q}`, await toBlob(st.frames[q].keep, 0.85));
      }
      await api("PUT", `/v1/swings/${st.swingId}/frames`, { frames: st.payload.frames, missing: st.payload.missing, ball: st.payload.ball });
      App.invalidate(st.sid);
      if (window.Checks) Checks.invalidate(st.sid);
      if (wake) wake.release().catch(() => {});
      st.frames = {}; st.missing.clear(); // 送り終えたので、戻るで「消えます」と聞かない
      await clearPendingFor(st); // 同じ動画を自動の途中から手で選んで送った: 途中の印を消す（ホームを「続きから」に固定しない）
      App.toast(poseErr ? "体の点を取れなかったコマがあります（その項目は判断できないになります）" : "測りました");
      App.go(`/session/${st.sid}/check`);
    } catch (e) {
      if (wake) wake.release().catch(() => {});
      showSendError(el, st, cat, body, e);
    }
  }

  // 送れなかったとき: 入力の誤り（400）とつながらない・測れない（圏外・503）を分ける
  function showSendError(el, st, cat, body, e) {
    const input = e && e.status === 400;
    const saved = st.swingId ? "スイングは作れています。もう一度送ると、同じスイングにコマを入れ直します（二本にはなりません）。" : "選んだコマと押した点は、この画面に残っています。";
    $("[data-err]", body).innerHTML = input
      ? App.errorHtml({ what: "押した点に直すところがあります", saved, next: `${e.message || ""}。点を置き直してから、もう一度送ってください。` })
        + `<button type="button" class="btn primary block" data-fix>点を置き直す</button>`
      : App.errOf(e, { what: "送れませんでした", saved, next: "つながる所で、もう一度送ってください。" }) + `<button type="button" class="btn primary block" data-again>もう一度送る</button>`;
    const again = $("[data-again]", body);
    if (again) { again.addEventListener("click", () => stepProcess(el, st, cat)); again.focus(); }
    const fix = $("[data-fix]", body);
    if (fix) { fix.addEventListener("click", () => { st.payload = null; stepTaps(el, st, cat); }); fix.focus(); }
  }

  // ======================================================================
  // 自動で取り出す（段2b・§6.3・§6.4）
  // ======================================================================
  const COARSE_HZ = 10;      // スイングを探す粗い走査（1秒に何コマ）
  const DENSE_HZ = 120;      // スイングの区間の細かい走査の上限（動画の fps がこれより低ければ全部のコマ）
  const POSE_SHORT = 360;    // 体の点を取るときのコマの短辺（§6.3-4。短辺 360〜480px。長辺で縛ると横長の動画は短辺 270px になる）
  const BATCH_FRAMES = 6000; // 一回に送るコマの上限（分析サービスは 10000 まで）。超えるときはスイングの組に分けて送る
  const COARSE_MAX = 9000;   // 粗い走査のコマの上限（1秒に10コマで15分）。これより長い動画は切ってもらう
  const ROI_PX = 24;         // ボールのまわりを比べる小さな絵の大きさ
  const AUTO_STAGES = ["読み込み", "スイングを探す", "体の点を取る", "形を選ぶ", "測る"];
  const REQ = PS; // 必須の P（確かめる対象）
  const ALL_P = ["P1", "P2", "P3", "P4", "P5", "P5_5", "P6", "P6_5", "P7", "P8", "P9", "P10"];
  const PENDING_DAYS = 7;    // 途中の印を生かす日数（これより古い印は捨てる。ホームを「続きから」に固定しない）
  const sigOf = (st) => `${st.file.name}|${st.file.size}|${st.file.lastModified}|${st.view}`;
  const poseEdge = (st) => {
    const w = st.video.videoWidth, h = st.video.videoHeight;
    return Math.min(Math.max(w, h), Math.round((POSE_SHORT * Math.max(w, h)) / Math.max(1, Math.min(w, h))));
  };

  // ---- 人の範囲（§6.3-4）: 人が小さく写った動画（練習場のモニターを離れて撮った、など）は、枠を切り出して拡大してから体の点を取る ----
  const BOX_SAMPLES = 6;      // 人の範囲を決めるのに見るコマの数（動画の頭から終わりまで等間隔）
  const BOX_SHORT = 720;      // そのとき見るコマの短辺（粗い走査より高めの解像度）
  const BOX_VIS = 0.5;        // 外枠に入れる点の visibility の下限
  const BOX_MAX_AREA = 0.7;   // 枠がコマのこの割合より大きければ切り出さない（拡大にならない）
  const POSE_CACHE_V = 2;     // 端末に残す体の点の版。枠を入れる前（版なし）に取った点は使い回さない（前の失敗の結果を引きずらない）
  // 取れた体の点（元のコマの 0〜1）の外枠 → クラブが伸びるぶんを広げた枠（元のコマの画素・整数）。取れなければ null
  function boxFrom(sets, W, H) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const lms of sets) {
      if (!lms || lms.length !== 33) continue;
      for (const q of lms) {
        if (!q || (q.visibility !== undefined && q.visibility < BOX_VIS)) continue;
        const x = q.x * W, y = q.y * H;
        if (!isFinite(x) || !isFinite(y)) continue;
        x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
      }
    }
    if (!(x1 > x0) || !(y1 > y0)) return null;
    const h = y1 - y0; // 体の高さ（頭〜足）
    // 上: 振り上げた手とクラブ（体の高さの約6割）。横: クラブが体の外へ伸びる（約7.5割）。下: 足元のボールと地面
    const bx0 = Math.max(0, Math.floor(x0 - 0.75 * h)), bx1 = Math.min(W, Math.ceil(x1 + 0.75 * h));
    const by0 = Math.max(0, Math.floor(y0 - 0.6 * h)), by1 = Math.min(H, Math.ceil(y1 + 0.2 * h));
    const w = bx1 - bx0, hh = by1 - by0;
    if (w < 16 || hh < 16 || w * hh > BOX_MAX_AREA * W * H) return null;
    return { x: bx0, y: by0, w, h: hh };
  }
  // 枠の中の 0〜1 → 元のコマの 0〜1（分析サービスには元のコマの座標で送るので、サービス側は変えずに済む）
  function fromBox(lms, box, W, H) {
    return lms.map((q) => {
      const o = { x: +((box.x + q.x * box.w) / W).toFixed(5), y: +((box.y + q.y * box.h) / H).toFixed(5) };
      if (q.visibility !== undefined) o.visibility = q.visibility;
      return o;
    });
  }
  const sameBox = (a, b) => (!a && !b) || (!!a && !!b && a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h);
  // 1コマの体の点（元のコマの 0〜1）。src は <video> か、そのコマを絵にした canvas。box があれば切り出して拡大してから取る
  function detectFrame(det, src, meta, box, edge = 0) {
    const isVideo = typeof HTMLVideoElement !== "undefined" && src instanceof HTMLVideoElement;
    if (!box) return det.detect(isVideo ? frameCanvas(src, edge) : src, meta);
    const k = (isVideo ? src.videoWidth : src.width) / meta.width; // src の画素 ÷ 元のコマの画素
    const z = Math.min(POSE_SHORT / Math.min(box.w, box.h), 1280 / Math.max(box.w, box.h));
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(box.w * z)); c.height = Math.max(1, Math.round(box.h * z));
    c.getContext("2d").drawImage(src, box.x * k, box.y * k, box.w * k, box.h * k, 0, 0, c.width, c.height);
    const r = det.detect(c, { ...meta, box });
    return r && r.length === 33 ? fromBox(r, box, meta.width, meta.height) : r;
  }
  // 数コマを高めの解像度で見て、人の範囲を決める
  async function personBox(st, det) {
    const W = st.video.videoWidth, H = st.video.videoHeight;
    const edge = Math.round(Math.max(W, H) * Math.min(1, BOX_SHORT / Math.max(1, Math.min(W, H))));
    const d = st.duration || 0;
    const sets = [];
    for (let i = 0; i < BOX_SAMPLES; i++) {
      if (st.abort) throw new Stopped();
      const t = d * (0.05 + (0.9 * (i + 0.5)) / BOX_SAMPLES);
      await seekTo(st.video, t);
      let r = null;
      try { r = det.detect(frameCanvas(st.video, edge), { t, view: st.view, width: W, height: H }); } catch { r = null; }
      if (r && r.length === 33) sets.push(r);
    }
    return { box: boxFrom(sets, W, H), found: sets.length, looked: BOX_SAMPLES };
  }

  // スイングが見つからなかった理由（分析サービスの diag）→ 何が起きたか・次に何をするか（行き止まりにしない）
  const NO_SWING = {
    no_pose: { what: "体の点がほとんど取れませんでした",
      next: "暗い・人が小さく写っている・画面越しに撮った動画は、体の点が取りにくくなります。明るい所で、人が画面の縦の半分ほどの大きさに写るまで近づいて撮り直すと見つかりやすくなります。シミュレーターの画面を撮った動画なら、シミュレーターから動画のファイルを書き出せると確実です。" },
    no_raise: { what: "振り上げ（手が胸より上に上がるところ）が見つかりませんでした",
      next: "頭の上からクラブの先まで画面に入っているか、スイングの途中で切れていないかを確かめてください。撮った向き（後ろから／正面から）の選び方も確かめてください。" },
    no_address: { what: "振り上げの前の構えが見つかりませんでした",
      next: "構えて少し止まるところから写っている動画にしてください（打つ直前から撮り始めると構えが写りません）。" },
    no_down: { what: "振り上げのあと、振り下ろすところが見つかりませんでした",
      next: "振り終わるまで写っている動画を選んでください。" },
  };
  function noSwingHtml(diag) {
    const d = (diag && NO_SWING[diag.reason]) || { what: "頭の上からクラブの先まで写ったスイングが見つかりませんでした", next: "撮った向きの選び方が合っているかを確かめてください。" };
    return App.errorHtml({ what: d.what, saved: "体の点を調べるために一度送りました（保存していません）。",
      next: `${d.next}［手で選ぶ］で、コマを自分で選ぶこともできます。` });
  }

  // ---- 端末の置き場（pose ストア）: 取った体の点を残し、閉じても続きから処理できるようにする ----
  async function idbGet(store, key) {
    try {
      const db = await idb();
      const v = await new Promise((ok, ng) => { const r = db.transaction(store).objectStore(store).get(key); r.onsuccess = () => ok(r.result); r.onerror = () => ng(r.error); });
      db.close();
      return v;
    } catch { return undefined; }
  }
  async function idbPut(store, key, val) {
    try {
      const db = await idb();
      await new Promise((ok, ng) => { const tx = db.transaction(store, "readwrite"); tx.objectStore(store).put(val, key); tx.oncomplete = ok; tx.onerror = () => ng(tx.error); });
      db.close();
      return true;
    } catch { return false; }
  }
  async function idbDel(store, key) {
    try {
      const db = await idb();
      await new Promise((ok) => { const tx = db.transaction(store, "readwrite"); tx.objectStore(store).delete(key); tx.oncomplete = ok; tx.onerror = ok; });
      db.close();
    } catch { /* 消せなくても次に上書きする */ }
  }
  // 途中の印（ホームの「動画の処理が途中です」）。localStorage にだけ置く（端末の中だけの状態）。古い印は捨てる
  function pending() {
    const p = LS.get("golf.videoPending");
    if (!p) return null;
    if (!p.at || Date.now() - p.at > PENDING_DAYS * 86400000) { LS.del("golf.videoPending"); return null; }
    return p;
  }
  function setPending(st) {
    LS.set("golf.videoPending", { date: st.date, session: st.session || st.sid || null, sig: sigOf(st), name: st.file.name || "", view: st.view, club: st.club, at: Date.now() });
  }
  async function clearPending(dropCache = false) {
    const p = pending();
    if (dropCache && p && p.sig) await idbDel("pose", p.sig);
    LS.del("golf.videoPending");
  }
  // 同じ動画で手で選んで送った・探しても見つからなかった: 途中の印を消す（ホームを「続きから」に固定しない）
  async function clearPendingFor(st) {
    const p = pending();
    if (p && st.file && p.sig === sigOf(st)) await clearPending(true);
  }
  function pendingNote() {
    const p = pending();
    if (!p || !p.sig) return "";
    return `<section class="note" data-pending aria-labelledby="h-pending"><p id="h-pending" style="margin:0"><b>処理が途中の動画があります</b>（${esc(p.name || "動画")}・${esc(p.view === "fo" ? "正面から" : "後ろから")}）。同じ動画をもう一度選ぶと、取ってある体の点を使って続きから処理します。</p>
      <button type="button" class="btn primary block" style="margin-top:var(--s2)" data-pick-same>同じ動画を選ぶ</button></section>`;
  }

  // ---- ボールのまわり（§6.3-5・§6.4 の P7 の挟み込み） ----
  function roiPatch(video, ball) {
    if (!ball) return null;
    const cx = (ball[0][0] + ball[1][0]) / 2, cy = (ball[0][1] + ball[1][1]) / 2;
    const r = Math.max(3, Math.hypot(ball[1][0] - ball[0][0], ball[1][1] - ball[0][1]) / 2);
    const c = document.createElement("canvas");
    c.width = ROI_PX; c.height = ROI_PX;
    const g = c.getContext("2d", { willReadFrequently: true });
    g.drawImage(video, cx - r, cy - r, 2 * r, 2 * r, 0, 0, ROI_PX, ROI_PX);
    const d = g.getImageData(0, 0, ROI_PX, ROI_PX).data;
    const out = [];
    const h = ROI_PX / 2;
    for (let y = 0; y < ROI_PX; y++) for (let x = 0; x < ROI_PX; x++) {
      if ((x + 0.5 - h) ** 2 + (y + 0.5 - h) ** 2 > h * h) continue; // ボールの丸の中だけ
      const i = (y * ROI_PX + x) * 4;
      out.push(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]);
    }
    return out;
  }
  const roiDiff = (a, b) => { if (!a || !b || a.length !== b.length) return null; let s = 0; for (let i = 0; i < a.length; i++) s += Math.abs(a[i] - b[i]); return Math.round((s / a.length / 255) * 1000) / 1000; };

  // 体の点を [x, y, v] の短い形に（送る量を減らす。小数4桁＝1画素より細かい）
  const compact = (lms) => (lms && lms.length === 33 ? lms.map((q) => [+(+q.x).toFixed(4), +(+q.y).toFixed(4), q.visibility === undefined ? 1 : +(+q.visibility).toFixed(2)]) : null);
  const expand = (lm) => (lm ? lm.map((q) => ({ x: q[0], y: q[1], visibility: q[2] })) : []);

  // ---- 処理中の状態（Wake Lock は画面に戻ったときに取り直す。§6.3） ----
  let running = null;
  async function holdWake(st) {
    try {
      if (!navigator.wakeLock || document.visibilityState !== "visible" || st.wake) return;
      const w = await navigator.wakeLock.request("screen");
      st.wake = w;
      w.addEventListener("release", () => { if (st.wake === w) st.wake = null; });
    } catch { /* 使えなくても続ける */ }
  }
  function dropWake(st) { if (st.wake) { const w = st.wake; st.wake = null; w.release().catch(() => {}); } }
  document.addEventListener("visibilitychange", () => { if (running && running.running && document.visibilityState === "visible") holdWake(running); });
  function startRun(st) { st.running = true; st.abort = false; running = st; holdWake(st); }
  function endRun(st) { st.running = false; dropWake(st); if (running === st) running = null; }

  // 段の表示（読み上げは段が変わったときだけ App.say の一つから。§11.4）
  function autoStage(body, i, sub = "") {
    const box = $("[data-astages]", body);
    if (!box) return;
    $$("li", box).forEach((li, j) => { li.className = j < i ? "done" : j === i ? "cur" : ""; });
    const now = $("[data-anow]", body);
    const text = `${AUTO_STAGES[i] || ""}${sub ? `（${sub}）` : ""}`;
    if (now && now.dataset.i !== String(i)) { now.dataset.i = String(i); App.say(`${AUTO_STAGES[i]}`); }
    if (now) now.innerHTML = `<b>いま</b>: ${esc(text)}`;
    prog(body, "");
  }
  // 段の中の進み具合（棒と「あと約◯分」。見込みは、この端末で前に測った一コマの時間と、いま測っている時間から）
  function prog(body, text, k = null, n = null, msPer = 0) {
    const p = $("[data-aprog]", body);
    if (p) p.textContent = text;
    const bar = $("[data-abar]", body);
    if (bar) {
      if (k != null && n) { bar.hidden = false; bar.max = n; bar.value = Math.min(k, n); } else bar.hidden = true;
    }
    const e = $("[data-aeta]", body);
    if (!e) return;
    const ms = k != null && n && msPer ? (n - k) * msPer : 0;
    e.innerHTML = ms <= 0 ? "" : ms < 60000 ? "この段は、あと一分かからない見込みです" : `この段は、あと約${lab("count", Math.ceil(ms / 60000) + "分")}の見込みです`;
  }
  const savedMsPer = () => { const v = LS.get("golf.posePerf"); return v && v.ms > 0 ? v.ms : 0; };

  class Stopped extends Error { constructor() { super("止めました"); this.stopped = true; } }

  // 並んだ時刻で体の点（とボールのまわり）を取る。cache に取ってあるコマは取り直さない
  async function scanAt(st, det, times, cache, { ballRef = null, roiOut = null, onTick = null } = {}) {
    const out = [];
    let k = 0;
    const tp = performance.now();
    let fresh = 0;
    const edge = poseEdge(st);
    for (const t of times) {
      if (st.abort) throw new Stopped();
      const key = t.toFixed(4);
      let lm = cache[key];
      const needRoi = ballRef && roiOut && roiOut[key] === undefined;
      if (lm === undefined || needRoi) {
        await seekTo(st.video, t);
        if (lm === undefined) {
          let r = null;
          try { r = detectFrame(det, st.video, { t, view: st.view, width: st.video.videoWidth, height: st.video.videoHeight }, st.box, edge); } catch { r = null; }
          lm = compact(r);
          cache[key] = lm;
          fresh += 1;
        }
        if (needRoi) roiOut[key] = roiDiff(roiPatch(st.video, st.ball), ballRef);
      }
      out.push({ t: Math.round(t * 10000) / 10000, lm });
      k += 1;
      if (onTick && k % 10 === 0) onTick(k, times.length, fresh >= 10 ? (performance.now() - tp) / fresh : savedMsPer());
    }
    if (fresh) {
      st.perf.pose_ms_per_frame = Math.round((performance.now() - tp) / fresh);
      if (fresh >= 20) LS.set("golf.posePerf", { ms: st.perf.pose_ms_per_frame, at: Date.now() }); // 次からこの端末の見込みに使う
    }
    return out;
  }
  const frameTimes = (st, t0, t1, hz) => {
    const step = Math.max(1, Math.round(st.fpsStep / hz));
    const a = Math.max(0, Math.ceil(t0 * st.fpsStep - 0.5)), b = Math.min(Math.round(st.duration * st.fpsStep) - 1, Math.floor(t1 * st.fpsStep - 0.5));
    const out = [];
    for (let n = a; n <= b; n += step) out.push((n + 0.5) / st.fpsStep);
    return out;
  };

  async function grabAt(st, t) {
    await seekTo(st.video, t);
    return { t: +st.video.currentTime.toFixed(4), frame: frameNo(st), blob: await toBlob(frameCanvas(st.video, GRAB_EDGE), 0.92) };
  }
  // 1スイングの P のコマを全解像度で一度だけ絵にする（あとで探し直さない）
  async function grabSwing(st, sw, fr, tick) {
    const one = { index: sw.index, det: sw, detFrames: fr, frames: {}, missing: new Set(), userMissing: new Set(), series: sw.series, check: sw.check.slice() };
    for (const p of sw.ps) {
      if (st.abort) throw new Stopped();
      if (p.t == null) { if (REQ.includes(p.p)) one.missing.add(p.p); continue; }
      const g = await grabAt(st, p.t);
      const f = fr[p.frame];
      one.frames[p.p] = { ...g, source: "auto", lm: f && f.lm ? f.lm : null, auto_t: p.t, method: p.method, status: p.status, confidence: p.confidence, reason_text: p.reason_text || "" };
      if (tick) tick();
    }
    return one;
  }

  // 処理の画面（段・棒・止める・手で選ぶ）。［手で選ぶ］は、処理中なら止めてから、止まっていればすぐ手で選ぶ画面へ
  function autoShell(el, st, cat, heading = "形を自動で探しています") {
    const body = freshBody(el);
    $("[data-step]", el).textContent = "";
    body.innerHTML = `<section class="card"><h2 class="t-headline" style="margin-top:0" data-ahead>${esc(heading)}</h2>
      <p data-anow></p>
      <ol class="stages" data-astages>${AUTO_STAGES.map((x) => `<li>${esc(x)}</li>`).join("")}</ol>
      <progress class="abar" data-abar max="1" value="0" hidden aria-label="この段の進み具合"></progress>
      <p class="caption" data-aprog></p>
      <p class="caption" data-aeta></p>
      <p class="caption" data-keepopen>このあいだは画面を閉じないでください。閉じたときは、同じ動画をもう一度選ぶと続きから処理します。</p>
      <div data-err></div>
      <div class="stack"><button type="button" class="btn block" data-stop>止める</button>
        <button type="button" class="btn block" data-manual>手で選ぶ</button></div></section>`;
    $("[data-stop]", body).addEventListener("click", () => { st.abort = true; });
    $("[data-manual]", body).addEventListener("click", () => {
      if (st.running) { st.abort = true; st.toManual = true; return; }
      st.abort = false; st.toManual = false; st.auto = null;
      stepChoose(el, st, cat);
    });
    enter(el);
    return body;
  }
  // 止まった・失敗した: 見出しを状態に合わせ、止まった段に印を付け、進行中の表示を隠す（状態を矛盾させない）
  function autoHalt(body, heading) {
    const h = $("[data-ahead]", body);
    if (h) h.textContent = heading;
    const cur = $("[data-astages] li.cur", body);
    if (cur) { cur.className = "stopped"; cur.insertAdjacentHTML("beforeend", `<span class="caption">（ここで止まりました）</span>`); }
    for (const s of ["[data-anow]", "[data-keepopen]", "[data-stop]", "[data-abar]", "[data-aeta]"]) { const e = $(s, body); if (e) e.hidden = true; }
    const p = $("[data-aprog]", body);
    if (p) p.textContent = "";
  }

  // ①〜④: 走査 → ボール → 細かい走査 → P
  async function stepAuto(el, st, cat) {
    const body = autoShell(el, st, cat);
    startRun(st);
    const t0 = performance.now();
    let keep = null;
    try {
      autoStage(body, 0);
      const det = await poseDetector();
      st.perf.pose_backend = det.backend;
      const sig = sigOf(st);
      const saved = (await idbGet("pose", sig)) || {};
      if (st.reball) { saved.roi = {}; saved.ball = null; st.ball = null; st.reball = false; } // ボールを押し直す
      // 版の違う点（人の範囲を入れる前に取った点）は使い回さない。前に「見つかりませんでした」になった点を引きずらない
      const fresh = saved.v === POSE_CACHE_V && "box" in saved;
      let cache = fresh ? saved.lm || {} : {};
      const roi = saved.roi || {};
      if (saved.ball && !st.ball) st.ball = saved.ball;
      setPending(st);
      keep = () => idbPut("pose", sig, { v: POSE_CACHE_V, box: st.box || null, lm: cache, roi, ball: st.ball || null, at: Date.now() });
      // ② スイングを探す。まず人の範囲（続きから処理するときは、前に決めた枠をそのまま使う＝取ってある点と同じ枠）
      autoStage(body, 1);
      if (fresh) st.box = saved.box || null;
      else {
        prog(body, "人の写っている範囲を探しています");
        const pb = await personBox(st, det);
        if (!sameBox(pb.box, saved.box)) cache = {}; // 枠が変われば点を取り直す
        st.box = pb.box;
        st.perf.box_found = pb.found; st.perf.box_looked = pb.looked;
      }
      const vw = st.video.videoWidth, vh = st.video.videoHeight;
      st.perf.crop = st.box ? [Math.round((st.box.w / vw) * 1000) / 1000, Math.round((st.box.h / vh) * 1000) / 1000] : null;
      st.perf.resumed = Object.keys(cache).length > 0;
      await keep();
      const tc = performance.now();
      const coarseT = frameTimes(st, 0, st.duration, COARSE_HZ);
      if (coarseT.length > COARSE_MAX) throw Object.assign(new Error("動画が長すぎます"), { status: 400, long: true });
      const coarse = await scanAt(st, det, coarseT, cache, { onTick: (k, n, ms) => { prog(body, `${k} / ${n} コマ`, k, n, ms); if (k % 50 === 0) keep(); } });
      await keep();
      st.perf.coarse_ms = Math.round(performance.now() - tc);
      st.perf.coarse_frames = coarse.length;
      const head = { view: st.view, handedness: (S.player && S.player.handedness) || App.pendingHand(), fps: st.fps || 0, width: st.video.videoWidth, height: st.video.videoHeight };
      const td = performance.now();
      const r1 = await api("POST", "/v1/video/checkpoints", { ...head, frames: coarse });
      st.perf.detect_ms = Math.round(performance.now() - td);
      if (!r1.swings.length) {
        endRun(st);
        await clearPendingFor(st);
        st.perf.no_swing = r1.diag || null;
        autoHalt(body, "スイングが見つかりませんでした");
        const box = body.querySelector("[data-err]");
        box.innerHTML = noSwingHtml(r1.diag);
        box.dataset.noSwing = (r1.diag && r1.diag.reason) || "";
        $("[data-manual]", body).focus();
        return;
      }
      // ボールの両端（最初のスイングの構え）。当たる瞬間の挟み込みと、ボール一個の物差しに使う
      if (!st.ball) {
        const p1 = r1.swings[0].ps.find((x) => x.p === "P1");
        const g = await grabAt(st, p1 && p1.t != null ? p1.t : r1.swings[0].window[0]);
        const c = await blobCanvas(g.blob);
        const k = c.width / st.video.videoWidth;
        const hand = (S.player && S.player.handedness) || App.pendingHand();
        const host = freshBody(el);
        const r = await tapEditor(host, { canvas: c, p: "P1", hand, zoomOnFirst: true, canBack: false, title: `${(cat.p_names || {}).P1 || "構え"}: ボールの両端`,
          hint: "ボールのあたりを一度押すと拡大します。ボールの左の端と右の端を押します（当たる瞬間を見分けるのと、ボール一個の大きさを物差しにするのに使います）。",
          points: [{ key: "b1", label: "左の端", shape: "half-l" }, { key: "b2", label: "右の端", shape: "half-r" }] });
        if (!st.running) return; // 押しているあいだに「<」で戻った
        st.ball = r ? [[Math.round(r.b1[0] / k), Math.round(r.b1[1] / k)], [Math.round(r.b2[0] / k), Math.round(r.b2[1] / k)]] : null;
        await keep();
      }
      return stepAuto2(el, st, cat, { det, cache, roi, coarse, r1, head, keep, t0 });
    } catch (e) {
      if (keep) await keep(); // 止めた・落ちたときも、ここまでの体の点を残す（続きから処理する）
      autoFail(el, st, cat, body, e);
    }
  }

  async function stepAuto2(el, st, cat, ctx) {
    const { det, cache, roi, coarse, r1, head, keep } = ctx;
    const body = autoShell(el, st, cat);
    if (!st.running) startRun(st);
    try {
      // ③ 体の点を取る（スイングの区間だけ細かく）。ボールがあれば、そのまわりの変わり方と、構えで丸の中にボールがあるか
      autoStage(body, 2);
      const tdn = performance.now();
      const all = r1.swings.concat(r1.excluded || []);
      const windows = all.map((s) => s.window);
      const denseBy = [];
      const ballSeen = [];
      let firstRef = null;
      let i = 0;
      for (const sw of all) {
        i += 1;
        let ref = null;
        if (st.ball) {
          const p1 = sw.ps.find((x) => x.p === "P1");
          const tp1 = p1 && p1.t != null ? p1.t : sw.window[0];
          await seekTo(st.video, tp1);
          ref = roiPatch(st.video, st.ball);
          if (!firstRef) firstRef = ref; // 最初のスイングの構え＝ボールの両端を押したコマ
          const d = roiDiff(ref, firstRef);
          if (d != null) ballSeen.push({ t: Math.round(tp1 * 10000) / 10000, d });
        }
        const ts = frameTimes(st, sw.window[0], sw.window[1], DENSE_HZ);
        const got = await scanAt(st, det, ts, cache, { ballRef: ref, roiOut: roi, onTick: (k, n, ms) => { prog(body, `スイング ${i} / ${all.length}: ${k} / ${n} コマ`, k, n, ms); if (k % 60 === 0) keep(); } });
        denseBy.push(got);
      }
      await keep();
      st.perf.dense_ms = Math.round(performance.now() - tdn);
      st.perf.dense_frames = denseBy.reduce((a, g) => a + g.length, 0);
      // ④ P を選ぶ。コマが多いときはスイングの組に分けて送る（分析サービスの上限を超えて、同じ失敗をくり返さない）
      autoStage(body, 3);
      const inWin = (t, ws) => ws.some((w) => t >= w[0] - 1e-6 && t <= w[1] + 1e-6);
      const batches = [];
      let cur = [];
      let curN = 0;
      const outside = coarse.filter((f) => !inWin(f.t, windows)).length;
      for (const [j, g] of denseBy.entries()) {
        if (cur.length && outside + curN + g.length > BATCH_FRAMES) { batches.push(cur); cur = []; curN = 0; }
        cur.push(j); curN += g.length;
      }
      if (cur.length) batches.push(cur);
      st.perf.batches = batches.length;
      const found = [], excluded = [];
      let lastDiag = null;
      const td = performance.now();
      for (const [bi, b] of batches.entries()) {
        if (st.abort) throw new Stopped();
        if (batches.length > 1) prog(body, `${bi + 1} / ${batches.length} 回目を送っています`, bi, batches.length);
        const ws = b.map((j) => windows[j]);
        // 組のスイングは細かいコマ、ほかは粗いコマ（組の外のスイングは、あとで捨てる）
        const byT = new Map();
        for (const f of coarse) if (!inWin(f.t, ws)) byT.set(f.t.toFixed(4), f);
        for (const j of b) for (const f of denseBy[j]) byT.set(f.t.toFixed(4), f);
        const frames = [...byT.values()].sort((x, y) => x.t - y.t);
        const req = { ...head, frames, practice_fallback: batches.length === 1 };
        if (st.ball) { req.roi = frames.map((f) => { const v = roi[f.t.toFixed(4)]; return v === undefined ? null : v; }); req.ball_seen = ballSeen; }
        const r2 = await api("POST", "/v1/video/checkpoints", req);
        if (r2.diag) lastDiag = r2.diag;
        const mine = (s) => inWin(s.t0, ws);
        for (const s of r2.swings) if (mine(s)) found.push({ s, fr: frames });
        for (const s of r2.excluded || []) if (mine(s)) excluded.push({ s, fr: frames });
      }
      st.perf.detect_ms = (st.perf.detect_ms || 0) + Math.round(performance.now() - td);
      // 組に分けたとき、全部が素振りに見えたら外さずに使う（一回で送ったときは分析サービスが同じことをする）
      if (!found.length && excluded.length) {
        for (const x of excluded.splice(0)) {
          x.s.kind = "swing"; x.s.practice_known = false; x.s.warnings = [...(x.s.warnings || []), "practice_unsure"];
          found.push(x);
        }
      }
      if (!found.length) throw Object.assign(new Error("細かく見たらスイングが見つかりませんでした"), { status: 422, diag: lastDiag || { reason: "" } });
      found.sort((x, y) => x.s.t0 - y.s.t0);
      found.forEach((x, k) => { x.s.index = k + 1; });
      const auto = [];
      let n = 0;
      const total = found.reduce((a, x) => a + x.s.ps.filter((p) => p.t != null).length, 0);
      for (const x of found) auto.push(await grabSwing(st, x.s, x.fr, () => { n += 1; prog(body, `コマを用意しています ${n} / ${total}`, n, total); }));
      st.auto = { swings: auto, excluded };
      st.perf.total_ms = Math.round(performance.now() - ctx.t0);
      endRun(st);
      stepReview(el, st, cat);
    } catch (e) {
      await keep();
      autoFail(el, st, cat, body, e);
    }
  }

  // 止めた・失敗した: 何が起きたか・何が残っているか・次に何ができるか（行き止まりにしない）
  function autoFail(el, st, cat, body, e) {
    endRun(st);
    if (st.toManual) { st.toManual = false; st.abort = false; st.auto = null; stepChoose(el, st, cat); return; }
    const box = $("[data-err]", body);
    if (e && e.stopped) {
      autoHalt(body, "止めました");
      box.innerHTML = `<div class="note" data-stopped><p style="margin:0">ここまでに取った体の点は、この端末に残しています。</p></div>
        <button type="button" class="btn primary block" data-resume>続きから処理する</button>`;
    } else if (e && e.long) {
      autoHalt(body, "動画が長すぎます");
      box.innerHTML = App.errorHtml({ what: "この動画は長すぎて、一度に処理できません", saved: "まだ何も送っていません。", next: "写真アプリで十五分ほどまでに切ってから、もう一度選んでください。［手で選ぶ］もできます。" });
      $("[data-manual]", body).focus();
      return;
    } else if (e && e.diag) {
      // 同じ点で探し直しても同じ結果になるので「もう一度」は置かない。撮り直すか、手で選ぶ
      autoHalt(body, "スイングが見つかりませんでした");
      box.innerHTML = noSwingHtml(e.diag);
      $("[data-manual]", body).focus();
      return;
    } else {
      autoHalt(body, "形を自動で探せませんでした");
      box.innerHTML = App.errOf(e, { what: "形を自動で探せませんでした", saved: "取った体の点は、この端末に残しています。", next: "もう一度押すと続きから処理します。ボールの位置がずれていたら押し直せます。［手で選ぶ］もできます。" })
        + `<button type="button" class="btn primary block" data-resume>もう一度（続きから）</button>`
        + (st.ball ? `<button type="button" class="btn block" data-reball>ボールを押し直す</button>` : "");
    }
    const b = $("[data-resume]", box);
    b.addEventListener("click", () => stepAuto(el, st, cat));
    const rb = $("[data-reball]", box);
    if (rb) rb.addEventListener("click", () => { st.reball = true; st.ball = null; stepAuto(el, st, cat); });
    b.focus();
  }

  // 確かめるところ: 自信の低いコマと、見つからなかった（本人がまだ「写っていない」と言っていない）P
  function reviewItems(st) {
    const items = [];
    for (const sw of st.auto.swings) for (const p of REQ) {
      const f = sw.frames[p];
      if ((!f && !sw.userMissing.has(p)) || (f && f.confidence === "low")) items.push({ sw, p });
    }
    return items;
  }

  // R2 コマの確認（確かめるところだけ。§10 R2）
  function stepReview(el, st, cat) {
    const items = reviewItems(st);
    const body = freshBody(el);
    $("[data-step]", el).textContent = "";
    const nAuto = st.auto.swings.length;
    const ex = st.auto.excluded.length;
    const sum = `<h2 class="t-headline" data-review-sum style="margin-top:0">スイング${lab("count", nAuto + "本")}の形を取り出しました</h2>
      ${ex ? `<div class="note" data-excluded><p style="margin:0">素振りと見て外したもの ${lab("count", ex + "本")}（構えで丸の中にあったボールが、振っても動いていませんでした）</p>
        <button type="button" class="btn block" style="margin-top:var(--s2)" data-use-excluded>外さずに使う</button></div>` : ""}`;
    const name = (p) => esc((cat.p_names || {})[p] || p);
    if (!items.length) {
      body.innerHTML = `<section class="card">${sum}<p data-review-none>確かめるところはありません。自動で選んだコマをそのまま使います。</p>
        <button type="button" class="btn primary block" data-review-next>次へ（クラブの点を押す）</button>
        <button type="button" class="btn block" data-review-all>全部のコマを見る</button></section>`;
      $("[data-review-next]", body).addEventListener("click", () => stepAutoTaps(el, st, cat));
      $("[data-review-all]", body).addEventListener("click", () => reviewOne(el, st, cat, st.auto.swings.flatMap((sw) => REQ.map((p) => ({ sw, p }))), 0, true));
    } else {
      body.innerHTML = `<section class="card">${sum}<p data-review-n>確かめるところ ${lab("count", items.length + "つ")}</p>
        <ul class="plain">${items.map((x) => `<li>${lab("count", x.sw.index + "本")}目・${name(x.p)} — ${x.sw.frames[x.p] ? "自信が低い" : "見つからなかった"} <span class="caption">${lab("p", x.p)}</span></li>`).join("")}</ul>
        <button type="button" class="btn primary block" data-review-go>確かめる</button>
        <button type="button" class="btn block" data-review-skip>確かめずに進む</button>
        <p class="caption">確かめずに進むと、自信の低いコマは自動のまま使い、見つからなかった所は判断できないにします。</p></section>`;
      $("[data-review-go]", body).addEventListener("click", () => reviewOne(el, st, cat, items, 0, false));
      $("[data-review-skip]", body).addEventListener("click", () => {
        for (const { sw, p } of items) if (!sw.frames[p]) sw.missing.add(p);
        stepAutoTaps(el, st, cat);
      });
    }
    const ue = $("[data-use-excluded]", body);
    if (ue) ue.addEventListener("click", () => useExcluded(el, st, cat, ue));
    enter(el);
  }
  // 外したスイングを戻す（素振りの判定は外れることがある。§6.4）
  async function useExcluded(el, st, cat, btn) {
    btn.setAttribute("aria-disabled", "true");
    btn.textContent = "コマを用意しています…";
    try {
      for (const x of st.auto.excluded) {
        x.s.kind = "swing"; x.s.practice_known = false;
        st.auto.swings.push(await grabSwing(st, x.s, x.fr));
      }
      st.auto.excluded = [];
      st.auto.swings.sort((a, b) => a.det.t0 - b.det.t0);
      st.auto.swings.forEach((sw, k) => { sw.index = k + 1; sw.det.index = k + 1; });
      stepReview(el, st, cat);
    } catch (e) {
      btn.insertAdjacentHTML("afterend", App.errOf(e, { what: "戻せませんでした", saved: "取り出したコマは、この画面に残っています。" }));
      btn.removeAttribute("aria-disabled");
      btn.textContent = "外さずに使う";
    }
  }

  // P の帯（§10 R2: ✓自動 ●いま △確かめる ―写っていない）
  function reviewStrip(sw, cur, pendingPs) {
    const cell = (p) => {
      const f = sw.frames[p], miss = !f;
      const now = p === cur, check = pendingPs.includes(p);
      const mark = now ? "●" : miss ? "―" : check ? "△" : "✓";
      const word = now ? "いま" : miss ? "写っていない" : check ? "確かめる" : f.source === "manual" ? "直した" : f.status === "estimated" ? "自動（目安）" : "自動";
      return `<span class="pchip ${now ? "" : miss ? "miss" : check ? "wrong" : "done"}" role="listitem" ${now ? `aria-current="step"` : ""} aria-label="${p} ${word}"><span aria-hidden="true">${mark}</span>${lab("p", p)}</span>`;
    };
    return `<div class="pchips" role="list" aria-label="このスイングの形" data-rstrip>${REQ.map(cell).join("")}</div>
      <p class="caption" aria-hidden="true">✓自動 ●いま △確かめる ―写っていない</p>`;
  }

  // 一つずつ: 見本の線画と定義・±1コマ・このコマでよい・写っていない・前へ・自動の位置に戻す
  function reviewOne(el, st, cat, items, k, optional) {
    if (k >= items.length) { stepAutoTaps(el, st, cat); return; }
    const { sw, p } = items[k];
    const body = freshBody(el);
    const f0 = sw.frames[p];
    const a = sw.det.ps.find((x) => x.p === p);
    const hasAuto = a && a.t != null;
    const later = optional ? [] : items.slice(k).filter((x) => x.sw === sw).map((x) => x.p); // 全部を見るときは「確かめる」印を付けない（自信の低いコマではない）
    const est = f0 && f0.source === "auto" && f0.status === "estimated";
    const doWhat = f0 ? "見本の線画と同じ形のコマかだけ見てください。違えばコマ送りで直します。" : "自動では見つかりませんでした。コマ送りで見本と同じ形のコマを探してください。写っていなければ［写っていない］を押します。";
    body.innerHTML = `
      <h2 class="t-headline" style="margin:0" data-review-p="${esc(p)}">${optional ? `スイング${lab("count", sw.index + "本")}目のコマ` : `${lab("count", sw.index + "本")}目・${pName(cat, p)}`}</h2>
      <p class="caption" style="margin:0">${optional ? `${pName(cat, p)}・` : ""}${lab("p", p)}・${optional ? "" : "確かめるところ "}${lab("count", (k + 1) + " / " + items.length)}${est ? `・<span class="chip none">目安</span>` : ""}</p>
      ${reviewStrip(sw, p, later)}
      <div class="vframe" data-vframe></div>
      <p class="sub" data-dowhat>${esc(doWhat)}${est ? "このコマは代わりの決め方で選んだ目安です。" : ""}</p>
      <div class="stepbtns" role="group" aria-label="コマ送り">
        <button type="button" class="btn small" data-mv="-0.5s" aria-label="半秒戻る">−半秒</button>
        <button type="button" class="btn small" data-mv="-1" aria-label="一コマ戻る">−1コマ</button>
        <button type="button" class="btn small" data-mv="+1" aria-label="一コマ進む">＋1コマ</button>
        <button type="button" class="btn small" data-mv="+0.5s" aria-label="半秒進む">＋半秒</button></div>
      <div class="row choosebtns even"><button type="button" class="btn primary" data-ok>このコマでよい</button>
        <button type="button" class="btn" data-miss>写っていない</button></div>
      <div class="row choosebtns even"><button type="button" class="btn" data-rprev>${icon("chevron-left")}前へ</button>
        ${hasAuto ? `<button type="button" class="btn" data-reset>自動の位置に戻す</button>` : ""}</div>
      <section class="card pinfo"><div class="prow"><div class="pdef"><p class="sub" style="margin:0">${esc(((cat.p_define || {})[p]) || "")}</p></div>
        <figure class="psample" aria-label="${esc((cat.p_names || {})[p] || "")} の見本の線画">${svgOf(cat, p, st.view)}</figure></div></section>
      ${k === items.length - 1 ? `<p class="caption" data-rnext>次: 1本目の上げ始めで、クラブの握りと先を押します</p>` : ""}
      ${optional ? `<button type="button" class="btn block" data-skip-rest>見るのをやめて次へ</button>` : ""}`;
    st.video.className = "vid";
    st.video.setAttribute("aria-label", "確かめているコマ");
    $("[data-vframe]", body).append(st.video);
    const start = f0 ? f0.t : guessT(sw, p);
    let chain = seekTo(st.video, start);
    body.addEventListener("click", (ev) => { chain = chain.then(() => onClick(ev)).catch((e) => console.warn(e)); });
    const onClick = async (ev) => {
      const mv = ev.target.closest("[data-mv]");
      if (mv) {
        const n = frameNo(st);
        const d = { "+1": 1, "-1": -1, "+0.5s": Math.round(st.fpsStep / 2), "-0.5s": -Math.round(st.fpsStep / 2) }[mv.dataset.mv];
        await toFrame(st, n + d);
        return;
      }
      if (ev.target.closest("[data-ok]")) {
        const t = +st.video.currentTime.toFixed(4);
        const same = f0 && Math.abs(t - f0.t) < 0.5 / st.fpsStep;
        if (!same) {
          const g = await grabAt(st, t);
          sw.frames[p] = { ...g, source: "manual", method: "", status: "", lm: null, auto_t: hasAuto ? a.t : null, confidence: "high" };
        } else sw.frames[p].confidence = "high";
        sw.missing.delete(p);
        sw.userMissing.delete(p);
        reviewOne(el, st, cat, items, k + 1, optional);
        return;
      }
      if (ev.target.closest("[data-miss]")) {
        delete sw.frames[p];
        sw.missing.add(p);
        sw.userMissing.add(p); // 手で決めたコマに数える（段2b の完了条件の数字を偏らせない）
        reviewOne(el, st, cat, items, k + 1, optional);
        return;
      }
      if (ev.target.closest("[data-rprev]")) {
        if (k > 0) reviewOne(el, st, cat, items, k - 1, optional); else stepReview(el, st, cat);
        return;
      }
      if (ev.target.closest("[data-reset]")) {
        const g = await grabAt(st, a.t);
        const f = (sw.detFrames || [])[a.frame];
        sw.frames[p] = { ...g, source: "auto", lm: f && f.lm ? f.lm : null, auto_t: a.t, method: a.method, status: a.status, confidence: a.confidence, reason_text: a.reason_text || "" };
        sw.missing.delete(p);
        sw.userMissing.delete(p);
        reviewOne(el, st, cat, items, k, optional);
        return;
      }
      if (ev.target.closest("[data-skip-rest]")) stepAutoTaps(el, st, cat);
    };
    enter(el);
  }
  // 見つからなかった P を探し始める時刻（前後の P の真ん中）
  function guessT(sw, p) {
    const i = ALL_P.indexOf(p);
    const before = ALL_P.slice(0, i).reverse().map((q) => sw.frames[q]).find(Boolean);
    const after = ALL_P.slice(i + 1).map((q) => sw.frames[q]).find(Boolean);
    if (before && after) return (before.t + after.t) / 2;
    return (before || after || { t: sw.det.t0 }).t;
  }

  // ⑤ 代表スイング（1本目）の P2 のクラブ（ドミノの起点。1回だけ。§6.5）
  async function stepAutoTaps(el, st, cat) {
    const sw = st.auto.swings[0];
    const f = sw.frames.P2;
    if (f) {
      const hand = (S.player && S.player.handedness) || App.pendingHand();
      const c = await blobCanvas(f.blob), k = c.width / st.video.videoWidth;
      const driver = classOf(st.club) === "driver";
      const pts = driver ? [...CLUB_PTS, ...WIDTH_PTS] : CLUB_PTS;
      const r = await tapEditor(freshBody(el), { canvas: c, p: "P2", hand, canBack: false, title: "1本目の上げ始め：クラブの握りと先を押す",
        hint: `クラブの握りの端と、クラブの先（真ん中）を押します${driver ? "。ドライバーはクラブの先の両端も押します" : ""}。上げ始めのクラブの先の位置を測ります（最初に見る項目）。${f.source === "auto" && f.status === "estimated" ? "このコマは代わりの決め方で選んだ目安です。" : ""}`,
        points: pts, initial: mapTaps(sw.taps && sw.taps.P2, (q) => [q[0] * k, q[1] * k]) });
      if (!st.auto) return; // 押しているあいだに「<」で戻った
      sw.taps = sw.taps || {};
      if (r && r !== "back") sw.taps.P2 = mapTaps(r, (q) => [Math.round(q[0] / k), Math.round(q[1] / k)]);
    }
    stepAutoSend(el, st, cat);
  }

  // 送って測る。送り直しは同じスイングへ（作ったスイングの id を覚える＝二本にしない）
  async function stepAutoSend(el, st, cat) {
    const body = autoShell(el, st, cat, "測っています");
    $("[data-manual]", body).hidden = true;
    $("[data-stop]", body).hidden = true;
    autoStage(body, 4);
    startRun(st);
    try {
      const det = await poseDetector();
      const p = await App.ensurePlayer();
      if (!st.sid) st.sid = st.session || (await api("POST", "/v1/sessions", { player_id: p.id, date: st.date, location: "" })).id;
      // 手で決めたコマ: 直した・選んだコマと、本人が「写っていない」を押した P（自動で見つからなかっただけの P は数えない）
      let nAuto = 0, nManual = 0, nMissMarked = 0;
      for (const sw of st.auto.swings) {
        for (const q of Object.values(sw.frames)) { if (q.source === "manual") nManual += 1; else nAuto += 1; }
        nMissMarked += sw.userMissing.size;
      }
      const baseCap = { container: st.container, fps_step: Math.round(st.fpsStep * 100) / 100, element_duration: Math.round((st.duration || 0) * 1000) / 1000,
        file_type: st.file.type || "", size_mb: Math.round((st.file.size / 1048576) * 10) / 10, ua: (navigator.userAgent || "").slice(0, 160),
        perf: { ...st.perf }, auto: { mode: "auto", swings: st.auto.swings.length, excluded: st.auto.excluded.length, frames_auto: nAuto, frames_manual: nManual + nMissMarked, missing_marked: nMissMarked } };
      let k = 0;
      for (const sw of st.auto.swings) {
        k += 1;
        prog(body, `スイング ${k} / ${st.auto.swings.length}`, k - 1, st.auto.swings.length);
        const frames = [];
        for (const pp of ALL_P) {
          const f = sw.frames[pp];
          if (!f) continue;
          const shot = await blobCanvas(f.blob);
          let lms = f.lm ? expand(f.lm) : null;
          if (!lms) {
            try { const r = detectFrame(det, shot, { t: f.t, frame: f.frame, p: pp, view: st.view, width: st.video.videoWidth, height: st.video.videoHeight }, st.box); lms = r && r.length === 33 ? r : []; } catch { lms = []; }
          }
          const thumb = await b64(await toBlob(scaled(shot, shot.width, shot.height, THUMB_EDGE), 0.8));
          const auto = f.source === "auto";
          frames.push({ checkpoint: pp, t: f.t, frame: f.frame, source: f.source, method: auto ? f.method || "" : "", status: auto ? f.status || "" : "", landmarks: lms, taps: (sw.taps && sw.taps[pp]) || {}, thumb });
          f.keep = scaled(shot, shot.width, shot.height, KEEP_EDGE);
        }
        const perSw = { manual: Object.values(sw.frames).filter((q) => q.source === "manual").length + sw.userMissing.size, auto: Object.values(sw.frames).filter((q) => q.source !== "manual").length,
          missing_marked: sw.userMissing.size, checked: sw.check.length, warnings: sw.det.warnings };
        if (!st.autoIds[sw.index]) {
          const cap = { ...baseCap, auto: { ...baseCap.auto, index: sw.index, ...perSw } };
          const made = await api("POST", `/v1/sessions/${st.sid}/swings`, { view: st.view, club: st.club, club_class: classOf(st.club), fps: st.fps || 0, fps_source: st.fpsSource,
            width: st.video.videoWidth, height: st.video.videoHeight, duration: st.duration || 0, ball: st.ball || [], capture: cap });
          st.autoIds[sw.index] = made.id;
          for (const q of Object.keys(sw.frames)) if (sw.frames[q].keep) keepFrame(`${made.id}:${q}`, await toBlob(sw.frames[q].keep, 0.85));
        }
        await api("PUT", `/v1/swings/${st.autoIds[sw.index]}/frames`, { frames, missing: [...sw.missing], ball: st.ball || [], series: sw.series });
      }
      App.invalidate(st.sid);
      if (window.Checks) Checks.invalidate(st.sid);
      endRun(st);
      await clearPending(true);
      st.auto = null;
      App.go(`/session/${st.sid}/check`); // 知らせ（トースト）は出さない。チェックの見出しで足りる（下の案内を隠さない）
    } catch (e) {
      endRun(st);
      autoHalt(body, "送れませんでした");
      const saved = Object.keys(st.autoIds).length ? "作れたスイングは残っています。もう一度送ると、同じスイングにコマを入れ直します（増えません）。" : "取り出したコマは、この画面に残っています。";
      $("[data-err]", body).innerHTML = App.errOf(e, { what: "送れませんでした", saved, next: "つながる所で、もう一度送ってください。" }) + `<button type="button" class="btn primary block" data-again>もう一度送る</button>`;
      const again = $("[data-again]", body);
      again.addEventListener("click", () => stepAutoSend(el, st, cat));
      again.focus();
    }
  }

  App.route("/video", render, { tab: "record", noTabbar: true });
  App.route("/video/:date", render, { tab: "record", noTabbar: true });
  return { catalog, containerInfo, selfTest, poseDetector, keptFrame, keepFrame, _crop: { boxFrom, fromBox, noSwingHtml } };
})();
