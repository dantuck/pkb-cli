// Shows the push the Worker sends: {title, body, tag}. Text is generic by design.
self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { /* fall back to the defaults below */ }
  event.waitUntil(self.registration.showNotification(data.title || "kb: TODO due", {
    body: data.body || "Run `kb remind` to see what's due.",
    tag: data.tag || "kb-due",
    renotify: true,
    icon: "/icon.png",
    badge: "/icon.png",
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
