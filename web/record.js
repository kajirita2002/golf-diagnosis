"use strict";
/*
  記録（docs/DESIGN_v2.md §10 R）。今日の記録の置き場。取り込むと同時にその日の記録（セッション）を作る
  （前は先に「新しいセッション」を作らないと取り込めなかった）。
  - 入れ方: TrackMan のスクショ（先頭）・表の貼り付け・CSV・レポートのリンク（表で開く）。
  - スクショは読む → 表を確かめる（検算）→ 直す → 取り込む。見出しの無い続きの画像は、見出しのある表へつなげる。
  - 同じ日にもう記録があれば、その記録に足すか、別の記録として入れるかを選べる
    （練習のプランは、きっかけの診断の記録とは別の記録に入れる約束があるため）。
    動いているプランのきっかけの記録がその日にあれば、既定は「新しい記録として入れる」にする
    （練習の球が診断の記録に混ざると診断が変わり、しかもその記録は練習として記録できない）。
  - 使う人がまだいなければ、最初の取り込みで作る。利き手は、ようこそ・設定で選んだもの（App.pendingHand）。
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
    const [all, trig] = S.player ? await Promise.all([App.sessions(true).catch(() => []), triggerSession()]) : [[], null];
    stop();
    if (!alive()) return;
    const onDay = all.filter((s) => s.date === date);
    // 既定の入れ先: その日のいちばん新しい記録。ただしプランのきっかけの記録なら新しい記録にする
    const def = onDay.find((s) => s.id !== trig);
    let target = query.target && onDay.some((s) => String(s.id) === query.target) ? Number(query.target) : (query.new ? "new" : (def ? def.id : "new"));
    const h = S.health || {};
    const warn = [];
    if (h.db_persistent === false) warn.push("保存先が一時的です。再起動すると入れた記録が消えます（サーバーの設定 DB_PATH が未設定）。");
    const canRead = h.anthropic_key !== false; // スクショを読めるか（分からなければ読める側で出す）
    const other = `
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
          <div data-tm-err></div>`;
    // スクショを読めないときは、使える「表の貼り付け」を主役にし、使えないスクショの枠は出さない
    const shotPart = canRead ? `
        <p class="sub">クラブごとの表を、一番下の平均（Average）の行まで入れてスクショします。</p>
        <label class="drop" data-drop tabindex="-1">${icon("camera")}
          <input type="file" data-shot-file accept="image/*" multiple class="visually-hidden">
          <span><b>写真・スクショを選ぶ</b><span class="caption">何枚でも。パソコンなら貼り付け（Ctrl+V）やドロップも</span></span></label>
        <p class="sub" data-shot-msg role="status"></p>
        <div data-shot-out></div>`
      : `<p class="note" data-noshot>いまはスクショを読めません。表の貼り付けか CSV で入れてください。</p>
        <input type="file" data-shot-file accept="image/*" multiple hidden><div data-drop hidden></div><p data-shot-msg hidden></p><div data-shot-out></div>`;
    body.innerHTML = `
      ${warn.map((w) => `<div class="note warn">${esc(w)}</div>`).join("")}
      <div class="daterow"><span class="t-headline" data-record-date>${lab("date", App.dateJa(date))}</span>
        <label><span class="visually-hidden">日付を変える</span><input type="date" data-date value="${esc(date)}" aria-label="日付を変える"></label></div>
      <div class="field" data-target-wrap ${onDay.length ? "" : "hidden"}><span>入れる先</span>
        <select data-target aria-label="入れる先">${onDay.map((s) => `<option value="${s.id}">この日の記録${s.location ? "・" + esc(s.location) : ""}（${s.n_shots || 0}球）${s.id === trig ? "・プランのきっかけ" : ""}</option>`).join("")}<option value="new">新しい記録として入れる（同じ日）</option></select>
        <p class="caption" data-trig-note hidden>プランのきっかけの診断の記録です。練習の球はここに入れず、新しい記録として入れてください（混ぜると診断が変わり、練習としても記録できません）。</p></div>
      <label class="field" data-loc-wrap><span>場所（任意）</span><input data-loc placeholder="例: 練習場" autocomplete="off"></label>
      <p class="caption" data-loc-fixed hidden></p>
      <p class="dgbody" data-record-howto>一回の練習で、<b>球のデータ（TrackMan）</b>と<b>スイングの動画</b>をセットで入れます。計測器で分かるのは当たる瞬間のクラブの様子までで、体のどの動きが原因かは動画で確かめます。</p>
      <section class="card block" aria-labelledby="h-tm">
        <h2 id="h-tm"><span class="dgno" aria-hidden="true">①</span> 球のデータ（${canRead ? "TrackMan のスクショ" : "TrackMan の表"}）</h2>
        ${shotPart}
        ${canRead ? `<details class="block" data-other><summary class="textbtn">ほかの入れ方（表の貼り付け・CSV・レポートのリンク）</summary>${other}</details>` : `<div data-other>${other}</div>`}

        <div data-import-msg role="status"></div>
      </section>
      <section class="card block" aria-labelledby="h-vid">
        <h2 id="h-vid"><span class="dgno" aria-hidden="true">②</span> スイングの動画（後ろから・できれば正面からも）</h2>
        <p class="sub">同じ練習の数スイングを撮ります。原因の動き（腰・手元・手首など）を、ガイドの形と比べて確かめます。</p>
        <a class="btn block vidbtn" data-video href="#/video/${esc(date)}">${icon("video")}動画を入れる</a>
        <p class="caption">動画は送りません（送るのは選んだコマの小さな写真と体の点だけ）。</p>
      </section>
      <section class="block" aria-labelledby="h-day"><h2 id="h-day">この日に入っているもの</h2><div data-day></div></section>
      <div class="block" data-primary-wrap></div>
      <ul class="navlist block">${App.navItem("#/progress?tab=records", "過去の記録", "日ごとの記録と診断")}</ul>`;
    active = { el, date, fromPractice, trig, target: () => target, setTarget: (v) => { target = v; showTarget(el); } };
    showTarget(el);

    $("[data-date]", el).addEventListener("change", (ev) => {
      const v = ev.target.value;
      if (/^\d{4}-\d{2}-\d{2}$/.test(v)) App.go(`/record/${v}${fromPractice ? "?from=practice" : ""}`);
    });
    const tsel = $("[data-target]", el);
    if (tsel) {
      tsel.value = String(target);
      tsel.addEventListener("change", () => { active.setTarget(tsel.value === "new" ? "new" : Number(tsel.value)); });
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

  // 動いているプランのきっかけの記録（無ければ null）。一覧だけを読む軽い口（解説は作らない）
  async function triggerSession() {
    try {
      const ps = await api("GET", `/v1/players/${S.player.id}/plans`);
      const p = (ps || []).find((x) => x.status === "active");
      return (p && p.trigger && p.trigger.session_id) || null;
    } catch { return null; }
  }

  // 入れる先に合わせて、場所の欄と注意書きを出し分ける。
  // 場所は新しい記録を作るときだけ入る（既にある記録の場所を直す口は無いので、直せるように見せない）
  function showTarget(el) {
    const a = active;
    if (!a || a.el !== el) return;
    const t = a.target();
    const all = (S.cache.sessions || []).filter((s) => s.date === a.date);
    const cur = all.find((s) => s.id === t);
    $("[data-loc-wrap]", el).hidden = t !== "new";
    const fixed = $("[data-loc-fixed]", el);
    fixed.hidden = t === "new";
    fixed.textContent = cur ? `場所: ${cur.location || "（書いていません）"}（場所は新しい記録を作るときに入れます）` : "";
    const note = $("[data-trig-note]", el);
    if (note) note.hidden = !(a.trig && t === a.trig);
    // 動画も同じ入れ先へ（新しい記録なら、動画を送るときに作る）
    const vid = $("[data-video]", el);
    if (vid) vid.setAttribute("href", `#/video/${a.date}${t !== "new" ? `?session=${t}` : ""}`);
  }

  function renderDay(el, onDay) {
    const box = $("[data-day]", el);
    const withShots = onDay.filter((s) => s.n_shots);
    box.innerHTML = withShots.length ? `<ul class="navlist">${withShots.map((s) => App.navItem(`#/session/${s.id}`,
      `${s.location ? esc(s.location) + "・" : ""}${lab("count", s.n_shots + "球")}`,
      (s.clubs || []).map((c) => `${esc(App.clubJa(c.club))} ${c.n}`).join("・"), `data-day-session="${s.id}"`)).join("")}</ul>`
      : `<p class="sub" data-day-empty>まだ何も入っていません。</p>`;
    // 動画のスイング（記録ごとに数を出し、チェックへ）
    Promise.all(onDay.map((s) => api("GET", `/v1/sessions/${s.id}/swings`).then((ws) => [s, ws]).catch(() => [s, []]))).then((rows) => {
      if (!box.isConnected) return;
      const vids = rows.filter(([, ws]) => ws.length);
      if (!vids.length) {
        // 球だけの日: 原因の動きは動画が無いと確かめられない（診断は「可能性」のまま）
        if (withShots.length) box.insertAdjacentHTML("beforeend", `<p class="note" data-day-novideo>動画はまだありません。原因の動きを確かめるには、②で動画を入れます（入れなくても診断は出ますが、体の動きは「可能性」のままです）。</p>`);
        return;
      }
      const empty = $("[data-day-empty]", box);
      if (empty) empty.remove();
      const views = (ws) => [...new Set(ws.map((w) => (w.view === "fo" ? "正面から" : "後ろから")))].join("と");
      box.insertAdjacentHTML("beforeend", `<ul class="navlist" style="margin-top:var(--s2)">${vids.map(([s, ws]) => App.navItem(`#/session/${s.id}/check`,
        `動画 ${views(ws)} ${lab("count", ws.length + "本")}`, "チェック（ガイドの基準で動きを見る）", `data-day-video="${s.id}"`)).join("")}</ul>`);
    });
    const pw = $("[data-primary-wrap]", el);
    const a = active;
    if (a && a.fromPractice) pw.innerHTML = `<a class="btn primary block" data-primary href="#/practice/result">練習の結果へ戻る</a>`;
    else if (withShots.length) pw.innerHTML = `<a class="btn primary block" data-primary href="#/session/${withShots[0].id}">診断レポートを見る</a>`;
    else pw.innerHTML = "";
  }

  // 取り込み先の記録（無ければこの日の記録を作る）
  async function ensureTarget(el) {
    const a = active;
    if (a.target() !== "new") {
      if (a.trig && a.target() === a.trig && !(await App.ask({ title: "プランのきっかけの記録です", text: "練習の球をここに入れると、診断が変わります。この記録は練習としても記録できません。練習の球なら、新しい記録として入れてください。", ok: "それでもこの記録に入れる", cancel: "やめる" }))) {
        const err = new Error("入れるのをやめました。入れる先を「新しい記録として入れる」にしてから、もう一度押してください。");
        err.cancelled = true;
        throw err;
      }
      return a.target();
    }
    const p = await App.ensurePlayer();
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
      tsel.innerHTML = onDay.map((s) => `<option value="${s.id}">この日の記録${s.location ? "・" + esc(s.location) : ""}（${s.n_shots || 0}球）${s.id === active.trig ? "・プランのきっかけ" : ""}</option>`).join("") + `<option value="new">新しい記録として入れる（同じ日）</option>`;
      tsel.value = String(sid);
      $("[data-target-wrap]", el).hidden = false;
    }
    showTarget(el);
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
    } catch (e) { msg.innerHTML = e.cancelled ? `<p class="fielderr">${esc(e.message)}</p>` : App.errOf(e, { what: "取り込めませんでした", saved: "貼り付けた表はそのまま残してあります。", next: "表の見出しの行から貼り付けたか、クラブ名を入れたかを確かめてください。" }); }
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
    } catch (e) { msg.innerHTML = e.cancelled ? `<p class="fielderr">${esc(e.message)}</p>` : App.errOf(e, { what: "CSV を取り込めませんでした", saved: "何も取り込んでいません。", next: "TrackMan から書き出した CSV か、単位の選び方を確かめてください。" }); }
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
