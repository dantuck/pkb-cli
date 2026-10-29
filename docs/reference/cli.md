# CLI command reference

```
kb new <type> "<title>" [--tags a,b] [--links id,id] [--body TEXT] [--json]
                                  type: tutorial|how-to|reference|explanation
kb journal [<date>] [-m [TEXT]] [--json]  today (default) or YYYY-MM-DD; -m TEXT quick-adds a
                                  timestamped line, bare -m opens $EDITOR for multiline input
kb journal --tag <tag>            past entries carrying <tag>, newest first
kb journal rollup [YYYY-MM] [--json]       generate/refresh that month's summary page (default: last month)
kb search "<query>" [--type T] [--tag TAG] [--all] [--keyword|--semantic] [--plain] [--json]
kb inbox [--plain] [--json]                interactive triage: promote / redirect / discard
kb inbox <id> promote <type>|redirect|discard [--json]   non-interactive triage (no terminal needed)
kb triage [--json]                read-only overdue-inbox report
kb links <id> [--json]            forward links + backlinks
kb show <id> [--json]             print an entry's full content by id
kb tag <id> [add|rm <tag>...] [--json]     view/add/remove tags (never hand-edit frontmatter)
kb link <id> [add|rm <id>...] [--json]     view/add/remove links, same reasoning
kb rm <id> [--force] [--json]              delete an entry (refuses if other entries link to it,
                                  unless --force, which also cleans up those links)
kb mv <id> [--type T] [--title "<title>"] [--json]   recategorize and/or rename a core entry
kb todo [--all] [--plain] [--json]   open bd TODOs, by priority then soonest due (undated last)
kb todo -a ["<title>"] [-p 0-4] [-t TYPE] [-d TEXT] [-l labels] [--due WHEN] [--json]
                                  quick-add a TODO; bare -a opens bd's interactive form
kb todo snooze <id> <when> [--json]        hide a TODO until <when>, then remind again
kb todo unsnooze <id> [--json]             end a snooze early
kb todo due <id> <when>|none [--json]      set or clear a TODO's reminder time
kb remind [check|test|install|uninstall|status] [--interval-minutes MIN] [--repeat-hours H] [--repo DIR]
                                  list what's due; `check` sends desktop notifications;
                                  `install` runs `check` on a timer (launchd/systemd --user)
kb remind push deploy|init [dir]|setup <worker-url>|sync|status|devices [rm <id>]|off
                                  also push due TODOs to your phone through your own
                                  Cloudflare Worker (setup asks for its sync token)
kb bd <any bd subcommand>         show/close/comment/create/update, resolved-repo-aware
kb sync [memos|gitlab|github|beads|all]  pull in external sources
kb secrets                        edit encrypted memos/gitlab/github credentials via sops
kb validate                       frontmatter/id/link integrity check
kb index [--full]                 rebuild/refresh the search index
kb config editor [<cmd>]          view/set the editor kb spawns when $EDITOR isn't set
kb push                           push auto-commit's history to the data repo's upstream
kb web [--port PORT] [--no-open]  local-only web UI (127.0.0.1, default port 4173);
                                  if one is already running, just opens it
kb open [--port PORT] [--no-open] alias for `kb web`
kb service install|uninstall|status|refresh [--port PORT] [--repo DIR]
                                  run `kb web` as a login service (launchd/systemd --user)
kb sync-service install|uninstall|status [--interval-minutes MIN] [--repo DIR]
                                  run `kb sync` on a recurring interval (launchd/systemd --user)
kb setup [--yes]                # guided onboarding: PATH, Claude Code skill, optional
                                 # deps (asks before each), hook, index, bd store, and (default no)
                                 # the `kb remind` timer.
                                 # --yes skips prompts and accepts defaults (scripted installs)
kb doctor                       # diagnose issues -- read-only, never writes anything
kb update [--check]             # reinstall the latest release (via uv/pipx), refresh installed services, or git pull
                                 # for a source checkout; --check reports without upgrading
```

Run `kb <command> -h` for full flag details on any of these, or `kb help` for
the top-level list.

## Machine-readable output

