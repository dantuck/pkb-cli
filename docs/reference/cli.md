# CLI command reference

```
kb new <type> "<title>" [--tags a,b] [--links id,id] [--body TEXT]
                                  type: tutorial|how-to|reference|explanation
kb journal [<date>] [-m [TEXT]]  today (default) or YYYY-MM-DD; -m TEXT quick-adds a
                                  timestamped line, bare -m opens $EDITOR for multiline input
kb journal --tag <tag>            past entries carrying <tag>, newest first
kb journal rollup [YYYY-MM]       generate/refresh that month's summary page (default: last month)
kb search "<query>" [--type T] [--tag TAG] [--all] [--plain] [--json]
kb inbox [--plain]                interactive triage: promote / redirect / discard
kb inbox <id> promote <type>|redirect|discard   non-interactive triage (no terminal needed)
kb triage [--json]                read-only overdue-inbox report
kb links <id> [--json]            forward links + backlinks
kb show <id> [--json]             print an entry's full content by id
kb tag <id> [add|rm <tag>...]     view/add/remove tags (never hand-edit frontmatter)
kb link <id> [add|rm <id>...]     view/add/remove links, same reasoning
kb todo [--all] [--plain] [--json]   open bd TODOs, sorted by priority
kb todo -a ["<title>"] [-p 0-4] [-t TYPE] [-d TEXT] [-l labels]
                                  quick-add a TODO; bare -a opens bd's interactive form
kb bd <any bd subcommand>         show/close/comment/create/update, resolved-repo-aware
kb sync [memos|gitlab|github|beads|all]  pull in external sources
kb secrets                        edit encrypted memos/gitlab/github credentials via sops
kb validate                       frontmatter/id/link integrity check
kb index [--full]                 rebuild/refresh the search index
kb config editor [<cmd>]          view/set the editor kb spawns when $EDITOR isn't set
kb push                           push auto-commit's history to the data repo's upstream
kb web [--port PORT] [--no-open]  local-only web UI (127.0.0.1, default port 4173)
kb service install|uninstall|status [--port PORT] [--repo DIR]
                                  run `kb web` as a login service (launchd/systemd --user)
kb sync-service install|uninstall|status [--interval-minutes MIN] [--repo DIR]
                                  run `kb sync` on a recurring interval (launchd/systemd --user)
kb setup [--yes]                # guided onboarding: PATH, Claude Code skill, optional
                                 # deps (asks before each), hook, index, bd store.
                                 # --yes skips prompts and accepts defaults (scripted installs)
kb doctor                       # diagnose issues -- read-only, never writes anything
kb update [--check]             # pull tool updates (git pull, or tarball refresh if
                                 # installed via install.sh); --check reports without pulling
```

Run `kb <command> -h` for full flag details on any of these, or `kb help` for
the top-level list.

`kb doctor` and `kb setup` share the same detection logic (hook state, index
freshness, tool presence) — `setup` acts on it, `doctor` only reports, plus a
few deeper checks `setup` doesn't do: a hook pointing at a pkb-cli install
that no longer exists, a search index whose row count has drifted from the
files on disk, and `cursors.json` sanity.

`kb new`, `kb tag`, `kb link`, and `kb inbox promote`/`redirect` all reindex
automatically — an entry is searchable immediately after any of these. Only
reach for `kb index` yourself after editing an entry's body by hand (never
the frontmatter).

`kb sync`, `kb update`, and `kb push` are the commands that touch the network
by default — search, journal, inbox, and links all work fully offline. Every
mutating command auto-commits to the data repo's git history if it's a git
repo (see [how-to: use a data repo](../how-to/use-a-data-repo.md)); that part
stays local. `kb push` is what sends that history to a remote — but if
`.pkb/config.yml` sets `auto_push: true`, every one of those auto-committing
commands (`kb new`, `kb journal`, `kb tag`, ...) pushes too, so "fully
offline" no longer holds once that's turned on.

Implementation: a thin Python wrapper (`scripts/kb`) dispatching to helper
scripts and modules alongside it (e.g. `kb_web.py` for `kb web`). No
daemon/server process for any other command — every invocation but `kb web`
is a one-shot script run.

`kb service` generates and loads a per-user launchd LaunchAgent (macOS) or
systemd `--user` unit (Linux) that runs `kb web --no-open` at login, so the
web UI is always reachable without running `kb web` by hand. It still binds
127.0.0.1 only and needs no root/admin privileges. Not supported on other
platforms.

`kb sync-service` is the same idea for `kb sync`: a launchd LaunchAgent with
`StartInterval` (macOS) or a systemd `--user` timer + oneshot service (Linux)
that runs `kb sync` every `--interval-minutes` (default 60) instead of you
running it, `/sync`, or your own cron entry by hand. Same per-user, no-root
posture; also not supported on other platforms. On Linux, if the user isn't
lingering, `install` prints the `loginctl enable-linger` command needed for
the timer to survive logout/reboot.

Search also works with zero setup via `rg`/`fzf` directly on a data repo's
file tree — see [how-to: search without the
CLI](../how-to/search-without-the-cli.md). `kb web` has its own walkthrough —
see [how-to: use the web UI](../how-to/use-the-web-ui.md).
