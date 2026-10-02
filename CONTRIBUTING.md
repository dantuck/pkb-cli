# Contributing

## Setup

No dependencies beyond `python3` (3.9+). Clone, then:

```bash
python3 -m unittest discover -s tests -v   # the whole suite, ~20s
python3 -m pip install ruff && ruff check . # lint (config in pyproject.toml)
```

CI runs both on every push and PR.

## Code map

| File | Role |
|---|---|
| `scripts/kb` | CLI entry point: argparse wiring and `cmd_*` handlers, doctor/setup/update, service installers |
| `scripts/pkb_entries.py` | Entry operations shared by CLI and web: create, tag, link, rewrite, delete, move. Return dicts (`{"error": ...}` on failure); never print or exit |
| `scripts/pkb_common.py` | Frontmatter I/O, ids, locking, git auto-commit, `config.yml` parsing and `DEFAULT_CONFIG` |
| `scripts/kb_web.py` + `templates/web/` | Stdlib HTTP server (routes registered with `@route`) and the Alpine front end |
| `scripts/sync_*.py` | One ingestion script per source |
| `scripts/index_fts.py` | SQLite FTS5 index (schema in `templates/schema.sql`) |

`kb` has no `.py` suffix, so tests load it with `SourceFileLoader` (see any
`tests/test_entry_*.py`). Anything reusable by both `kb` and `kb web` belongs in
`pkb_entries.py` or `pkb_common.py`, not in `kb`.

## Conventions

- **Never hand-edit frontmatter** in code or docs — go through `entry_set_tags`,
  `entry_set_links`, etc., which lock, validate, reindex, and auto-commit.
- Mutating operations must reindex (`_reindex_fast`) and `pc.git_autocommit`.
- Tests patch `pkb_entries._reindex_fast` rather than building a real index.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/).

## Adding a sync source

1. Drop `scripts/sync_<name>.py` in place — `kb sync <name>` and `kb sync all`
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
