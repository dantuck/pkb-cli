
## Personal knowledge base (kb)

Notes, journal entries, and TODOs for this project live in the `kb` CLI
(a personal Diataxis knowledge base shared across projects), not a
project-local scratch file — tag anything you add with `pkb-cli` so it's
filterable later:

- `kb journal -m "..."` then `kb tag <id> add pkb-cli`
- `kb todo -a "title" -l pkb-cli`
- `kb new <type> "title" --tags pkb-cli`

## Previewing the phone page

After changing anything in `pkb_cli/templates/worker/public/`, run
`scripts/dev-worker.py` (fake TODOs, token `demo`, http://localhost:4198) and check
the page in a browser before calling it done: the list, the settings shelf, and
offline (stop the script and reload). `scripts/dev-web.sh` does the same for `kb web`.
Stop the preview when finished.
