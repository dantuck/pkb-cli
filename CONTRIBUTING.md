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
| `pkb_cli/cli.py` | CLI entry point: argparse wiring and the `cmd_*` handlers for search, journal, inbox, tags, links, setup |
| `pkb_cli/search.py` | Querying the index: FTS5 keyword match with a plain-terms fallback, semantic ranking, fusion. `kb_web` imports it directly |
| `pkb_cli/journal.py` | Journal entries on disk: paths, creation, listing, appending, look-back helpers (no terminal I/O) |
| `pkb_cli/project.py` | Project-side docs: `kb project`, `kb spec`, `kb adr`, `kb agents` (specs/ADRs as markdown in the project, AGENTS.md/CLAUDE.md). Registers its own parsers via `add_parsers` |
| `pkb_cli/projdocs.py` | `kb docs`: create, check (misfiled/unlinked pages, broken links) and search a project's `docs/` pages |
| `pkb_cli/todos.py` | TODO logic over the `bd` CLI: list/create/update/snooze, due dates, reminder de-dup state |
| `pkb_cli/reminders.py`, `pkb_cli/notifier.py` | The reminder timer (install/info/status) and desktop notifications |
| `pkb_cli/service.py` | launchd / systemd units for `kb web`, `kb sync` and the reminder timer |
| `pkb_cli/updater.py` | `kb update`: how kb was installed, release check, applying/scheduling updates |
| `pkb_cli/health.py` | Status checks shared by `kb setup` and `kb doctor` |
| `pkb_cli/sources.py` | Discovery of `sync_<name>.py` modules and their `SOURCE_META` |
| `pkb_cli/proc.py` | Running kb / sibling modules as subprocesses |
| `pkb_cli/pkb_entries.py` | Entry operations shared by CLI and web: create, tag, link, rewrite, delete, move. Return dicts (`{"error": ...}` on failure); never print or exit |
| `pkb_cli/pkb_common.py` | Frontmatter I/O, ids, locking, git auto-commit, `config.yml` parsing and `DEFAULT_CONFIG` |
| `pkb_cli/kb_web.py` + `pkb_cli/templates/web/` | Stdlib HTTP server (routes registered with `@route`) and the Alpine front end |
| `pkb_cli/sync_*.py` | One ingestion script per source |
| `pkb_cli/index_fts.py` | SQLite FTS5 index (schema in `pkb_cli/templates/schema.sql`) |

Anything reusable by both `kb` and `kb web` belongs in `pkb_entries.py` or
`pkb_common.py`, not in `cli.py`. Tests patch a function on the module that
*defines* it (`mock.patch.object(todos, "bd_list")`), because callers look it up
there. `kb_web` still reaches a few functions through the `cli` module, which
re-exports them; `tests/test_module_contracts.py` fails if one goes missing.

## Releasing

Releases are automated; don't edit `version` or tag by hand.

1. Open PRs with a [Conventional Commit](https://www.conventionalcommits.org/)
   title (`feat:`, `fix:`, `feat!:` for breaking, ...) and **squash-merge** them —
   the `pr-title` check enforces the format, since the title becomes the commit
   release-please reads. `feat` bumps the minor version, `fix` the patch
   (`docs:`, `chore:`, `test:` etc. don't trigger a release on their own).
2. On every push to `main`, the `release` workflow opens or updates a
   **release PR** that bumps `pyproject.toml` and `CHANGELOG.md`.
3. Merge that PR when you want to ship. The workflow tags `vX.Y.Z`, creates the
   GitHub release, and attaches the built sdist/wheel — which is what
   `kb update` and `install.sh` pick up.

One-time repo setup: Settings → Actions → General → "Allow GitHub Actions to
create and approve pull requests". If `main` requires status checks, also add a
`RELEASE_PLEASE_TOKEN` secret (a PAT or app token) so CI runs on the release PR.

## Conventions

- **Never hand-edit frontmatter** in code or docs — go through `entry_set_tags`,
  `entry_set_links`, etc., which lock, validate, reindex, and auto-commit.
- Mutating operations must reindex (`_reindex_fast`) and `pc.git_autocommit`.
- Tests patch `pkb_entries._reindex_fast` rather than building a real index.
- Commits and PR titles follow [Conventional Commits](https://www.conventionalcommits.org/) — releases are derived from them.

## Adding a sync source

1. Drop `pkb_cli/sync_<name>.py` in place — `kb sync <name>` and `kb sync all`
   pick it up automatically.
2. Declare `SOURCE_META` and follow the cursor/idempotency rules in
   [reference: ingestion](docs/reference/ingestion.md), including the
   read-only mirror convention (`pc.write_entry_readonly` + trailing upstream
   link).
3. Add defaults under `sync.<name>` in `DEFAULT_CONFIG` and document them in
   [reference: config](docs/reference/config.md).
4. Add tests; `tests/test_sync_memos.py` shows how to stub the network.

## Documentation

Docs follow [Diataxis](docs/README.md). `tests/test_docs.py` fails if a
subcommand is missing from `docs/reference/cli.md`, a config key is missing
from `docs/reference/config.md`, or a relative link is broken — update the docs
in the same change as the code.
