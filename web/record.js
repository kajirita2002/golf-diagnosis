"use strict";
/*
  記録（docs/DESIGN_v2.md §10 R）。今日の記録の置き場。取り込むと同時にその日の記録（セッション）を作る
  （前は先に「新しいセッション」を作らないと取り込めなかった）。
  - 入れ方: TrackMan のスクショ（先頭）・表の貼り付け・CSV・レポートのリンク（表で開く）。
  - スクショは読む → 表を確かめる（検算）→ 直す → 取り込む。見出しの無い続きの画像は、見出しのある表へつなげる。
  - 同じ日にもう記録があれば、その記録に足すか、別の記録として入れるかを選べる
    （練習のプランは、きっかけの診断の記録とは別の記録に入れる約束があるため）。
  - 失敗はその場に出す（alert を使わない）。検算が合わないまま取り込むときはシートで確かめる。
*/
const Record = (() => {
  const { $, esc, icon, lab, S, api, request } = App;
  let active = null; // いま開いている記録の画面（貼り付けの受け口）

  // TrackMan の表示名（"6Iron"）を、このアプリのクラブ名（"6 Iron"）にそろえる
  const clubName = (c) => (c || "").replace(/^(\d+)\s*(Iron|Wood|Hybrid)$/i, "$1 $2");

  function checkHtml(c) {
    if (!c) return "";
    const cols = (c.columns || []).map((x) => x.status === "ok" ? `<span class="chip good">✓ ${esc(x.column)}</span>`
      : x.status === "mismatch" ? `<span class="chip bad" title="読み取り ${esc(x.computed)} / 画面 ${esc(x.shown)}">★ ${esc(x.column)}</span>`
        : `<span class="chip none">${esc(x.column)}</span>`).join(" ");
    const head = c.ok ? `<span class="chip good">✓ 検算OK（${c.n_rows}球・Average と一致）</span>` : `<span class="chip bad">× 要確認</span>`;
    return `<div>${head}</div><div class="row" style="margin-top:var(--s1)">${cols}</div>${(c.problems || []).map((p) => `<p class="fielderr">・${esc(p)}</p>`).join("")}`;
  }

  // ---- 読み取った表（TSV）の分解と結合 ----
  // 1行目が見出し、2行目が単位（**位置で取る**。単位が写っていないと空行になる）。
  function splitTsv(tsv) {
    const lines = tsv.replace(/\r/g, "").replace(/^\n+|\n+$/g, "").split("\n");
    const cells = (l) => (l || "").split("\t");
    const rest = lines.slice(2).filter((l) => l.trim()).map(cells);
    return {
      head: cells(lines[0]), units: cells(lines[1]),
      body: rest.filter((r) => r[0] !== "Average" && r[0] !== "Consistency"),
      avg: rest.find((r) => r[0] === "Average") || null,
      cons: rest.find((r) => r[0] === "Consistency") || null,
    };
  }
  const joinTsv = (t) => [t.head, t.units, ...t.body, t.avg, t.cons].filter(Boolean).map((r) => r.join("\t")).join("\n") + "\n";
  const headless = (tsv) => splitTsv(tsv).head.length <= 1;
  function bodyWidth(tsv) {
    const n = {};
    for (const r of splitTsv(tsv).body) n[r.length - 1] = (n[r.length - 1] || 0) + 1;
    return +Object.keys(n).sort((a, b) => n[b] - n[a])[0] || 0;
  }
  // 見出しのある表に、見出しの無い続きをつなげる。同じ番号の行は前のほうを残し、番号順に並べ直す
  function mergeTsv(base, cont) {
    const a = splitTsv(base), b = splitTsv(cont);
    const seen = new Set(a.body.map((r) => r[0]));
    a.body = [...a.body, ...b.body.filter((r) => !seen.has(r[0]))];
    const no = (r) => parseInt(r[0], 10);
    if (a.body.every((r) => !isNaN(no(r)))) a.body.sort((x, y) => no(x) - no(y));
    a.avg = a.avg || b.avg; a.cons = a.cons || b.cons;
    return joinTsv(a);
  }

  // ---- 画像 ----
  // そのまま送れる形式。ほか（HEIC など）と、大きすぎる写真は縮めて JPEG にする（サーバーの上限は 5MB）
  const SEND_AS_IS = ["image/png", "image/jpeg", "image/webp", "image/gif"];
  const MAX_SEND_BYTES = 4.5 * 1024 * 1024, MAX_EDGE = 2000;
  const isImage = (f) => f.type.startsWith("image/") || /\.(png|jpe?g|webp|gif|heic|heif)$/i.test(f.name || "");
  async function decodeImage(f) {
    if (window.createImageBitmap) {
      try { return await createImageBitmap(f); } catch { /* 下の img で試す */ }
    }
    const url = URL.createObjectURL(f);
    try {
      const img = new Image();
      await new Promise((ok, ng) => { img.onload = ok; img.onerror = ng; img.src = url; });
      return img;
    } finally { URL.revokeObjectURL(url); }
  }
  async function prepareImage(f) {
    if (SEND_AS_IS.includes(f.type) && f.size <= MAX_SEND_BYTES) {
      const bmp = await decodeImage(f).catch(() => null);
      if (!bmp || Math.max(bmp.width, bmp.height) <= MAX_EDGE * 1.5) return f;
    }
    let src;
    try { src = await decodeImage(f); } catch {
      throw new Error("この形式の画像はこのブラウザで読めません。スクショ（PNG・JPEG）で撮り直してください");
    }
    const scale = Math.min(1, MAX_EDGE / Math.max(src.width, src.height));
    const c = document.createElement("canvas");
    c.width = Math.round(src.width * scale); c.height = Math.round(src.height * scale);
    c.getContext("2d").drawImage(src, 0, 0, c.width, c.height);
    const blob = await new Promise((ok) => c.toBlob(ok, "image/jpeg", 0.9));
    if (!blob) throw new Error("画像を変換できませんでした");
    return new File([blob], (f.name || "photo").replace(/\.[^.]+$/, "") + ".jpg", { type: "image/jpeg" });
  }

  // ---- 画面 ----
  async function render({ el, params, query, alive }) {
    const date = /^\d{4}-\d{2}-\d{2}$/.test(params.date || "") ? params.date : App.localDate();
    const fromPractice = query.from === "practice";
    el.innerHTML = `<div class="pagehead"><h1>記録</h1></div><div data-body></div>`;
    const body = $("[data-body]", el);
    const stop = App.loading(body);
    const all = S.player ? await App.sessions(true).catch(() => []) : [];
    stop();
    if (!alive()) return;
    const onDay = all.filter((s) => s.date === date);
    let target = query.target && onDay.some((s) => String(s.id) === query.target) ? Number(query.target) : (query.new ? "new" : (onDay[0] ? onDay[0].id : "new"));
    const h = S.health || {};
    const warn = [];
    if (h.db_persistent === false) warn.push("保存先が一時的です。再起動すると入れた記録が消えます（サーバーの設定 DB_PATH が未設定）。");
    if (h.anthropic_key === false) warn.push("スクショの読み取りは使えません（サーバーに Claude の API キーがありません）。表の貼り付けと CSV は使えます。");
    body.innerHTML = `
      ${warn.map((w) => `<div class="note warn">${esc(w)}</div>`).join("")}
      <div class="row"><span class="t-headline" data-record-date>${lab("date", App.dateJa(date))}</span>
        <label class="grow1" style="text-align:right"><span class="visually-hidden">日付を変える</span><input type="date" data-date value="${esc(date)}" aria-label="日付を変える"></label></div>
      <label class="field"><span>場所（任意）</span><input data-loc placeholder="例: 練習場" autocomplete="off"></label>
      <div class="field" data-target-wrap ${onDay.length ? "" : "hidden"}><span>入れる先</span>
        <select data-target aria-label="入れる先">${onDay.map((s) => `<option value="${s.id}">この日の記録${s.location ? "・" + esc(s.location) : ""}（${s.n_shots || 0}球）</option>`).join("")}<option value="new">新しい記録として入れる（同じ日）</option></select></div>
      <section class="card block" aria-labelledby="h-tm">
        <h2 id="h-tm">TrackMan のスクショ</h2>
        <p class="sub">クラブごとの表を、Average の行まで入れてスクショします。</p>
        <label class="drop" data-drop tabindex="-1">${icon("camera")}
          <input type="file" data-shot-file accept="image/*" multiple class="visually-hidden">
          <span><b>写真・スクショを選ぶ</b><span class="caption">何枚でも。パソコンなら貼り付け（Ctrl+V）やドロップも</span></span></label>
        <p class="sub" data-shot-msg role="status"></p>
        <div data-shot-out></div>
        <details class="block" data-other><summary class="textbtn">ほかの入れ方（表の貼り付け・CSV・レポートのリンク）</summary>
          <label class="field"><span>単位の書いていない列</span><select data-units><option value="imperial">mph・yd・in</option><option value="metric">m/s・m・cm</option></select></label>
          <h3>表を貼り付ける</h3>
          <p class="sub">TrackMan の画面の表の見出し（Club Speed など）から最後の行までを選んでコピーし、貼り付けます。表にクラブの列が無いので、クラブ名を入れてください。</p>
          <textarea data-paste rows="6" placeholder="ここに貼り付け" aria-label="表の貼り付け"></textarea>
          <div class="row" style="margin-top:var(--s2)"><input data-paste-club placeholder="クラブ（例: 6 Iron）" aria-label="クラブ"><button class="btn" data-paste-go>貼り付けたものを取り込む</button></div>
          <h3 style="margin-top:var(--s5)">CSV を入れる</h3>
          <div class="row"><label class="btn">${icon("upload")}<input type="file" data-csv accept=".csv,text/csv,text/plain,text/tab-separated-values,image/*" class="visually-hidden">CSV を選ぶ</label>
            <span class="grow1 caption" data-csv-name>まだ選んでいません</span><button class="btn" data-csv-go>CSV を取り込む</button></div>
          <h3 style="margin-top:var(--s5)">レポートのリンクから表を開く</h3>
          <p class="sub">TrackMan のレポートの URL を貼ると、10項目の表の画面で開きます。そこでスクショします。</p>
          <div class="row"><input type="url" data-tm-url placeholder="https://web-dynamic-reports.trackmangolf.com/?a=..." class="grow1" aria-label="レポートの URL"><button class="btn" data-tm-open>表で開く</button></div>
          <div data-tm-err></div>
        </details>
        <div data-import-msg role="status"></div>
      </section>
      <section class="block" aria-labelledby="h-day"><h2 id="h-day">この日に入っているもの</h2><div data-day></div></section>
      <div class="block" data-primary-wrap></div>
      <ul class="navlist block">${App.navItem("#/progress?tab=records", "過去の記録", "日ごとの記録と診断")}</ul>`;
    active = { el, date, fromPractice, target: () => target, setTarget: (v) => { target = v; } };
    const loc = $("[data-loc]", el);
    const cur = onDay.find((s) => s.id === target);
    if (cur && cur.location) loc.value = cur.location;

    $("[data-date]", el).addEventListener("change", (ev) => {
      const v = ev.target.value;
      if (/^\d{4}-\d{2}-\d{2}$/.test(v)) App.go(`/record/${v}${fromPractice ? "?from=practice" : ""}`);
    });
    const tsel = $("[data-target]", el);
    if (tsel) {
      tsel.value = String(target);
      tsel.addEventListener("change", () => { target = tsel.value === "new" ? "new" : Number(tsel.value); });
    }
    renderDay(el, onDay);

    // スクショ
    $("[data-shot-file]", el).addEventListener("change", (ev) => { readShots(el, ev.target.files); ev.target.value = ""; });
    const drop = $("[data-drop]", el);
    drop.addEventListener("dragover", (ev) => { ev.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (ev) => { ev.preventDefault(); drop.classList.remove("over"); readShots(el, ev.dataTransfer.files); });
    // CSV の欄で画像を選んだら、スクショとして読む（押し間違えても迷わないように）
    $("[data-csv]", el).addEventListener("change", (ev) => {
      const files = [...ev.target.files];
      $("[data-csv-name]", el).textContent = files[0] ? files[0].name : "まだ選んでいません";
      if (files.length && files.every(isImage)) {
        ev.target.value = "";
        $("[data-csv-name]", el).textContent = "画像だったので、スクショとして読みます";
        drop.scrollIntoView({ behavior: "smooth", block: "start" });
        readShots(el, files);
      }
    });
    $("[data-csv-go]", el).addEventListener("click", () => importCsv(el));
    $("[data-paste-go]", el).addEventListener("click", () => importPaste(el));
    $("[data-tm-open]", el).addEventListener("click", async () => {
      const box = $("[data-tm-err]", el);
      box.innerHTML = "";
      try {
        const r = await api("GET", `/v1/trackman/report-link?url=${encodeURIComponent($("[data-tm-url]", el).value)}`);
        window.open(r.url, "_blank", "noopener");
      } catch (e) { box.innerHTML = App.errOf(e, { what: "レポートを開けませんでした", next: "URL が TrackMan のレポートのものか確かめてください。" }); }
    });
  }

  function renderDay(el, onDay) {
    const box = $("[data-day]", el);
    const withShots = onDay.filter((s) => s.n_shots);
    box.innerHTML = withShots.length ? `<ul class="navlist">${withShots.map((s) => App.navItem(`#/session/${s.id}`,
      `${s.location ? esc(s.location) + "・" : ""}${lab("count", s.n_shots + "球")}`,
      (s.clubs || []).map((c) => `${esc(App.clubJa(c.club))} ${c.n}`).join("・"), `data-day-session="${s.id}"`)).join("")}</ul>`
      : `<p class="sub">まだ何も入っていません。</p>`;
    const pw = $("[data-primary-wrap]", el);
    const a = active;
    if (a && a.fromPractice) pw.innerHTML = `<a class="btn primary block" data-primary href="#/practice/result">練習の結果へ戻る</a>`;
    else if (withShots.length) pw.innerHTML = `<a class="btn primary block" data-primary href="#/session/${withShots[0].id}">診断を見る</a>`;
    else pw.innerHTML = "";
  }

  // 取り込み先の記録（無ければこの日の記録を作る）
  async function ensureTarget(el) {
    const a = active;
    if (a.target() !== "new") return a.target();
    const p = await App.ensurePlayer("R");
    const loc = $("[data-loc]", el).value.trim();
    const s = await api("POST", "/v1/sessions", { player_id: p.id, date: a.date, location: loc });
    a.setTarget(s.id);
    return s.id;
  }

  async function afterImport(el, sid, r) {
    App.invalidate(sid);
    const msg = `${r.imported}球を取り込みました（#${r.seq_from}〜${r.seq_to}・飛ばした行 ${r.skipped}）`
      + ((r.warnings || []).length ? ` ⚠︎ ${r.warnings.join(" / ")}` : "")
      + ((r.ignored || []).length ? ` ／ 読まなかった列: ${r.ignored.join("、")}` : "");
    $("[data-import-msg]", el).innerHTML = `<div class="note good" data-imported>${esc(msg)}</div>`;
    App.say(msg);
    const all = await App.sessions(true).catch(() => []);
    const onDay = all.filter((s) => s.date === active.date);
    const tsel = $("[data-target]", el);
    if (tsel) {
      tsel.innerHTML = onDay.map((s) => `<option value="${s.id}">この日の記録${s.location ? "・" + esc(s.location) : ""}（${s.n_shots || 0}球）</option>`).join("") + `<option value="new">新しい記録として入れる（同じ日）</option>`;
      tsel.value = String(sid);
      $("[data-target-wrap]", el).hidden = false;
    }
    renderDay(el, onDay);
  }

  async function importText(el, text, club, units) {
    const sid = await ensureTarget(el);
    const q = new URLSearchParams({ units, club });
    let r;
    try { r = await request("POST", `/v1/sessions/${sid}/import?${q}`, { raw: text }); } catch (e) {
      const err = new Error("届きません"); err.offline = true; err.detail = String(e); throw err;
    }
    if (!r.ok) throw new Error((r.body && r.body.error) || r.text || r.status);
    await afterImport(el, sid, r.body);
    return r.body;
  }

  async function importPaste(el) {
    const msg = $("[data-import-msg]", el);
    msg.innerHTML = "";
    const text = $("[data-paste]", el).value;
    if (!text.trim()) { msg.innerHTML = `<p class="fielderr">表を貼り付けてください</p>`; return; }
    try {
      await importText(el, text, $("[data-paste-club]", el).value.trim(), $("[data-units]", el).value);
      $("[data-paste]", el).value = "";
    } catch (e) { msg.innerHTML = App.errOf(e, { what: "取り込めませんでした", saved: "貼り付けた表はそのまま残してあります。", next: "表の見出しの行から貼り付けたか、クラブ名を入れたかを確かめてください。" }); }
  }

  async function importCsv(el) {
    const msg = $("[data-import-msg]", el);
    msg.innerHTML = "";
    const f = $("[data-csv]", el).files[0];
    if (!f) { msg.innerHTML = `<p class="fielderr">CSV を選んでください</p>`; return; }
    try {
      const sid = await ensureTarget(el);
      const fd = new FormData();
      fd.append("file", f);
      const r = await api("POST", `/v1/sessions/${sid}/import?units=${$("[data-units]", el).value}`, fd);
      $("[data-csv]", el).value = "";
      $("[data-csv-name]", el).textContent = "まだ選んでいません";
      await afterImport(el, sid, r);
    } catch (e) { msg.innerHTML = App.errOf(e, { what: "CSV を取り込めませんでした", saved: "何も取り込んでいません。", next: "TrackMan から書き出した CSV か、単位の選び方を確かめてください。" }); }
  }

  // 見出しの無い続きのカードを、見出しのある表（取り込む前のもの）につなげる
  async function joinContinuations(el) {
    const cards = [...el.querySelectorAll(".shotcard")];
    for (const [i, c] of cards.entries()) {
      if (!c.isConnected || !headless(c.tsv())) continue;
      const w = bodyWidth(c.tsv());
      const fits = (x) => x.isConnected && x !== c && !x.imported && !headless(x.tsv()) && splitTsv(x.tsv()).head.length - 1 === w;
      const target = cards.slice(0, i).reverse().find(fits) || cards.slice(i + 1).find(fits);
      if (!target) continue;
      const labels = splitTsv(c.tsv()).body.map((r) => r[0]);
      target.setTsv(mergeTsv(target.tsv(), c.tsv()));
      target.note(`続きの画像（${labels[0]}〜${labels[labels.length - 1]}）をこの表につなげました`);
      c.remove();
      await target.recheck().catch(() => {});
    }
  }

  function shotCard(el, t) {
    const box = document.createElement("div");
    box.className = "shotcard";
    box.innerHTML = `
      <div class="row"><b>表</b><input data-f="club" placeholder="クラブ（例: 6 Iron）" aria-label="クラブ"><span class="caption">読み取ったクラブ: ${esc(t.club || "—")}</span></div>
      <p class="caption" data-f="note"></p>
      <div data-f="check" style="margin-top:var(--s1)">${checkHtml(t.check)}</div>
      <details ${t.check && t.check.ok ? "" : "open"}><summary>読み取った表（直せます）</summary>
        <textarea data-f="tsv" rows="8" aria-label="読み取った表"></textarea>
        <button class="btn small" data-act="recheck">もう一度検算</button>
      </details>
      <div class="row" style="margin-top:var(--s2)"><button class="btn primary" data-act="import">この表を取り込む</button><button class="btn" data-act="drop">消す（取り込まない）</button></div>
      <div data-f="err"></div>`;
    const q = (f) => box.querySelector(`[data-f=${f}]`);
    q("club").value = clubName(t.club);
    q("tsv").value = t.tsv;
    box.tsv = () => q("tsv").value;
    box.setTsv = (v) => { q("tsv").value = v; };
    box.note = (m) => { q("note").textContent = m; };
    box.recheck = async () => {
      const c = await api("POST", "/v1/screenshot/verify", { tsv: q("tsv").value });
      q("check").innerHTML = checkHtml(c);
      q("check").nextElementSibling.open = !c.ok;
      return c;
    };
    box.addEventListener("click", async (ev) => {
      const b = ev.target.closest("button[data-act]");
      if (!b) return;
      q("err").innerHTML = "";
      if (b.dataset.act === "drop") { box.remove(); return; }
      try {
        if (b.dataset.act === "recheck") { await box.recheck(); return; }
        if (headless(q("tsv").value)) throw new Error("見出し（列の名前）が無いので取り込めません。見出しが写った画像も読み込むと、自動でつながります");
        if (!q("club").value.trim()) throw new Error("クラブを入れてください");
        const c = await api("POST", "/v1/screenshot/verify", { tsv: q("tsv").value });
        q("check").innerHTML = checkHtml(c);
        if (!c.ok && !(await App.ask({ title: "検算が合っていません", text: "このまま取り込みますか？ 読み違いがあると診断がずれます。表を直してから「もう一度検算」を押すこともできます。", ok: "このまま取り込む", cancel: "表を直す" }))) return;
        const r = await importText(el, q("tsv").value, q("club").value.trim(), "metric");
        b.disabled = true; b.textContent = `取り込みました（#${r.seq_from}〜${r.seq_to}）`;
        box.imported = true;
        box.querySelector("[data-act=drop]").textContent = "閉じる";
      } catch (e) {
        q("err").innerHTML = e.offline ? App.errOf(e) : `<p class="fielderr">${esc(e.message)}</p>`;
      }
    });
    return box;
  }

  async function readShots(el, files) {
    const imgs = [...files].filter(isImage);
    if (!imgs.length) return;
    const msg = $("[data-shot-msg]", el), out = $("[data-shot-out]", el);
    let cost = 0;
    for (const [i, f] of imgs.entries()) {
      msg.textContent = `読み取り中… ${i + 1} / ${imgs.length}（1枚に数十秒かかります）`;
      try {
        const img = await prepareImage(f);
        const fd = new FormData();
        fd.append("image", img, img.name || "screenshot.png");
        const r = await api("POST", "/v1/screenshot", fd);
        cost += (r.usage && r.usage.cost_usd) || 0;
        if (!r.tables.length) out.insertAdjacentHTML("beforeend", `<p class="fielderr">${esc(f.name || "画像")}: 表が見つかりませんでした</p>`);
        for (const t of r.tables) out.appendChild(shotCard(el, t));
      } catch (e) {
        out.insertAdjacentHTML("beforeend", `<p class="fielderr">${esc(f.name || "画像")}: ${esc(e.message)}</p>`);
      }
    }
    await joinContinuations(el);
    msg.textContent = `${imgs.length}枚を読み取りました（Claude の利用料 約 $${cost.toFixed(3)}）。確かめてから取り込んでください。`;
  }

  // 画像の貼り付け（文字の貼り付け・URL は邪魔しない）。記録の画面を開いているときだけ受ける
  document.addEventListener("paste", (ev) => {
    if (!active || !active.el.isConnected) return;
    const files = [...(ev.clipboardData ? ev.clipboardData.files : [])].filter((f) => f.type.startsWith("image/"));
    if (files.length && !ev.target.closest("textarea,input")) { ev.preventDefault(); readShots(active.el, files); }
  });

  App.route("/record", render, { tab: "record" });
  App.route("/record/:date", render, { tab: "record" });
  return {};
})();
