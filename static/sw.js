// Max Alert service worker: lets the installed app open with bad or no signal.
//
// Everything is "network first": the newest version is always used when the network
// answers in time. If it's slow (NET_TIMEOUT_MS) or down, the copy saved on the phone
// is used instead, and the network keeps going in the background to refresh that copy.
// Saved API answers come back with an "X-MaxAlert-Offline: 1" header so the page can
// say it's showing a saved menu.
//
// Only public, read-only data is saved (pages, menus, hours, ratings). Logins, calorie
// logs and favorites always go to the network, so nothing personal is kept here.
// Bump VERSION to throw away everything saved by an older version.
const VERSION = "v2";
const SHELL = `maxalert-shell-${VERSION}`;
const DATA = `maxalert-data-${VERSION}`;
const NET_TIMEOUT_MS = 4000;
const SHELL_FILES = [
  "/", "/static/style.css", "/static/account.js", "/static/hours.js", "/static/plate.js",
  "/static/favorites.js", "/static/pwa.js", "/static/app.js", "/manifest.webmanifest",
  "/static/icons/icon-192.png", "/static/icons/apple-touch-icon.png", "/static/icons/favicon-32.png",
];
const SAVED_APIS = ["/api/menus", "/api/hours", "/api/ratings"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith("maxalert-") && k !== SHELL && k !== DATA)
      .map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (SAVED_APIS.includes(url.pathname)) {
    e.respondWith(networkFirst(e, DATA, true));
  } else if (req.mode === "navigate") {
    e.respondWith(networkFirst(e, SHELL, false, "/"));
  } else if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest") {
    e.respondWith(networkFirst(e, SHELL, false));
  }
  // Everything else (logins, calorie log, favorites, AI summary) goes straight to the network.
});

async function networkFirst(event, cacheName, markOffline, fallbackPath) {
  const req = event.request;
  const cache = await caches.open(cacheName);
  const key = fallbackPath || req;  // all pages share the saved "/" (it's one page)
  const network = fetch(req).then(async (res) => {
    if (res.ok) await cache.put(key, res.clone());
    return res;
  });
  event.waitUntil(network.catch(() => {}));  // keep refreshing the saved copy even if we answer early

  let timer;
  const timeout = new Promise((resolve) => { timer = setTimeout(resolve, NET_TIMEOUT_MS); });
  try {
    const res = await Promise.race([network, timeout]);
    if (res) return res;
  } catch { /* network failed: fall through to the saved copy */ }
  finally { clearTimeout(timer); }

  const saved = await cache.match(key);
  if (saved) return markOffline ? withOfflineHeader(saved) : saved;
  return network;  // nothing saved yet: wait for the network after all (or fail)
}

async function withOfflineHeader(res) {
  const headers = new Headers(res.headers);
  headers.set("X-MaxAlert-Offline", "1");
  return new Response(await res.blob(), { status: res.status, statusText: res.statusText, headers });
}