Every command that writes (`new`, `journal -m`/`rollup`, `inbox <id> ...`, `tag`,
`link`, `rm`, `mv`, `todo -a`/`snooze`/`unsnooze`/`due`) and every read command
that has one (`search`, `show`, `links`, `triage`, `inbox`, `todo`) accepts
`--json`. On success stdout is exactly one JSON value, with no other text. On
failure the exit code is 1 and stderr is `{"error": "..."}`. The shape of each
result is tabulated in the [Claude Code skill](../../pkb_cli/skills/kb/SKILL.md),
which is what an agent driving `kb` reads.

`kb doctor` and `kb setup` share the same detection logic (hook state, index
freshness, tool presence) — `setup` acts on it, `doctor` only reports, plus a
few deeper checks `setup` doesn't do: a hook pointing at a pkb-cli install
that no longer exists, a search index whose row count has drifted from the
files on disk, `cursors.json` sanity, and a promoted entry (tutorial/how-to/
reference/explanation) whose source (e.g. a synced usememos memo) was
re-synced with a newer `updated` timestamp after promotion — promotion is a
one-time copy, so nothing else notices the source moving on; `kb doctor`
flags it for manual review/reconciliation.

`kb new`, `kb tag`, `kb link`, `kb rm`, `kb mv`, and `kb inbox promote`/
`redirect` all reindex automatically — an entry is searchable immediately
after any of these. Only reach for `kb index` yourself after editing an
entry's body by hand (never the frontmatter).

`kb sync`, `kb update`, and `kb push` are the commands that touch the network
by default — search, journal, inbox, and links all work fully offline. Every
mutating command auto-commits to the data repo's git history if it's a git
repo (see [how-to: use a data repo](../how-to/use-a-data-repo.md)); that part
stays local. `kb push` is what sends that history to a remote — but if
`.pkb/config.yml` sets `auto_push: true`, every one of those auto-committing
commands (`kb new`, `kb journal`, `kb tag`, ...) pushes too, so "fully
offline" no longer holds once that's turned on.

Implementation: a thin Python wrapper (`pkb_cli/cli.py`) dispatching to helper
scripts and modules alongside it (e.g. `kb_web.py` for `kb web`). No
daemon/server process for any other command — every invocation but `kb web`
is a one-shot script run.

`kb service` generates and loads a per-user launchd LaunchAgent (macOS) or
systemd `--user` unit (Linux) that runs `kb web --no-open` at login, so the
web UI is always reachable without running `kb web` by hand. It still binds
127.0.0.1 only and needs no root/admin privileges. Not supported on other
platforms. `kb service refresh` repoints any installed `kb service` / `kb sync-service`
unit at the current install (keeping its repo, port, and interval) and restarts it; `kb update`
runs it after a successful reinstall so a long-running `kb web` picks up the new code.

`kb sync-service` is the same idea for `kb sync`: a launchd LaunchAgent with
`StartInterval` (macOS) or a systemd `--user` timer + oneshot service (Linux)
that runs `kb sync` every `--interval-minutes` (default 60) instead of you
running it, `/sync`, or your own cron entry by hand. Same per-user, no-root
posture; also not supported on other platforms. On Linux, if the user isn't
lingering, `install` prints the `loginctl enable-linger` command needed for
the timer to survive logout/reboot.

