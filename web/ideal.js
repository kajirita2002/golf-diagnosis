"use strict";
/*
  理想との比較（docs/DESIGN_v2.md §7・§10 I・§11.6〜§11.7・§15 段3）。
  - 相手は3つ: ①ガイドの範囲（既定。本人のコマの上に範囲の面と、範囲の外の部位だけを範囲に入れた線）
              ②自分のベスト（同じ項目が範囲の中だった過去のスイング。新しい順に3本まで）
              ③端末の参照動画（本人が端末で選ぶ。**コマと点だけを IndexedDB golf-local の refs に置く・5本まで。
                サーバーにも AI にも送らない**。数字も出さない）
  - 範囲の形と線はサーバー（checkpoints/ideal.py）が本人の点から計算する。ここは描くだけで、座標も数字も作らない。
  - 線の見分けは色だけに頼らない（WCAG 1.4.1）: いまのあなた＝実線・範囲に入れた目安＝破線で太め＋矢印・
    ガイドの範囲＝色の面と縁の破線・お手本＝点線。図の上に線の見本つきの凡例を1行、注記は常に出す。
  - 映像の上の線は黒の縁＋色（§11.7）。縁は同じ線を太い黒で下に敷いて作る（縁を省かない）。
  - 図には代わりの文（<title>／<desc>）を入れる（WCAG 1.1.1）。
  - 「画像にする」は本人のコマと範囲・線だけを PNG にする。端末の動画を相手にしているときは参照の層を外す
    （他人の動画の絵を端末の外へ出す道を作らない）。
  - 動かして比べる: P の区切りでそろえた両方のコマを同時に送る（ゆっくり）。動きを減らす設定では自動で流さない。
  - 画面の言葉に「帯」を使わない（最初の面の禁止語）。
*/
const Ideal = (() => {
  const { $, $$, esc, icon, lab, S, api, LS } = App;
  const PS = ["P1", "P2", "P3", "P4", "P5", "P6", "P7"];
  const MAX_REFS = 5;
  const pLabel = (p) => String(p || "").replace("_5", ".5");
  const reduced = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- 取り寄せ ----
  const cache = {};
  async function load(swingId, item) {
    const k = `${swingId}|${item}`;
    if (!cache[k]) cache[k] = api("GET", `/v1/swings/${swingId}/ideal?item=${encodeURIComponent(item)}`).catch((e) => { delete cache[k]; throw e; });
    return cache[k];
  }
  // 項目の判定から、線を描くスイングを1本選ぶ（範囲の外だったスイングのうち、その P のサムネイルがあるもの）
  function pickSwing(data, it) {
    const has = (sid, p) => (data.swings || []).some((s) => s.id === sid && (s.frames || []).some((f) => f.checkpoint === p && f.has_thumb));
    const p = String(it.p || "").split("-")[0];
    const outs = (it.values || []).filter((v) => v.state === it.state).map((v) => v.swing_id);
    const all = (it.values || []).map((v) => v.swing_id);
    return outs.find((sid) => has(sid, p)) || all.find((sid) => has(sid, p)) || null;
  }

  // ---- 描く（SVG の文字列。座標はスイングの元の画素） ----
  function arrowHead(a, b, size) {
    const dx = b[0] - a[0], dy = b[1] - a[1], n = Math.hypot(dx, dy) || 1;
    const ux = dx / n, uy = dy / n;
    const l = [b[0] - ux * size - uy * size * 0.6, b[1] - uy * size + ux * size * 0.6];
    const r = [b[0] - ux * size + uy * size * 0.6, b[1] - uy * size - ux * size * 0.6];
    return `${b[0]},${b[1]} ${l[0]},${l[1]} ${r[0]},${r[1]}`;
  }
  function fanPath(z) {
    const [cx, cy] = z.c, r = z.r;
    const p0 = [cx + z.d0[0] * r, cy + z.d0[1] * r], p1 = [cx + z.d1[0] * r, cy + z.d1[1] * r];
    let da = Math.atan2(z.d1[1], z.d1[0]) - Math.atan2(z.d0[1], z.d0[0]);
    while (da <= -Math.PI) da += 2 * Math.PI;
    while (da > Math.PI) da -= 2 * Math.PI;
    return `M${cx},${cy} L${p0[0]},${p0[1]} A${r},${r} 0 0 ${da > 0 ? 1 : 0} ${p1[0]},${p1[1]} Z`;
  }
  function zoneSvg(z, W, H) {
    if (!z) return "";
    if (z.kind === "band_x") return `<rect class="z" x="${z.x0}" y="0" width="${Math.max(1, z.x1 - z.x0)}" height="${H}"/>`;
    if (z.kind === "band_y") return `<rect class="z" x="0" y="${z.y0}" width="${W}" height="${Math.max(1, z.y1 - z.y0)}"/>`;
    if (z.kind === "circle") return `${z.r_hi ? `<circle class="z" cx="${z.c[0]}" cy="${z.c[1]}" r="${z.r_hi}"/>` : ""}${z.r_lo ? `<circle class="z inner" cx="${z.c[0]}" cy="${z.c[1]}" r="${z.r_lo}"/>` : ""}`;
    if (z.kind === "fan") return `<path class="z" d="${fanPath(z)}"/>`;
    return "";
  }
  // 線を2回描く（下に黒の縁・上に色）。cls: now（いま・実線）／fix（目安・破線）／ref（お手本・点線）
  const edged = (cls, x1, y1, x2, y2) => `<line class="e ${cls}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/><line class="${cls}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
  function fixedSvg(fx, sk, W, H) {
    if (!fx) return "";
    const before = { ...sk, ...fx.before }, after = { ...sk, ...fx.after };
    let now = "", fix = "", arr = "";
    for (const [a, b] of fx.segments || []) {
      if (!before[a] || !before[b]) continue;
      now += edged("now", before[a][0], before[a][1], before[b][0], before[b][1]);
      fix += edged("fix", after[a][0], after[a][1], after[b][0], after[b][1]);
    }
    const size = Math.min(W, H) * 0.025;
    for (const n of fx.moved || []) {
      const a = fx.before[n], b = fx.after[n];
      if (!a || !b || Math.hypot(b[0] - a[0], b[1] - a[1]) < size * 1.2) continue;
      arr += edged("arr", a[0], a[1], b[0], b[1]) + `<polygon class="arrh" points="${arrowHead(a, b, size)}"/>`;
    }
    return `<g class="lnow">${now}</g><g class="lfix">${fix}</g><g class="larr">${arr}</g>`;
  }
  function lookSvg(lk) {
    return lk ? `<circle class="e look" cx="${lk.c[0]}" cy="${lk.c[1]}" r="${lk.r}"/><circle class="look" cx="${lk.c[0]}" cy="${lk.c[1]}" r="${lk.r}"/>` : "";
  }
  function refSvg(pts, bones) {
    if (!pts) return "";
    return (bones || []).filter(([a, b]) => pts[a] && pts[b] && a !== "grip").map(([a, b]) => edged("ref", pts[a][0], pts[a][1], pts[b][0], pts[b][1])).join("");
  }
  let uid = 0;
  // 重ねる図（<svg class="ov">）。alt は層1の所見の文と同じ定型文
  function overlaySvg(d, { ref = null, showFix = true } = {}) {
    const W = d.width || 1, H = d.height || 1, id = `ov${++uid}`;
    const desc = [d.alt, d.fixed && showFix ? "破線は、範囲の外の部分だけを範囲に入れた目安です" : "", ref ? "点線は、あなたが選んだお手本の骨格です" : ""].filter(Boolean).join("。");
    return `<svg class="ov" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-labelledby="${id}t ${id}d" data-ov>
      <title id="${id}t">${esc(d.alt || "あなたのコマ")}</title><desc id="${id}d">${esc(desc)}</desc>
      <g class="ovl" data-ovl>${ref ? `<g data-ref-layer>${refSvg(ref, d.bones)}</g>` : ""}${zoneSvg(d.zone, W, H)}${showFix ? fixedSvg(d.fixed, d.skeleton || {}, W, H) : ""}${lookSvg(d.look)}</g></svg>`;
  }
  function legendHtml(d, { ref = false } = {}) {
    const it = [];
    if (d.zone) it.push(`<span class="lg"><svg class="lgs" viewBox="0 0 24 12" aria-hidden="true"><rect class="z" x="1" y="1" width="22" height="10"/></svg>色の面＝ガイドの範囲</span>`);
    if (d.fixed) it.push(`<span class="lg"><svg class="lgs" viewBox="0 0 24 12" aria-hidden="true"><line class="now" x1="1" y1="6" x2="23" y2="6"/></svg>実線＝いまのあなた</span>`,
      `<span class="lg"><svg class="lgs" viewBox="0 0 24 12" aria-hidden="true"><line class="fix" x1="1" y1="6" x2="23" y2="6"/></svg>破線＝範囲に入れた目安</span>`);
    if (d.look) it.push(`<span class="lg"><svg class="lgs" viewBox="0 0 24 12" aria-hidden="true"><circle class="look" cx="12" cy="6" r="4"/></svg>丸＝見る所</span>`);
    if (ref) it.push(`<span class="lg"><svg class="lgs" viewBox="0 0 24 12" aria-hidden="true"><line class="ref" x1="1" y1="6" x2="23" y2="6"/></svg>点線＝お手本</span>`);
    return it.length ? `<p class="idlegend" data-legend>${it.join("")}</p>` : "";
  }
  // コマ＋重ねる図（C-1・ホーム・D の小さな図でも同じ部品）
  function figureHtml(d, opt = {}) {
    const src = opt.src || d.thumb;
    if (!src) return "";
    const z = opt.noOverlay ? "" : zoomStyle(d);
    return `<figure class="idfig ${opt.small ? "small" : ""}" data-idfig><div class="idzoom" ${z ? `style="${z}" data-zoom` : ""}><img src="${esc(src)}" alt="" loading="lazy">${opt.noOverlay ? "" : overlaySvg(d, opt)}</div></figure>`;
  }
  // 課題の部分に寄る（範囲と線がコマの中で小さいとき。最大3倍）。全体の面（位置の範囲）のときは寄らない
  function zoomStyle(d) {
    const W = d.width || 1, H = d.height || 1, pts = [];
    const z = d.zone;
    if (z && z.kind === "circle") { const r = (z.r_hi || z.r_lo || 0) * 3; pts.push([z.c[0] - r, z.c[1] - r], [z.c[0] + r, z.c[1] + r]); }
    if (z && z.kind === "fan") pts.push([z.c[0] - z.r, z.c[1] - z.r], [z.c[0] + z.r, z.c[1] + z.r]);
    if (d.fixed) for (const q of [...Object.values(d.fixed.before || {}), ...Object.values(d.fixed.after || {})]) pts.push(q);
    if (d.look) pts.push([d.look.c[0] - d.look.r * 2, d.look.c[1] - d.look.r * 2], [d.look.c[0] + d.look.r * 2, d.look.c[1] + d.look.r * 2]);
    if (!pts.length || (z && (z.kind === "band_x" || z.kind === "band_y"))) return "";
    const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
    const x0 = Math.max(0, Math.min(...xs)), x1 = Math.min(W, Math.max(...xs)), y0 = Math.max(0, Math.min(...ys)), y1 = Math.min(H, Math.max(...ys));
    const k = Math.max(1, Math.min(3, 0.8 * Math.min(W / Math.max(1, x1 - x0), H / Math.max(1, y1 - y0))));
    if (k < 1.2) return "";
    return `transform:scale(${k.toFixed(2)});transform-origin:${(((x0 + x1) / 2) / W * 100).toFixed(1)}% ${(((y0 + y1) / 2) / H * 100).toFixed(1)}%`;
  }
  const drawable = (d) => d && (d.zone || d.fixed || d.look);

  // C-1 の図を差し替える（描けないときは元の写真のまま）
  async function enhance(fig, data, it, sid) {
    const swingId = pickSwing(data, it);
    if (!fig || !swingId) return;
    let d;
    try { d = await load(swingId, it.id); } catch { return; }
    if (!drawable(d) || !fig.isConnected || !d.thumb) return;
    fig.outerHTML = `${legendHtml(d)}${figureHtml(d)}<p class="caption" data-ideal-note>${esc(d.note)}</p>
      <a class="btn block" data-ideal-open href="#/session/${sid}/ideal/${encodeURIComponent(it.id)}">${icon("layers")}理想と比べる</a>`;
  }
  // ホーム・D の小さな図（課題のカードの写真の代わり）。描けなければ空
  async function thumbHtml(data, it) {
    const swingId = pickSwing(data, it);
    if (!swingId) return "";
    try {
      const d = await load(swingId, it.id);
      return drawable(d) ? figureHtml(d, { small: true }) : "";
    } catch { return ""; }
  }

  // ---- 端末の参照動画（IndexedDB golf-local の refs。video.js と同じ置き場・同じ版） ----
  function idb() {
    return new Promise((ok, ng) => {
      if (!("indexedDB" in window)) { ng(new Error("IndexedDB がありません")); return; }
      const r = indexedDB.open("golf-local", 1);
      r.onupgradeneeded = () => { const db = r.result; for (const n of ["swingFrames", "pose", "refs", "strips"]) if (!db.objectStoreNames.contains(n)) db.createObjectStore(n); };
      r.onsuccess = () => ok(r.result);
      r.onerror = () => ng(r.error);
    });
  }
  async function refsAll() {
    try {
      const db = await idb();
      const out = await new Promise((ok, ng) => { const q = db.transaction("refs").objectStore("refs").getAll(); q.onsuccess = () => ok(q.result || []); q.onerror = () => ng(q.error); });
      db.close();
      return out.sort((a, b) => String(b.created).localeCompare(String(a.created)));
    } catch { return []; }
  }
  async function refPut(r) {
    const db = await idb();
    await new Promise((ok, ng) => { const tx = db.transaction("refs", "readwrite"); tx.objectStore("refs").put(r, r.id); tx.oncomplete = ok; tx.onerror = () => ng(tx.error); });
    db.close();
  }
  async function refDel(id) {
    const db = await idb();
    await new Promise((ok) => { const tx = db.transaction("refs", "readwrite"); tx.objectStore("refs").delete(id); tx.oncomplete = ok; tx.onerror = ok; });
    db.close();
  }
  const REF_NOTICE = "この動画は端末の中だけで使います。送信しません。他の人の動画は、使ってよいものだけを選んでください";
  async function refNoticeOnce() {
    if (LS.get("golf.refNotice")) return true;
    const ok = await App.ask({ title: "端末の動画を使います", text: REF_NOTICE, ok: "わかりました", cancel: "やめる" });
    if (ok) LS.set("golf.refNotice", true);
    return ok;
  }

  // 参照の骨格を本人のコマへ合わせる: P1 の足元の真ん中で位置、胴の長さで縮尺（回転はしない）。利き手が違えば反転
  const LMI = { left_shoulder: 11, right_shoulder: 12, left_elbow: 13, right_elbow: 14, left_wrist: 15, right_wrist: 16, left_hip: 23, right_hip: 24,
    left_knee: 25, right_knee: 26, left_ankle: 27, right_ankle: 28, left_heel: 29, right_heel: 30, left_toe: 31, right_toe: 32 };
  const SWAP = (n) => n.startsWith("left_") ? "right_" + n.slice(5) : n.startsWith("right_") ? "left_" + n.slice(6) : n;
  function refPoints(ref, p, flip) {
    const f = ref.frames && ref.frames[p];
    if (!f || !f.lm) return null;
    const out = {};
    for (const [n, i] of Object.entries(LMI)) {
      const q = f.lm[i];
      if (!q) continue;
      const x = q.x * ref.w, y = q.y * ref.h;
      out[flip ? SWAP(n) : n] = [flip ? ref.w - x : x, y];
    }
    return out;
  }
  const mid = (a, b) => a && b ? [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] : null;
  function torsoOf(pts) {
    const s = mid(pts.left_shoulder, pts.right_shoulder), h = mid(pts.left_hip, pts.right_hip);
    return s && h ? Math.hypot(s[0] - h[0], s[1] - h[1]) : 0;
  }
  function alignRef(d, ref, p) {
    const flip = (ref.hand || "R") !== (d.handedness || "R");
    const me0 = (d.skeletons || {}).P1 || (d.skeletons || {})[p];
    const rf0 = refPoints(ref, "P1", flip) || refPoints(ref, p, flip);
    const rp = refPoints(ref, p, flip);
    if (!me0 || !rf0 || !rp) return null;
    const lm = torsoOf(me0), lr = torsoOf(rf0);
    const am = mid(me0.left_ankle, me0.right_ankle), ar = mid(rf0.left_ankle, rf0.right_ankle);
    if (!lm || !lr || !am || !ar) return null;
    const s = lm / lr;
    const out = {};
    for (const [n, q] of Object.entries(rp)) out[n] = [+(am[0] + (q[0] - ar[0]) * s).toFixed(1), +(am[1] + (q[1] - ar[1]) * s).toFixed(1)];
    return out;
  }

  // ---- I 理想と比べる（全画面） ----
  const TARGETS = [["guide", "ガイドの範囲"], ["best", "あなたのベスト"], ["ref", "端末の動画"]];
  const MODES = [["over", "重ねる"], ["side", "並べる"], ["play", "動かして比べる"]];
  const ALPHAS = [["0", "0"], ["50", "50"], ["100", "100"]];
  const segHtml = (name, label, opts, cur, attrs = "") => `<div class="seg wrap" role="group" aria-label="${esc(label)}" data-seg="${name}" ${attrs}>${opts.map(([v, t]) =>
    `<button type="button" data-v="${v}" aria-pressed="${String(v === cur)}">${esc(t)}</button>`).join("")}</div>`;
  const md = (iso) => { const m = /^\d{4}-(\d{2})-(\d{2})/.exec(iso || ""); return m ? `${+m[1]}月${+m[2]}日` : ""; };

  async function renderI({ el, params, alive }) {
    const sid = Number(params.id), iid = params.item;
    el.innerHTML = `<div class="pagehead">${App.backBtn(`#/session/${sid}/check/${encodeURIComponent(iid)}`, "項目へ戻る")}<h1>理想と比べる</h1></div><div data-body></div>`;
    const body = $("[data-body]", el);
    const stop = App.loading(body);
    let data, it, d = null, swingId;
    try {
      data = await Checks.load(sid);
      it = ((data.checks && data.checks.items) || []).find((x) => x.id === iid);
      swingId = it && pickSwing(data, it);
      if (swingId) d = await load(swingId, iid);
    } catch (e) {
      stop();
      body.innerHTML = App.errOf(e, { what: "比べる図を出せませんでした" });
      return;
    } finally { stop(); }
    if (!alive()) return;
    if (!it || !d) { body.innerHTML = App.errorHtml({ what: "この項目のコマが見つかりませんでした", next: "チェックの一覧から開き直してください。" }); return; }
    const itemP = d.p && PS.includes(d.p) ? d.p : PS.find((p) => (d.thumbs || []).includes(p)) || "P1";
    const st = { target: "guide", mode: "over", p: itemP, alpha: "100", best: null, bestIdx: 0, refs: null, refId: LS.get("golf.refPick") || "", timer: null, urls: [] };
    const myThumb = (p) => (d.thumbs || []).includes(p) ? `/v1/swings/${d.swing_id}/thumbs/${p}` : "";
    const freeUrls = () => { for (const u of st.urls) URL.revokeObjectURL(u); st.urls = []; };
    const stopPlay = () => { if (st.timer) { clearInterval(st.timer); st.timer = null; } };

    async function ensureBest() {
      if (st.best) return;
      try {
        const pid = S.player && S.player.id;
        const r = pid ? await api("GET", `/v1/players/${pid}/best?item=${encodeURIComponent(iid)}&p=${encodeURIComponent(itemP)}`) : { best: [] };
        st.best = (r.best || []).filter((b) => b.swing_id !== d.swing_id);
      } catch { st.best = []; }
    }
    async function ensureRefs() { if (!st.refs) st.refs = await refsAll(); }
    const curRef = () => (st.refs || []).find((r) => r.id === st.refId) || (st.refs || [])[0] || null;
    const refUrl = (ref, p) => {
      const f = ref && ref.frames && ref.frames[p];
      if (!f || !f.blob) return "";
      const u = URL.createObjectURL(f.blob);
      st.urls.push(u);
      return u;
    };

    function stage() {
      freeUrls();
      const p = st.p, mine = myThumb(p);
      const onItem = p === itemP;
      const base = onItem ? d : { ...d, zone: null, fixed: null, look: null, alt: `${pLabel(p)} のあなたのコマ` };
      const noImg = (t) => `<div class="noimg idnoimg" role="img" aria-label="${esc(t)}">${icon("video")}</div>`;
      const mineFig = (opt = {}) => mine ? figureHtml(base, { src: mine, ...opt }) : noImg(`${pLabel(p)} のあなたのコマはありません`);
      if (st.target === "guide") {
        return `${onItem ? legendHtml(d) : ""}${mineFig()}
          ${onItem ? `<p class="caption" data-ideal-note>${esc(d.note)}</p>` : `<p class="caption">範囲と目安は ${lab("p", pLabel(itemP))} のコマにだけ描きます（課題の項目の P）。</p>`}`;
      }
      if (st.target === "best") {
        const b = (st.best || [])[st.bestIdx];
        if (!b) return `<div class="note" data-best-none>この項目が範囲の中だった日は、まだありません。範囲の中に入った日が来ると、ここで並べて見られます。</div>`;
        const who = `${md(b.date)}のあなた（この日は範囲の中でした）`;
        const other = `<figure class="idfig" data-idfig><img src="/v1/swings/${b.swing_id}/thumbs/${p}" alt="${esc(who)} の ${pLabel(p)}" loading="lazy" data-hide-err></figure>`;
        const pick = st.best.length > 1 ? segHtml("bestpick", "どの日と比べるか", st.best.map((x, i) => [String(i), md(x.date)]), String(st.bestIdx)) : "";
        return `${pick}<div class="idpair ${st.mode === "play" ? "play" : ""}"><div><p class="label">いまのあなた</p>${mineFig({ noOverlay: true })}</div>
          <div><p class="label" data-best-who>${esc(who)}</p>${other}</div></div>
          <p class="caption">見比べる材料です。合否には使いません。</p>`;
      }
      // 端末の動画
      const ref = curRef();
      if (!ref) return `<div class="note" data-ref-none><p style="margin:0">この端末には、お手本の動画がまだありません。</p><a class="btn block" style="margin-top:var(--s2)" href="#/refs">${icon("video")}端末の動画を入れる</a></div>`;
      const pick = st.refs.length > 1 ? segHtml("refpick", "どのお手本と比べるか", st.refs.map((r) => [r.id, r.name || "お手本"]), ref.id) : "";
      const local = `<p class="caption" data-ref-local>あなたが選んだお手本（この端末の中だけで使っています）。体格もカメラも違うので、数字は出しません。</p>`;
      if (st.mode === "over") {
        const pts = alignRef(d, ref, p);
        const fig = mine ? figureHtml({ ...base, zone: null, fixed: null, look: null }, { src: mine, ref: pts }) : noImg("あなたのコマはありません");
        return `${pick}${pts ? legendHtml({}, { ref: true }) : `<p class="caption">このお手本は ${lab("p", pLabel(p))} の体の点が取れていないので、重ねられません。</p>`}${fig}${local}`;
      }
      const ru = refUrl(ref, p);
      return `${pick}<div class="idpair ${st.mode === "play" ? "play" : ""}"><div><p class="label">いまのあなた</p>${mineFig({ noOverlay: true })}</div>
        <div><p class="label">お手本</p>${ru ? `<figure class="idfig" data-idfig><img src="${ru}" alt="お手本の ${pLabel(p)}"></figure>` : noImg(`お手本の ${pLabel(p)} のコマはありません`)}</div></div>${local}`;
    }

    function draw() {
      const modes = st.target === "ref" ? MODES : st.target === "best" ? MODES.slice(1) : [];
      if (st.target === "best" && st.mode === "over") st.mode = "side";
      const k = PS.indexOf(st.p);
      body.innerHTML = `
        <p class="t-headline" data-ideal-say>${esc(it.fault_label || it.title)}</p>
        <p class="sub">${esc(it.title)}</p>
        ${segHtml("target", "比べる相手", TARGETS, st.target)}
        ${modes.length ? segHtml("mode", "見せ方", modes, st.mode) : ""}
        <div class="idstage" data-stage data-alpha="${st.alpha}">${stage()}</div>
        ${st.mode === "play" && st.target !== "guide" ? `<div class="stack"><button type="button" class="btn block" data-play aria-pressed="${String(!!st.timer)}">${st.timer ? "止める" : "P の順に流す（ゆっくり）"}</button>
          ${reduced() ? `<p class="caption" data-reduced>動きを減らす設定なので、自動では流しません。P のボタンで進めてください。</p>` : ""}</div>` : ""}
        <div class="idps" role="group" aria-label="P を選ぶ" data-idps>${PS.map((p) => `<button type="button" data-p="${p}" aria-pressed="${String(p === st.p)}">${lab("p", p)}</button>`).join("")}</div>
        <div class="itemnav"><button type="button" class="btn" data-pprev ${k <= 0 ? "disabled" : ""}>${icon("chevron-left")}前の P</button>
          <button type="button" class="btn" data-pnext ${k >= PS.length - 1 ? "disabled" : ""}>次の P${icon("chevron-right")}</button></div>
        ${st.target === "guide" || (st.target === "ref" && st.mode === "over") ? `<div class="field"><span id="lb-alpha">重ねる線の濃さ</span>${segHtml("alpha", "重ねる線の濃さ", ALPHAS.map(([v]) => [v, v === "0" ? "消す" : v === "50" ? "半分" : "そのまま"]), st.alpha, `aria-labelledby="lb-alpha"`)}</div>` : ""}
        <div class="stack block"><button type="button" class="btn block" data-export>${icon("upload")}画像にする</button>
          ${st.target === "ref" ? `<p class="caption" data-export-note>お手本の動画は画像に入りません。</p>` : ""}</div>`;
      $$("img[data-hide-err]", body).forEach((im) => im.addEventListener("error", () => { im.closest("figure").outerHTML = `<div class="noimg idnoimg" role="img" aria-label="この日の ${esc(pLabel(st.p))} のコマはありません">${icon("video")}</div>`; }, { once: true }));
    }

    function setP(p) { if (PS.includes(p)) { st.p = p; draw(); } }
    body.addEventListener("click", async (ev) => {
      const b = ev.target.closest("button");
      if (!b || b.disabled) return;
      const seg = b.closest("[data-seg]");
      if (seg) {
        const v = b.dataset.v, name = seg.dataset.seg;
        if (name === "target") {
          if (v === "ref") { await ensureRefs(); if (!(await refNoticeOnce())) return; }
          if (v === "best") await ensureBest();
          stopPlay();
          st.target = v;
        } else if (name === "mode") { stopPlay(); st.mode = v; } else if (name === "alpha") st.alpha = v;
        else if (name === "bestpick") st.bestIdx = Number(v);
        else if (name === "refpick") { st.refId = v; LS.set("golf.refPick", v); }
        draw();
        return;
      }
      if (b.dataset.p) { setP(b.dataset.p); return; }
      if (b.hasAttribute("data-pprev")) { setP(PS[PS.indexOf(st.p) - 1]); return; }
      if (b.hasAttribute("data-pnext")) { setP(PS[PS.indexOf(st.p) + 1]); return; }
      if (b.hasAttribute("data-play")) {
        if (st.timer) { stopPlay(); draw(); return; }
        if (reduced()) { setP(PS[(PS.indexOf(st.p) + 1) % PS.length]); return; }
        st.p = "P1";
        st.timer = setInterval(() => {
          if (!alive() || !body.isConnected) { stopPlay(); return; }
          const k = PS.indexOf(st.p);
          if (k >= PS.length - 1) { stopPlay(); draw(); return; }
          st.p = PS[k + 1];
          draw();
        }, 1600);
        draw();
        return;
      }
      if (b.hasAttribute("data-export")) exportPng(b);
    });

    // PNG に書き出す（本人のコマ＋範囲と線だけ。参照の層は外す。位置情報は入らない）
    async function exportPng(btn) {
      const img = $("[data-stage] figure[data-idfig] img", body);
      if (!img || !myThumb(st.p)) { App.toast("このコマは画像にできません"); return; }
      try {
        const bm = await fetch(myThumb(st.p)).then((r) => r.blob()).then((bl) => createImageBitmap(bl));
        const k = 2, cv = document.createElement("canvas");
        cv.width = bm.width * k; cv.height = bm.height * k;
        const g = cv.getContext("2d");
        g.drawImage(bm, 0, 0, cv.width, cv.height);
        const svg = $("[data-stage] svg[data-ov]", body);
        if (svg && st.alpha !== "0") {
          const cl = svg.cloneNode(true);
          const rl = cl.querySelector("[data-ref-layer]");
          if (rl) rl.remove(); // お手本の骨格は画像に入れない
          const cs = getComputedStyle(document.documentElement);
          const v = (n) => cs.getPropertyValue(n).trim();
          cl.setAttribute("xmlns", "http://www.w3.org/2000/svg");
          cl.setAttribute("width", cv.width); cl.setAttribute("height", cv.height);
          const style = document.createElementNS("http://www.w3.org/2000/svg", "style");
          style.textContent = `.z{fill:${v("--ov-range")};fill-opacity:.28;stroke:${v("--ov-edge")};stroke-width:2;stroke-dasharray:6 4}.z.inner{fill:none}
            line,polygon{vector-effect:non-scaling-stroke;stroke-linecap:round}.e{stroke:${v("--ov-edge")};stroke-width:9}
            .now{stroke:${v("--ov-me")};stroke-width:4}.fix,.arr{stroke:${v("--ov-gap")};stroke-width:6;stroke-dasharray:12 8}.arr{stroke-dasharray:none;stroke-width:3}
            .arrh{fill:${v("--ov-gap")};stroke:${v("--ov-edge")};stroke-width:2}.look{fill:none;stroke:${v("--ov-me")};stroke-width:4;stroke-dasharray:8 6}.e.look{stroke-width:8;stroke-dasharray:none}
            .ovl{opacity:${Number(st.alpha) / 100}}`;
          cl.insertBefore(style, cl.firstChild);
          const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(cl)], { type: "image/svg+xml" }));
          try {
            const im = await new Promise((ok, ng) => { const i = new Image(); i.onload = () => ok(i); i.onerror = ng; i.src = url; });
            g.drawImage(im, 0, 0, cv.width, cv.height);
          } finally { URL.revokeObjectURL(url); }
        }
        const blob = await new Promise((ok) => cv.toBlob(ok, "image/png"));
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `swing-${st.p}.png`;
        document.body.appendChild(a);
        a.click();
        setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
        App.toast(st.target === "ref" ? "画像にしました（お手本の動画は入れていません）" : "画像にしました");
      } catch (e) {
        btn.insertAdjacentHTML("afterend", App.errOf(e, { what: "画像にできませんでした" }));
      }
    }
    draw();
  }

  // ---- 端末の動画の一覧と取り込み（設定から開く。§7.3・§10 S） ----
  async function renderRefs({ el, alive }) {
    el.innerHTML = `<div class="pagehead">${App.backBtn("#/settings", "設定へ戻る")}<h1>端末の動画</h1></div><div data-body></div>`;
    const body = $("[data-body]", el);
    const refs = await refsAll();
    if (!alive()) return;
    body.innerHTML = `<p>${esc(REF_NOTICE)}。</p>
      <p data-ref-count>この端末に ${lab("count", refs.length + "本")}（${lab("count", MAX_REFS + "本")}まで）</p>
      ${refs.length ? `<ul class="navlist" data-ref-list>${refs.map((r) => `<li class="row"><span class="grow1">${esc(r.name || "お手本")}<small>${esc(r.hand === "L" ? "左打ち" : "右打ち")}・${lab("count", Object.keys(r.frames || {}).length + "コマ")}</small></span>
        <button type="button" class="btn danger" data-ref-del="${esc(r.id)}">消す</button></li>`).join("")}</ul>` : ""}
      ${refs.length < MAX_REFS ? `<label class="btn primary block" data-ref-add>${icon("video")}動画を選ぶ<input type="file" accept="video/*" class="visually-hidden" data-ref-file></label>` : `<p class="note">${lab("count", MAX_REFS + "本")}まで置けます。入れるときは、どれかを消してください。</p>`}`;
    body.addEventListener("click", async (ev) => {
      const del = ev.target.closest("[data-ref-del]");
      if (!del) return;
      const ok = await App.ask({ title: "この動画を消しますか", text: "この端末から消します。元に戻せません。", ok: "消す", cancel: "やめる", danger: true });
      if (!ok) return;
      await refDel(del.dataset.refDel);
      App.toast("消しました");
      App.render();
    });
    const inp = $("[data-ref-file]", body);
    if (inp) inp.addEventListener("change", async () => {
      const f = inp.files && inp.files[0];
      if (!f) return;
      if (!(await refNoticeOnce())) { inp.value = ""; return; }
      refImport(body, f, alive);
    });
  }

  // 取り込み: コマ送りで P1〜P7 を選び、選んだコマでだけ体の点を取る（端末の中だけ。サーバーへは何も送らない）
  function refImport(body, file, alive) {
    const url = URL.createObjectURL(file);
    const st = { i: 0, picks: {}, hand: "R", name: "お手本" };
    body.innerHTML = `<div class="field"><label for="ref-name">名前（この端末の中だけ）</label><input id="ref-name" type="text" maxlength="20" value="お手本" data-ref-name></div>
      <div class="field"><span id="lb-rh">お手本の利き手</span><div class="seg" role="group" aria-labelledby="lb-rh" data-rhand><button type="button" data-v="R" aria-pressed="true">右打ち</button><button type="button" data-v="L" aria-pressed="false">左打ち</button></div></div>
      <p class="t-headline" data-ref-step></p>
      <video class="refvid" src="${url}" playsinline muted preload="auto" data-ref-video></video>
      <div class="stepbtns"><button type="button" class="btn" data-dt="-0.5">0.5秒前</button><button type="button" class="btn" data-dt="-0.0334">1コマ前</button>
        <button type="button" class="btn" data-dt="0.0334">1コマ後</button><button type="button" class="btn" data-dt="0.5">0.5秒後</button></div>
      <button type="button" class="btn primary block" data-ref-pick></button>
      <p class="caption" data-ref-picked></p><div data-ref-msg></div>`;
    const v = $("[data-ref-video]", body);
    const say = () => {
      const p = PS[st.i];
      $("[data-ref-step]", body).innerHTML = `${lab("p", p)} のコマを選んでください`;
      $("[data-ref-pick]", body).innerHTML = `このコマを ${lab("p", p)} にする`;
      $("[data-ref-picked]", body).innerHTML = Object.keys(st.picks).length ? `選んだ: ${Object.keys(st.picks).map((x) => lab("p", x)).join("・")}` : "";
    };
    say();
    body.addEventListener("click", async (ev) => {
      const b = ev.target.closest("button");
      if (!b) return;
      if (b.closest("[data-rhand]")) { st.hand = b.dataset.v; $$("[data-rhand] button", body).forEach((x) => x.setAttribute("aria-pressed", String(x === b))); return; }
      if (b.dataset.dt) { v.currentTime = Math.max(0, Math.min((v.duration || 0) - 0.001, v.currentTime + Number(b.dataset.dt))); return; }
      if (!b.hasAttribute("data-ref-pick") || b.getAttribute("aria-disabled") === "true") return;
      b.setAttribute("aria-disabled", "true");
      try {
        const k = Math.min(1, 720 / Math.max(v.videoWidth || 1, v.videoHeight || 1));
        const cv = document.createElement("canvas");
        cv.width = Math.round((v.videoWidth || 1) * k); cv.height = Math.round((v.videoHeight || 1) * k);
        cv.getContext("2d").drawImage(v, 0, 0, cv.width, cv.height);
        const det = await Video.poseDetector();
        const lm = det.detect(cv, { t: v.currentTime, view: "dtl" });
        const blob = await new Promise((ok) => cv.toBlob(ok, "image/jpeg", 0.85));
        st.picks[PS[st.i]] = { blob, lm: lm || null };
        st.w = cv.width; st.h = cv.height;
        st.i += 1;
        if (st.i < PS.length) { say(); b.removeAttribute("aria-disabled"); return; }
        const r = { id: "r" + Date.now(), name: ($("[data-ref-name]", body).value || "お手本").slice(0, 20), hand: st.hand, w: st.w, h: st.h, created: new Date().toISOString(), frames: st.picks };
        await refPut(r);
        URL.revokeObjectURL(url);
        App.toast("端末に入れました（送信していません）");
        if (alive()) App.render();
      } catch (e) {
        b.removeAttribute("aria-disabled");
        $("[data-ref-msg]", body).innerHTML = App.errOf(e, { what: "このコマを使えませんでした", saved: "選んだコマはそのままです。" });
      }
    });
  }

  App.route("/session/:id/ideal/:item", renderI, { tab: "record" });
  App.route("/refs", renderRefs, { tab: "home" });
  return { load, pickSwing, overlaySvg, legendHtml, figureHtml, enhance, thumbHtml, refsAll, alignRef, MAX_REFS };
})();
