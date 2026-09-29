/*
  圏外でも画面を開き直せるようにする Service Worker（docs/DESIGN_coaching.md §8.7「圏外でも動く」）。
  - 置くのは画面のファイル（/・index.html・today.js・figures.js）だけ。API（/v1/…）には触らない
    （古い数字を出さない。今日の練習の写しは today.js が localStorage に持つ）。
  - ネットワークを先に使い、届かないときだけ手元の写しを返す（直した画面がすぐ届く）。
*/
const CACHE = "golf-shell-v1";
const SHELL = ["./", "index.html", "today.js", "figures.js"];

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
  if (url.origin !== self.location.origin || url.pathname.startsWith("/v1/") || url.pathname === "/healthz") return;
  const key = url.pathname === "/" ? "./" : url.pathname.replace(/^\//, "");
  if (!SHELL.includes(key)) return;
  ev.respondWith(
    fetch(req).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(key, copy)).catch(() => {}); }
      return res;
    }).catch(() => caches.match(key).then((hit) => hit || Response.error())),
  );
});
