# How to search a data repo with no tooling at all

`rg` and `fzf` operate directly on the markdown tree. This works immediately
after `git clone`, needs no index, and is always the fallback if
`.pkb/*.db` doesn't exist yet — this is what [Tier
1](../explanation/search-architecture.md) means.

## ripgrep

```bash
# full-text search across core content
rg -n "tailscale" tutorials how-to reference explanation

# case-insensitive, with headings
rg -in --heading "sidecar"

# search everything including journal/inbox/sources
rg -n "tailscale" .

# search only frontmatter tags
rg -n "^tags:.*networking"

# find an entry by id
rg -rn "^id: 2026-08-31-1423$"
```

## fzf (interactive fuzzy find)

```bash
# fuzzy-find a file by name/path, open in $EDITOR
fzf --preview 'bat --color=always {}' | xargs -r $EDITOR

# combine with rg for interactive full-text search (requires fzf --bind reload)
rg -n "" tutorials how-to reference explanation | fzf
```

Install with `brew install ripgrep fzf` if not already present.

## When to move to Tier 2

Once you want relevance ranking, faceted queries (`--type`, `--tag`), or the corpus
grows large enough that `rg` output is unwieldy, run `kb index` and use `kb search`
instead (see [reference: CLI commands](../reference/cli.md)).
