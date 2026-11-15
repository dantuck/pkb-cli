# Journal

- One file per day: `journal/YYYY/MM/YYYY-MM-DD.md`, created on first write
  via `kb journal`.
- `type: journal`, `extension: journal` frontmatter.
- Journals reference core content via `links:`, they do not duplicate it —
  e.g., a day's entry links to a `reference/` doc rather than containing the
  write-up itself.
- **Quick notes:** `kb journal -m "<note>"` appends a timestamped line to
  today's entry (creating it first if needed) without opening an editor.
  `kb journal <date> -m "<note>"` backfills a specific day. Bare `-m` with no
  text opens a scratch buffer in `$EDITOR` for a longer, multiline note
  instead.
- **Monthly rollup:** `kb journal rollup [YYYY-MM]` generates/refreshes
  `journal/summaries/YYYY-MM.md` — that month's daily entries
  concatenated onto one page plus a tag-frequency line — for browsing without
  opening 30 daily files. Defaults to last month; rerunning it for the same
  month refreshes the same file/id in place rather than minting a new one.
  It is a `type: journal` entry, not core content: the core Diataxis dirs stay
  hand-curated, so a generated page never shows up among your `reference`
  entries. A page an older kb wrote to `reference/journal-summaries/` is
  moved here, keeping its id, the next time that month is rolled up.
- **Look-back surfacing:** opening today's entry (`kb journal`, no explicit
  date) prints/pins an "on this day" section — entries from 1 week ago, 1
  month ago, and this month/day in earlier years, whichever exist — so the
  journal resurfaces past entries instead of only ever being written forward.
  `kb journal --tag <tag>` lists past entries by tag, newest first, for
  following one theme over time.
