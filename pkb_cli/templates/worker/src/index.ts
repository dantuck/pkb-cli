import { buildPushPayload, type PushSubscription } from "@block65/webcrypto-web-push";

// An overdue TODO is pushed again this long after the last push, matching
// `kb remind check --repeat-hours` (default 4).
const REPEAT_SECONDS = 4 * 3600;
const MAX_REMINDERS = 500;
const MAX_SUBSCRIPTIONS = 20;
const MAX_BODY_BYTES = 64 * 1024;
// a phone that's offline can still get a reminder this long after it was sent
const PUSH_TTL_SECONDS = 4 * 3600;
const MAX_TITLES_IN_BODY = 3;
const MAX_TITLE_CHARS = 200;
const MAX_LABEL_CHARS = 80;
const MAX_PENDING_ACTIONS = 200;
// What the phone may ask for a snooze, in seconds. Fixed choices, so the Worker needs no
// idea of the user's time zone.
const SNOOZE_SECONDS: Record<string, number> = { "1h": 3600, "3h": 3 * 3600, "1d": 86400, "1w": 7 * 86400 };

// Subscribers hand us a URL we will POST to, so only accept the real push services.
const PUSH_HOST_SUFFIXES = [
  "fcm.googleapis.com",
  "push.services.mozilla.com",
  "push.apple.com",
  "notify.windows.com",
];

interface Reminder {
  id: string;
  due_at: number;
  priority: number;
  title: string | null;
}

class HttpError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

function json(body: unknown, status = 200): Response {
  return Response.json(body, { status, headers: { "cache-control": "no-store" } });
}

// Compare digests, not the secrets: fixed length, and timingSafeEqual doesn't leak
// where a mismatch starts. An unset secret never matches anything.
async function hasBearer(request: Request, secret: string | undefined): Promise<boolean> {
  if (!secret) return false;
  const header = request.headers.get("authorization") ?? "";
  const given = header.startsWith("Bearer ") ? header.slice(7) : "";
  const enc = new TextEncoder();
  const [a, b] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(given)),
    crypto.subtle.digest("SHA-256", enc.encode(secret)),
  ]);
  return crypto.subtle.timingSafeEqual(a, b);
}

async function requireBearer(request: Request, secret: string | undefined): Promise<void> {
  if (!(await hasBearer(request, secret))) throw new HttpError(401, "unauthorized");
}

