# Contributing

## Setup

No runtime dependencies beyond `python3` (3.9+). Clone, then:

```bash
python3 -m pip install -e '.[dev]'          # puts `kb` on PATH, backed by your checkout (+ ruff)
python3 -m unittest discover -s tests -v   # the whole suite, ~25s
ruff check .                                # lint (config in pyproject.toml)
```

CI runs both on every push and PR, plus a type-check of the Worker
(`pnpm --dir pkb_cli/templates/worker check`) and `node --check` on the front-end JS.

## Code map

| File | Role |
|---|---|
| `pkb_cli/cli.py` | CLI entry point: argparse wiring and the `cmd_*` handlers for search, journal, inbox, tags, links, reminders |
| `pkb_cli/admin.py`, `pkb_cli/onboarding.py` | Maintenance commands (sync, index, validate, triage) and `kb setup`; `kb_web` runs them too |
| `pkb_cli/inbox.py` | Resolving an inbox item: promote, redirect into the journal, discard |
| `pkb_cli/search.py` | Querying the index: FTS5 keyword match with a plain-terms fallback, semantic ranking, fusion. `kb_web` imports it directly |
| `pkb_cli/journal.py` | Journal entries on disk: paths, creation, listing, appending, look-back helpers (no terminal I/O) |
| `pkb_cli/specs.py` | Project specs and decision records: markdown store under `docs/specs|decisions`, lifecycle gates (per-kind in `KINDS`), stable task ids, bd links. Returns dicts |
| `pkb_cli/agentfiles.py` | `AGENTS.md` managed block, `CLAUDE.md` import, opt-in SessionStart hook and approval-prompt settings |
| `pkb_cli/project.py` | `kb project/spec/adr/agents`: docs scaffold plus the argparse and rendering layer (`add_parsers`, shared helpers) |
| `pkb_cli/projdocs.py` | `kb docs`: create, check (misfiled/unlinked pages, broken links) and search a project's `docs/` pages |
| `pkb_cli/todos.py` | TODO logic over the `bd` CLI: list/create/update/snooze, due dates, reminder de-dup state |
| `pkb_cli/reminders.py`, `pkb_cli/notifier.py` | The reminder timer (install/info/status) and desktop notifications |
| `pkb_cli/service.py` | launchd / systemd units for `kb web`, `kb sync` and the reminder timer |
| `pkb_cli/updater.py` | `kb update`: how kb was installed, release check, applying/scheduling updates |
| `pkb_cli/health.py` | Status checks shared by `kb setup` and `kb doctor`, and the `kb doctor` command |
| `pkb_cli/sources.py` | Discovery of `sync_<name>.py` modules (a listing only) and `source_meta()`, which imports one for its `SOURCE_META` |
| `pkb_cli/proc.py` | Running kb / sibling modules as subprocesses |
| `pkb_cli/pkb_entries.py` | Entry operations shared by CLI and web: create, tag, link, rewrite, delete, move. Return dicts (`{"error": ...}` on failure); never print or exit |
| `pkb_cli/pkb_common.py` | Frontmatter I/O, ids, locking, git auto-commit, `config.yml` parsing and `DEFAULT_CONFIG` |
| `pkb_cli/kb_web.py` + `pkb_cli/templates/web/` | Stdlib HTTP server (routes registered with `@route`) and the Alpine front end |
| `pkb_cli/sync_*.py` | One ingestion script per source |
| `pkb_cli/index_fts.py` | SQLite FTS5 index (schema in `pkb_cli/templates/schema.sql`) |

Anything reusable by both `kb` and `kb web` belongs in a module of its own
(`pkb_entries.py`, `inbox.py`, `journal.py`, `pkb_common.py`...), not in `cli.py`:
`kb_web` calls those modules directly and never imports `cli`. Tests patch a
function on the module that *defines* it (`mock.patch.object(todos, "bd_list")`),
because callers look it up there; `kb_web` tests use `fakes.patched_kb_web()`,
which mocks every function `kb_web` calls into another module. 
`tests/test_module_contracts.py` fails if one goes missing, or if `kb_web`
starts importing `cli`.
