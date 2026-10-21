# How to search by meaning

`kb search` normally matches words. With the optional `semantic` extra it also
matches *meaning* — "remote access to my home network" finds the Tailscale
note even if it never uses those words. It runs a small embedding model inside
the `kb` process: no server, no daemon, and no network at search time.

## Install

Needs Python 3.10+.

```bash
uv tool install 'pkb-cli[semantic] @ git+https://github.com/dantuck/pkb-cli'
# or, with the installer: PKB_CLI_SEMANTIC=1 curl -fsSL .../install.sh | bash
kb index     # fetches the model once (~30 MB), then embeds every entry
```

`kb setup` does the same `kb index` step. Afterwards, plain `kb search "..."`
combines keyword and semantic matches; there is nothing else to switch on.

## Use

```bash
kb search "tests that fail randomly"            # keyword + semantic, fused
kb search "baking" --semantic                   # meaning only (errors if unavailable)
kb search "tailscale" --keyword                 # exact FTS5 match only
```

## Keep it up to date

You normally don't have to. Every command that writes (`new`, `journal -m`,
`tag`, `mv`, `rm`, `inbox`, `sync`, the web UI) re-embeds just what changed.
If the vectors are missing — a fresh clone, an entry hand-edited outside `kb`
— `kb search` builds them first and says so on stderr. `kb doctor` reports
`N/M entries embedded`. `kb index --full` rebuilds the keyword index from
scratch and reuses any vector whose text and model haven't changed.

## Keep it entirely offline

Only `kb index` and `kb setup` may fetch the model, and only when it isn't
already on disk. If your network blocks Hugging Face, copy the model directory
over by any approved route and point kb at it:

```bash
export PKB_SEMANTIC_MODEL=/path/to/potion-base-8M     # a local directory
kb index
```

Without the model, `kb search` falls back to keyword search and tells you why;
nothing breaks. To turn the feature off in a data repo, set
`semantic.enabled: false` in `.pkb/config.yml` (see [config
reference](../reference/config.md)).
