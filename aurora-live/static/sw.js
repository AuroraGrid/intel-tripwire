/* AURORA LIVE service worker: offline shell only. Never treat cached intelligence as current truth. */
const SHELL = "aurora-live-shell-v1";
const SHELL_URLS = ["/", "/gop", "/static/gop.html", "/static/aurora-live.js", "/static/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(SHELL_URLS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== SHELL).map((key) => caches.delete(key)))).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/.well-known/")) {
    // Always network for intelligence APIs.
    event.respondWith(fetch(event.request));
    return;
  }
  if (event.request.method !== "GET") {
    return;
  }
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const network = fetch(event.request)
        .then((response) => {
          const copy = response.clone();
          caches.open(SHELL).then((cache) => cache.put(event.request, copy));
          return response;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
