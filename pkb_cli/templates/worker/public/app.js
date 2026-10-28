const $ = (id) => document.getElementById(id);
const state = $("state"), msg = $("msg"), token = $("token"), enable = $("enable"), disable = $("disable");

function say(text, isError = false) {
  msg.textContent = text;
  msg.className = isError ? "error" : "";
}

function urlBase64ToUint8Array(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

async function api(method, path, body) {
  if (!token.value.trim()) throw new Error("Enter the subscribe token first.");
  const res = await fetch(path, {
    method,
    headers: { "content-type": "application/json", authorization: `Bearer ${token.value.trim()}` },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(res.status === 401 ? "That token isn't right." : err.error || `request failed (${res.status})`);
  }
}

async function refresh() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    state.textContent = "This browser can't receive push notifications.";
    enable.disabled = disable.disabled = true;
    return null;
  }
  const reg = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  state.textContent = sub ? "Notifications are on for this device." : "Notifications are off.";
  enable.disabled = !!sub;
  disable.disabled = !sub;
  return reg;
}

enable.addEventListener("click", async () => {
  if (!token.value.trim()) return say("Enter the subscribe token first.", true);
  enable.disabled = true;
  try {
    const reg = await refresh();
    if ((await Notification.requestPermission()) !== "granted") throw new Error("Notification permission was denied.");
    const { publicKey } = await (await fetch("/api/vapid-key")).json();
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey),
    });
    try {
      await api("POST", "/api/subscribe", sub.toJSON());
    } catch (e) {
      await sub.unsubscribe(); // don't keep a subscription the Worker doesn't know about
      throw e;
    }
    say("Done. Run `kb remind test` on your computer to check it.");
  } catch (e) {
    say(e.message, true);
  }
  await refresh();
});

disable.addEventListener("click", async () => {
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      await api("DELETE", "/api/subscribe", { endpoint: sub.endpoint });
      await sub.unsubscribe();
    }
    say("Turned off.");
  } catch (e) {
    say(e.message, true);
  }
  await refresh();
});

refresh().catch((e) => { state.textContent = "Couldn't start: " + e.message; });
