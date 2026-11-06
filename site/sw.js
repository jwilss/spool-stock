// Keeps the app opening with no signal. Tries the network first so updates arrive,
// and falls back to the saved copy when the network is missing or too slow.
const CACHE = "spool-stock-v3";
const FILES = ["./", "index.html", "config.js", "catalogue.json", "manifest.webmanifest", "icon-192.png", "icon-512.png",
  "vendor/qrcode.js", "vendor/jsQR.js", "vendor/jspdf.umd.min.js",
  "vendor/firebase-app-compat.js", "vendor/firebase-auth-compat.js", "vendor/firebase-firestore-compat.js"];
self.addEventListener("install", e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES)).then(() => self.skipWaiting())));
self.addEventListener("activate", e => e.waitUntil(
  caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  const mine = u.origin === location.origin, font = /fonts\.(googleapis|gstatic)\.com$/.test(u.hostname);
  if (e.request.method !== "GET" || !(mine || font)) return;
  e.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const net = fetch(e.request).then(r => { if (r.ok || r.type === "opaque") cache.put(e.request, r.clone()); return r; });
    const slow = new Promise(res => setTimeout(res, 2500));
    const first = await Promise.race([net.catch(() => null), slow]);
    if (first) return first;
    const saved = await cache.match(e.request, {ignoreSearch: true}) || (e.request.mode === "navigate" ? await cache.match("index.html") : null);
    return saved || net;
  })());
});
