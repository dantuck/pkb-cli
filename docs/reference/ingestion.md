# Ingestion pipeline contract

One script per source, all following the same contract:

| Source | Script | Cursor stored | API/mechanism |
|---|---|---|---|
| usememos | `sync_memos.py` | last memo `id`/timestamp | REST API `/api/v1/memos`, over Tailscale |
| GitLab | `sync_gitlab.py` | last `updated_after` timestamp | `glab api` or GitLab REST API, filtered by project |
| beads | `sync_beads.py` | last bead timestamp/id | local `bd` CLI or its backing store, queried directly |

**Contract each script must satisfy:**

1. Read its cursor from `.pkb/cursors.json`.
2. Fetch only items created/updated after the cursor.
3. Write one markdown file per item into `sources/<tool>/`, with frontmatter
   populated (`source`, `source_id`, `created`, `type: source`,
   `extension: source`).
4. Append a corresponding stub file into `inbox/` for anything that looks
   like it needs human triage (heuristic: configurable per source, e.g. all
   new GitLab issues; only memos above a length threshold).
5. Update the cursor **only after a successful full write**, so a crash
   mid-run re-fetches safely (idempotent — re-writing an already-synced item
   must not duplicate it; key on `source_id`).
6. Exit non-zero with a clear message on auth/network failure; never
   partially advance the cursor on failure.

Runnable via cron, systemd timer, or a Claude Code slash command (`/sync`).
Since ingestion requires network/API access, it is explicitly the *only* part
of the system allowed to depend on connectivity — search and editing must
work without it.

See [how-to: set up ingestion credentials](../how-to/set-up-ingestion-credentials.md)
for configuring the scripts, and [how-to: triage the
inbox](../how-to/triage-the-inbox.md) for clearing what they drop into
`inbox/`.
