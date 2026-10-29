// Shows the push the Worker sends: {title, body, tag}. Text is generic by design.
self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { /* fall back to the defaults below */ }
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
