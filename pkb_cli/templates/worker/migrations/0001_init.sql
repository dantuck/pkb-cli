-- One row per TODO that has a due time, replaced wholesale by each sync.
-- Times are unix seconds. last_sent is NULL until the first push, then the time of
-- the latest one (an overdue TODO is re-sent every 4 hours); a changed due_at resets it.
CREATE TABLE reminders (
  id        TEXT PRIMARY KEY,
  due_at    INTEGER NOT NULL,
  priority  INTEGER NOT NULL DEFAULT 2,
  last_sent INTEGER,
  gen       INTEGER NOT NULL
);
CREATE INDEX reminders_due ON reminders (due_at);

-- Browser push subscriptions (the phone). endpoint is the push service's URL for it.
CREATE TABLE subscriptions (
  endpoint   TEXT PRIMARY KEY,
  p256dh     TEXT NOT NULL,
  auth       TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
