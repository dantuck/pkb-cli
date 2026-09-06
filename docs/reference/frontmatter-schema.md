# Frontmatter schema

Every file in the data repo carries this frontmatter:

```yaml
---
id: 2026-08-31-1423              # required, sortable unique id: YYYY-MM-DD-HHMM[-n]
created: 2026-08-31T14:23:00-06:00   # required, ISO 8601 with offset
updated: 2026-08-31T14:23:00-06:00   # required, bumped on every edit
type: reference                   # required: tutorial|how-to|reference|explanation|journal|inbox|source
extension: null                   # null | journal | inbox | source — null means fully-triaged core content
source: manual                    # manual|memos|gitlab|github|beads
source_id: null                   # external id (memo id, issue iid/number, bead id) if source != manual
tags: [tailscale, networking]
links: [2026-08-20-0900]          # ids of related pkb entries
title: "Tailscale sidecar config for Synology"
---
```

## Validation rules

Enforced by `kb validate` (also run in CI / pre-commit):

- `type` must be one of the 7 enum values above.
- If `type` is one of the 4 Diataxis types, `extension` **must** be `null`.
- If `type` is `journal`, `inbox`, or `source`, `extension` must match `type`.
- `id` must be unique repo-wide and match the timestamp-based pattern.
- `links` targets must resolve to an existing `id` in the repo (broken-link
  check).
- Any file failing validation blocks commit (pre-commit hook) and is flagged
  in CI.

`kb tag` and `kb link` are the sanctioned way to add/remove tags and links —
never hand-edit frontmatter directly.
