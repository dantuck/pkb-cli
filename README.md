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
  surface, `config.yml` keys, repo layout, frontmatter schema, ingestion contract, journal
  conventions.
- Curious why it's built this way? See [explanation](docs/explanation/) — design
  principles and the search architecture.

## Install

Requires Python 3.9+ and [uv](https://docs.astral.sh/uv/) or
[pipx](https://pipx.pypa.io/):

```bash
uv tool install git+https://github.com/dantuck/pkb-cli     # or: pipx install git+https://github.com/dantuck/pkb-cli
kb setup
```

or, in one step (installs the latest release, then runs `kb setup --yes`):

```bash
curl -fsSL https://raw.githubusercontent.com/dantuck/pkb-cli/main/install.sh | bash
```

`kb` has no Python dependencies. Update with `kb update` (it checks the latest
GitHub release and runs `uv tool upgrade` / `pipx upgrade` for you). To hack on
it, `git clone` the repo and `pip install -e .`; `kb update` then does a
`git pull` instead.

Next: point it at a data repo — see [how-to: use a data
repo](docs/how-to/use-a-data-repo.md), or walk through the full
[getting started tutorial](docs/tutorials/getting-started.md).

## Layout

```
pkb-cli/
  pkb_cli/              the installable package (the `kb` command)
    cli.py              argparse wiring and command handlers
    sync_*.py, ...      ingestion, indexing, validation, `kb web` server
    skills/kb/          Claude Code skill (SKILL.md), `kb setup` offers to install it
    templates/          schema.sql (loaded at runtime, never copied into a data repo),
                        secrets.env.example (documentation only), and web/ (the
                        `kb web` front end: plain HTML/CSS/JS + vendored Alpine)
  tests/                stdlib unittest suite
  docs/                 tutorials/, how-to/, reference/, explanation/
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
command on Python 3.9 and 3.12, plus `ruff check .`, for every push/PR against
`main`. See [CONTRIBUTING.md](CONTRIBUTING.md) for the code map and how to add
a sync source.

## License

[Apache License 2.0](LICENSE).
