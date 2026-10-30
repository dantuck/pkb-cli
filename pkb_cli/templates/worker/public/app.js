const $ = (id) => document.getElementById(id);
const msg = $("msg"), token = $("token"), todosEl = $("todos"), chipsEl = $("chips");
const stateEl = $("state"), switchEl = $("switch"), countEl = $("count"), dotEl = $("dot"), bannerEl = $("banner");
const SNOOZES = ["1h", "3h", "1d", "1w"];

// kept on this phone only. The token is a plain string (so it opens straight to the list);
// the last TODO list is JSON, so the app still opens with no signal. Forget clears both.
const store = (key, json = false) => ({
  get() { try { const v = localStorage.getItem(key); return json ? JSON.parse(v) : v || ""; } catch { return json ? null : ""; } },
  set(value) { try { localStorage.setItem(key, json ? JSON.stringify(value) : value); } catch { /* private mode: retype the token, no offline copy */ } },
  clear() { try { localStorage.removeItem(key); } catch { /* nothing stored */ } },
});
const remember = store("kb-token"), cache = store("kb-todos", true);
let saved = remember.get();
let todos = [], now = 0, filter = "all", subscribed = false, pushOk = true, opened = null;
let offline = false;
const pending = new Map(); // id -> "Closing" / "Snoozed 1h": sent, but kb hasn't applied it yet

// "Chrome on Pixel 8", for `kb remind push devices`; the Worker keeps only this text
function deviceLabel() {
  const ua = navigator.userAgent;
  const browser = /SamsungBrowser/.test(ua) ? "Samsung Internet" : /Edg\//.test(ua) ? "Edge" : /Firefox/.test(ua) ? "Firefox"
    : /Chrome/.test(ua) ? "Chrome" : /Safari/.test(ua) ? "Safari" : "Browser";
  const model = (/Android[^;)]*;\s*([^;)]+)/.exec(ua) || [])[1];
  return model ? `${browser} on ${model.trim()}` : browser;
}

function say(text, kind = "") { // kind: "error" | "offline" | "" (a class on #msg)
  msg.textContent = text;
  msg.className = kind;
}

function urlBase64ToUint8Array(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

async function api(method, path, body, withToken = saved) {
  if (!withToken) throw new Error("Enter the phone token first.");
  const res = await fetch(path, {
    method,
    headers: { "content-type": "application/json", authorization: `Bearer ${withToken}` },
    body: body ? JSON.stringify(body) : undefined,
  }).catch(() => { throw Object.assign(new Error("No connection."), { offline: true }); }); // fetch only rejects when there's no network
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw Object.assign(new Error(res.status === 401 ? "That token isn't right." : err.error || `request failed (${res.status})`), { status: res.status });
  }
  return res.json().catch(() => ({}));
}

// ---- shelf (bottom sheet): a native <dialog>, so focus trapping and Escape come free ----
const shelf = $("shelf");
$("open-shelf").addEventListener("click", () => shelf.showModal());
$("close-shelf").addEventListener("click", () => shelf.close());
shelf.addEventListener("click", (e) => { if (e.target === shelf) shelf.close(); }); // a tap on the backdrop

// ---- notifications ----
async function refreshPush() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    pushOk = false;
    stateEl.textContent = "This browser can't receive push notifications.";
    switchEl.disabled = true;
    return null;
  }
  const reg = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;
  subscribed = !!(await reg.pushManager.getSubscription());
  stateEl.textContent = subscribed ? "On for this device" : "Off";
  switchEl.setAttribute("aria-checked", String(subscribed));
  dotEl.classList.toggle("on", subscribed);
  bannerEl.hidden = subscribed || !pushOk || !saved;
  return reg;
}

async function turnOn() {
  switchEl.disabled = true;
  try {
    const reg = await navigator.serviceWorker.ready; // already registered at startup
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
    say("Notifications on. Run `kb remind test` on your computer to check.");
  } catch (e) {
    say(e.message, "error");
  }
  switchEl.disabled = !pushOk;
  await refreshPush();
}

