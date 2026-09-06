# How to point `kb` at a data repo

`kb` has no opinion about your notes — it operates on whichever data repo it
resolves. This covers both starting points.

## Clone an existing data repo

```bash
git clone <your-private-notes-repo-url> ~/.pkb
cd ~/.pkb
kb setup       # pre-commit hook, search index, local bd store if `bd` is installed
```

## Start a data repo from scratch

There's no `kb init`. Create the `.pkb/` marker directory yourself, then run
`kb setup` from inside it:

```bash
mkdir -p ~/.pkb/.pkb
cd ~/.pkb
git init
kb setup
```

Content type directories (`tutorials/`, `how-to/`, `reference/`,
`explanation/`) don't need to exist ahead of time — `kb new` creates them on
first use.

## How `kb` finds your data repo

`kb` resolves which data repo to use by walking up from the current directory
looking for a `.pkb/` subdirectory, so a repo you're actually standing inside
(e.g. a separate work/scratch pkb elsewhere) always wins. If that walk finds
nothing, it falls back to `~/.pkb`, so day-to-day commands (`kb search`, `kb
triage`, `kb inbox`, ...) work from anywhere without `cd`-ing into a specific
repo first.

See [reference: repository layout](../reference/repository-layout.md) for
what `kb setup` expects to find or create inside the data repo.
