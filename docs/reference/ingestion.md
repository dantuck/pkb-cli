# Ingestion pipeline contract

One script per source, all following the same contract. `kb` auto-discovers
any `sync_<name>.py` script dropped into `scripts/` — it becomes `kb sync
<name>` (and joins `kb sync all`) with no other file needing an edit. To also
plug into `kb doctor`/`kb setup`'s health checks, the sops-injection decision,
and frontmatter's `source` field validation, the script declares a
module-level `SOURCE_META` dict:

```python
SOURCE_META = {
    "cli_tool": "gh",                        # external CLI this source needs, or omit if none
    "auth_check": ["gh", "auth", "status"],  # argv whose exit 0 means authenticated; omit if cli_tool needs none
    "not_found_hint": "gh CLI not found -- ...",   # doctor/setup todo text when cli_tool is missing
    "not_authed_hint": "gh is installed but not authenticated -- ...",  # todo text when auth_check fails
    "env_keys": ["PKB_GITHUB_REPO"],         # env vars that must be set for this source to be "configured"
    "uses_sops": True,                       # wrap the subprocess in `sops exec-env` when secrets.enc.env exists
}
```

Every field is optional — an empty (or absent) `SOURCE_META` still gets
`kb sync <name>` dispatch, just without the automated checks. See
`sync_beads.py` for a source that only declares `cli_tool` (its store-init
logic is bespoke enough to stay hand-written in `kb doctor`/`kb setup`
directly) and `sync_gitlab.py`/`sync_github.py` for the full shape.

| Source | Script | Cursor stored | API/mechanism |
|---|---|---|---|
| usememos | `sync_memos.py` | last memo `id`/timestamp | REST API `/api/v1/memos`, over Tailscale |
| GitLab | `sync_gitlab.py` | last `updated_after` timestamp | `glab api` or GitLab REST API, filtered by project |
| GitHub | `sync_github.py` | last `updated_after` timestamp | `gh api`, filtered by repo (`owner/repo`) |
| beads | `sync_beads.py` | last bead timestamp/id | local `bd` CLI or its backing store, queried directly |

**Contract each script must satisfy:**

1. Read its cursor from `.pkb/cursors.json`.
2. Fetch only items created/updated after the cursor.
3. Write one markdown file per item into `sources/<tool>/`, with frontmatter
   populated (`source`, `source_id`, `created`, `type: source`,
   `extension: source`).
4. Append a corresponding stub file into `inbox/` for anything that looks
   like it needs human triage (heuristic: configurable per source, e.g. all
   new GitLab/GitHub issues; only memos above a length threshold).
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
