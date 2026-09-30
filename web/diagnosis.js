"use strict";
/*
  診断レポート（docs/DESIGN_diagnosis.md の diagnosis を読む画面）。本人の声:
  「現在はこんな課題があって、それはこの動きが原因で発生してて、理想の動きはこうだから、こうゆうふうになおしましょうね。
   を構造化してわかりやすく」「見るだけでは結果は変わらない。アクションプランが要る」。
  - 課題ごとに ①いま起きていること → ②原因の動き → ③理想の動き → ④直し方（アクションプラン）→ ⑤直ったかの確かめ方 を、
    開かなくても全部読める形で並べる（1つ目の課題）。2つ目からは見出しと①だけを見せ、残りは開いて読む。
  - 最初の面に数字・角度・専門用語を出さない（文はサーバーの定型文で、検査済み）。数字は各段の「なぜそう言える？」の中だけ。
    構造のラベル（球数・P の番号・クラブ・練習法の出どころ）は data-label の決まった形だけ（config.PLAIN_LABEL_PATTERNS）。
  - TrackMan が言えるのは当たる瞬間のクラブの様子まで。体の動きは根拠の札（動画で確かめた／動画の見た目／球のデータから見た可能性）
    で分け、動画が無いときは［動画を撮って原因を確かめる］を原因の段に大きく置く。
  - ドリルは隠さない。出どころの札（「PGAガイド p.X」／「一般的な練習法」）と安全の注意を必ず添える。
  - 文を作らない。ここで足すのは見出しと、段のつなぎの短い言葉だけ。
*/
const Diagnosis = (() => {
  const { $, esc, lab, icon, S } = App;

  // 根拠の札（色だけに頼らず、形の印と字で分ける）
  const BASIS = {
    measured: { word: "動画で確かめた", cls: "good", mark: "✓" },
    seen: { word: "動画の見た目", cls: "brand", mark: "◎" },
    likely: { word: "球のデータから見た可能性", cls: "warn", mark: "△" },
  };
  const VIEW = { dtl: "後ろから", fo: "正面から", both: "後ろから（できれば正面からも）" };
  const STEP = [
    ["now", "①", "いま起きていること"],
    ["cause", "②", "原因の動き"],
    ["ideal", "③", "理想の動き"],
    ["fix", "④", "直し方（アクションプラン）"],
    ["check", "⑤", "直ったかの確かめ方"],
  ];
  const stepHead = (key, tag = "h3") => {
    const s = STEP.find((x) => x[0] === key);
    return `<${tag} class="dgstep" data-step="${key}"><span class="dgno" aria-hidden="true">${s[1]}</span> <span>${s[2]}</span></${tag}>`;
  };
  const whyBtn = (key, i) => `<button type="button" class="textbtn" data-dgwhy="${key}" data-issue="${i}">${icon("info")}なぜそう言える？<span class="visually-hidden">（${esc(STEP.find((x) => x[0] === key)[2])}）</span></button>`;

  // 範囲の名前（「アイアン（まとめ）・4番ユーティリティ」）。クラブ1本の名前だけはラベルとして数字を出す
  const CLUB_RE = /^\d{1,2}番(?:アイアン|ウッド|ユーティリティ)$/;
  const scopeHtml = (text) => String(text || "").split("・").filter(Boolean)
    .map((x) => (CLUB_RE.test(x) ? lab("club", x) : esc(x))).join("・");
  const pChip = (p, name) => (p && /^P\d/.test(p) ? `<span class="chip none dgp">${lab("p", p.replace("_5", ".5"))}${name ? ` ${esc(name)}` : ""}</span>` : "");
  const srcChip = (label, guide) => (label ? `<span class="chip ${guide ? "brand" : "none"}" data-dgsrc="${guide ? "guide" : "general"}">${guide ? icon("check") : ""}${lab("source", label)}</span>` : "");
  const basisChip = (b) => { const x = BASIS[b] || BASIS.likely; return `<span class="chip ${x.cls}" data-basis="${esc(b)}">${x.mark} ${x.word}</span>`; };
  const KANJI = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"];
  const sentences = (t) => String(t || "").split(/(?<=。)/).map((x) => x.trim()).filter(Boolean);

  // ---- 絵（自前の線画。色は CSS の変数から。数字は描かない） ----
  // 値は右打ちの向きで持つ（「右」＝面が開く向き）。左打ちは描くときに左右を入れ替える
  function figSvg(fig, mode, hand) {
    if (!fig || !fig.kind) return "";
    const L = hand === "L";
    const cmp = mode === "compare";
    const W = 320, H = 170;
    const mx = (x) => (L ? W - x : x);
    let body = "", title = "", legend = "";
    if (fig.kind === "strike") {
      // クラブの面を正面から見た絵: 片側がネック、反対側が先。いまの当たる場所の点と、理想（芯）の点線の丸
      const hx = mx(58), tx = mx(262);
      const face = L ? `M${W - 40} 52 L${W - 250} 40 Q${W - 290} 40 ${W - 292} 80 L${W - 290} 120 Q${W - 286} 138 ${W - 250} 138 L${W - 40} 132 Z` : "M40 52 L250 40 Q290 40 292 80 L290 120 Q286 138 250 138 L40 132 Z";
      const hosel = L ? `M${W - 40} 60 L${W - 14} 18` : "M40 60 L14 18";
      const at = { heel: [[92, 92], [104, 80], [100, 104], [86, 110], [112, 96]], toe: [[222, 92], [232, 80], [236, 104], [214, 104], [244, 92]],
        scatter: [[96, 88], [162, 110], [230, 84], [130, 76], [206, 112]], center: [[160, 90], [168, 84], [154, 98], [166, 100], [150, 86]] }[fig.now] || [];
      body = `<path class="ln" d="${face}"/><path class="ln" d="${hosel}"/>
        <text x="${hx}" y="160" text-anchor="middle">ネック側</text><text x="${tx}" y="160" text-anchor="middle">先の側</text>
        <circle class="core" cx="${mx(160)}" cy="90" r="4"/><text x="${mx(160)}" y="30" text-anchor="middle">芯</text><path class="guide" d="M${mx(160)} 36 L${mx(160)} 80"/>
        ${at.map(([x, y]) => `<circle class="now" cx="${mx(x)}" cy="${y}" r="6"/>`).join("")}
        ${cmp ? `<circle class="ideal" cx="${mx(160)}" cy="90" r="22"/>` : ""}`;
      const where = { heel: "ネック寄り", toe: "先寄り", scatter: "毎回ちがう場所", center: "芯の近く" }[fig.now] || "";
      title = cmp ? `クラブの面の絵。いまは${where}に当たっています。理想は芯（点線の丸）です` : `クラブの面の絵。いまは${where}に当たっています`;
      legend = cmp ? "塗った点＝いま当たっている場所／点線の丸＝理想（芯）" : "塗った点＝いま当たっている場所";
    } else if (fig.kind === "face" || fig.kind === "path") {
      // 上から見た絵: 下にボール、上に目標。面の向き（線）か、クラブの進む向き（矢印）
      const bx = 160, by = 128;
      const tilt = (dir) => (dir === "right" ? 1 : dir === "left" ? -1 : 0) * (L ? -1 : 1);
      const rot = (deg) => { const a = (deg * Math.PI) / 180; return [Math.cos(a), Math.sin(a)]; };
      const target = `<path class="guide" d="M${bx} ${by - 16} L${bx} 20"/><path class="guide" d="M${bx - 6} 30 L${bx} 20 L${bx + 6} 30"/><text x="${bx + 10}" y="26">目標</text>
        <circle class="ball" cx="${bx}" cy="${by}" r="8"/><text x="${bx - 58}" y="${by + 30}" text-anchor="end">ボール</text>`;
      if (fig.kind === "face") {
        const faceLine = (deg, cls) => { const [c, s] = rot(deg); const hw = 46; return `<path class="${cls}" d="M${bx - hw * c} ${by + 14 - hw * s} L${bx + hw * c} ${by + 14 + hw * s}"/>`; };
        const launch = (deg, cls) => { const [c, s] = rot(deg - 90); return `<path class="${cls}" d="M${bx} ${by - 10} L${bx + 90 * c} ${by - 10 + 90 * s}"/>`; };
        const nowLines = fig.now === "scatter" ? [-18, 4, 16].map((d) => faceLine(d, "nowline") + launch(d, "nowarrow")).join("")
          : faceLine(tilt(fig.now) * 18, "nowline") + launch(tilt(fig.now) * 18, "nowarrow");
        body = `${target}${cmp ? faceLine(0, "ideal") : ""}${nowLines}`;
        const w = { right: "右を向いて", left: "左を向いて", scatter: "毎回ちがう向きで" }[fig.now] || "";
        const ww = L ? w.replace("右", "□").replace("左", "右").replace("□", "左") : w;
        title = cmp ? `上から見た絵。当たる瞬間、クラブの面が${ww}当たっています。理想は目標を向いた面（点線）です` : `上から見た絵。当たる瞬間、クラブの面が${ww}当たっています`;
        legend = cmp ? "太い線＝いまの面の向き（細い矢印はボールが出る向き）／点線＝理想（目標を向いた面）" : "太い線＝いまの面の向き（細い矢印はボールが出る向き）";
      } else {
        // クラブは下（体の側）から来て、ボールの上を通って上へ抜ける。塗った点が抜けていく先
        const ends = (deg) => { const a = (deg * Math.PI) / 180, sx = Math.sin(a), cy = Math.cos(a); return [bx - 70 * sx, by + 70 * cy - 32, bx + 70 * sx, by - 70 * cy]; };
        const arrow = (deg, cls) => { const [x1, y1, x2, y2] = ends(deg); return `<path class="${cls}" d="M${x1} ${y1} L${x2} ${y2}"/>`; };
        const d = tilt(fig.now) * 20;
        const [, , ex, ey] = ends(d);
        body = `${target}${cmp ? arrow(0, "ideal") : ""}${arrow(d, "nowline")}<circle class="now" cx="${ex}" cy="${ey}" r="5"/>`;
        const w = { right: "右", left: "左" }[fig.now] || "";
        const ww = L ? (w === "右" ? "左" : "右") : w;
        title = cmp ? `上から見た絵。クラブが目標より${ww}へ振り抜かれています。理想は目標へまっすぐ（点線）です` : `上から見た絵。クラブが目標より${ww}へ振り抜かれています`;
        legend = cmp ? "太い線＝いまクラブの進む向き（塗った点の側へ抜ける）／点線＝理想（目標へまっすぐ）" : "太い線＝いまクラブの進む向き（塗った点の側へ抜ける）";
      }
    } else if (fig.kind === "low_point" || fig.kind === "attack") {
      // 横から見た絵: 地面・ボール・クラブの通り道（いちばん低い所）
      const g = 120, ball = mx(170);
      // 通り道は2次の曲線。いちばん低い所が (lowX, lowY)
      const arc = (lowX, lowY, cls) => { const y0 = g - 70, cy = 2 * lowY - y0; return `<path class="${cls}" d="M${mx(lowX - 110)} ${y0} Q${mx(lowX)} ${cy} ${mx(lowX + 110)} ${y0}"/>`; };
      let now = "";
      if (fig.kind === "low_point") now = fig.now === "high" ? arc(170, g - 14, "nowline") : arc(120, g + 6, "nowline");
      else now = fig.now === "steep" ? `<path class="nowline" d="M${mx(90)} 20 L${mx(170)} ${g + 6}"/>` : `<path class="nowline" d="M${mx(40)} ${g - 8} L${mx(290)} ${g - 8}"/>`;
      body = `<path class="ln" d="M10 ${g + 8} L310 ${g + 8}"/><text x="14" y="${g + 30}">地面</text>
        <circle class="ball" cx="${ball}" cy="${g}" r="8"/><text x="${ball}" y="${g + 30}" text-anchor="middle">ボール</text>
        <text x="${mx(270)}" y="${g + 30}" text-anchor="middle">目標の側</text>
        ${cmp ? arc(205, g + 6, "ideal") : ""}${now}`;
      const w = fig.kind === "low_point" ? (fig.now === "high" ? "高いところを通って" : "ボールの手前でいちばん低くなって") : (fig.now === "steep" ? "上から急に" : "横から平らに");
      title = cmp ? `横から見た絵。クラブが${w}ボールに当たっています。理想はボールの先でいちばん低くなる通り道（点線）です` : `横から見た絵。クラブが${w}ボールに当たっています`;
      legend = cmp ? "太い線＝いまのクラブの通り道／点線＝理想（ボールの先でいちばん低くなる）" : "太い線＝いまのクラブの通り道";
    } else return "";
    return `<figure class="dgfig" data-dgfig="${esc(fig.kind)}" data-mode="${cmp ? "compare" : "now"}">
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-labelledby="dgt${fig.kind}${mode}"><title id="dgt${fig.kind}${mode}">${esc(title)}</title><desc>${esc(title)}</desc>${body}</svg>
      <figcaption class="caption">${esc(legend)}</figcaption></figure>`;
  }

  // P の見本の線画（カタログの自前の線画。圏外でも前回の写し）。左打ちは左右を入れ替えて描く
  async function pArtHtml(p, view, hand, name) {
    if (!p || !/^P\d/.test(p) || typeof Video === "undefined") return "";
    let cat = null;
    try { cat = await Video.catalog(); } catch { return ""; }
    const v = view === "fo" ? "fo" : "dtl";
    const svg = (cat && cat.svg && cat.svg[`${p.toLowerCase()}.${v}`]) || "";
    if (!svg || !/^<svg[\s>]/.test(svg.trim())) return "";
    return `<figure class="dgpart ${hand === "L" ? "flip" : ""}" data-dgpart="${esc(p)}" aria-label="${esc(`${name || p}の見本の線画（${VIEW[v]}）`)}">${svg}
      <figcaption class="caption">見本の線画（${esc(VIEW[v])}・アプリで描いた絵）: ${lab("p", p.replace("_5", ".5"))} ${esc(name || "")}</figcaption></figure>`;
  }

  // ---- 課題1つ ----
  function nowHtml(is, i, hand) {
    const fig = is.ideal && is.ideal.figure && is.ideal.figure.kind !== "checkpoint" ? is.ideal.figure : null;
    return `<section class="dgsec" data-sec="now">${stepHead("now")}
      <p class="dgbody">${esc(is.now)}</p>
      ${is.impact ? `<div class="dgsub"><p class="label">それでどうなるか</p><p class="dgbody">${esc(is.impact)}</p></div>` : ""}
      ${fig ? figSvg(fig, "now", hand) : ""}
      ${whyBtn("now", i)}</section>`;
  }

  function causeHtml(is, i) {
    const cs = is.causes || [];
    const allLikely = cs.length && cs.every((c) => c.basis === "likely");
    const items = cs.map((c, k) => `<li class="dgcause" data-cause="${esc(c.id)}">
        <p class="dgctitle"><span class="dgrank" aria-hidden="true">${["一", "二", "三"][k] || ""}</span><b>${esc(c.title)}</b></p>
        <p class="row dgchips">${basisChip(c.basis)}${c.checkpoint ? pChip(c.checkpoint.p, c.checkpoint.p_name) : ""}${c.source === "guide" ? `<span class="chip none">ガイドの考え方</span>` : `<span class="chip none">一般的な見方</span>`}</p>
        <p class="dgbody">${esc(c.explain)}</p>
        <p class="caption">${esc(c.basis_text)}</p></li>`).join("");
    const vc = is.video_cta;
    const cta = vc ? `<div class="dgcta" data-video-cta>
        <p class="t-headline" style="margin:0">体のどの動きが原因かは、まだ確かめていません</p>
        <p class="dgbody">${esc(vc.text)}</p>
        <p class="row">${(vc.p || []).map((p) => `<span class="chip none">${lab("p", p.replace("_5", ".5"))}</span>`).join("")}<span class="chip none">${esc(VIEW[vc.view] || VIEW.dtl)}</span></p>
        <a class="btn block dgctabtn" data-act="video" data-view="${esc(vc.view)}" href="${esc(videoHref(vc.view))}">${icon("video")}${esc(vc.button)}</a></div>` : "";
    const out = (is.causes_ruled_out || []).length ? `<div class="dgsub" data-ruled-out><p class="label">動画で範囲の中だった動き（原因から外しました）</p>
        <ul class="dglist">${is.causes_ruled_out.map((t) => `<li>✓ ${esc(typeof t === "string" ? t : t.title)}</li>`).join("")}</ul></div>` : "";
    return `<section class="dgsec" data-sec="cause">${stepHead("cause")}
      ${is.club_state ? `<div class="dgfact"><p class="row dgchips" style="margin:0"><span class="label">当たる瞬間のクラブ</span><span class="chip good">✓ 球のデータで確かめた事実</span></p><p class="dgbody">${esc(is.club_state)}</p></div>` : ""}
      ${cs.length ? `<p class="label dglead">${is.club_state ? "そうなる体の動き" : "体の動き"}（可能性の高い順）</p>
        ${allLikely ? `<p class="dgbody sub" data-tentative>ここに並ぶのは候補です。動画で確かめるまでは推測です。</p>` : ""}
        <ol class="dgcauses">${items}</ol>` : ""}
      ${cta}${out}
      ${whyBtn("cause", i)}</section>`;
  }

  function idealHtml(is, i) {
    const idl = is.ideal || {};
    const fig = idl.figure && idl.figure.kind !== "checkpoint" ? idl.figure : null;
    return `<section class="dgsec" data-sec="ideal">${stepHead("ideal")}
      ${idl.p ? `<p class="row dgchips">${pChip(idl.p, idl.p_name)}</p>` : ""}
      <p class="dgbody">${esc(idl.text)}</p>
      <div class="dgart" data-dgart></div>
      ${fig ? figSvg(fig, "compare", S.player && S.player.handedness === "L" ? "L" : "R") : ""}
      ${whyBtn("ideal", i)}</section>`;
  }

  function drillHtml(d, n) {
    const guide = d.source && d.source.kind === "guide";
    return `<article class="dgdrill ${d.tentative ? "tentative" : ""}" data-drill="${esc(d.id)}">
      <header><p class="dgdname"><span class="dgdno" aria-hidden="true">${KANJI[n] || ""}</span><b>${esc(d.name)}</b></p>
        <p class="row dgchips">${srcChip(d.checked_by || (d.source && d.source.label), guide)}${d.role === "check" ? `<span class="chip none">確かめる道具</span>` : ""}${d.tentative ? `<span class="chip warn">△ 候補の動きに効く練習</span>` : ""}</p></header>
      ${d.tentative && d.tentative_note ? `<p class="caption">${esc(d.tentative_note)}</p>` : ""}
      <dl class="dgkv">
        ${d.equipment ? `<div><dt>道具</dt><dd>${esc(d.equipment)}</dd></div>` : ""}
        ${d.place ? `<div><dt>置き方</dt><dd>${esc(d.place)}</dd></div>` : ""}
        ${d.reps_text ? `<div><dt>球数</dt><dd>${esc(d.reps_text)}</dd></div>` : ""}
      </dl>
      <p class="label" style="margin:var(--s3) 0 0">手順</p>
      <ol class="dgsteps">${(d.steps || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ol>
      ${d.why ? `<p class="dgbody"><span class="label">何に効くか</span><br>${esc(d.why)}</p>` : ""}
      ${d.safety ? `<p class="dgsafe" role="note">${icon("alert-triangle")}<span>${esc(d.safety)}</span></p>` : ""}
    </article>`;
  }

  function menuHtml(fx) {
    const m = fx.menu || [];
    if (!m.length) return fx.practice ? `<p class="dgbody">${esc(fx.practice)}</p>` : "";
    const total = m.reduce((a, x) => a + (Number(x.balls) || 0), 0);
    const KIND = { baseline: ["いつも通り", "none"], drill: ["ドリル", "warn"], main: ["本番", "brand"] };
    // 練習の組み方の文のうち、並び（「…の順です」）はリストと同じなので、後ろの説明だけを添える
    const tail = sentences(fx.practice).filter((s) => !/の順です/.test(s)).join("");
    return `<ol class="dgmenu" data-menu>${m.map((x, k) => { const [w, c] = KIND[x.kind] || [x.kind, "none"];
      return `<li data-kind="${esc(x.kind)}"><span class="dgmno" aria-hidden="true">${KANJI[k] || ""}</span><span class="grow1">${x.title === w ? "" : `<span class="chip ${c}">${esc(w)}</span> `}<b>${esc(x.title)}</b><br><span class="sub">${esc(x.what)}</span></span><span class="dgballs">${lab("count", x.balls + "球")}</span></li>`; }).join("")}</ol>
      <p class="caption" style="text-align:right">全部で ${lab("count", total + "球")}</p>
      ${tail ? `<p class="dgbody sub">${esc(tail)}</p>` : ""}`;
  }

  function fixHtml(is, i) {
    const fx = is.fix || {};
    const drills = fx.drills || [];
    return `<section class="dgsec" data-sec="fix">${stepHead("fix")}
      ${(fx.setup || []).length ? `<div class="dgsub" data-fix="setup"><h4>構えで直すこと</h4>
        <ul class="dgcheck">${fx.setup.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
      ${fx.cue_move || fx.cue ? `<div class="dgsub" data-fix="move"><h4>本番で意識する体の動き</h4>
        <div class="focuscard"><p class="t-headline" style="margin:0" data-cue-move>${esc(fx.cue_move || fx.cue)}</p>
        ${fx.cue_why ? `<p class="dgbody" style="margin-bottom:0"><span class="label">なぜこの動きか</span><br>${esc(fx.cue_why)}</p>` : ""}</div></div>` : ""}
      ${drills.length ? `<div class="dgsub" data-fix="drills"><h4>ドリル</h4>${drills.map(drillHtml).join("")}</div>` : ""}
      <div class="dgsub" data-fix="menu"><h4>今日の練習メニュー</h4>${menuHtml(fx)}</div>
      ${whyBtn("fix", i)}</section>`;
  }

  const checkHtml = (is, i) => `<section class="dgsec" data-sec="check">${stepHead("check")}
      <p class="dgbody">${esc(is.check)}</p>${whyBtn("check", i)}</section>`;

  function videoHref(view) {
    const sid = cur && cur.sid, date = cur && cur.date;
    return date ? `#/video/${date}${sid ? `?session=${sid}` : ""}` : "#/record";
  }

  // 練習を組むボタン（球の課題は候補のプラン、動きの課題はチェックの画面の「この動きで練習を組む」）
  function planBtn(is, i, rep) {
    const primary = i === 0;
    const cls = `btn block ${primary ? "primary" : ""}`;
    const pa = primary ? "data-primary" : "";
    if (is.from === "video") {
      const it = (is.causes || [])[0];
      const item = it && it.checkpoint && it.checkpoint.item_id;
      return item ? `<a class="${cls}" ${pa} data-act="motionplan" href="#/session/${cur.sid}/check/${encodeURIComponent(item)}">この動きで練習を組む</a>` : "";
    }
    const hit = candOf(rep, is);
    if (!hit) return `<p class="caption" data-noplan>この課題は、いまの記録だけでは練習の組み方を決められません（先に測れるようにします）。</p>`;
    return `<button type="button" class="${cls}" ${pa} data-act="startplan" data-issue="${i}">この課題で練習を組む</button>`;
  }
  // 課題 → 解説の範囲と候補（プランを作る口に渡すもの）
  function candOf(rep, is) {
    if (!is.plan_candidate) return null;
    for (const sid of is.scope_ids || []) {
      const sc = (rep.scopes || []).find((x) => x.scope_id === sid && x.kind === "main");
      const cand = sc && sc.candidates && (sc.candidates.candidates || []).find((c) => c.id === is.plan_candidate);
      if (cand && cand.kind !== "measure") return { sc, cand, which: sc.candidates.now === cand.id ? "now" : "next" };
    }
    return null;
  }

  function issueHtml(is, i, rep, hand) {
    const first = i === 0;
    const tag = is.now_or_next === "now" || first ? `<span class="chip bad strong">× いま取り組む</span>` : `<span class="chip none">次に取り組む</span>`;
    const head = `<header class="dgihead"><p class="row dgchips" style="margin:0">${tag}${is.club_scope ? `<span class="chip none">${scopeHtml(is.club_scope)}</span>` : ""}${is.from === "video" ? `<span class="chip brand">動画で見つけた動き</span>` : ""}</p>
      <h2 id="dg-${esc(is.id)}"><span class="dgino">課題${["一", "二", "三"][i] || ""}</span>${esc(is.title)}</h2></header>`;
    const rest = `${causeHtml(is, i)}${idealHtml(is, i)}${fixHtml(is, i)}${checkHtml(is, i)}<div class="block">${planBtn(is, i, rep)}</div>`;
    return `<article class="card dgissue ${first ? "first" : ""}" data-issue="${i}" data-issue-id="${esc(is.id)}" aria-labelledby="dg-${esc(is.id)}">${head}${nowHtml(is, i, hand)}
      ${first ? rest : `<details class="dgmore" data-more><summary class="textbtn">原因・理想の動き・直し方を読む</summary>${rest}</details>`}</article>`;
  }

  // ---- 画面 ----
  let cur = null; // { sid, date, rep, dg }
  function html(dg, rep, se) {
    const hand = rep.handedness === "L" ? "L" : "R";
    const issues = dg.issues || [];
    const toc = issues.length > 1 ? `<nav class="dgtoc" aria-label="課題の一覧"><p class="label" style="margin:0 0 var(--s1)">課題（取り組む順）</p><ol>${issues.map((x, k) =>
      `<li><a href="#dg-${esc(x.id)}" data-toc="${k}"><span class="grow1">${esc(x.title)}</span><span class="chip ${k === 0 ? "bad" : "none"}">${k === 0 ? "いま" : "次"}</span></a></li>`).join("")}</ol></nav>` : "";
    const video = dg.video_needed ? `<div class="note dgvideo" data-video-needed><p style="margin:0"><b>${icon("video")}動画があると、原因の動きを確かめられます</b></p><p class="dgbody" style="margin-bottom:0">${esc(dg.video_hint)}</p></div>` : "";
    return `<section class="card dgsum" data-summary aria-labelledby="dg-sum"><h2 id="dg-sum">今日のまとめ</h2>
        <p class="dgbody">${esc(dg.summary)}</p>
        ${(dg.strengths || []).length ? `<div class="dgsub"><p class="label">良かったところ</p><ul class="dglist good">${dg.strengths.map((x) => `<li>✓ ${esc(x)}</li>`).join("")}</ul></div>` : ""}
      </section>${video}${toc}
      ${issues.map((x, k) => issueHtml(x, k, rep, hand)).join("")}`;
  }

  function openWhy(key, is) {
    const ev = is.evidence || [];
    const evHtml = (list) => list.map((e) => `<div class="wgrp"><p class="wq">${esc(e.label)}</p><p class="claim">${esc(App.textJa(e.value_text))}</p></div>`).join("");
    let h = "";
    if (key === "now") h = evHtml(ev) || `<p class="sub">この段の数字はありません。</p>`;
    else if (key === "cause") {
      h = (is.causes || []).map((c) => `<div class="wgrp"><p class="wq">${esc(c.title)}</p>
        <p class="claim">${esc(BASIS[c.basis] ? BASIS[c.basis].word : c.basis)}: ${esc(c.basis_text)}</p>
        ${c.guide ? `<p class="claim">出どころ: ${esc(c.source_label || "")}・${esc(c.guide.label || "")}（${esc(c.guide.section || "")}）</p>` : `<p class="claim">出どころ: ${esc(c.source_label || "")}</p>`}
        ${c.checkpoint ? `<p class="claim">動画で見る所: ${esc(c.checkpoint.p)} ${esc(c.checkpoint.p_name || "")}（${esc(VIEW[c.checkpoint.view] || "")}）</p>` : ""}</div>`).join("");
      h += evHtml(ev.filter((e) => /miss\.strike|impact\.face|summary\.path_face|miss\.extreme|item_id/.test(e.claim_id || (e.item_id ? "item_id" : ""))));
      h += `<p class="caption">計測器（TrackMan）が測るのは、球と、当たる瞬間のクラブの様子です。体の動きは測っていないので、動画で確かめるまでは「可能性」と書きます（ガイドも、動きと球筋のつながりは確定ではなく可能性だとしています。p.133〜145）。</p>`;
    } else if (key === "ideal") {
      const idl = is.ideal || {};
      const pages = [...new Set((is.causes || []).map((c) => c.guide && c.guide.label).filter(Boolean))];
      h = `${idl.p ? `<p class="claim">見る形: ${esc(idl.p)} ${esc(idl.p_name || "")}</p>` : ""}${pages.length ? `<p class="claim">基準: ${pages.map(esc).join("、")}</p>` : ""}
        <p class="caption">見本の線画はアプリで描いた絵で、ガイドの図ではありません。範囲の数字は、動画を撮ったあとのチェックの画面の「数字を見る」で出ます。</p>`;
    } else if (key === "fix") {
      const fx = is.fix || {};
      h = (fx.drills || []).map((d) => `<div class="wgrp"><p class="wq">${esc(d.name)}</p><p class="claim">出どころ: ${esc(d.checked_by || "")}${d.reps ? `・球数の目安 ${Number(d.reps)}球` : ""}</p>${d.tentative ? `<p class="claim">原因の候補の動き（まだ動画で確かめていません）に効く練習なので、最後に置いています。</p>` : ""}</div>`).join("");
      h += `<p class="claim">${esc(fx.practice || "")}</p>
        <p class="caption">ガイドの練習法はページをつけています（ページはノートの節の範囲）。「一般的な練習法」はガイドに無い、よく使われる練習です。どのドリルも安全の注意を守ってください。</p>`;
    } else if (key === "check") {
      h = evHtml(ev.filter((e) => /next\.(now|next|hypothesis)/.test(e.claim_id || "")))
        + `<p class="caption">「十球のうち八球」はガイドの練習の合格ライン（十球テスト・p.158〜183）に合わせたもので、確率ではありません。一回では偶然でも出るので、別の日にもう一回できてから合格にします。</p>`;
    }
    App.openSheet({ title: "なぜそう言える？", label: "dgwhy", size: "full", html: `<p class="sub" style="margin-top:0">${esc(is.title)}・${esc(STEP.find((x) => x[0] === key)[2])}</p>${h}
      <p class="caption">文は全部、計測の数字と知識の表から決まった型で作ったものです（AI の文ではありません）。</p>` });
  }

  // D（#/session/:id）から呼ぶ。body に描き、ボタンをつなぐ
  async function render(body, { sid, rep, se, alive, startPlan }) {
    const dg = rep.diagnosis;
    cur = { sid, date: se && se.date, rep, dg };
    body.innerHTML = `<div class="dg" data-report>${html(dg, rep, se)}</div>`;
    const root = $("[data-report]", body);
    // 理想の動き: 動画で確かめた原因なら本人のコマに範囲と線を重ねた図、無ければ P の見本の線画
    const hand = rep.handedness === "L" ? "L" : "R";
    (dg.issues || []).forEach(async (is, k) => {
      const slot = root.querySelector(`[data-issue="${k}"] [data-dgart]`);
      if (!slot) return;
      const idl = is.ideal || {};
      let h = "";
      if (idl.figure && idl.figure.kind === "checkpoint" && typeof Checks !== "undefined" && typeof Ideal !== "undefined") {
        try {
          const d = await Checks.load(sid);
          const it = ((d && d.checks && d.checks.items) || []).find((x) => x.id === idl.figure.item_id);
          if (it) h = await Ideal.thumbHtml(d, it);
          if (h) h = `<div class="dgov" data-dgov>${h}<p class="caption">あなたのコマに、ガイドの範囲（面）と、範囲に入れた目安の線を重ねています。</p></div>`;
        } catch (e) { console.warn(e); }
      }
      const view = (idl.figure && idl.figure.view) || ((is.causes || []).find((c) => c.checkpoint && c.checkpoint.p === idl.p) || {}).checkpoint?.view || "dtl";
      if (!h) h = await pArtHtml(idl.p, view, hand, idl.p_name);
      if (alive() && slot.isConnected) { if (h) slot.innerHTML = h; else slot.remove(); }
    });
    root.addEventListener("click", (ev) => {
      const w = ev.target.closest("[data-dgwhy]");
      if (w) { openWhy(w.dataset.dgwhy, dg.issues[Number(w.dataset.issue)]); return; }
      const sp = ev.target.closest("[data-act=startplan]");
      if (sp) {
        const is = dg.issues[Number(sp.dataset.issue)];
        const hit = candOf(rep, is);
        if (hit) startPlan(sid, rep, hit.sc, hit.cand, hit.which, { cue: (is.fix && is.fix.cue_move) || "", diag: is });
        return;
      }
      const v = ev.target.closest("[data-act=video]");
      if (v) { const vw = v.dataset.view; if (vw === "dtl" || vw === "fo") App.LS.set("golf.cpView", vw); else if (vw === "both") App.LS.set("golf.cpView", "dtl"); }
      const t = ev.target.closest("[data-toc]");
      if (t) {
        ev.preventDefault();
        const is = dg.issues[Number(t.dataset.toc)];
        const card = root.querySelector(`[data-issue="${t.dataset.toc}"]`);
        const more = card && card.querySelector("details[data-more]");
        if (more) more.open = true;
        const h2 = document.getElementById(`dg-${is.id}`);
        if (h2) { h2.scrollIntoView({ block: "start" }); h2.tabIndex = -1; h2.focus({ preventScroll: true }); }
      }
    });
  }

  // 課題 → プランを作るときの材料（ホーム・練習が使う）
  const firstPlanOf = (rep) => {
    for (const is of (rep.diagnosis && rep.diagnosis.issues) || []) { const hit = candOf(rep, is); if (hit) return { is, ...hit }; }
    return null;
  };
  // プランの候補と範囲から、診断の課題を探す（練習の画面がドリルの手順と意識する動きを出すのに使う）
  function issueForPlan(rep, issueId, scopeId) {
    const list = (rep && rep.diagnosis && rep.diagnosis.issues) || [];
    return list.find((x) => x.plan_candidate === issueId && (!scopeId || (x.scope_ids || []).includes(scopeId)))
      || list.find((x) => x.plan_candidate === issueId) || null;
  }

  return { render, figSvg, pArtHtml, drillHtml, menuHtml, basisChip, scopeHtml, firstPlanOf, issueForPlan, candOf, BASIS, VIEW };
})();
