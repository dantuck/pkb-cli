const $ = (id) => document.getElementById(id);
const state = $("state"), msg = $("msg"), token = $("token"), enable = $("enable"), disable = $("disable");
const todos = $("todos"), SNOOZES = ["1h", "3h", "1d", "1w"];

// the token is remembered on this phone only, so the list opens without retyping it
const remember = {
  get() { try { return localStorage.getItem("kb-token") || ""; } catch { return ""; } },
  set(value) { try { localStorage.setItem("kb-token", value); } catch { /* private mode: just retype it */ } },
  clear() { try { localStorage.removeItem("kb-token"); } catch { /* nothing stored */ } },
};
token.value = remember.get();

// "Chrome on Pixel 8", for `kb remind push devices`; the Worker keeps only this text
function deviceLabel() {
  const ua = navigator.userAgent;
  const browser = /SamsungBrowser/.test(ua) ? "Samsung Internet" : /Edg\//.test(ua) ? "Edge" : /Firefox/.test(ua) ? "Firefox"
    : /Chrome/.test(ua) ? "Chrome" : /Safari/.test(ua) ? "Safari" : "Browser";
  const model = (/Android[^;)]*;\s*([^;)]+)/.exec(ua) || [])[1];
  return model ? `${browser} on ${model.trim()}` : browser;
}

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
  if (!token.value.trim()) throw new Error("Enter the phone token first.");
  const res = await fetch(path, {
    method,
    headers: { "content-type": "application/json", authorization: `Bearer ${token.value.trim()}` },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(res.status === 401 ? "That token isn't right." : err.error || `request failed (${res.status})`);
  }
  remember.set(token.value.trim());
  return res.json().catch(() => ({}));
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
  if (!token.value.trim()) return say("Enter the phone token first.", true);
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
      await api("POST", "/api/subscribe", { ...sub.toJSON(), label: deviceLabel() });
    } catch (e) {
      await sub.unsubscribe(); // don't keep a subscription the Worker doesn't know about
      throw e;
    }
    say("Done. Run `kb remind test` on your computer to check it.");
  } catch (e) {
    say(e.message, true);
  }
  await refresh();
  await loadTodos();
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

function dueText(dueAt, now) {
  const secs = dueAt - now, abs = Math.abs(secs);
  const span = abs < 3600 ? `${Math.max(1, Math.round(abs / 60))}m` : abs < 86400 ? `${Math.round(abs / 3600)}h` : `${Math.round(abs / 86400)}d`;
  return secs <= 0 ? { text: `overdue ${span}`, overdue: true } : { text: `due in ${span}`, overdue: false };
}

function render(list, now) {
  todos.replaceChildren();
  if (!list.length) {
    const li = document.createElement("li");
    li.textContent = "Nothing is due or scheduled.";
    todos.append(li);
    return;
  }
  for (const t of list) {
    const li = document.createElement("li");
    const title = document.createElement("div");
    title.className = "title";
    title.textContent = t.title || t.id; // textContent, never innerHTML: titles are your own text but still untrusted markup
    const due = dueText(t.due_at, now);
    const meta = document.createElement("div");
    meta.className = "meta" + (due.overdue ? " overdue" : "");
    meta.textContent = `${t.id} · P${t.priority} · ${due.text}`;
    const actions = document.createElement("div");
    actions.className = "actions";
    const act = (label, body, secondary) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      if (secondary) b.className = "secondary";
      b.addEventListener("click", async () => {
        actions.querySelectorAll("button").forEach((x) => { x.disabled = true; });
        try {
          await api("POST", "/api/action", { id: t.id, ...body });
          say(`${body.action === "close" ? "Closing" : "Snoozing"} ${t.id} -- your computer applies it shortly.`);
        } catch (e) {
          say(e.message, true);
        }
        await loadTodos();
      });
      actions.append(b);
    };
    act("Close", { action: "close" });
    for (const s of SNOOZES) act(`Snooze ${s}`, { action: "snooze", for: s }, true);
    li.append(title, meta, actions);
    todos.append(li);
  }
}

async function loadTodos() {
  if (!token.value.trim()) return todos.replaceChildren();
  try {
    const { todos: list, now } = await api("GET", "/api/todos");
    render(list, now);
  } catch (e) {
    todos.replaceChildren();
    say(e.message, true);
  }
}

$("reload").addEventListener("click", loadTodos);
$("forget").addEventListener("click", () => {
  remember.clear();
  token.value = "";
  todos.replaceChildren();
  say("Token forgotten on this phone.");
});

refresh().then(loadTodos).catch((e) => { state.textContent = "Couldn't start: " + e.message; });
