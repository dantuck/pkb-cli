-- The phone page lists TODOs (with titles, so you can tell them apart) and queues
-- close/snooze requests that `kb` picks up and applies on the Mac.
ALTER TABLE reminders ADD COLUMN title TEXT;

-- A human label for each phone, so `kb remind push devices` can say which is which.
ALTER TABLE subscriptions ADD COLUMN label TEXT;

-- Requests from the phone, waiting for kb. `until` is unix seconds for a snooze.
CREATE TABLE actions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  todo_id    TEXT NOT NULL,
  action     TEXT NOT NULL,
  until      INTEGER,
  created_at INTEGER NOT NULL
);
