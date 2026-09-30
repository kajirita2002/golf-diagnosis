"use strict";
/*
  経過（docs/DESIGN_v2.md §10 T）。[課題ごと｜記録ごと]。
  - 課題ごと: 進行中のプランの進み（最初の「いつも通り」の推移・練習の回数）と、これまでのプラン（件数。割合は出さない）。
    同じプランを2回並べない（進行中のものは上のカードだけ）。推移の図と数字は「推移を見る」のシートの中。
  - 記録ごと: 日ごとの記録の一覧（押すと診断）と、2日の比較（前の「前回と比べる」）。
*/
const Progress = (() => {
  const { $, esc, lab, S, api, fmt, LABEL } = App;
  const STATUS = { active: ["進行中", "brand"], done: ["次に進んだ", "brand"], switched: ["替えた", "none"], blocked: ["詰まった", "warn"], abandoned: ["やめた", "none"] };
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

  // 十球テストの「◯/10」の推移（§8.5）。8 の線を引く。条件（向き・版）が違う日は線でつながない。合計は描かない
  // 判定できなかった回は0の高さに描かない（「0回」に見える）。軸の下の帯に四角で置く。
  // 軸の数字は numbers のときだけ（最初の面は言葉だけ。数字は「数字を見る」の中）
  function trendSvg(tests, title, numbers = false) {
    if (!tests.length) return `<p class="sub">まだ十球テストがありません。</p>`;
    const W = 360, H = 190, L = 36, R = 12, Tp = 12, B = 44;
    const n = tests.length;
    const x = (i) => (n === 1 ? (L + W - R) / 2 : L + (i * (W - L - R)) / (n - 1));
    const y = (v) => Tp + ((10 - v) * (H - Tp - B)) / 10;
    let s = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img"><title>${esc(title)}</title><desc>十球テストで範囲の中だった回数を日ごとに打った図。点線が合格の線（八）。塗った点が合格、中抜きがまだ、軸の下の四角が判定できなかった日</desc>`;
    s += `<line class="g-axis" x1="${L}" y1="${H - B}" x2="${W - R}" y2="${H - B}"/><line class="g-axis" x1="${L}" y1="${Tp}" x2="${L}" y2="${H - B}"/>`;
    if (numbers) for (const v of [0, 8, 10]) s += `<text class="g-sub t11" x="${L - 4}" y="${y(v) + 4}" text-anchor="end">${v}</text>`;
    else s += `<text class="g-sub t11" x="${L - 4}" y="${y(8) + 4}" text-anchor="end">合格</text>`;
    const band = H - B + 10; // 判定できなかった回の帯（軸の下）
    s += `<line class="g-pass" data-t="passline" x1="${L}" y1="${y(8)}" x2="${W - R}" y2="${y(8)}" style="stroke:var(--good);stroke-dasharray:4 4"/>`;
    for (let i = 1; i < n; i++) {
      const a = tests[i - 1], b = tests[i];
      if (a.view === b.view && a.catalog_version === b.catalog_version && a.passed !== null && b.passed !== null) s += `<line class="g-flight g-good" x1="${x(i - 1)}" y1="${y(a.in_range)}" x2="${x(i)}" y2="${y(b.in_range)}"/>`;
    }
    tests.forEach((t, i) => {
      s += `<text class="g-sub t11" x="${x(i)}" y="${H - B + 30}" text-anchor="middle">${esc((t.date || "").slice(5))}</text>`;
      if (t.passed === null) s += `<rect class="g-miss hollow" data-t="tunk" x="${x(i) - 4}" y="${band - 4}" width="8" height="8"/>`;
      else s += `<circle class="${t.passed ? "g-good" : "g-miss hollow"}" data-t="${t.passed ? "tpass" : "tnot"}" cx="${x(i)}" cy="${y(t.in_range)}" r="5"/>`;
    });
    const unk = tests.some((t) => t.passed === null) ? `<figcaption class="caption" data-t="tunkcap">軸の下の四角は、判定できるスイングが足りず撮り直しになった日です。</figcaption>` : "";
    return `<figure class="fig trend8" data-t="trend10">${s}</svg>${unk}</figure>`;
  }
  const TW = (t) => (t.passed === true ? "合格" : t.passed === false ? "まだ" : "判定できない");
  function trendTable(tests) {
    return `<div class="scroll"><table data-t="trend10tbl"><tr><th class="l">日</th><th>範囲の中</th><th class="l">結果</th><th class="l">撮影</th></tr>${tests.map((t) =>
      `<tr><td class="l">${esc(t.date || "")}</td><td>${t.in_range}/${t.judged}</td><td class="l">${TW(t)}</td><td class="l">${t.block === "baseline" ? "いつも通り" : "練習の最後"}・${t.view === "fo" ? "正面" : "後ろ"}</td></tr>`).join("")}</table></div>
      <p class="caption">分母は判定できたスイングの数です。八回以上で合格（ガイドの練習の合格ラインで、確率ではありません）。向きやカタログの版が違う日は線でつなぎません。</p>`;
  }

  async function motionPlanPart(box, p, r) {
    const m = r.body.motion || {}, tests = r.body.tests || [];
    box.innerHTML = `<article class="card" data-t="prog" data-kind="motion"><p class="label">いまのプラン <span class="chip brand">進行中</span> <span class="chip none">動き</span></p><h3>${esc(Practice.planTitle(p))}</h3>
      <p data-t="progplain"><b>${esc(m.head || "")}</b> ${esc(m.text || "")}</p><p class="sub">${esc(m.next_text || "")}</p>
      <p class="sub" data-t="progruns">${tests.length ? `十球テスト ${lab("count", tests.length + "回")}・合格 ${lab("count", tests.filter((t) => t.passed === true).length + "回")}` : "まだ十球テストがありません。練習の最後の十球を撮って数えると、ここに出ます。"}</p>
      <button class="textbtn" data-t="progwhy">推移を見る</button></article>`;
    box.querySelector("[data-t=progwhy]").addEventListener("click", () => {
      App.openSheet({ title: "十球テストの推移", label: "progwhy", size: "full", html: trendSvg(tests, "十球テストの推移", true) + trendTable(tests) });
    });
  }

  // 課題ごとの十球テストの推移（プランの外のテストも含めて、項目ごとに1枚）
  async function focusPart(box) {
    const r = await App.request("GET", `/v1/players/${S.player.id}/focus-tests`).catch(() => ({ ok: false }));
    if (!r.ok || !(r.body.tests || []).length) { box.innerHTML = ""; return; }
    const by = {};
    for (const t of r.body.tests) (by[t.item_id] = by[t.item_id] || []).push(t);
    let cat = null;
    try { cat = await Video.catalog(); } catch { /* 名前が無ければ項目の id のまま */ }
    const name = (id) => { const it = cat && (cat.items || []).find((x) => x.id === id); return it ? it.title : id; };
    box.innerHTML = `<h2>十球テストの推移</h2>` + Object.entries(by).map(([id, ts]) => `<article class="card block" data-t="focusitem"><h3>${esc(name(id))}</h3>
      ${trendSvg(ts, name(id) + "の十球テスト")}<details class="folded"><summary>数字を見る</summary>${trendTable(ts)}</details></article>`).join("");
  }

  // 練習の回数の1行（0回なら数字を出さずに、何をすれば出るかを書く）
  function runsLine(x) {
    if (!x || !x.runs.length) return `<p class="sub" data-t="progruns">まだ練習していません。練習の結果を入れると、ここに変化が出ます。</p>`;
    const ev = x.runs.filter((y) => y.evaluated);
    const good = ev.filter((y) => y.counts_as_worked).length;
    return `<p class="sub" data-t="progruns">練習 ${lab("count", x.runs.length + "回")}・判定した ${lab("count", ev.length + "回")}・良くなった ${lab("count", good + "回")}</p>`;
  }

  async function planPart(box, rows) {
    await Practice.load().catch(() => false);
    const p = Practice.plan();
    if (!p) { box.innerHTML = `<p class="sub" data-t="noplan">進行中のプランはありません。</p>`; return; }
    const mine = (rows || []).find((x) => x.plan && x.plan.id === p.id);
    const r = await App.request("GET", `/v1/plans/${p.id}/progress`).catch(() => ({ ok: false, status: 0 }));
    if (!r.ok) { box.innerHTML = App.errorHtml({ what: "進み具合を読めませんでした", next: r.status === 0 ? "電波のあるところで開き直してください。" : ((r.body && r.body.error) || "") }); return; }
    if (r.body.plan && r.body.plan.kind === "motion") return motionPlanPart(box, p, r);
    const pl = r.body.plan, runs = r.body.runs || [];
    const [key, label] = VAL[pl.goal] || ["mean", "平均"];
    const latest = r.body.latest && r.body.latest.progress;
    const refRun = latest && latest.reference_run != null ? latest.reference_run : null;
    const pts = runs.map((x, i) => {
      const e = x.evaluation || {}, fa = e.first_a || {}, b = (e.stats && e.stats.B) || {};
      return { i, date: x.date, a: (fa.measured ?? fa.n ?? 0) >= 5 ? fa[key] : null, an: fa.measured ?? fa.n ?? 0, b: (b.n || 0) >= 5 ? b[key] : null, bn: b.n || 0,
        ev: e, fresh: x.fresh, ref: refRun != null && x.run_id === refRun };
    });
    box.innerHTML = `<article class="card" data-t="prog"><p class="label">いまのプラン <span class="chip brand">進行中</span></p><h3>${esc(Practice.planTitle(p))}</h3>
      ${latest ? `<p data-t="progplain"><b>${esc(latest.title || "")}</b> ${esc(latest.text || "")}</p>` : ""}
      ${runsLine(mine)}
      <button class="textbtn" data-t="progwhy">推移を見る</button></article>`;
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

  async function records() {
    if (!S.player) return { ok: true, rows: [] };
    const r = await App.request("GET", `/v1/players/${S.player.id}/record`).catch(() => ({ ok: false, status: 0 }));
    return r.ok ? { ok: true, rows: r.body || [] } : { ok: false, rows: [] };
  }

  // これまでのプラン（進行中のものは上のカードにあるので除く）
  function recordPart(box, got) {
    if (!got.ok) { box.innerHTML = App.errorHtml({ what: "記録を読めませんでした", next: App.netDown() ? "電波のあるところで開き直してください。" : "少し待ってから開き直してください。" }); return; }
    const rows = got.rows.filter((x) => x.plan.status !== "active");
    if (!rows.length) { box.innerHTML = `<p class="sub">これまでのプランはまだありません。</p>`; return; }
    box.innerHTML = rows.map((x) => {
      const p = x.plan, [sl, sc] = STATUS[p.status] || [p.status, "none"];
      const ev = x.runs.filter((y) => y.evaluated);
      return `<div class="card" data-t="rec"><h3>${esc(Practice.planTitle(p))} <span class="chip ${sc}">${esc(sl)}</span>${x.state_title ? ` <span class="chip none">${esc(x.state_title)}</span>` : ""}</h3>
        <p class="sub">やり方: ${p.drill_id ? esc(p.drill_id) : `自分で書いた1点「${esc(p.cue)}」`}・${lab("club", App.clubJa(p.club))}</p>
        ${runsLine(x)}
        <details class="folded"><summary>日ごとの判定</summary>${ev.map((y) => { const [rl, rc] = Practice.plainOf(y); return `<p>${lab("date", App.dateJa(y.date, false))} <span class="chip ${rc}">${esc(rl)}</span></p>`; }).join("") || "—"}</details></div>`;
    }).join("") + `<p class="caption">同じ直しで次のやり方を選ぶときは、記録のあるやり方を先に試します。</p>`;
  }

  async function render({ el, query, alive }) {
    const tab = query.tab === "records" ? "records" : "plans";
    el.innerHTML = `<div class="pagehead"><h1>経過</h1></div>
      <div class="seg" role="group" aria-label="見るもの" data-progress-tabs><a href="#/progress" ${tab === "plans" ? 'aria-current="page"' : ""}>課題ごと</a><a href="#/progress?tab=records" ${tab === "records" ? 'aria-current="page"' : ""}>記録ごと</a></div>
      <div data-body class="block"></div>`;
    const body = $("[data-body]", el);
    if (!S.player) {
      body.innerHTML = `<div class="empty" data-t="noplayer"><svg class="art" viewBox="0 0 96 96" aria-hidden="true"><path d="M14 78l22-24 16 14 30-36"/><path d="M66 32h16v16"/></svg>
        <p class="t-headline">まだ記録がありません。</p><p class="sub">記録と練習を入れると、ここに日ごとの変化が出ます。</p></div>
        <a class="btn primary block" data-primary href="#/record">最初の記録を入れる</a>`;
      return;
    }
    if (tab === "plans") {
      body.innerHTML = `<section data-part="plan"></section><section class="block" data-part="focus"></section><section class="block"><h2>これまでのプラン</h2><div data-part="rec"></div></section>`;
      // 読み込みの段（1秒で骨組み・3秒で文）は、あとから埋まる部分にもかける
      const stop = App.loading($("[data-part=plan]", body));
      const stop2 = App.loading($("[data-part=rec]", body));
      const got = await records();
      if (!alive()) return;
      await planPart($("[data-part=plan]", body), got.rows).finally(stop);
      if (!alive()) return;
      stop2();
      recordPart($("[data-part=rec]", body), got);
      await focusPart($("[data-part=focus]", body)).catch(() => {});
      return;
    }
    const stop = App.loading(body);
    const ss = await App.sessions(true).catch(() => []);
    stop();
    if (!alive()) return;
    body.innerHTML = `<ul class="navlist" data-t="sessions">${ss.map((s) => App.navItem(`#/session/${s.id}`,
      `${lab("date", App.dateJa(s.date))}${s.location ? "・" + esc(s.location) : ""}`, `${s.n_shots || 0}球${(s.clubs || []).length ? "・" + (s.clubs || []).map((c) => esc(App.clubJa(c.club))).join("・") : ""}`,
      `data-session="${s.id}"`)).join("") || `<li><p class="sub" style="padding:var(--s4)">まだ記録がありません。</p></li>`}</ul>
      ${ss.filter((s) => s.n_shots).length > 1 ? `<div class="block"><a class="btn block" href="#/progress/compare">2日を比べる</a></div>` : ""}`;
  }

  // 最初の面は言葉だけ（何が変わったか・当たる瞬間の動きで説明できるか）。数字と専門用語は「数字を見る」の中（§3 R1）
  const PLAIN_OUT = { carry: "飛んだ距離", side: "落ちた左右の位置", launch_direction: "打ち出した向き", spin_axis: "曲がり方", ball_speed: "球の速さ", launch_angle: "打ち出しの高さ" };
  function renderCompareOut(r) {
    let h = "";
    for (const [club, c] of Object.entries(r.clubs)) {
      h += `<section class="card" data-t="cmpclub"><h3>${lab("club", App.clubJa(club))} <span class="caption">前 ${lab("count", c.n[0] + "球")} → 後 ${lab("count", c.n[1] + "球")}</span></h3>`;
      if (!c.explanations.length) {
        const ins = Object.values(c.l0).some((x) => x.status === "insufficient");
        h += `<p class="sub">${ins ? "球が足りないので比べられないものがあります。" : "球筋に、はっきりした変化はありません。"}</p>`;
      } else {
        h += `<ul class="plainlist">${c.explanations.map((e) => `<li>${esc(PLAIN_OUT[e.outcome] || "球筋")}が変わりました。${e.explained_by.length ? "当たる瞬間のクラブの動きの変化で説明できます。" : "当たる瞬間の測れているものでは説明できません。"}</li>`).join("")}</ul>`;
      }
      let nums = "";
      for (const e of c.explanations) {
        const why = e.explained_by.length
          ? e.explained_by.map((x) => `<b>${esc(LABEL[x.metric] || x.metric)} ${fmt(x.metric, x.diff, true)}</b>`).join("、") + " が変わったため"
          : `当たる瞬間の測れている値では説明できません${e.unmeasured.length ? `（測れていない: ${e.unmeasured.map((m) => esc(LABEL[m] || m)).join("・")}）` : ""}`;
        nums += `<div class="finding">${esc(LABEL[e.outcome] || e.outcome)}が <b>${fmt(e.outcome, e.diff, true)}</b> 変わりました ―― ${why}。</div>`;
      }
      const changed = Object.entries(c.l1).filter(([, x]) => x.status === "changed");
      if (changed.length) nums += `<p class="caption">当たる瞬間に変わったもの: ${changed.map(([k, x]) => `${esc(LABEL[k] || k)} ${fmt(k, x.diff, true)}`).join(" / ")}</p>`;
      if (nums) h += `<details class="folded" data-t="cmpnums"><summary>数字を見る</summary>${nums}</details>`;
      h += `</section>`;
    }
    if (r.only_in_a.length || r.only_in_b.length) h += `<p class="caption">片方にしか無いクラブ: ${[...r.only_in_a, ...r.only_in_b].map((c) => esc(App.clubJa(c))).join("、")}</p>`;
    return h || `<p class="sub">共通のクラブがありません。</p>`;
  }

  // TrackMan の2日の再確認（§8.4）: 言葉と言える範囲だけを最初に出す。数字と球数の見積もりは「数字を見る」
  const VT = { better: "good", worse: "bad", same: "none", unknown: "warn" };
  async function recheckPart(box, a, b) {
    box.innerHTML = `<p class="loading-text">再確認しています…</p>`;
    const key = `golf.checkup.${a}.${b}`;
    let c = null;
    try {
      const id = App.LS.get(key);
      if (id) c = await api("GET", `/v1/checkups/${id}`).catch(() => null);
      if (!c) {
        const plan = Practice.plan();
        c = await api("POST", `/v1/players/${S.player.id}/checkups`, { baseline_session_id: a, recheck_session_id: b, plan_id: plan && plan.kind !== "motion" ? plan.id : 0 });
        App.LS.set(key, c.id);
      }
    } catch (e) { box.innerHTML = App.errOf(e, { what: "再確認できませんでした" }); return; }
    const res = (c && c.result) || {};
    const rows = res.results || [];
    if (!rows.length) { box.innerHTML = `<h2>前の日と比べた再確認</h2><p class="sub">両方の日に同じクラブの球がありません。</p>`; return; }
    box.innerHTML = `<h2>前の日と比べた再確認</h2><section class="card" data-t="checkup"><ul class="plainlist">${rows.map((x) =>
      `<li data-verdict="${esc(x.verdict || x.state || "")}"><b>${esc(x.name)}</b> <span class="chip ${VT[x.verdict] || "brand"}">${esc(x.word)}</span><br><span class="sub">${esc(x.text)}</span></li>`).join("")}</ul>
      ${(res.notes || []).map((n) => `<p class="note">${esc(n)}</p>`).join("")}
      <details class="folded" data-t="checkupnums"><summary>数字を見る</summary><ul class="parts">${rows.map((x) => `<li>${esc(LABEL[x.metric] || x.metric)}（${esc(App.clubJa(x.club))}）: ${x.source === "plan" ? "プランの判定をそのまま出しています" :
        `球 ${x.n ? x.n.join("・") : "—"}${x.need_n ? `・「良くなった」を見分けるには1日 約${x.need_n.better}球、「変わらない」と言うには約${x.need_n.same}球` : ""}${x.diff != null ? `・差 ${fmt(x.metric, x.diff, true)}` : ""}`}</li>`).join("")}</ul>
      <p class="caption">「変わらない」は、差があっても意味のある大きさより小さいと言える球数を打てたときだけ言います。項目は最初に作った時点で固定しています。</p></details></section>`;
  }

  // 別の日の動画の比較（§8.5）: 同じ向き・同じカタログの版のときだけ
  async function vidPart(box, a, b) {
    const r = await App.request("GET", `/v1/checks/compare?a=${a}&b=${b}`).catch(() => ({ ok: false }));
    if (!r.ok) { box.innerHTML = ""; return; }
    const c = r.body;
    if (!c.comparable) { box.innerHTML = c.reason === "no_video" ? "" : `<h2>動画</h2><p class="sub" data-t="vidreason">${esc(c.text)}</p>`; return; }
    let cat = null;
    try { cat = await Video.catalog(); } catch { /* id のまま */ }
    const name = (id) => { const it = cat && (cat.items || []).find((x) => x.id === id); return it ? it.title : id; };
    // 最初に出すのは、どちらかの日に範囲の外が多かった項目だけ。ほかは畳む（長い一覧で読ませない）
    const out = (x) => x.a.out_range > x.a.in_range || x.b.out_range > x.b.in_range;
    const li = (x) => `<li><b>${esc(name(x.item_id))}</b><br><span class="sub">${esc(x.label)}</span></li>`;
    const main = (c.items || []).filter(out), rest = (c.items || []).filter((x) => !out(x));
    box.innerHTML = `<h2>動画</h2><section class="card" data-t="vidcmp">${main.length ? `<ul class="plainlist">${main.map(li).join("")}</ul>` : `<p class="sub">どちらの日も、範囲の外が多い項目はありません。</p>`}
      ${rest.length ? `<details class="folded"><summary>どちらの日も範囲の中が多い項目（${lab("count", rest.length + "件")}）</summary><ul class="plainlist">${rest.map(li).join("")}</ul></details>` : ""}
      <p class="caption">十球テストの合格・不合格のほかに、確率や「直った」は言いません。</p></section>`;
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
        $("[data-out]", body).innerHTML = renderCompareOut(r) + `<section class="block" data-t="recheck"></section><section class="block" data-t="vidcmp"></section>`;
        history.replaceState(null, "", `#/progress/compare?a=${a.value}&b=${b.value}`);
        recheckPart($("[data-t=recheck]", body), Number(a.value), Number(b.value));
        vidPart($("[data-t=vidcmp]", body), Number(a.value), Number(b.value));
      } catch (e) { $("[data-err]", body).innerHTML = App.errOf(e, { what: "比べられませんでした" }); }
    });
    if (query.a && query.b) $("[data-go]", body).click();
  }

  App.route("/progress", render, { tab: "progress" });
  App.route("/progress/compare", renderCompare, { tab: "progress" });
  return {};
})();
