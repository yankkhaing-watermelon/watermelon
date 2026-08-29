/* Service worker — shell caching for the installed app.
 *
 * Three rules learned the hard way:
 *   1. Never let respondWith() reject. A rejected promise on a navigation is
 *      what the browser reports as ERR_FAILED, and in a standalone PWA that
 *      is a blank unusable window rather than a normal error page.
 *   2. Network first for navigations, cache only as a fallback, so a bad or
 *      stale cached shell can never brick the installed app.
 *   3. cache.addAll() is atomic — one 404 (a file not deployed yet) fails the
 *      whole install and leaves the PREVIOUS worker in charge. Add files
 *      individually and tolerate misses.
 */
const CACHE = "bmk-shell-v53";
const SHELL = ["./", "./index.html", "./app.js", "./charts.js", "./config.js",
               "./manifest.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((c) => Promise.allSettled(SHELL.map((u) => c.add(u))))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
      .catch(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  let url;
  try {
    url = new URL(req.url);
  } catch {
    return;
  }
  // Leave the Worker API (different origin) completely alone.
  if (url.origin !== self.location.origin) return;

  // Navigations: always try the network, fall back to the cached shell, and
  // as a last resort return a readable message instead of failing.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .catch(() => caches.match("./index.html")
          .then((hit) => hit || caches.match("./")))
        .then((res) => res || new Response(
          "<h1>Offline</h1><p>Reconnect and reopen the app.</p>",
          { headers: { "Content-Type": "text/html" }, status: 200 }))
    );
    return;
  }

  // Scan data must never come from cache — stale results are worse than none.
  if (/\/(latest|history|weekly|backtest|status)\.json$/.test(url.pathname)) return;

  event.respondWith(
    caches.match(req)
      .then((hit) => hit || fetch(req).then((res) => {
        if (res && res.ok && res.type === "basic") {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      }))
      .catch(() => caches.match(req).then((hit) => hit || Response.error()))
  );
});
