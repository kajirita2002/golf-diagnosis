/*
  圏外でも画面を開き直せるようにする Service Worker（docs/DESIGN_coaching.md §8.7・docs/DESIGN_v2.md §12）。
  - 置くのは画面のファイル（殻・デザインシステム・各画面の JS・アイコン・図）だけ。API（/v1/…）には触らない
    （古い数字を出さない。使う人・ホーム・今日の練習の写しは app.js / home.js / practice.js が localStorage に持つ）。
  - /vendor/（MediaPipe・mp4box。約18MB）は入れない（大きく、開いたときだけ読む。サーバーが immutable で配るので、ブラウザの
    キャッシュに残る）。video.js と checkpoints.js は入れる（圏外でもコマ選びの画面とチェックの写しが開けるように）。
  - ネットワークを先に使い、届かないときだけ手元の写しを返す（直した画面がすぐ届く）。
*/
const CACHE = "golf-shell-v3";
const SHELL = ["./", "index.html", "tokens.css", "icons.svg", "app.js", "home.js", "record.js", "report.js", "practice.js", "progress.js", "settings.js", "figures.js", "video.js", "checkpoints.js"];

self.addEventListener("install", (ev) => {
  ev.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (ev) => {
  ev.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (ev) => {
  const req = ev.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/v1/") || url.pathname.startsWith("/vendor/") || url.pathname === "/healthz") return;
  const key = url.pathname === "/" ? "./" : url.pathname.replace(/^\//, "");
  if (!SHELL.includes(key)) return;
  ev.respondWith(
    fetch(req).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(key, copy)).catch(() => {}); }
      return res;
    }).catch(() => caches.match(key).then((hit) => hit || Response.error())),
  );
});