`kb remind` is the reminder side of `kb todo --due`. With no action it lists TODOs
whose due date has passed. `kb remind check` posts one desktop notification per due TODO
(more than five collapse into a summary)
and records when it did, so the same TODO isn't announced on every run; a TODO that stays
overdue is announced again after `--repeat-hours` (default 4) until it's closed or
snoozed. `kb remind install` runs `check` every `--interval-minutes` (default 5) the same
way `kb sync-service` runs `kb sync`, and shares its `kb service refresh` and
per-user/no-root behavior. The "already announced" record is per-machine, kept in
`$XDG_STATE_HOME/pkb-cli/reminded.json` (default `~/.local/state/`), never in the data repo.
On macOS the notification is posted by `~/Applications/kb reminders.app`, a copy of
`terminal-notifier.app` with kb's icon and its own bundle id (`dev.pkb-cli.notifier`) that
`kb remind install` builds when `terminal-notifier` is installed (`kb setup` offers it);
without it, `osascript` is used and macOS shows Script Editor's icon. Because it is a separate
app, macOS asks for notification permission for "kb reminders" on its own: `install` ends by
sending a test notification, opening System Settings if it was denied, and `kb remind test`
repeats that check (exit 1 when it didn't work). `uninstall` removes the app. On Linux,
`notify-send` is used. `kb doctor` reports the permission state.
See [how-to: get reminded about a TODO](../how-to/get-reminded-about-a-todo.md).

`kb remind push` adds phone notifications, even while the machine is asleep, through a
Worker you deploy yourself. `push deploy` sets the Worker up on your Cloudflare account, or updates
it, by driving `wrangler`: it copies the source (which ships inside the package) to
`$XDG_DATA_HOME/pkb-cli/worker` (normally `~/.local/share/pkb-cli/worker`), installs its
dependencies with pnpm or npm, logs in if needed, finds or creates the `kb-reminders` D1
database, applies migrations, and deploys, uploading freshly generated secrets with the
first deploy through a private temp file. A first deploy saves the Worker URL and sync
token (see below) and prints the phone token once; a later one only redeploys.
`kb update` runs it in a terminal: with a Worker already set up it redeploys only when
the packaged source changed since the last deploy (recorded as a hash in
`.kb-deployed`), and without one it offers setup once (a no is remembered as
`push_offer_declined` in the machine-local config). Without a terminal (the web UI's
update, a script) it only says to run `kb remind push deploy`. `push init [dir]` copies
the source out without deploying, for deploying by hand; run again it refreshes the
source and removes files an earlier copy shipped that the release dropped (tracked in a
`.kb-manifest` file, so your own files are never touched), keeping `node_modules` and the
`database_id` in `wrangler.jsonc`. Once `push setup <worker-url>` has verified the
Worker's URL and sync token (prompted for, or read from `$KB_PUSH_TOKEN` or stdin) and
saved them to the machine-local `push` key in `~/.config/kb/config.json` (see
[config](config.md#machine-local-preferences)), every `kb remind check` run replaces the
Worker's reminders with the current set. Only each unclosed, dated TODO's id, title, due time
and priority are uploaded, never descriptions or comments; a snoozed TODO reports when its snooze
ends. The Worker sends one push per run, listing ids only, and repeats an overdue one every 4
hours. The phone page, opened with the phone token, lists those TODOs by title and can close or
snooze them (1h, 3h, 1d, 1w); the Worker queues each request, and the next `check` (or `push
sync`) fetches the queue, applies it with `bd`, acknowledges it and only then uploads, so a
TODO the phone just closed isn't put back. A request that can't be applied (the TODO is gone)
prints a warning and is dropped. A failed upload prints a warning to stderr and never fails
`check`. `push sync` does that exchange immediately. `push devices` lists the subscribed phones
(id, the label the page sent, push service, when added) and `push devices rm <id>` removes
one; only this machine's sync token can, the phone's token can't. `push status` reports whether the Worker is reachable, the token accepted and a
phone subscribed (exit 1 if not), and `push off` stops uploading. `kb remind test` and
`kb remind status` also cover the phone. `$KB_PUSH_URL` and `$KB_PUSH_TOKEN` override the
saved values. See [how-to: get TODO reminders on your phone](../how-to/get-phone-reminders.md)
and [explanation: phone reminders](../explanation/design.md#phone-reminders).

`<when>` is anything `bd --due` takes -- `+6h`, `tomorrow`, `friday`, `5pm`,
`tomorrow 9am`, `2026-11-02` -- plus `30m`/`2h`/`3d`/`1w` shorthand. Minutes are
resolved by `kb` itself because bd reads a bare `m` as months.

Search also works with zero setup via `rg`/`fzf` directly on a data repo's
file tree — see [how-to: search without the
CLI](../how-to/search-without-the-cli.md). `kb web` has its own walkthrough —
see [how-to: use the web UI](../how-to/use-the-web-ui.md).
