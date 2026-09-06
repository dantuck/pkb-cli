# pkb-cli

The `kb` command-line tool for a [Diataxis](https://diataxis.fr/)-based personal
knowledge base. This repo is the tool only — it has no opinion about your actual
notes, which live in a separate (typically private) data repo containing
`tutorials/`, `how-to/`, `reference/`, `explanation/`, `journal/`, `inbox/`,
`sources/`, and a `.pkb/` config directory.

**Documentation** (also organized by Diataxis — see [docs/](docs/)):

- New here? Start with the [getting started tutorial](docs/tutorials/getting-started.md).
- Task in mind? See the [how-to guides](docs/how-to/) — pointing `kb` at a
  data repo, searching with no tooling at all, triaging the inbox, ingestion
  credentials, the Claude Code skill, the web UI.
- Looking something up? See [reference](docs/reference/) — the full CLI
  surface, repo layout, frontmatter schema, ingestion contract, journal
  conventions.
- Curious why it's built this way? See [explanation](docs/explanation/) — design
  principles and the search architecture.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/dantuck/pkb-cli/main/install.sh | bash
```

Downloads a tarball snapshot of this repo to `~/pkb-cli` (override with
`$PKB_CLI_HOME`) and symlinks `kb` onto your PATH. Safe to re-run — updates in
place and re-runs setup idempotently. **`python3` is the only requirement** —
no `git`, `curl`, or `tar` needed; the installer's own download/extract and
`kb update` both use Python's stdlib `urllib`/`tarfile` against the GitHub API,
not a git checkout. (If `$PKB_CLI_HOME` already happens to be a git checkout —
e.g. you cloned it yourself to contribute — the installer detects that and
runs `git pull` there instead, so that workflow isn't disrupted.)

The symlink target is the first of `~/.local/bin`, `/opt/homebrew/bin`, or
`/usr/local/bin` that already exists **and** is already on your `PATH` (in that
order), so it can install without editing your shell profile. On most Macs
with Homebrew that's `/opt/homebrew/bin`; on a bare-bones machine with none of
those set up yet, it creates `~/.local/bin` and prints the `export PATH=...`
line to add.

Next: point it at a data repo — see [how-to: use a data
repo](docs/how-to/use-a-data-repo.md), or walk through the full
[getting started tutorial](docs/tutorials/getting-started.md).

## Layout

```
pkb-cli/
  scripts/      the kb CLI and everything it dispatches to
  skills/kb/    Claude Code skill (SKILL.md), installed via `kb setup --install-skill`
  templates/    schema.sql (loaded at runtime, never copied into a data repo)
                and secrets.env.example (documentation only)
  docs/         tutorials/, how-to/, reference/, explanation/
  install.sh
```

See [reference: repository layout](docs/reference/repository-layout.md) for
the data repo's own expected layout.

## Development

No external dependencies to install -- the test suite runs on the stdlib
`unittest` runner:

```bash
python3 -m unittest discover -s tests -v
```

CI ([.github/workflows/test.yml](.github/workflows/test.yml)) runs the same
command on Python 3.9 and 3.12 for every push/PR against `main`.

## License

[Apache License 2.0](LICENSE).
