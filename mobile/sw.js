/* P.E.T.E.R. service worker — enables the home-screen "app" experience.
   We keep the network-first strategy so live data (tokens, Spotify) is
   always fresh, and only fall back to a cached shell when offline. */
const CACHE = 'peter-v1';
const SHELL = ['/', '/manifest.json', '/icon-192.png', '/icon-512.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  // Always go network-first for API calls (they need fresh tokens/data).
  if (event.request.method !== 'GET' || url.pathname.startsWith('/connection') ||
      url.pathname.startsWith('/spotify') || url.pathname.startsWith('/pending') ||
      url.pathname.startsWith('/files')) {
    return;
  }
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(event.request, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(event.request))
  );
});