async function turnOff() {
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      await api("DELETE", "/api/subscribe", { endpoint: sub.endpoint });
      await sub.unsubscribe();
    }
    say("Notifications off.");
  } catch (e) {
    say(e.message, "error");
  }
  await refreshPush();
}

switchEl.addEventListener("click", () => (subscribed ? turnOff() : turnOn()));
$("banner-on").addEventListener("click", turnOn);

// ---- TODO list ----
const GROUPS = [
  ["overdue", "Overdue"], ["today", "Today"], ["tomorrow", "Tomorrow"], ["week", "This week"], ["later", "Later"],
];

function dayStart(secs) {
  const d = new Date(secs * 1000);
  d.setHours(0, 0, 0, 0);
  return d.getTime() / 1000;
}

const daysAway = (t) => Math.round((dayStart(t.due_at) - dayStart(now)) / 86400);

function groupOf(t) {
  if (t.due_at <= now) return "overdue";
  const days = daysAway(t);
  return days <= 0 ? "today" : days === 1 ? "tomorrow" : days < 7 ? "week" : "later";
}

function dueText(t) {
  const secs = t.due_at - now, abs = Math.abs(secs);
  const span = abs < 3600 ? `${Math.max(1, Math.round(abs / 60))}m` : abs < 86400 ? `${Math.round(abs / 3600)}h` : `${Math.round(abs / 86400)}d`;
  if (secs <= 0) return { text: `overdue ${span}`, overdue: true };
  const when = new Date(t.due_at * 1000);
  const time = when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const day = daysAway(t) >= 2 ? when.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }) + " " : "";
  return { text: `${day}${time}`, overdue: false };
}

function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text; // textContent, never innerHTML: titles are your own text but still untrusted markup
  return e;
}

async function runAction(t, body, done, actions) {
  actions.querySelectorAll("button").forEach((x) => { x.disabled = true; });
  try {
    await api("POST", "/api/action", { id: t.id, ...body });
    pending.set(t.id, done);
    opened = null;
    say(`${done} ${t.id} -- your computer applies it shortly.`);
  } catch (e) {
    say(e.message, "error");
  }
  render();
}

function card(t) {
  const note = pending.get(t.id);
  const c = el("div", `card p${Math.min(t.priority, 4)}${note ? " pending" : ""}${opened === t.id ? " open" : ""}`);
  const head = el("button", "card-head");
  head.type = "button";
  const due = dueText(t);
  const meta = el("span", "meta");
  meta.append(el("span", due.overdue ? "overdue" : "", note || due.text), ` · P${t.priority} · ${t.id}`);
  head.append(el("span", "title", t.title || t.id), meta);
  head.addEventListener("click", () => {
    opened = opened === t.id ? null : t.id;
    render();
  });
  c.append(head);

  if (!note) {
    const actions = el("div", "actions");
    const act = (label, body, done, secondary) => {
      const b = el("button", secondary ? "secondary" : "primary", label);
      b.type = "button";
      b.addEventListener("click", () => runAction(t, body, done, actions));
      actions.append(b);
    };
    act("Done", { action: "close" }, "Closing");
    for (const s of SNOOZES) act(`Snooze ${s}`, { action: "snooze", for: s }, `Snoozed ${s}`, true);
    c.append(actions);
  }
  return c;
}

function renderChips() {
  const counts = { all: 0 };
  for (const t of todos) {
    counts.all++;
    const g = groupOf(t);
    counts[g] = (counts[g] || 0) + 1;
  }
  chipsEl.replaceChildren();
  for (const [key, label] of [["all", "All"], ...GROUPS]) {
    if (key !== "all" && key !== filter && !counts[key]) continue;
    const b = el("button", "chip", `${label} ${counts[key] || 0}`);
    b.type = "button";
    b.setAttribute("aria-pressed", String(filter === key));
    b.addEventListener("click", () => { filter = key; render(); });
    chipsEl.append(b);
  }
}

