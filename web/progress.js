"use strict";
/*
  経過（docs/DESIGN_v2.md §10 T）。[課題ごと｜記録ごと]。
  - 課題ごと: 動いているプランの進み（最初の「いつも通り」の推移）と、あなたの記録（プランごとの件数。割合は出さない）。
    推移の図と数字は「なぜそう言える？」のシートの中。
  - 記録ごと: 日ごとの記録の一覧（押すと診断）と、2日の比較（前の「前回と比べる」）。
*/
const Progress = (() => {
  const { $, esc, lab, S, api, fmt, LABEL } = App;
  const STATUS = { active: ["動いている", "good"], done: ["次に進んだ", "brand"], switched: ["替えた", "none"], blocked: ["詰まった", "warn"], abandoned: ["やめた", "none"] };
  const VAL = { reduce_abs: ["mean_abs", "ずれの大きさの平均"], reduce_sd: ["sd", "ばらつき"], increase: ["mean", "平均"], decrease: ["mean", "平均"] };

  function progressSvg(pts, metric, label) {
    const vals = pts.flatMap((x) => [x.a, x.b]).filter((v) => v != null);
    if (!vals.length) return `<p class="sub">まだ点を打てる練習がありません。</p>`;
    const W = 360, H = 200, L = 56, R = 12, Tp = 14, B = 34;
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi - lo < 1e-9) { lo -= Math.abs(lo) * 0.2 + 1e-3; hi += Math.abs(hi) * 0.2 + 1e-3; }
    const pad = (hi - lo) * 0.15; lo -= pad; hi += pad;
    const n = pts.length;
    const x = (i) => (n === 1 ? (L + W - R) / 2 : L + (i * (W - L - R)) / (n - 1));
    const y = (v) => Tp + ((hi - v) * (H - Tp - B)) / (hi - lo);
    let s = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img"><title>最初の「いつも通り」の推移</title><desc>${esc(label)}を練習の回ごとに打った図。実線が最初の「いつも通り」、中抜きの点が本番</desc>`;
    s += `<line class="g-axis" x1="${L}" y1="${H - B}" x2="${W - R}" y2="${H - B}"/><line class="g-axis" x1="${L}" y1="${Tp}" x2="${L}" y2="${H - B}"/>`;
    for (const v of [lo + pad, (lo + hi) / 2, hi - pad]) s += `<text class="g-sub t11" x="${L - 4}" y="${y(v) + 4}" text-anchor="end">${esc(fmt(metric, v))}</text>`;
    const ref = pts.find((p) => p.a != null && p.ref);
    if (ref) s += `<line class="g-diag" x1="${L}" y1="${y(ref.a)}" x2="${W - R}" y2="${y(ref.a)}"/>`;
    const line = pts.filter((p) => p.a != null).map((p) => `${x(p.i)},${y(p.a)}`).join(" ");
    if (line.includes(" ")) s += `<polyline class="g-flight g-good" style="fill:none" points="${line}"/>`;
    for (const p of pts) {
      s += `<text class="g-sub t11" x="${x(p.i)}" y="${H - B + 16}" text-anchor="middle">${p.i + 1}回目</text>`;
      if (p.b != null) s += `<circle class="g-miss hollow" data-t="bpt" cx="${x(p.i)}" cy="${y(p.b)}" r="4"/>`;
      if (p.a != null) s += `<circle class="g-good" data-t="apt" cx="${x(p.i)}" cy="${y(p.a)}" r="5"/>`;
    }
    return `<figure class="fig" data-t="progfig">${s}</svg></figure>`;
  }

  async function planPart(box) {
    await Practice.load().catch(() => false);
    const p = Practice.plan();
    if (!p) { box.innerHTML = `<p class="sub" data-t="noplan">動いているプランはありません。</p>`; return; }
    const r = await App.request("GET", `/v1/plans/${p.id}/progress`).catch(() => ({ ok: false, status: 0 }));
    if (!r.ok) { box.innerHTML = App.errorHtml({ what: "進み具合を読めませんでした", next: r.status === 0 ? "電波のあるところで開き直してください。" : ((r.body && r.body.error) || "") }); return; }
    const pl = r.body.plan, runs = r.body.runs || [];
    const [key, label] = VAL[pl.goal] || ["mean", "平均"];
    const latest = r.body.latest && r.body.latest.progress;
    const refRun = latest && latest.reference_run != null ? latest.reference_run : null;
    const pts = runs.map((x, i) => {
      const e = x.evaluation || {}, fa = e.first_a || {}, b = (e.stats && e.stats.B) || {};
      return { i, date: x.date, a: (fa.measured ?? fa.n ?? 0) >= 5 ? fa[key] : null, an: fa.measured ?? fa.n ?? 0, b: (b.n || 0) >= 5 ? b[key] : null, bn: b.n || 0,
        ev: e, fresh: x.fresh, ref: refRun != null && x.run_id === refRun };
    });
    box.innerHTML = `<article class="card" data-t="prog"><p class="label">いまのプラン</p><h3>${esc(Practice.planTitle(p))}</h3>
      ${latest ? `<p data-t="progplain"><b>${esc(latest.title || "")}</b> ${esc(latest.text || "")}</p>` : `<p class="sub">まだ判定した練習がありません。</p>`}
      <button class="textbtn" data-t="progwhy">なぜそう言える？（推移の図と数字）</button></article>`;
    box.querySelector("[data-t=progwhy]").addEventListener("click", () => {
      let h = progressSvg(pts, pl.target_metric, label);
      h += `<div class="scroll"><table data-t="trend"><tr><th class="l">練習</th><th>最初の「いつも通り」</th><th>本番</th><th class="l">その日</th></tr>${pts.map((x) => {
        const [gl, gc] = Practice.plainOf(x.ev);
        return `<tr><td class="l">${x.i + 1}回目 ${esc(x.date)}${x.ref ? "（物差し）" : ""}</td><td>${x.a == null ? "—" : fmt(pl.target_metric, x.a)}（${x.an}球）</td><td>${x.b == null ? "—" : fmt(pl.target_metric, x.b)}（${x.bn}球）</td><td class="l"><span class="chip ${gc}">${esc(gl)}</span>${x.fresh === false ? ` <span class="caption">保存した判定</span>` : ""}</td></tr>`;
      }).join("")}</table></div>`;
      const wk = (latest && latest.weekly) || [];
      if (wk.length) {
        h += `<div class="scroll"><table data-t="weekly"><tr><th class="l">週</th><th>いつも通りの${esc(label)}</th></tr>${wk.map((w) =>
          `<tr><td class="l">${esc(w.from || "")}〜${esc(w.to || "")}</td><td>${w.shown && w.value != null ? `${fmt(pl.target_metric, w.value)}（${Number(w.n)}球）` : `—（${Number(w.n)}球。5球未満）`}</td></tr>`).join("")}</table></div>
          <p class="caption">週ごとの数字は、練習の「いつも通り」の球だけで数えます（意識して打った本番の球は良く出るので入れません）。</p>`;
      }
      h += `<p class="caption">点は、その日の最初の「いつも通り」の${esc(LABEL[pl.target_metric] || pl.target_metric)}（${esc(label)}）。点線が物差しの回${refRun != null ? "" : "（まだありません）"}。中抜きの点は意識して打った本番で、良く出るので物差しにはしません。5球未満の回は点を打ちません。</p>`;
      App.openSheet({ title: "推移の図と数字", label: "progwhy", size: "full", html: h });
    });
  }

  async function recordPart(box) {
    if (!S.player) { box.innerHTML = ""; return; }
    const r = await App.request("GET", `/v1/players/${S.player.id}/record`).catch(() => ({ ok: false, status: 0 }));
    if (!r.ok) { box.innerHTML = App.errorHtml({ what: "記録を読めませんでした", next: "電波のあるところで開き直してください。" }); return; }
    const rows = r.body || [];
    if (!rows.length) { box.innerHTML = `<p class="sub">まだプランの記録がありません。</p>`; return; }
    box.innerHTML = rows.map((x) => {
      const p = x.plan, [sl, sc] = STATUS[p.status] || [p.status, "none"];
      const ev = x.runs.filter((y) => y.evaluated);
      const good = ev.filter((y) => y.counts_as_worked).length;
      return `<div class="card" data-t="rec"><h3>${esc(Practice.planTitle(p))} <span class="chip ${sc}">${esc(sl)}</span>${x.state_title ? ` <span class="chip none">${esc(x.state_title)}</span>` : ""}</h3>
        <p class="sub">やり方: ${p.drill_id ? esc(p.drill_id) : `自分で書いた1点「${esc(p.cue)}」`}・${esc(App.clubJa(p.club))}</p>
        <p class="sub">練習: ${x.runs.length}回（判定した ${ev.length}回のうち、良くなった回 ${good}）</p>
        <details class="folded"><summary>日ごとの判定</summary>${ev.map((y) => { const [rl, rc] = Practice.plainOf(y); return `<p>${esc(y.date)} <span class="chip ${rc}">${esc(rl)}</span></p>`; }).join("") || "—"}</details></div>`;
    }).join("") + `<p class="caption">同じ直しで次のやり方を選ぶときは、記録のあるやり方を先に試します。</p>`;
  }

  async function render({ el, query, alive }) {
    const tab = query.tab === "records" ? "records" : "plans";
    el.innerHTML = `<div class="pagehead"><h1>経過</h1></div>
      <div class="seg" role="group" aria-label="見るもの" data-progress-tabs><a href="#/progress" ${tab === "plans" ? 'aria-current="page"' : ""}>課題ごと</a><a href="#/progress?tab=records" ${tab === "records" ? 'aria-current="page"' : ""}>記録ごと</a></div>
      <div data-body class="block"></div>`;
    const body = $("[data-body]", el);
    if (tab === "plans") {
      body.innerHTML = `<section data-part="plan"></section><section class="block"><h2>あなたの記録</h2><div data-part="rec"></div></section>`;
      const stop = App.loading($("[data-part=plan]", body));
      await planPart($("[data-part=plan]", body)).finally(stop);
      if (!alive()) return;
      await recordPart($("[data-part=rec]", body));
      return;
    }
    const ss = await App.sessions(true).catch(() => []);
    if (!alive()) return;
    body.innerHTML = `<ul class="navlist" data-t="sessions">${ss.map((s) => App.navItem(`#/session/${s.id}`,
      `${lab("date", App.dateJa(s.date))}${s.location ? "・" + esc(s.location) : ""}`, `${s.n_shots || 0}球${(s.clubs || []).length ? "・" + (s.clubs || []).map((c) => esc(App.clubJa(c.club))).join("・") : ""}`,
      `data-session="${s.id}"`)).join("") || `<li><p class="sub" style="padding:var(--s4)">まだ記録がありません。</p></li>`}</ul>
      ${ss.filter((s) => s.n_shots).length > 1 ? `<div class="block"><a class="btn block" href="#/progress/compare">2日を比べる</a></div>` : ""}`;
  }

  function renderCompareOut(r) {
    let h = "";
    for (const [club, c] of Object.entries(r.clubs)) {
      h += `<section class="card"><h3>${esc(club)} <span class="caption">前 ${c.n[0]}球 → 後 ${c.n[1]}球</span></h3>`;
      if (!c.explanations.length) {
        const ins = Object.values(c.l0).some((x) => x.status === "insufficient");
        h += `<p class="sub">${ins ? "球が足りないので比べられない項目があります。" : "球筋に意味のある変化はありません。"}</p>`;
      }
      for (const e of c.explanations) {
        const why = e.explained_by.length
          ? e.explained_by.map((x) => `<b>${esc(LABEL[x.metric] || x.metric)} ${fmt(x.metric, x.diff, true)}</b>`).join("、") + " が変わったため"
          : `インパクトの測れている値では説明できません${e.unmeasured.length ? `（測れていない: ${e.unmeasured.map((m) => esc(LABEL[m] || m)).join("・")}）` : ""}`;
        h += `<div class="finding">${esc(LABEL[e.outcome] || e.outcome)}が <b>${fmt(e.outcome, e.diff, true)}</b> 変わりました ―― ${why}。</div>`;
      }
      const changed = Object.entries(c.l1).filter(([, x]) => x.status === "changed");
      if (changed.length) h += `<p class="caption">インパクトで変わったもの: ${changed.map(([k, x]) => `${esc(LABEL[k] || k)} ${fmt(k, x.diff, true)}`).join(" / ")}</p>`;
      h += `</section>`;
    }
    if (r.only_in_a.length || r.only_in_b.length) h += `<p class="caption">片方にしか無いクラブ: ${[...r.only_in_a, ...r.only_in_b].map(esc).join("、")}</p>`;
    return h || `<p class="sub">共通のクラブがありません。</p>`;
  }

  async function renderCompare({ el, query, alive }) {
    el.innerHTML = `<div class="pagehead">${App.backBtn("#/progress?tab=records", "記録ごとへ戻る")}<h1>2日を比べる</h1></div><div data-body></div>`;
    const body = $("[data-body]", el);
    const ss = (await App.sessions().catch(() => [])).filter((s) => s.n_shots);
    if (!alive()) return;
    const opt = (s) => `<option value="${s.id}">${esc(App.dateJa(s.date))}${s.location ? "・" + esc(s.location) : ""}（${s.n_shots}球）</option>`;
    body.innerHTML = `<p class="sub">球筋の変化を、当たる瞬間のクラブの数字の変化で説明します。</p>
      <label class="field"><span>前（比べる元）</span><select data-a>${ss.map(opt).join("")}</select></label>
      <label class="field"><span>後</span><select data-b>${ss.map(opt).join("")}</select></label>
      <button class="btn primary block" data-primary data-go>比べる</button><div data-err></div><div data-out class="block"></div>`;
    const a = $("[data-a]", body), b = $("[data-b]", body);
    if (ss[1]) a.value = String(query.a || ss[1].id);
    if (ss[0]) b.value = String(query.b || ss[0].id);
    $("[data-go]", body).addEventListener("click", async () => {
      $("[data-err]", body).innerHTML = "";
      if (a.value === b.value) { $("[data-err]", body).innerHTML = `<p class="fielderr">違う日を選んでください</p>`; return; }
      try {
        const r = await api("GET", `/v1/sessions/${b.value}/compare?with=${a.value}`);
        $("[data-out]", body).innerHTML = renderCompareOut(r);
        history.replaceState(null, "", `#/progress/compare?a=${a.value}&b=${b.value}`);
      } catch (e) { $("[data-err]", body).innerHTML = App.errOf(e, { what: "比べられませんでした" }); }
    });
    if (query.a && query.b) $("[data-go]", body).click();
  }

  App.route("/progress", render, { tab: "progress" });
  App.route("/progress/compare", renderCompare, { tab: "progress" });
  return {};
})();
