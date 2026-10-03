# Configuration reference

Per-data-repo settings live in `.pkb/config.yml` (committed, so they apply to
every clone). Every key is optional; anything omitted falls back to the
defaults below (`DEFAULT_CONFIG` in `pkb_cli/pkb_common.py`). Nested
sections are merged key by key, so overriding one `sync.memos` key keeps the
rest.

| Key | Default | Meaning |
|---|---|---|
| `inbox_triage_days` | `14` | Age in days after which `kb triage` flags an inbox item as overdue. |
| `fts_default_scope` | `core` | Which content `kb search` covers by default (`--all` widens it). |
| `auto_push` | `false` | Push after every auto-commit. See [how-to: use a data repo](../how-to/use-a-data-repo.md). |
| `sync.memos.base_url_env` / `token_env` | `PKB_MEMOS_URL` / `PKB_MEMOS_TOKEN` | Names of the env vars holding the usememos URL and token. |
| `sync.memos.inbox_min_length` | `280` | Memos at least this long land in the inbox; shorter ones stay in `sources/`. |
| `sync.gitlab.project_env` | `PKB_GITLAB_PROJECT` | Env var naming the GitLab project. |
| `sync.gitlab.inbox_all_issues` | `true` | Send every synced issue to the inbox. |
| `sync.github.repo_env` | `PKB_GITHUB_REPO` | Env var naming the GitHub repo. |
| `sync.github.inbox_all_issues` | `true` | Send every synced issue to the inbox. |
| `sync.beads.cli` | `bd` | Beads executable to invoke. |
| `sync.beads.inbox_all` | `false` | Send every synced bead to the inbox. |

Machine-local preferences that should *not* be committed (currently the
editor) are set with `kb config editor`, not here. Credentials never go in
this file; see [how-to: set up ingestion
credentials](../how-to/set-up-ingestion-credentials.md).