async function readJson(request: Request): Promise<unknown> {
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (declared > MAX_BODY_BYTES) throw new HttpError(413, "body too large");
  const text = await request.text();
  if (text.length > MAX_BODY_BYTES) throw new HttpError(413, "body too large");
  try {
    return JSON.parse(text);
  } catch {
    throw new HttpError(400, "invalid JSON");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseReminders(body: unknown): Reminder[] {
  if (!isRecord(body) || !Array.isArray(body.reminders)) throw new HttpError(400, "expected {reminders: [...]}");
  if (body.reminders.length > MAX_REMINDERS) throw new HttpError(400, `at most ${MAX_REMINDERS} reminders`);
  return body.reminders.map((r) => {
    if (
      !isRecord(r) ||
      typeof r.id !== "string" || !/^[A-Za-z0-9._-]{1,64}$/.test(r.id) ||
      typeof r.due_at !== "number" || !Number.isSafeInteger(r.due_at) || r.due_at < 0 ||
      typeof r.priority !== "number" || !Number.isInteger(r.priority) || r.priority < 0 || r.priority > 4 ||
      (r.title !== undefined && r.title !== null && typeof r.title !== "string")
    ) {
      throw new HttpError(400, "each reminder needs id, integer due_at (unix seconds), priority 0-4 and an optional title");
    }
    return { id: r.id, due_at: r.due_at, priority: r.priority, title: r.title ? r.title.slice(0, MAX_TITLE_CHARS) : null };
  });
}

function parseSubscription(body: unknown): PushSubscription {
  if (!isRecord(body) || typeof body.endpoint !== "string" || !isRecord(body.keys)) {
    throw new HttpError(400, "expected a push subscription");
  }
  const { p256dh, auth } = body.keys;
  if (typeof p256dh !== "string" || typeof auth !== "string" || p256dh.length > 200 || auth.length > 100) {
    throw new HttpError(400, "bad subscription keys");
  }
  let url: URL;
  try {
    url = new URL(body.endpoint);
  } catch {
    throw new HttpError(400, "bad endpoint");
  }
  const allowedHost = PUSH_HOST_SUFFIXES.some((s) => url.hostname === s || url.hostname.endsWith(`.${s}`));
  if (url.protocol !== "https:" || !allowedHost || body.endpoint.length > 2048) {
    throw new HttpError(400, "endpoint isn't a known push service");
  }
  return { endpoint: body.endpoint, expirationTime: null, keys: { p256dh, auth } };
}

async function count(db: D1Database, table: "reminders" | "subscriptions"): Promise<number> {
  const row = await db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).first<{ n: number }>();
  return row?.n ?? 0;
}

// Replace the whole set in one transaction: anything the Mac no longer lists (closed,
// deleted, due date cleared) is dropped, and a TODO whose due time is unchanged keeps
// its last_sent so a sync never re-triggers a push that already went out.
async function syncReminders(env: Env, reminders: Reminder[]): Promise<void> {
  const gen = Date.now();
  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO reminders (id, due_at, priority, title, gen)
       SELECT json_extract(value, '$.id'), json_extract(value, '$.due_at'), json_extract(value, '$.priority'),
              json_extract(value, '$.title'), ?1
       FROM json_each(?2) WHERE true
       ON CONFLICT (id) DO UPDATE SET
         due_at = excluded.due_at,
         priority = excluded.priority,
         title = excluded.title,
         gen = excluded.gen,
         last_sent = CASE WHEN reminders.due_at = excluded.due_at THEN reminders.last_sent ELSE NULL END`,
    ).bind(gen, JSON.stringify(reminders)),
    env.DB.prepare("DELETE FROM reminders WHERE gen <> ?1").bind(gen),
  ]);
}

interface StoredSubscription {
  endpoint: string;
  p256dh: string;
  auth: string;
}

interface PushResult {
  delivered: number;
  failed: number;
  removed: number;
}

// Deliver one message to every subscribed device. A subscription the push service
// reports gone (404/410) is deleted; anything else counts as a failure to retry.
async function pushToAll(
  env: Env,
  data: { title: string; body: string },
  urgency: "normal" | "high",
): Promise<PushResult> {
  const { results: subs } = await env.DB
    .prepare("SELECT endpoint, p256dh, auth FROM subscriptions")
    .all<StoredSubscription>();
  const vapid = { subject: env.VAPID_SUBJECT, publicKey: env.VAPID_PUBLIC_KEY, privateKey: env.VAPID_PRIVATE_KEY };
  const out: PushResult = { delivered: 0, failed: 0, removed: 0 };

  await Promise.all(subs.map(async (s) => {
    try {
      const subscription: PushSubscription = {
        endpoint: s.endpoint, expirationTime: null, keys: { p256dh: s.p256dh, auth: s.auth },
      };
      const payload = await buildPushPayload(
        { data: { ...data, tag: "kb-due" }, options: { ttl: PUSH_TTL_SECONDS, urgency, topic: "kb-due" } },
        subscription,
        vapid,
      );
      const res = await fetch(s.endpoint, payload);
      if (res.ok) {
        out.delivered++;
      } else if (res.status === 404 || res.status === 410) {
        await env.DB.prepare("DELETE FROM subscriptions WHERE endpoint = ?1").bind(s.endpoint).run();
        out.removed++;
      } else {
        out.failed++;
        console.error(JSON.stringify({ message: "push rejected", status: res.status }));
      }
    } catch (e) {
      out.failed++;
      console.error(JSON.stringify({ message: "push failed", error: e instanceof Error ? e.message : String(e) }));
    }
  }));
  return out;
}

// One summary push per run, not one per TODO. The body lists titles (falling back to the id
// for an untitled one); Web Push is encrypted end to end, so the relay can't read them, but
// they are visible on the phone's lock screen.
async function sendDue(env: Env): Promise<void> {
  const now = Math.floor(Date.now() / 1000);
  const { results: due } = await env.DB
    .prepare(
      `SELECT id, title, priority FROM reminders
       WHERE due_at <= ?1 AND (last_sent IS NULL OR last_sent <= ?2)
       ORDER BY priority, due_at LIMIT 50`,
    )
    .bind(now, now - REPEAT_SECONDS)
    .all<{ id: string; title: string | null; priority: number }>();
  if (due.length === 0) return;

  const shown = due.slice(0, MAX_TITLES_IN_BODY).map((r) => r.title || r.id).join("\n");
  const more = due.length > MAX_TITLES_IN_BODY ? `\n+${due.length - MAX_TITLES_IN_BODY} more` : "";
  const urgency = due.some((r) => r.priority <= 1) ? "high" : "normal";
  const result = await pushToAll(
    env,
    { title: `kb: ${due.length} TODO${due.length === 1 ? "" : "s"} due`, body: `${shown}${more}` },
    urgency,
  );
  console.log(JSON.stringify({ message: "sent due reminders", due: due.length, ...result }));

  // Only mark them sent once a device actually got the push; with no subscriber yet (or a
  // push service outage) they stay pending and go out as soon as one can.
  if (result.delivered > 0) {
    await env.DB
      .prepare(`UPDATE reminders SET last_sent = ?1 WHERE id IN (${due.map((_, i) => `?${i + 2}`).join(",")})`)
      .bind(now, ...due.map((r) => r.id))
      .run();
  }
}

function parseLabel(body: unknown): string | null {
  const label = isRecord(body) && typeof body.label === "string" ? body.label.trim() : "";
  return label ? label.slice(0, MAX_LABEL_CHARS) : null;
}

function parseIds(body: unknown): number[] {
  if (!isRecord(body) || !Array.isArray(body.ids) || body.ids.length > MAX_PENDING_ACTIONS ||
      !body.ids.every((n) => Number.isSafeInteger(n))) {
    throw new HttpError(400, "expected {ids: [integers]}");
  }
  return body.ids as number[];
}

// A request from the phone: close a TODO, or snooze it for one of the fixed lengths. It is
// queued for kb to apply on the Mac (which owns the TODOs), and applied here at once so the
// phone's list and the due-reminder pushes agree until kb's next sync replaces everything.
async function queueAction(env: Env, body: unknown): Promise<void> {
  if (!isRecord(body) || typeof body.id !== "string" || (body.action !== "close" && body.action !== "snooze")) {
    throw new HttpError(400, "expected {id, action: close|snooze}");
  }
  const seconds = body.action === "snooze" ? SNOOZE_SECONDS[String(body.for)] : undefined;
  if (body.action === "snooze" && seconds === undefined) {
    throw new HttpError(400, `snooze needs "for": ${Object.keys(SNOOZE_SECONDS).join(", ")}`);
  }
  const now = Math.floor(Date.now() / 1000);
  const until = seconds === undefined ? null : now + seconds;
  const queued = await env.DB
    .prepare(
      `INSERT INTO actions (todo_id, action, until, created_at)
       SELECT ?1, ?2, ?3, ?4
       WHERE EXISTS (SELECT 1 FROM reminders WHERE id = ?1)
         AND (SELECT COUNT(*) FROM actions) < ?5`,
    )
    .bind(body.id, body.action, until, now, MAX_PENDING_ACTIONS)
    .run();
  if (queued.meta.changes === 0) {
    const known = await env.DB.prepare("SELECT 1 AS x FROM reminders WHERE id = ?1").bind(body.id).first();
    throw known ? new HttpError(429, "too many pending requests -- wait for kb to pick them up") : new HttpError(404, "no such TODO");
  }
  await env.DB
    .prepare(
      until === null
        ? "DELETE FROM reminders WHERE id = ?1"
        : "UPDATE reminders SET due_at = ?2, last_sent = NULL WHERE id = ?1",
    )
    .bind(...(until === null ? [body.id] : [body.id, until]))
    .run();
}

type Handler = (request: Request, env: Env) => Promise<Response>;

const routes: Record<string, Handler> = {
  "GET /api/vapid-key": async (_request, env) => json({ publicKey: env.VAPID_PUBLIC_KEY }),

  "POST /api/subscribe": async (request, env) => {
    await requireBearer(request, env.SUBSCRIBE_TOKEN);
    const body = await readJson(request);
    const sub = parseSubscription(body);
    const label = parseLabel(body);
    // the cap only applies to new endpoints, so a known one can always re-subscribe
    const saved = await env.DB
      .prepare(
        `INSERT INTO subscriptions (endpoint, p256dh, auth, created_at, label)
         SELECT ?1, ?2, ?3, ?4, ?6
         WHERE EXISTS (SELECT 1 FROM subscriptions WHERE endpoint = ?1)
            OR (SELECT COUNT(*) FROM subscriptions) < ?5
         ON CONFLICT (endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth,
           label = COALESCE(excluded.label, subscriptions.label)`,
      )
      .bind(sub.endpoint, sub.keys.p256dh, sub.keys.auth, Math.floor(Date.now() / 1000), MAX_SUBSCRIPTIONS, label)
      .run();
    if (saved.meta.changes === 0) throw new HttpError(409, "too many subscriptions");
    return json({ ok: true }, 201);
  },

  "DELETE /api/subscribe": async (request, env) => {
    await requireBearer(request, env.SUBSCRIBE_TOKEN);
    const body = await readJson(request);
    if (!isRecord(body) || typeof body.endpoint !== "string") throw new HttpError(400, "expected {endpoint}");
    await env.DB.prepare("DELETE FROM subscriptions WHERE endpoint = ?1").bind(body.endpoint).run();
    return json({ ok: true });
  },

  "PUT /api/sync": async (request, env) => {
    await requireBearer(request, env.SYNC_TOKEN);
    const reminders = parseReminders(await readJson(request));
    await syncReminders(env, reminders);
    return json({ ok: true, reminders: reminders.length });
  },

  // The phone's own view: what is tracked, and the two things it may do about it. The
  // phone token can't list or remove devices -- that is kb's job (SYNC_TOKEN, below).
  "GET /api/todos": async (request, env) => {
    await requireBearer(request, env.SUBSCRIBE_TOKEN);
    const { results } = await env.DB
      .prepare("SELECT id, title, due_at, priority FROM reminders ORDER BY priority, due_at LIMIT 100")
      .all();
    return json({ todos: results, now: Math.floor(Date.now() / 1000) });
  },

  "POST /api/action": async (request, env) => {
    await requireBearer(request, env.SUBSCRIBE_TOKEN);
    await queueAction(env, await readJson(request));
    return json({ ok: true }, 202);
  },

  "GET /api/actions": async (request, env) => {
    await requireBearer(request, env.SYNC_TOKEN);
    const { results } = await env.DB
      .prepare("SELECT id, todo_id, action, until FROM actions ORDER BY id LIMIT ?1")
      .bind(MAX_PENDING_ACTIONS)
      .all();
    return json({ actions: results });
  },

  "POST /api/actions/ack": async (request, env) => {
    await requireBearer(request, env.SYNC_TOKEN);
    const ids = parseIds(await readJson(request));
    if (ids.length) {
      await env.DB.prepare("DELETE FROM actions WHERE id IN (SELECT value FROM json_each(?1))").bind(JSON.stringify(ids)).run();
    }
    return json({ ok: true, acked: ids.length });
  },

  "GET /api/devices": async (request, env) => {
    await requireBearer(request, env.SYNC_TOKEN);
    const { results } = await env.DB
      .prepare("SELECT rowid AS id, label, created_at, substr(endpoint, 9, instr(substr(endpoint, 9), '/') - 1) AS service FROM subscriptions ORDER BY created_at")
      .all();
    return json({ devices: results });
  },

  "DELETE /api/devices": async (request, env) => {
    await requireBearer(request, env.SYNC_TOKEN);
    const body = await readJson(request);
    if (!isRecord(body) || !Number.isSafeInteger(body.id)) throw new HttpError(400, "expected {id}");
    const removed = await env.DB.prepare("DELETE FROM subscriptions WHERE rowid = ?1").bind(body.id).run();
    if (removed.meta.changes === 0) throw new HttpError(404, "no such device");
    return json({ ok: true });
  },

  "GET /api/status": async (request, env) => {
    await requireBearer(request, env.SYNC_TOKEN);
    const [reminders, subscriptions] = await Promise.all([count(env.DB, "reminders"), count(env.DB, "subscriptions")]);
    return json({ reminders, subscriptions });
  },

  "POST /api/test": async (request, env) => {
    await requireBearer(request, env.SYNC_TOKEN);
    return json(await pushToAll(env, { title: "kb reminders are on", body: "Phone reminders work." }, "normal"));
  },
};

async function route(request: Request, env: Env): Promise<Response> {
  const { pathname } = new URL(request.url);
  const handler = routes[`${request.method} ${pathname}`];
  if (handler) return handler(request, env);
  const pathExists = Object.keys(routes).some((key) => key.endsWith(` ${pathname}`));
  throw new HttpError(pathExists ? 405 : 404, pathExists ? "method not allowed" : "not found");
}

export default {
  async fetch(request, env): Promise<Response> {
    try {
      return await route(request, env);
    } catch (e) {
      if (e instanceof HttpError) return json({ error: e.message }, e.status);
      console.error(JSON.stringify({ message: "request failed", error: e instanceof Error ? e.message : String(e) }));
      return json({ error: "internal error" }, 500);
    }
  },

  async scheduled(_controller, env): Promise<void> {
    await sendDue(env);
  },
} satisfies ExportedHandler<Env>;
