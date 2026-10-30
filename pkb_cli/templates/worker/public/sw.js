// The app shell is cached so the page opens with no signal; the API is never cached (it needs the token).
const SHELL = "kb-shell-v1";
const FILES = ["/", "/style.css", "/app.js", "/manifest.webmanifest", "/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    await Promise.all((await caches.keys()).filter((k) => k !== SHELL).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

// network first, so a redeploy shows up at once; the cache only answers when the network doesn't
self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/") || url.pathname === "/sw.js") return;
  event.respondWith((async () => {
    const cache = await caches.open(SHELL);
    try {
      const res = await fetch(request);
      if (res.ok) cache.put(request, res.clone());
      return res;
    } catch (e) {
      const hit = await cache.match(request, { ignoreSearch: true }) || (request.mode === "navigate" && await cache.match("/"));
      if (hit) return hit;
      throw e;
    }
  })());
});

// Shows the push the Worker sends: {title, body, tag}.
self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { /* fall back to the defaults below */ }
  self.navigator.setAppBadge?.().catch(() => {}); // a dot on the icon; the page puts the exact count there when opened
  event.waitUntil(self.registration.showNotification(data.title || "kb: TODO due", {
    body: data.body || "Run `kb remind` to see what's due.",
    tag: data.tag || "kb-due",
    renotify: true,
    icon: "/icon-192.png",
    // the status-bar glyph: Android reads only its alpha channel, so this one is a
    // single-colour bell on transparent (the full-colour icon would show as a blank square)
    badge: "/badge.png",
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const open = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    if (open.length) return open[0].focus();
    return self.clients.openWindow("/");
  })());
});
