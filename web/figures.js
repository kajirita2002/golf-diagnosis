/*
  解説レポートの図 F1〜F7 を素の SVG で描く部品（docs/DESIGN_coaching.md §5.6）。
  - 図の中身（点・帯・注記）はサーバーが JSON で返す。ここは描くだけで、数字を作らない。
  - 座標は右打ちのまま届く。左打ちは描くときに左右を反転する（打点のトゥ／ヒールは反転しない。
    向きは heel_on で決まる）。
  - 色は tokens.css の CSS 変数で塗る。要素には class（g-good・g-miss・g-ext・g-mishit・g-band・g-axis …）
    だけを付け、fill や stroke の色を属性に直書きしない（暗い表示にそのまま追従させるため）。
    形でも区別する（帯の中 ●・外 ○・極端な打点 ▲・候補 ×）。
  - 文字は全部 textContent で入れる（クラブ名は利用者の CSV から来る文字列）。
  - 点は data-shot="<球の id>" を持つ。タップの受け口は画面の側に1つだけある。
  - スマホ: viewBox="0 0 360 H"・幅100%・文字12〜13・目盛り5本まで・点の半径5に透明な当たり判定の半径12。
*/
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const W = 360;
  let uid = 0;

  // ---- 小さな道具 ----
  function el(tag, attrs, text) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) e.setAttribute(k, v);
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function h(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  const r1 = (v) => Math.round(v * 10) / 10;
  const minus = (s) => s.replace(/^-/, "−");
  const lin = (d0, d1, r0, r1_) => (v) => r0 + ((v - d0) / (d1 - d0 || 1)) * (r1_ - r0);
  function niceTicks(lo, hi, n = 5) {
    const span = hi - lo;
    if (!(span > 0)) return [lo];
    const raw = span / Math.max(1, n - 1);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n - 0.01) || 10 * mag;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.abs(v) < 1e-9 ? 0 : v);
    return out;
  }
  // 目盛りの桁は間隔を正しく書ける桁にする（0.25 を小数1桁に丸めて「0.3」、2.5 を「3」と書かない）
  const decimals = (step) => {
    let d = 0;
    while (d < 4 && Math.abs(Math.round(step * 10 ** d) - step * 10 ** d) > 1e-6) d++;
    return d;
  };
  const fmtTick = (v, step) => minus(v.toFixed(decimals(step)));
  // まとめの F1 の横軸（左右 ÷ キャリー）は % で書く（0.25 → 25%）
  const pctTick = (v, step) => minus((v * 100).toFixed(decimals(step * 100))) + "%";

  // 点の形（mark）と色（class）。当たり判定の透明な円と一緒に data-shot を持たせる。
  function point(g, x, y, p, extra) {
    const cls = p.class || "g-miss";
    const grp = el("g", { class: "pt", "data-shot": p.shot_id, tabindex: "0", role: "button" });
    grp.appendChild(el("title", {}, `#${p.seq}${p.club ? " " + p.club : ""}`));
    const m = p.mark || "none";
    if (m === "ext" || (extra && extra.warn && m !== "mishit")) {
      grp.appendChild(el("path", { d: `M${x},${y - 6}L${x + 5.5},${y + 4}L${x - 5.5},${y + 4}Z`, class: `${cls} g-ext` }));
    } else if (m === "mishit") {
      grp.appendChild(el("path", { d: `M${x - 4.5},${y - 4.5}L${x + 4.5},${y + 4.5}M${x - 4.5},${y + 4.5}L${x + 4.5},${y - 4.5}`, class: `${cls} g-cross` }));
    } else if (m === "out") {
      grp.appendChild(el("circle", { cx: x, cy: y, r: 4.5, class: `${cls} hollow` }));
    } else {
      grp.appendChild(el("circle", { cx: x, cy: y, r: m === "none" ? 4 : 5, class: cls }));
    }
    grp.appendChild(el("circle", { cx: x, cy: y, r: 16, class: "g-hit" }));
    g.appendChild(grp);
    return grp;
  }

  function svgRoot(fig, H) {
    const s = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "data-fig": fig.id, "aria-labelledby": `fig${++uid}t fig${uid}d` });
    s.appendChild(el("title", { id: `fig${uid}t` }, fig.title || fig.id));
    s.appendChild(el("desc", { id: `fig${uid}d` }, fig.desc || ""));
    return s;
  }

  function axes(s, x, y, xd, yd, box, opt = {}) {
    const g = el("g", { class: "g-axisg" });
    const xt = niceTicks(xd[0], xd[1], 5), yt = niceTicks(yd[0], yd[1], 5);
    const xs = xt.length > 1 ? xt[1] - xt[0] : 1, ys = yt.length > 1 ? yt[1] - yt[0] : 1;
    for (const v of xt) {
      g.appendChild(el("line", { x1: x(v), x2: x(v), y1: box.t, y2: box.b, class: v === 0 ? "g-axis g-zero" : "g-grid" }));
      g.appendChild(el("text", { x: x(v), y: box.b + 13, class: "g-sub t11", "text-anchor": "middle" }, (opt.xfmt || fmtTick)(opt.mirror ? -v : v, xs)));
    }
    for (const v of yt) {
      g.appendChild(el("line", { x1: box.l, x2: box.r, y1: y(v), y2: y(v), class: v === 0 ? "g-axis g-zero" : "g-grid" }));
      g.appendChild(el("text", { x: box.l - 4, y: y(v) + 4, class: "g-sub t11", "text-anchor": "end" }, (opt.yfmt || fmtTick)(opt.ymirror ? -v : v, ys)));
    }
    g.appendChild(el("rect", { x: box.l, y: box.t, width: box.r - box.l, height: box.b - box.t, class: "g-axis g-frame" }));
    s.appendChild(g);
  }

  function legend(items) {
    const box = h("div", "fig-legend");
    for (const it of items) {
      if (!it.label) continue;
      const sp = h("span", "lg");
      const sv = el("svg", { viewBox: "0 0 14 14", width: "14", height: "14", "aria-hidden": "true" });
      const c = it.cls || "g-miss";
      if (it.shape === "tri") sv.appendChild(el("path", { d: "M7,1L12.5,11L1.5,11Z", class: c }));
      else if (it.shape === "cross") sv.appendChild(el("path", { d: "M2.5,2.5L11.5,11.5M2.5,11.5L11.5,2.5", class: `${c} g-cross` }));
      else if (it.shape === "ring") sv.appendChild(el("circle", { cx: 7, cy: 7, r: 4.5, class: `${c} hollow` }));
      else if (it.shape === "rect") sv.appendChild(el("rect", { x: 1, y: 3, width: 12, height: 8, class: c }));
      else if (it.shape === "line") sv.appendChild(el("line", { x1: 1, y1: 7, x2: 13, y2: 7, class: c }));
      else sv.appendChild(el("circle", { cx: 7, cy: 7, r: 5, class: c }));
      sp.appendChild(sv);
      sp.appendChild(document.createTextNode(it.label));
      box.appendChild(sp);
    }
    return box;
  }

  function wrap(fig, svg, notes, lg) {
    const f = h("figure", "fig");
    f.dataset.fig = fig.id;
    f.appendChild(h("figcaption", "fig-title", `${fig.id} ${fig.title || ""}`));
    f.appendChild(svg);
    if (lg) f.appendChild(lg);
    for (const n of notes || []) {
      if (!n) continue;
      const p = h("p", "fig-note" + (n.cls ? " " + n.cls : ""), n.text !== undefined ? n.text : n);
      if (n.data) for (const [k, v] of Object.entries(n.data)) p.dataset[k] = v;
      f.appendChild(p);
    }
    return f;
  }

  // 点の凡例（F1・F3 で共通）
  function pointLegend(lg, marks) {
    const items = [];
    if (marks.has("in")) items.push({ cls: "g-good", label: lg.in });
    if (marks.has("out")) items.push({ cls: "g-miss", shape: "ring", label: lg.out });
    if (marks.has("none")) items.push({ cls: "g-miss", label: "帯を判定できない球（フェースが無いなど）" });
    if (marks.has("ext")) items.push({ cls: "g-ext", shape: "tri", label: lg.ext });
    if (marks.has("mishit")) items.push({ cls: "g-mishit", shape: "cross", label: lg.mishit });
    return items;
  }

  // ---- F1 着弾の散布 ＋ 狙いの幅 ----
  function F1(fig, ctx) {
    const sx = ctx.hand === "L" ? -1 : 1;
    const H = 290, box = { l: 46, r: W - 14, t: 12, b: H - 42 };
    const ratio = (fig.units || {}).x === "side_ratio";
    const t = fig.target || {};
    const half = (yv) => Math.max(t.side_min_m || 0, (t.side_pct || 0) * (ratio ? 1 : yv));
    const pts = fig.points || [];
    // ばらつきの形（1SD 楕円）はデータの座標で点を作ってから写す
    const ell = [];
    if (fig.ellipse && fig.ellipse.rx) {
      const e = fig.ellipse, a = (e.angle_deg * Math.PI) / 180;
      for (let i = 0; i <= 48; i++) {
        const th = (i / 48) * 2 * Math.PI, u = e.rx * Math.cos(th), v = e.ry * Math.sin(th);
        ell.push([sx * (e.cx + u * Math.cos(a) - v * Math.sin(a)), e.cy + u * Math.sin(a) + v * Math.cos(a)]);
      }
    }
    const ys = pts.map((p) => p.y).concat(ell.map((q) => q[1]));
    let y0 = Math.min(...ys), y1 = Math.max(...ys);
    const pad = (y1 - y0) * 0.06 || 1;
    y0 -= pad; y1 += pad;
    const xmax = Math.max(...pts.map((p) => Math.abs(p.x)), ...ell.map((q) => Math.abs(q[0])), half(y1)) * 1.08;
    const x = lin(-xmax, xmax, box.l, box.r), y = lin(y0, y1, box.b, box.t);
    const s = svgRoot(fig, H);
    // 狙いの幅（帯ではなく Good の幅）。1本なら幅がキャリーで変わるので多角形
    const band = [];
    const steps = ratio ? [y0, y1] : Array.from({ length: 13 }, (_, i) => y0 + ((y1 - y0) * i) / 12);
    for (const yv of steps) band.push([x(half(yv)), y(yv)]);
    for (const yv of steps.slice().reverse()) band.push([x(-half(yv)), y(yv)]);
    s.appendChild(el("polygon", { points: band.map((q) => q.join(",")).join(" "), class: "g-target" }));
    // 左打ちは表示の座標（＝実際の向き・右が＋）で目盛りを書く。定型文も左打ちは符号を入れ替えて書いている
    axes(s, x, y, [-xmax, xmax], [y0, y1], box, ratio ? { xfmt: pctTick } : {});
    if (ell.length) s.appendChild(el("polyline", { points: ell.map((q) => `${x(q[0])},${y(q[1])}`).join(" "), class: "g-ellipse" }));
    const g = el("g");
    const marks = new Set();
    for (const p of pts) { marks.add(p.mark); point(g, x(sx * p.x), y(p.y), p); }
    s.appendChild(g);
    const al = fig.axis_labels || {};
    s.appendChild(el("text", { x: (box.l + box.r) / 2, y: H - 12, class: "g-sub t12", "text-anchor": "middle" }, al.x || ""));
    s.appendChild(el("text", { x: box.l + 2, y: H - 12, class: "g-sub t11" }, "← 左"));
    s.appendChild(el("text", { x: box.r - 2, y: H - 12, class: "g-sub t11", "text-anchor": "end" }, "右 →"));
    s.appendChild(el("text", { x: 12, y: (box.t + box.b) / 2, class: "g-sub t12", "text-anchor": "middle", transform: `rotate(-90 12 ${(box.t + box.b) / 2})` }, al.y || ""));
    const items = pointLegend(fig.legend || {}, marks);
    items.push({ cls: "g-target", shape: "rect", label: "狙いの幅（Good）" });
    if (ell.length) items.push({ cls: "g-ellipse", shape: "line", label: (fig.ellipse || {}).legend });
    const notes = [];
    if ((fig.missing_seqs || []).length) notes.push({ text: `着弾が無い球: ${fig.missing_seqs.map((q) => "#" + q).join(" ")}` });
    return wrap(fig, s, notes, legend(items));
  }

  // ---- F2 打ち出し×曲がりの9分類 ----
  const START_L = { R: { push: "右に出る", straight: "まっすぐ", pull: "左に出る" }, L: { push: "左に出る", straight: "まっすぐ", pull: "右に出る" } };
  const CURVE_L = { R: { left: "左へ曲がる", none: "曲がらない", right: "右へ曲がる" }, L: { left: "右へ曲がる", none: "曲がらない", right: "左へ曲がる" } };
  const CAUSE_L = { extreme_strike: "ネック・先端寄り", face_to_path: "フェース・トゥ・パス", strike: "打点", mixed: "原因が1つに決まらない", none: "曲がらない", unknown: "原因が分からない", thin: "薄い当たり" };
  const CAUSE_C = { extreme_strike: "c-extreme", extreme_only: "c-extreme", extreme_and_face: "c-extreme", strike: "c-strike", face_to_path: "c-ftp", mixed: "c-mixed", thin: "c-thin", none: "c-none", unknown: "c-other", start: "c-other" };
  function F2(fig, ctx) {
    const hand = ctx.hand === "L" ? "L" : "R";
    const cols = hand === "L" ? (fig.cols || []).slice().reverse() : fig.cols || [];
    const rows = fig.rows || [];
    const lw = 66, cw = (W - lw - 8) / 3, top = 30, rh = 64, H = top + rows.length * rh + 8;
    const s = svgRoot(fig, H);
    const cell = (r, c) => (fig.cells || []).find((q) => q.start === r && q.curve === c);
    cols.forEach((c, j) => s.appendChild(el("text", { x: lw + cw * j + cw / 2, y: 18, class: "g-sub t12", "text-anchor": "middle" }, CURVE_L[hand][c] || c)));
    const used = new Set();
    rows.forEach((r, i) => {
      const y0 = top + i * rh;
      s.appendChild(el("text", { x: lw - 6, y: y0 + rh / 2 + 4, class: "g-sub t12", "text-anchor": "end" }, START_L[hand][r] || r));
      cols.forEach((c, j) => {
        const q = cell(r, c) || { n: 0, breakdown: [] };
        const x0 = lw + cw * j;
        s.appendChild(el("rect", { x: x0 + 2, y: y0 + 2, width: cw - 4, height: rh - 4, rx: 6, class: q.top ? "g-cell g-top" : "g-cell" }));
        s.appendChild(el("text", { x: x0 + cw / 2, y: y0 + 30, class: q.n ? "g-text t16" : "g-sub t13", "text-anchor": "middle" }, q.n ? `${q.n}球` : "0"));
        if (q.n) {
          let bx = x0 + 10;
          const bw = cw - 20;
          for (const b of q.breakdown || []) {
            const w = (bw * b.n) / q.n;
            used.add(b.cause);
            s.appendChild(el("rect", { x: bx, y: y0 + 40, width: Math.max(0, w - 1), height: 10, class: CAUSE_C[b.cause] || "c-other" }, null))
              .appendChild(el("title", {}, `${CAUSE_L[b.cause] || b.cause} ${b.n}`));
            bx += w;
          }
        }
      });
    });
    const items = [...used].map((c) => ({ cls: CAUSE_C[c] || "c-other", shape: "rect", label: CAUSE_L[c] || c }));
    const notes = [];
    if (fig.unknown) notes.push(`曲がりが測れなかった球: ${fig.unknown}`);
    if (fig.mishit_included) notes.push(`ミスヒットの候補 ${fig.mishit_included}球も数えています（起きた球なので）`);
    return wrap(fig, s, notes, legend(items));
  }

  // ---- F3 フェース×パス ＋ 帯 ----
  function F3(fig, ctx) {
    const sx = ctx.hand === "L" ? -1 : 1;
    const H = 300, box = { l: 42, r: W - 12, t: 12, b: H - 40 };
    const pts = fig.points || [];
    const bs = fig.band_used ? ctx.bandShape : null;
    const ranges = bs && bs.ok ? (bs.ranges || []).filter((q) => q.ok).sort((a, b) => a.path - b.path) : [];
    const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
    const inRange = (p) => xs.length === 0 || (p >= Math.min(...xs) - 1.5 && p <= Math.max(...xs) + 1.5);
    const shown = ranges.filter((q) => inRange(q.path));
    for (const q of shown) { xs.push(q.path); ys.push(q.face_min, q.face_max); }
    if (bs && bs.window && bs.window.ok) { xs.push(bs.window.path); ys.push(bs.window.face_min, bs.window.face_max); }
    let x0 = Math.min(-3, ...xs), x1 = Math.max(3, ...xs), y0 = Math.min(-3, ...ys), y1 = Math.max(3, ...ys);
    const px = (x1 - x0) * 0.05, py = (y1 - y0) * 0.05;
    x0 -= px; x1 += px; y0 -= py; y1 += py;
    // 左打ちは左右を反転（表示の値は実際の向き）
    const X = lin(sx > 0 ? x0 : -x1, sx > 0 ? x1 : -x0, box.l, box.r);
    const Y = lin(sx > 0 ? y0 : -y1, sx > 0 ? y1 : -y0, box.b, box.t);
    const x = (v) => X(sx * v), y = (v) => Y(sx * v);
    const s = svgRoot(fig, H);
    if (shown.length > 1) {
      const up = shown.map((q) => `${x(q.path)},${y(q.face_max)}`), lo = shown.map((q) => `${x(q.path)},${y(q.face_min)}`).reverse();
      s.appendChild(el("polygon", { points: up.concat(lo).join(" "), class: "g-band" }));
    }
    const dx = sx > 0 ? [x0, x1] : [-x1, -x0], dy = sx > 0 ? [y0, y1] : [-y1, -y0];
    axes(s, X, Y, dx, dy, box);
    // 対角線（フェース＝パス）は曲がらない球。表示の座標でも y=x のまま
    const a = Math.max(dx[0], dy[0]), b = Math.min(dx[1], dy[1]);
    s.appendChild(el("line", { x1: X(a), y1: Y(a), x2: X(b), y2: Y(b), class: "g-diag" }));
    const dg = fig.diagonal || {};
    // 右打ちの座標で「上」の側は、左打ちで反転すると表示では下になる
    const tl = sx > 0 ? dg.label_above : dg.label_below, br = sx > 0 ? dg.label_below : dg.label_above;
    s.appendChild(el("text", { x: box.l + 6, y: box.t + 16, class: "g-sub t12" }, tl || ""));
    s.appendChild(el("text", { x: box.r - 6, y: box.b - 8, class: "g-sub t12", "text-anchor": "end" }, br || ""));
    if (bs && bs.window && bs.window.ok) {
      const w = bs.window;
      s.appendChild(el("line", { x1: x(w.path), x2: x(w.path), y1: y(w.face_min), y2: y(w.face_max), class: "g-window" }))
        .appendChild(el("title", {}, "まっすぐ落ちるフェースの範囲（今のパス）"));
    }
    const g = el("g");
    const marks = new Set();
    for (const p of pts) { marks.add(p.warn && p.mark !== "mishit" ? "ext" : p.mark); point(g, x(p.x), y(p.y), p, { warn: p.warn }); }
    s.appendChild(g);
    const al = fig.axis_labels || {};
    s.appendChild(el("text", { x: (box.l + box.r) / 2, y: H - 12, class: "g-sub t12", "text-anchor": "middle" }, al.x || ""));
    s.appendChild(el("text", { x: box.l + 2, y: H - 12, class: "g-sub t11" }, "← 左"));
    s.appendChild(el("text", { x: box.r - 2, y: H - 12, class: "g-sub t11", "text-anchor": "end" }, "右 →"));
    s.appendChild(el("text", { x: 13, y: (box.t + box.b) / 2, class: "g-sub t12", "text-anchor": "middle", transform: `rotate(-90 13 ${(box.t + box.b) / 2})` }, al.y || ""));
    const items = pointLegend(fig.legend || { in: "インパクトが帯に入った球", out: "入らなかった球", ext: "ネック・先端寄り", mishit: "ミスヒットの候補" }, marks);
    if (shown.length > 1) items.push({ cls: "g-band", shape: "rect", label: "帯（まっすぐ落ちる組み合わせ）" });
    if (bs && bs.window && bs.window.ok) items.push({ cls: "g-window", shape: "line", label: "今のパスでの範囲" });
    items.push({ cls: "g-diag", shape: "line", label: dg.note });
    const notes = (fig.notes || []).map((t, i) => (i === 0 ? { text: t, data: { notShown: (fig.not_shown || {}).n } } : t));
    if (!fig.band_used && fig.band_reason) notes.push({ text: fig.band_reason, cls: "fig-warn" });
    if (fig.band_used) for (const t of fig.notes_band || []) notes.push(t);
    return wrap(fig, s, notes, legend(items));
  }

  // ---- F4 上から見た1球（模式図・角度は誇張） ----
  function F4(fig, ctx) {
    const rep = fig.representative;
    const sx = ctx.hand === "L" ? -1 : 1, k = fig.exaggerate || 3;
    const H = 250, bx = W / 2, by = H - 40;
    const s = svgRoot(fig, H);
    const dir = (deg) => { const a = (sx * deg * k * Math.PI) / 180; return [Math.sin(a), -Math.cos(a)]; };
    s.appendChild(el("line", { x1: bx, y1: by + 26, x2: bx, y2: 14, class: "g-axis g-target-line" }));
    s.appendChild(el("text", { x: bx, y: 11, class: "g-sub t11", "text-anchor": "middle" }, "目標"));
    s.appendChild(el("text", { x: 14, y: 26, class: "g-sub t11" }, "← 左"));
    s.appendChild(el("text", { x: W - 14, y: 26, class: "g-sub t11", "text-anchor": "end" }, "右 →"));
    const flight = (launch, axis, cls) => {
      const d = dir(launch), e = dir(launch + axis * 0.5), L = 150;
      const c = [bx + d[0] * L * 0.6, by + d[1] * L * 0.6], end = [bx + e[0] * L, by + e[1] * L];
      s.appendChild(el("line", { x1: bx, y1: by, x2: bx + d[0] * L, y2: by + d[1] * L, class: `${cls} g-launch` }));
      s.appendChild(el("path", { d: `M${bx},${by}Q${c[0]},${c[1]} ${end[0]},${end[1]}`, class: `${cls} g-flight` }));
      return end;
    };
    const club = (path, face, cls) => {
      const p = dir(path);
      s.appendChild(el("line", { x1: bx - p[0] * 60, y1: by - p[1] * 60, x2: bx - p[0] * 10, y2: by - p[1] * 10, class: `${cls} g-path` }));
      const ah = [bx - p[0] * 10, by - p[1] * 10], n = [-p[1], p[0]];
      s.appendChild(el("path", { d: `M${ah[0]},${ah[1]}L${ah[0] - p[0] * 8 + n[0] * 4},${ah[1] - p[1] * 8 + n[1] * 4}L${ah[0] - p[0] * 8 - n[0] * 4},${ah[1] - p[1] * 8 - n[1] * 4}Z`, class: `${cls} g-arrow` }));
      const f = dir(face), t = [-f[1], f[0]], fx = bx - f[0] * 8, fy = by - f[1] * 8;
      s.appendChild(el("line", { x1: fx - t[0] * 22, y1: fy - t[1] * 22, x2: fx + t[0] * 22, y2: fy + t[1] * 22, class: `${cls} g-face` }));
    };
    const w = ctx.bandShape && ctx.bandShape.ok && ctx.bandShape.window && ctx.bandShape.window.ok ? ctx.bandShape.window : null;
    if (w && fig.window_center) {
      club(w.path, w.face_center, "g-wc");
      flight(w.predicted_launch_direction, w.predicted_spin_axis, "g-wc");
    }
    if (rep) {
      club(rep.club_path, rep.face_angle, "g-rep");
      const end = flight(rep.launch_direction, rep.spin_axis, "g-rep");
      const grp = point(el("g"), end[0], end[1], { shot_id: rep.shot_id, seq: rep.seq, club: rep.club, mark: "none", class: "g-ext" });
      s.appendChild(grp.parentNode);
      s.appendChild(el("text", { x: end[0] + (end[0] > bx ? -8 : 8), y: end[1] - 10, class: "g-text t12", "text-anchor": end[0] > bx ? "end" : "start" }, `#${rep.seq}`));
    }
    s.appendChild(el("circle", { cx: bx, cy: by, r: 4, class: "g-ball" }));
    const items = [{ cls: "g-rep g-face", shape: "line", label: "短い太線: フェースの向き" }, { cls: "g-rep g-path", shape: "line", label: "矢印: クラブパス" }];
    if (rep) items.push({ cls: "g-rep g-flight", shape: "line", label: `典型の1球 #${rep.seq}${rep.club ? "（" + rep.club + "）" : ""}` });
    if (w && fig.window_center) items.push({ cls: "g-wc g-flight", shape: "line", label: "まっすぐ落ちる範囲の中央（パスが今のまま）" });
    return wrap(fig, s, [fig.note], legend(items));
  }

  // ---- F5 打点（トゥ⇔ヒールの1次元）。目標側からフェースを見た形 ----
  function F5(fig, ctx) {
    const heelRight = fig.heel_on ? fig.heel_on === "right" : ctx.hand !== "L";
    const pts = fig.points || [];
    const c = fig.center_mm || 10, ex = fig.extreme_mm || 30;
    const m = Math.max(ex + 12, ...pts.map((p) => Math.abs(p.offset_mm) + 4));
    // 余白を左右対称にし、0 がちょうど図の真ん中に来るようにする（ヒールが右半分、を読めるように）
    const box = { l: 20, r: W - 20 };
    const dx = (mm) => (heelRight ? -mm : mm);
    const x = lin(-m, m, box.l, box.r);
    // 同じ場所の点は上に積む（縦の位置に意味はない）
    const bin = 3, stack = {};
    const placed = pts.map((p) => { const kk = Math.round(dx(p.offset_mm) / bin); stack[kk] = (stack[kk] || 0) + 1; return [p, stack[kk] - 1]; });
    const maxStack = Math.max(1, ...Object.values(stack));
    const base = 100, step = 11, H = base + maxStack * step + 44;
    const s = svgRoot(fig, H);
    const stripT = base - 14, stripB = base + maxStack * step + 6;
    // 芯（±中央）と極端（外側）の帯
    s.appendChild(el("rect", { x: x(-c), y: stripT, width: x(c) - x(-c), height: stripB - stripT, class: "g-core" }));
    s.appendChild(el("rect", { x: box.l, y: stripT, width: x(-ex) - box.l, height: stripB - stripT, class: "g-extband" }));
    s.appendChild(el("rect", { x: x(ex), y: stripT, width: box.r - x(ex), height: stripB - stripT, class: "g-extband" }));
    // フェースのシルエット（アイアンは刃、ウッドは丸い頭）。ヒール側にネック
    const hx = heelRight ? x(m * 0.72) : x(-m * 0.72), tx = heelRight ? x(-m * 0.72) : x(m * 0.72);
    const lft = Math.min(hx, tx), rgt = Math.max(hx, tx);
    if (fig.club_kind === "wood") {
      s.appendChild(el("path", { d: `M${lft + 10},62 Q${lft - 6},40 ${lft + 18},26 L${rgt - 18},24 Q${rgt + 2},30 ${rgt - 4},62 Z`, class: "g-club" }));
    } else {
      const toeTop = heelRight ? `M${lft},58 Q${lft - 4},28 ${lft + 22},24 L${rgt - 8},38 L${rgt},58 Z` : `M${lft},58 L${lft + 8},38 L${rgt - 22},24 Q${rgt + 4},28 ${rgt},58 Z`;
      s.appendChild(el("path", { d: toeTop, class: "g-club" }));
    }
    const neckX = heelRight ? rgt : lft;
    s.appendChild(el("path", { d: `M${neckX + (heelRight ? -6 : 6)},40 L${neckX + (heelRight ? 16 : -16)},6`, class: "g-neck" }));
    s.appendChild(el("line", { x1: x(0), x2: x(0), y1: 20, y2: stripB, class: "g-diag" }));
    const lb = fig.labels || {};
    s.appendChild(el("text", { x: heelRight ? box.r : box.l, y: 76, class: "g-text t12", "text-anchor": heelRight ? "end" : "start" }, lb.heel || "ネック"));
    s.appendChild(el("text", { x: heelRight ? box.l : box.r, y: 76, class: "g-text t12", "text-anchor": heelRight ? "start" : "end" }, lb.toe || "先端"));
    s.appendChild(el("text", { x: x(0), y: 76, class: "g-sub t11", "text-anchor": "middle" }, "芯"));
    const g = el("g");
    for (const [p, i] of placed) {
      // 横の位置は丸めない（−1mm を 0 に寄せるとヒールかトゥか分からなくなる）。積むのだけ3mm ごと
      point(g, x(dx(p.offset_mm)), base + (maxStack - 1 - i) * step, p).dataset.offset = p.offset_mm;
    }
    s.appendChild(g);
    const now = (fig.layers || {}).now;
    if (now && now.median_mm !== null && now.median_mm !== undefined) {
      s.appendChild(el("line", { x1: x(dx(now.median_mm)), x2: x(dx(now.median_mm)), y1: stripT - 4, y2: stripB + 4, class: "g-now" }));
      s.appendChild(el("text", { x: x(dx(now.median_mm)), y: stripB + 17, class: "g-text t11", "text-anchor": "middle" }, "今日の中央値"));
    }
    // 目盛り（mm）。ヒールの向きの数字にする
    for (const v of [-ex, -c, c, ex]) {
      s.appendChild(el("text", { x: x(v), y: H - 8, class: "g-sub t11", "text-anchor": "middle" }, `${Math.abs(v)}`));
    }
    s.appendChild(el("text", { x: x(0), y: H - 8, class: "g-sub t11", "text-anchor": "middle" }, "mm"));
    const marks = new Set(pts.map((p) => p.mark));
    const items = pointLegend(fig.legend || { in: "インパクトが帯に入った球", out: "入らなかった球", ext: "ネック・先端寄り", mishit: "ミスヒットの候補" }, marks).concat([
      { cls: "g-core", shape: "rect", label: `芯の範囲（±${c}mm）` },
      { cls: "g-extband", shape: "rect", label: `ネック・先端寄り（${ex}mm 超）` },
    ]);
    const notes = [fig.vertical_note];
    if (fig.missing) notes.push(`打点が「-」の球: ${fig.missing}（${(fig.missing_seqs || []).map((q) => "#" + q).join(" ")}）`);
    const goal = (fig.layers || {}).goal;
    if (goal) notes.push(`目標: 芯から ${goal.within_mm}mm 以内・ネック寄りは ${goal.heel_extreme}球`);
    const im = (fig.layers || {}).ideal_missing;
    if (!(fig.layers || {}).ideal && im) notes.push(`本人の良い球の分布はまだ出せません（該当の球 ${im.have} / ${im.needed}）`);
    return wrap(fig, s, notes, legend(items));
  }

  // ---- F6 はみ出した距離の内訳（横棒） ----
  function F6(fig) {
    const bars = fig.bars || [];
    const top = 26, bh = 46, H = top + bars.length * bh + 6, lx = 8, rx = W - 58;
    const s = svgRoot(fig, H);
    const id = `hatch${++uid}`;
    const defs = el("defs");
    const pat = el("pattern", { id, width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" });
    pat.appendChild(el("rect", { width: 6, height: 6, class: "c-ftp" }));
    pat.appendChild(el("line", { x1: 0, y1: 0, x2: 0, y2: 6, class: "g-hatch" }));
    defs.appendChild(pat);
    s.appendChild(defs);
    const max = Math.max(1, ...bars.map((b) => b.excess_m));
    const x = lin(0, max, lx, rx);
    s.appendChild(el("text", { x: lx, y: 16, class: "g-sub t12" }, `合計 ${r1(fig.total_m).toFixed(1)}m（はみ出した ${fig.n_over}球 / ${fig.n_side}球）`));
    bars.forEach((b, i) => {
      const y0 = top + i * bh;
      s.appendChild(el("text", { x: lx, y: y0 + 13, class: "g-text t12" }, `${b.label}  ${b.n}球`));
      const r = el("rect", { x: lx, y: y0 + 19, width: Math.max(1, x(b.excess_m) - lx), height: 14, rx: 2, class: b.hatched ? "g-hatched" : CAUSE_C[b.cause] || "c-other" });
      if (b.hatched) r.setAttribute("style", `fill:url(#${id})`);
      s.appendChild(r);
      if (b.light && b.light.excess_m > 0) {
        s.appendChild(el("rect", { x: x(b.excess_m - b.light.excess_m), y: y0 + 19, width: Math.max(1, x(b.light.excess_m) - lx), height: 14, class: "g-light" }))
          .appendChild(el("title", {}, `${b.light.label} ${b.light.n}球 ${r1(b.light.excess_m).toFixed(1)}m`));
      }
      s.appendChild(el("text", { x: x(b.excess_m) + 4, y: y0 + 31, class: "g-text t12" }, `${r1(b.excess_m).toFixed(1)}m`));
    });
    const notes = bars.filter((b) => b.light && b.light.n).map((b) => `${b.label}のうち薄い色: ${b.light.label} ${b.light.n}球 ${r1(b.light.excess_m).toFixed(1)}m（${b.light.seqs.map((q) => "#" + q).join(" ")}）`);
    if (bars.some((b) => b.hatched)) notes.push("斜線: 打点とフェースの両方が当てはまる球");
    if (fig.mishit_included) notes.push(`ミスヒットの候補 ${fig.mishit_included}球も数えています（起きた球なので）`);
    return wrap(fig, s, notes);
  }

  // ---- F7 クラブ別の比較 ----
  const COL_L = { face_angle: "フェースの向き", club_path: "クラブパス", impact_offset: "打点のずれ" };
  function F7(fig, ctx) {
    const rows = fig.rows || [], cols = fig.cols || [];
    const lx = 6, pw = (W - lx * 2) / Math.max(1, cols.length), top = 40, rh = 50, H = top + rows.length * rh + 22;
    const s = svgRoot(fig, H);
    const L = ctx.hand === "L";
    cols.forEach((c, j) => {
      const unit = c.unit === "deg" ? "°" : c.unit;
      const cx = lx + pw * j + pw / 2;
      s.appendChild(el("text", { x: cx, y: 13, class: "g-text t11", "text-anchor": "middle" }, `${COL_L[c.metric] || c.metric}（${unit}）`));
      const lr = c.metric === "impact_offset" ? (L ? ["ネック", "先端"] : ["先端", "ネック"]) : ["左", "右"];
      s.appendChild(el("text", { x: lx + pw * j + 6, y: 28, class: "g-sub t11" }, "← " + lr[0]));
      s.appendChild(el("text", { x: lx + pw * (j + 1) - 6, y: 28, class: "g-sub t11", "text-anchor": "end" }, lr[1] + " →"));
      // 表示の向き: 角度は左打ちで反転、打点はヒール（負）を heel の側へ
      const flip = c.metric === "impact_offset" ? !L : L;
      const tv = (v) => (flip ? -v : v);
      const vals = [c.mmd, -c.mmd];
      for (const r of rows) {
        const q = (r.cells || {})[c.metric];
        if (!q) continue;
        vals.push(q.q1, q.q3, q.median, ...(q.ci95 || []));
      }
      const fin = vals.filter((v) => v !== null && v !== undefined && isFinite(v)).map(tv);
      const ext = Math.max(...fin.map(Math.abs)) * 1.1;
      const x = lin(-ext, ext, lx + pw * j + 6, lx + pw * (j + 1) - 6);
      s.appendChild(el("rect", { x: x(-c.mmd), y: top - 4, width: x(c.mmd) - x(-c.mmd), height: rows.length * rh, class: "g-mmd" }));
      s.appendChild(el("line", { x1: x(0), x2: x(0), y1: top - 4, y2: top + rows.length * rh - 4, class: "g-axis g-zero" }));
      // 目盛り（大きさを読めるように、0 と左右の1つずつ）。表示の向きの値で書く
      const tk = niceTicks(-ext, ext, 3);
      const tstep = tk.length > 1 ? tk[1] - tk[0] : 1;
      for (const v of tk) s.appendChild(el("text", { x: x(v), y: top + rows.length * rh + 10, class: "g-sub t11", "text-anchor": "middle" }, fmtTick(Math.abs(v), tstep)));
      rows.forEach((r, i) => {
        const q = (r.cells || {})[c.metric];
        const y0 = top + i * rh + 30;
        if (!q || q.median === null || q.median === undefined) {
          s.appendChild(el("text", { x: cx, y: y0 + 4, class: "g-sub t11", "text-anchor": "middle" }, "—"));
          return;
        }
        if (q.ci95) s.appendChild(el("line", { x1: x(tv(q.ci95[0])), x2: x(tv(q.ci95[1])), y1: y0, y2: y0, class: "g-ci" }));
        s.appendChild(el("line", { x1: x(tv(q.q1)), x2: x(tv(q.q3)), y1: y0, y2: y0, class: "g-iqr" }));
        s.appendChild(el("circle", { cx: x(tv(q.median)), cy: y0, r: 4.5, class: q.bias ? "g-bias" : "g-nobias" }))
          .appendChild(el("title", {}, `${r.label}（${q.n}球）`));
      });
    });
    rows.forEach((r, i) => {
      const y0 = top + i * rh + 12;
      s.appendChild(el("text", { x: lx, y: y0, class: "g-text t12" }, `${r.label}  ${r.n}球${r.mishit_excluded ? `（候補 ${r.mishit_excluded}球を除く）` : ""}`));
    });
    const items = [{ cls: "g-bias", label: "偏りあり（区間が0をまたがない）" }, { cls: "g-nobias", label: "偏りは言えない" }, { cls: "g-mmd", shape: "rect", label: "意味のある最小の差（±）" }];
    return wrap(fig, s, [fig.legend], legend(items));
  }


  // ---- C1 いま と 理想（要点の「理想との差」。最初に見える図） ----
  // 利用者の方針（2026-09-29）: 細かい軸や目盛りは出さない。数字は件数（「26球中9球」）だけ。
  // 左に「いま」、右に「理想」（今日すでに打てている球）を同じ縮尺で並べる。3つの段:
  //   球の散らばり（上から見た着弾。縦は飛んだ距離・横は左右。緑の帯が狙いの幅）
  //   クラブの面の向き（扇。1本が1球。角度は見やすいよう大きく描いた模式）
  //   当たる場所（クラブの面の形の上の点。目標側から見た形で、右打ちはネックが右）
  function C1(fig, ctx) {
    const L = ctx.hand === "L";
    const gap = 12, pw = (W - gap) / 2, px = [0, pw + gap];
    const rows = [{ id: "landing", h: 176 }];
    if ((fig.face || {}).show) rows.push({ id: "face", h: 96 });
    if ((fig.strike || {}).show) rows.push({ id: "strike", h: 78 });
    const head = 24, rowGap = 22;
    const H = head + rows.reduce((a, r) => a + r.h + rowGap, 0);
    const s = svgRoot(fig, H);
    const lb = fig.labels || {};
    px.forEach((x0, i) => s.appendChild(el("text", { x: x0 + pw / 2, y: 15, class: `t13 ${i ? "g-good g-strong" : "g-text"}`, "text-anchor": "middle" }, i ? lb.ideal : lb.now)));
    s.appendChild(el("line", { x1: pw + gap / 2, x2: pw + gap / 2, y1: 4, y2: H - 4, class: "g-grid" }));
    let y0 = head;
    const mir = (v) => (L ? -v : v);
    for (const r of rows) {
      s.appendChild(el("text", { x: W / 2, y: y0 + 12, class: "g-sub t12", "text-anchor": "middle" }, lb[r.id] || ""));
      const top = y0 + 18, bot = y0 + r.h;
      if (r.id === "landing") {
        const now = (fig.now || {}).points || [], idl = (fig.ideal || {}).points || [];
        const half = fig.target_half || 0.05;
        // 横の縮尺: 外れ値1球で全体が潰れないよう、9割の球が入る幅で決め、はみ出す球は端に置く
        const ax = now.map((p) => Math.abs(p.x)).sort((a, b) => a - b);
        const q90 = ax.length ? ax[Math.min(ax.length - 1, Math.floor(ax.length * 0.9))] : half;
        const xmax = Math.max(half * 2.5, q90) * 1.1;
        const clampX = (v) => Math.max(-xmax * 0.97, Math.min(xmax * 0.97, v));
        const ys = now.map((p) => p.y).filter((v) => isFinite(v));
        const ylo = Math.min(...ys, 0.8), yhi = Math.max(...ys, 1.1);
        px.forEach((x0, i) => {
          const x = lin(-xmax, xmax, x0 + 8, x0 + pw - 8), y = lin(ylo - 0.05, yhi + 0.05, bot - 4, top + 4);
          s.appendChild(el("rect", { x: x(-half), y: top, width: x(half) - x(-half), height: bot - top, class: "g-band" }));
          s.appendChild(el("line", { x1: x(0), x2: x(0), y1: top, y2: bot, class: "g-axis g-zero" }));
          s.appendChild(el("text", { x: x0 + 6, y: top + 10, class: "g-sub t11" }, "左"));
          s.appendChild(el("text", { x: x0 + pw - 6, y: top + 10, class: "g-sub t11", "text-anchor": "end" }, "右"));
          for (const p of i ? idl : now) {
            const cx = x(clampX(mir(p.x))), cy = y(p.y);
            if (i || p.in) s.appendChild(el("circle", { cx, cy, r: 4.5, class: "g-good" }));
            else s.appendChild(el("circle", { cx, cy, r: 4, class: "g-miss hollow" }));
          }
        });
      } else if (r.id === "face") {
        const f = fig.face || {};
        px.forEach((x0, i) => {
          const ox = x0 + pw / 2, oy = bot - 2, len = bot - top - 6;
          s.appendChild(el("line", { x1: ox, x2: ox, y1: oy, y2: top, class: "g-axis g-zero" }));
          const vals = i ? f.ideal || [] : f.now || [];
          for (const v of vals) {
            const a = (Math.max(-20, Math.min(20, mir(v))) * 3 * Math.PI) / 180;
            s.appendChild(el("line", { x1: ox, y1: oy, x2: ox + Math.sin(a) * len, y2: oy - Math.cos(a) * len, class: i ? "g-fan g-fan-good" : "g-fan" }));
          }
          if (i && !vals.length) s.appendChild(el("text", { x: ox, y: top + 20, class: "g-sub t11", "text-anchor": "middle" }, "狙いの方向にそろう"));
          s.appendChild(el("circle", { cx: ox, cy: oy, r: 3, class: "g-text-fill" }));
        });
      } else {
        const st = fig.strike || {};
        const heelRight = st.heel_on !== "left";
        const lim = Math.max(35, st.extreme_mm + 8);
        px.forEach((x0, i) => {
          const x = lin(-lim, lim, x0 + 12, x0 + pw - 12);
          const toScreen = (mm) => x(heelRight ? -mm : mm);  // ヒール（負）をネックの側へ
          const fy = top + 8, fh = bot - top - 22;
          const rr = st.club_kind === "wood" ? fh / 2 : 6;
          s.appendChild(el("rect", { x: x0 + 12, y: fy, width: pw - 24, height: fh, rx: rr, class: "g-clubface" }));
          s.appendChild(el("rect", { x: x(-st.center_mm), y: fy, width: x(st.center_mm) - x(-st.center_mm), height: fh, class: i ? "g-core g-core-strong" : "g-core" }));
          const hx = heelRight ? x0 + pw - 12 : x0 + 12, tx = heelRight ? x0 + 12 : x0 + pw - 12;
          s.appendChild(el("text", { x: hx, y: bot + 2, class: "g-sub t11", "text-anchor": heelRight ? "end" : "start" }, lb.heel || "ネック"));
          s.appendChild(el("text", { x: tx, y: bot + 2, class: "g-sub t11", "text-anchor": heelRight ? "start" : "end" }, lb.toe || "先"));
          const vals = i ? st.ideal || [] : st.now || [];
          const used = {};
          for (const mm of vals) {
            const cx = toScreen(Math.max(-lim, Math.min(lim, mm)));
            const k = Math.round(cx / 7);
            used[k] = (used[k] || 0) + 1;
            const cy = fy + fh / 2 + ((used[k] % 2 ? 1 : -1) * Math.floor(used[k] / 2) * 5);
            s.appendChild(el("circle", { cx, cy: Math.max(fy + 3, Math.min(fy + fh - 3, cy)), r: 3.5, class: i || Math.abs(mm) <= st.center_mm ? "g-good" : "g-miss" }));
          }
          if (i && !vals.length) s.appendChild(el("text", { x: x0 + pw / 2, y: fy - 3, class: "g-sub t11", "text-anchor": "middle" }, "芯の近くに当たる"));
        });
      }
      if (r.id === "landing") {
        s.appendChild(el("text", { x: pw / 2, y: bot + 16, class: "g-text t12", "text-anchor": "middle" }, (fig.now || {}).caption || ""));
        s.appendChild(el("text", { x: pw + gap + pw / 2, y: bot + 16, class: "g-good g-strong t12", "text-anchor": "middle" }, (fig.ideal || {}).caption || ""));
        y0 += 12;
      }
      y0 += r.h + rowGap;
    }
    s.setAttribute("viewBox", `0 0 ${W} ${y0}`);
    const f = h("figure", "fig fig-cmp");
    f.dataset.fig = fig.id;
    f.appendChild(s);
    if (fig.note) f.appendChild(h("p", "fig-note", fig.note));
    return f;
  }

  const RENDER = { F1, F2, F3, F4, F5, F6, F7, C1 };
  window.Figures = {
    /** 図1つを <figure> にして返す。fig はサーバーの図の中身、ctx は {hand, bandShape}。 */
    render(fig, ctx) {
      const f = RENDER[fig && fig.id];
      if (!f) return null;
      try { return f(fig, ctx || {}); } catch (e) {
        const p = h("p", "err", `${fig.id} を描けませんでした: ${e.message}`);
        return p;
      }
    },
  };
})();
