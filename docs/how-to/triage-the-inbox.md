# How to triage the inbox

`inbox/` is a queue, not storage — see [explanation: design
principles](../explanation/design.md) for why nothing lives there
permanently. This is the day-to-day procedure for clearing it.

## Check what's waiting

```bash
kb inbox
```

Lists current inbox contents with age, and opens an interactive
promote/redirect/discard picker if you have `fzf` and a terminal.

## Resolve one item

Every inbox file ends in one of three states:

```bash
kb inbox <id> promote how-to    # rewrite/move into core content (tutorials/how-to/reference/explanation)
kb inbox <id> redirect          # append into that day's journal entry instead
kb inbox <id> discard           # delete outright, not worth keeping
```

These also work non-interactively (no `fzf`/terminal needed), which is what
makes them scriptable.

## Check for overdue items

```bash
kb triage
```

Read-only: flags anything older than the configured threshold (default 14
days). Pass `--json` for scripts or monitoring — run it on the same cadence
as ingestion so nothing quietly rots in the inbox.