function render() {
  const live = todos.filter((t) => !pending.has(t.id));
  countEl.textContent = live.length ? `${live.length} open` : "";
  renderChips();
  todosEl.replaceChildren();
  const buckets = new Map(GROUPS.map(([k]) => [k, []]));
  for (const t of todos) buckets.get(groupOf(t)).push(t);
  let shown = 0;
  for (const [key, label] of GROUPS) {
    if (filter !== "all" && filter !== key) continue;
    // sent-but-unapplied TODOs sink to the bottom of their group
    const list = buckets.get(key).sort((a, b) => (pending.has(a.id) - pending.has(b.id))
      || (key === "overdue" ? a.priority - b.priority || a.due_at - b.due_at : a.due_at - b.due_at || a.priority - b.priority));
    if (!list.length) continue;
    const title = el("div", `group-title${key === "overdue" ? " overdue" : ""}`);
    title.append(el("span", "", label), el("span", "", String(list.length)));
    todosEl.append(title, ...list.map(card));
    shown += list.length;
  }
  if (!shown) todosEl.append(el("div", "empty", todos.length ? "Nothing in this view." : "All clear. Nothing is due or scheduled."));
}

function showView() {
  $("setup").hidden = !!saved;
  $("app").hidden = !saved;
  $("reload").hidden = !saved;
}

// the number on the home-screen icon is how many TODOs are overdue (where the browser supports it)
function setBadge() {
  const n = todos.filter((t) => t.due_at <= now && !pending.has(t.id)).length;
  if (!navigator.setAppBadge) return; // no Badging API
  (n && !offline ? navigator.setAppBadge(n) : navigator.clearAppBadge()).catch(() => {});
}

let loading = null;
function loadTodos() { // visibilitychange and online can fire together: share one request
  return loading ||= fetchTodos().finally(() => { loading = null; });
}

async function fetchTodos() {
  if (!saved) return;
  try {
    ({ todos, now } = await api("GET", "/api/todos"));
    if (offline) say(""); // back online: drop the offline notice
    offline = false;
    cache.set({ todos, now });
    const ids = new Set(todos.map((t) => t.id));
    for (const id of pending.keys()) if (!ids.has(id)) pending.delete(id); // kb applied it
  } catch (e) {
    if (e.status === 401) {
      say(e.message, "error");
      remember.clear(); cache.clear(); saved = ""; showView();
      return;
    }
    const last = cache.get();
    if (!e.offline || !last) return say(e.message, "error");
    todos = last.todos;
    now = Math.floor(Date.now() / 1000); // groups follow the clock even though the list is old
    offline = true;
    say(`Offline. Showing the list from ${new Date(last.now * 1000).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}.`, "offline");
  }
  render();
  setBadge();
}

$("reload").addEventListener("click", async () => { say(""); await loadTodos(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadTodos(); });

$("connect").addEventListener("click", async () => {
  const value = token.value.trim();
  if (!value) return say("Enter the phone token first.", "error");
  try {
    await api("GET", "/api/todos", undefined, value); // only keep a token the Worker accepts
    remember.set(value);
    saved = value;
    token.value = "";
    say("");
    showView();
    await refreshPush();
    await loadTodos();
  } catch (e) {
    say(e.message, "error");
  }
});

$("forget").addEventListener("click", () => {
  remember.clear();
  cache.clear();
  saved = "";
  todos = [];
  now = 0;
  pending.clear();
  shelf.close();
  showView();
  setBadge();
  bannerEl.hidden = true;
  say("Token forgotten on this phone.");
});

showView();
refreshPush().catch((e) => { say("Couldn't start: " + e.message, "error"); });
loadTodos();
window.addEventListener("online", loadTodos);
