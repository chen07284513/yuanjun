// Network-first service worker: always try fresh data, fall back to the last copy when offline.
const CACHE = "yuanjun-v1";
self.addEventListener("install", (e) => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;
  e.respondWith(
    fetch(req).then((res) => {
      const copy = res.clone();
      const key = req.url.split("?")[0];
      caches.open(CACHE).then((c) => c.put(key, copy));
      return res;
    }).catch(() => caches.match(req.url.split("?")[0]))
  );
});
