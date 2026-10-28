import { buildPushPayload, type PushSubscription } from "@block65/webcrypto-web-push";

// An overdue TODO is pushed again this long after the last push, matching
// `kb remind check --repeat-hours` (default 4).
const REPEAT_SECONDS = 4 * 3600;
const MAX_REMINDERS = 500;
const MAX_SUBSCRIPTIONS = 20;
const MAX_BODY_BYTES = 64 * 1024;
// a phone that's offline can still get a reminder this long after it was sent
const PUSH_TTL_SECONDS = 4 * 3600;
const MAX_IDS_IN_BODY = 3;

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
      typeof r.priority !== "number" || !Number.isInteger(r.priority) || r.priority < 0 || r.priority > 4
    ) {
      throw new HttpError(400, "each reminder needs id, integer due_at (unix seconds) and priority 0-4");
    }
    return { id: r.id, due_at: r.due_at, priority: r.priority };
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
      `INSERT INTO reminders (id, due_at, priority, gen)
       SELECT json_extract(value, '$.id'), json_extract(value, '$.due_at'), json_extract(value, '$.priority'), ?1
       FROM json_each(?2) WHERE true
       ON CONFLICT (id) DO UPDATE SET
         due_at = excluded.due_at,
         priority = excluded.priority,
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

// One summary push per run, not one per TODO. The text is deliberately generic -- ids
// only, never titles -- so nothing sensitive sits on Cloudflare or a push relay.
async function sendDue(env: Env): Promise<void> {
  const now = Math.floor(Date.now() / 1000);
  const { results: due } = await env.DB
    .prepare(
      `SELECT id, priority FROM reminders
       WHERE due_at <= ?1 AND (last_sent IS NULL OR last_sent <= ?2)
       ORDER BY priority, due_at LIMIT 50`,
    )
    .bind(now, now - REPEAT_SECONDS)
    .all<{ id: string; priority: number }>();
  if (due.length === 0) return;

  const shown = due.slice(0, MAX_IDS_IN_BODY).map((r) => r.id).join(", ");
  const more = due.length > MAX_IDS_IN_BODY ? ` +${due.length - MAX_IDS_IN_BODY} more` : "";
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

type Handler = (request: Request, env: Env) => Promise<Response>;

const routes: Record<string, Handler> = {
  "GET /api/vapid-key": async (_request, env) => json({ publicKey: env.VAPID_PUBLIC_KEY }),

  "POST /api/subscribe": async (request, env) => {
    await requireBearer(request, env.SUBSCRIBE_TOKEN);
    const sub = parseSubscription(await readJson(request));
    // the cap only applies to new endpoints, so a known one can always re-subscribe
    const saved = await env.DB
      .prepare(
        `INSERT INTO subscriptions (endpoint, p256dh, auth, created_at)
         SELECT ?1, ?2, ?3, ?4
         WHERE EXISTS (SELECT 1 FROM subscriptions WHERE endpoint = ?1)
            OR (SELECT COUNT(*) FROM subscriptions) < ?5
         ON CONFLICT (endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth`,
      )
      .bind(sub.endpoint, sub.keys.p256dh, sub.keys.auth, Math.floor(Date.now() / 1000), MAX_SUBSCRIPTIONS)
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
