# Design principles

A portable, offline-capable personal knowledge base with **Diataxis as the
core content model**, extended (not diluted) by journaling and multi-source
ingestion (usememos, GitLab issues, GitHub issues, beads).

Non-negotiable principles, in priority order:

1. **Diataxis is the only classification for knowledge content.** Four
   types, full stop: `tutorial`, `how-to`, `reference`, `explanation`.
2. **Portability.** The source of truth is a git repository of plain
   markdown files. No server, database daemon, or network access required to
   read, write, or search it.
3. **Offline-first.** All indexing and search tooling must run fully
   locally. Sync to external sources (usememos, GitLab, GitHub, beads) is a one-way
   *ingestion* step, never a runtime dependency.
4. **Non-core content never competes with core content.** Journals, inbox
   captures, and raw synced source dumps are extensions that link to or feed
   into the Diataxis core — they are never a fifth content type.
5. **Idempotent, incremental everything.** Ingestion and indexing must be
   safe to re-run and must not reprocess unchanged data.

These principles are why the [frontmatter
schema](../reference/frontmatter-schema.md) enforces `extension` as null for
core content, why [search](search-architecture.md) is tiered so nothing
requires a running service, and why [ingestion](../reference/ingestion.md) is
cursor-based rather than a live sync.

## Why this repo applies Diataxis to itself too

The data-repo content model above treats Diataxis as non-negotiable for
*notes*. This tool repo's own documentation (this `docs/` tree) follows the
same structure for the same reason: a tutorial, a how-to guide, a reference
page, and an explanation page answer different questions and shouldn't be
smashed into one README. See [diataxis.fr](https://diataxis.fr/) for the
framework itself.

## Phone reminders

`kb remind` notifies the desktop only while the machine is awake, so a TODO due
at 9am is missed if the laptop is closed. [Phone
reminders](../how-to/get-phone-reminders.md) fix that with a small Cloudflare
Worker the user deploys to their own account (its source ships in the package, at
`pkb_cli/templates/worker`, and `kb remind push init` copies it out). It is optional and
off by default, which keeps the "no required server" rule below intact. The
design choices, and what each one trades away:

- **Something outside the machine has to send it.** Anything that fires while the
  laptop is asleep must run elsewhere. A Worker with a cron trigger is the
  smallest such thing: no server to patch, free-tier sized, and in the user's own
  account rather than a third party's.
- **Web Push, not a notification service.** A relay such as ntfy.sh identifies a
  channel by a topic name that works as a password (anyone who learns it can read
  and forge notifications), and the operator can read every message. Web Push
  encrypts the payload to the phone's own key, so the push service only relays
  ciphertext, and nothing is deliverable without a subscription the phone made.
  The cost is a PWA on the phone and VAPID keys to keep.
- **`kb update` carries the Worker along, but only where someone is there.** The Worker
  runs in the user's own account, so deploying is a change to their infrastructure:
  `kb update` updates an existing one only when its source changed, offers setup once,
  and does nothing from the web UI or a script, where wrangler couldn't prompt or log in.
- **The push carries ids; titles are stored for the phone page.** The notification
  reads `kb-qjp, kb-e3s`, not a title, so nothing readable sits on a push relay or a lock
  screen. But the phone page lists TODOs by title and lets you close or snooze them, which
  needs the Worker to hold each dated TODO's title (never descriptions or comments). That
  is a deliberate step back from "ids only": titles can be sensitive (rotating a key, a
  medical appointment), and they now sit in the user's own Cloudflare D1 database and
  behind the phone token. A TODO with no due date is never uploaded.
- **The machine uploads a full snapshot, not changes.** Each `kb remind check`
  replaces the Worker's reminders with the current set. A closed, deleted, or
  re-dated TODO then needs no special handling and a missed upload heals on the
  next one. The cost is a copy that can lag by up to one interval: a TODO closed
  just before it came due can still produce one last push.
- **The Worker is a mirror with an inbox, not a store of record.** It keeps the latest
  snapshot, when each reminder was last sent, and a short queue of what the phone asked
  for. Beads stays the single source of truth: a close or snooze from the phone is applied
  on the Mac by `kb remind check`, which fetches the queue, runs `bd`, acknowledges it and
  only then uploads, so it takes effect within one interval (five minutes) rather than
  instantly. The Worker applies it to its own copy at once so the page and the pushes agree
  until then. The alternative, making the Worker the store of record, would have meant
  syncing beads both ways.
- **Two tokens, so a leak is bounded.** The phone token (`SUBSCRIBE_TOKEN`) can subscribe a
  device (capped, and only to known push services, so it can't aim the Worker at arbitrary
  URLs), list the tracked TODOs with their titles, and ask to close or snooze one. It can
  not list or remove devices, and it can't upload or read anything else. The sync token,
  which lives only in kb's machine-local config, is the one that replaces the reminders and
  manages devices (`kb remind push devices`), so a lost phone is removed from the computer.
  The phone can still turn off its own subscription, since it holds that endpoint.
  Requests from the Worker are not trusted by kb either: ids that could be read as flags
  are refused and only close and snooze run.
- **One rule for "when is it due".** The desktop notification, the due label, the
  phone upload and the list order all read `todo_remind_at`, so a snoozed TODO
  can't be quiet on one device and loud on another.

## Explicit non-goals (v1)

- No required server process — the CLI is file-based and one-shot. `kb web`
  is an optional local UI that runs only while you want it, and the phone-reminder
  Worker is an optional add-on.
- No real-time sync/webhooks from usememos/GitLab/GitHub/beads — polling/cursor-based
  batch sync only.
- No automatic Diataxis classification of inbox content — triage is
  human-in-the-loop.
- No multi-device conflict resolution beyond standard git merge — this is a
  single-user, git-synced repo, not a CRDT system.

## Build order (historical, for context)

The tool was built in phases, each depending on the last:

| Phase | Deliverable | Depends on |
|---|---|---|
| 1 | Repo scaffold: folder layout, `.gitignore`, `schema.sql`, `config.yml` | — |
| 2 | `validate_frontmatter.py` + pre-commit hook | 1 |
| 3 | `kb new`, `kb journal` (manual content creation works end-to-end) | 1, 2 |
| 4 | `rg`/`fzf` documented as day-one search (no code, just usage doc) | 1 |
| 5 | `index_fts.py` + `kb search` | 1, 2 |
| 6 | `sync_memos.py` (first ingestion source — usememos is the active capture point) | 1, 2 |
| 7 | `triage_report.py` + `kb triage`, `kb inbox` | 6 |
| 8 | `sync_gitlab.py`, `sync_github.py`, `sync_beads.py` (same contract as memos) | 1, 2 |
| 9 | `kb links` (graph/backlink queries from frontmatter) | 2 |
| 10 | Embeddings layer (`index_embeddings.py`) — only if FTS5 relevance proves insufficient | 5 |
| 11 | Monthly journal rollups + on-this-day look-back | 8 (journal volume exists) — done |

Phases 1–7 constitute a usable v1. Phases 8–11 extend it without any
architectural change.
