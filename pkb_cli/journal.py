"""Journal entries on disk: paths, creation, listing, appending, and the look-back (on-this-day) helpers. No terminal I/O here, so kb web can share it."""
import calendar
import contextlib
import os
from datetime import datetime, timedelta

from . import pkb_common as pc
from . import pkb_entries

def _looks_like_date(text):
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _journal_path(root, target):
    dir_path = os.path.join(root, "journal", f"{target.year:04d}", f"{target.month:02d}")
    return os.path.join(dir_path, f"{target.isoformat()}.md")


def _ensure_journal_entry(root, target):
    """Create target's journal file (with frontmatter) if it doesn't exist yet.
    Returns its path either way."""
    file_path = _journal_path(root, target)
    if not os.path.exists(file_path):
        all_ids = pc.collect_existing_ids(root)
        now = pc.now_iso()
        entry_id = pc.gen_id(all_ids, dt=datetime.combine(target, datetime.now().time()))
        fm = {
            "id": entry_id,
            "created": now,
            "updated": now,
            "type": "journal",
            "extension": "journal",
            "source": "manual",
            "source_id": None,
            "tags": [],
            "links": [],
            "title": target.isoformat(),
        }
        pc.write_entry(file_path, fm, f"# {target.isoformat()}\n\n")
    return file_path


def list_journal_entries(root, limit=None):
    """(date_str, path) for journal files, newest first.

    Walks journal/YYYY/MM/ in descending order and stops as soon as `limit`
    entries are collected, instead of always listing every entry ever written
    just to keep the most recent few -- callers like cmd_journal's "recent
    entries" hint only ever want a handful.
    """
    journal_dir = os.path.join(root, "journal")
    if not os.path.isdir(journal_dir):
        return []

    def _subdirs_desc(path):
        return sorted((d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))), reverse=True)

    entries = []
    for year in (y for y in _subdirs_desc(journal_dir) if y.isdigit()):  # not journal/summaries/
        year_dir = os.path.join(journal_dir, year)
        for month in _subdirs_desc(year_dir):
            month_dir = os.path.join(year_dir, month)
            names = sorted((n for n in os.listdir(month_dir) if n.endswith(".md")), reverse=True)
            for name in names:
                entries.append((name[:-3], os.path.join(month_dir, name)))
                if limit and len(entries) >= limit:
                    return entries
    return entries


def _shift_months(date, delta):
    """date, shifted by `delta` months, clamped to the shifted month's length
    (e.g. Aug 31 - 1 month -> Jul 31, not an overflow into August)."""
    month_index = date.month - 1 + delta
    year = date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(date.day, calendar.monthrange(year, month)[1])
    return date.replace(year=year, month=month, day=day)


def _on_this_day(root, target):
    """(label, date_str, path) for past journal entries at the offsets a
    "look back" feature cares about -- a week ago, a month ago, and this same
    month/day in each earlier year -- restricted to offsets that actually have
    an entry, so this never pads the list with empty prompts to fill in the
    past."""
    candidates = [("1 week ago", target - timedelta(days=7)),
                  ("1 month ago", _shift_months(target, -1))]
    for years in range(1, 11):
        try:
            d = target.replace(year=target.year - years)
        except ValueError:  # target is Feb 29 and this year isn't a leap year
            d = target.replace(year=target.year - years, day=28)
        label = "1 year ago" if years == 1 else f"{years} years ago"
        candidates.append((label, d))

    seen, results = set(), []
    for label, d in candidates:
        if d >= target or d in seen:
            continue
        seen.add(d)
        path = _journal_path(root, d)
        if os.path.exists(path):
            results.append((label, d.isoformat(), path))
    return results


def journal_append(root, target, text):
    """Append `text` to target's journal entry, creating it (with frontmatter)
    first if needed. No editor involved -- for jotting something down without
    leaving the shell (or, for `kb web`'s quick-capture box, without leaving the
    browser). A single line becomes a timestamped bullet; multiple lines (e.g.
    from _journal_add_editor) become a timestamped heading block, same shape
    `kb inbox`'s redirect action already uses.

    Returns {"path": <relpath>, "time": "HH:MM"} on success, or None if `text`
    was empty (nothing written). Shared by the CLI and the web capture endpoint
    so there's exactly one place this logic lives."""
    text = text.strip("\n")
    if not text:
        return None

    file_path = _ensure_journal_entry(root, target)
    fm, body = pc.read_entry(file_path)
    stamp = datetime.now().strftime("%H:%M")
    block = f"## {stamp}\n\n{text}\n" if "\n" in text else f"- {stamp}  {text}\n"
    body = body.rstrip("\n") + f"\n\n{block}"
    fm["updated"] = pc.now_iso()
    pc.write_entry(file_path, fm, body)
    pkb_entries._reindex_fast(root)
    pc.git_autocommit(root, f"journal: {target.isoformat()}")
    return {"path": os.path.relpath(file_path, root), "time": stamp}


def journal_months(root):
    """YYYY-MM for every month with at least one journal entry, newest first --
    the months `kb journal rollup` has something to roll up."""
    return list(dict.fromkeys(date[:7] for date, _path in list_journal_entries(root)))


def _rollup_month(month_arg):
    """(year, month) for `month_arg` (YYYY-MM), or the previous calendar month
    if not given -- the current month is usually still in progress, so it's a
    poor default for "summarize what happened"."""
    if month_arg:
        try:
            dt = datetime.strptime(month_arg, "%Y-%m")
        except ValueError:
            return None
        return dt.year, dt.month
    today = datetime.now().date()
    year, month = today.year, today.month - 1
    if month == 0:
        year, month = year - 1, 12
    return year, month


def journal_rollup(root, month_arg, json_out=False):
    """Generate/refresh journal/summaries/YYYY-MM.md: every day's entry in the
    month concatenated under a heading, plus a tag-frequency line, so a month of
    daily files can be browsed as one page instead of opened one by one.
    Deliberately not AI summarization -- just the raw entries assembled in one
    place; the point is browsability, not compression.

    A journal-extension entry, not core content: the core Diataxis dirs are
    hand-curated only, and a machine-written page there would turn up in
    `reference` searches. Pages an older kb wrote to reference/journal-summaries/
    are moved here (same id) the next time that month is rolled up."""
    parsed = _rollup_month(month_arg)
    if parsed is None:
        return pc.report_error(f"invalid month '{month_arg}' -- expected YYYY-MM", json_out)
    year, month = parsed
    month_label = f"{year:04d}-{month:02d}"

    month_dir = os.path.join(root, "journal", f"{year:04d}", f"{month:02d}")
    if not os.path.isdir(month_dir):
        return pc.report_error(f"no journal entries found for {month_label}", json_out)

    days = []
    for name in sorted(os.listdir(month_dir)):
        if not name.endswith(".md"):
            continue
        fm, body = pc.read_entry(os.path.join(month_dir, name))
        days.append((name[:-3], fm, body))
    if not days:
        return pc.report_error(f"no journal entries found for {month_label}", json_out)

    tag_counts, day_ids, sections = {}, [], []
    for date_str, fm, body in days:
        if fm.get("id"):
            day_ids.append(fm["id"])
        for t in fm.get("tags") or []:
            tag_counts[t] = tag_counts.get(t, 0) + 1

        text = body.strip("\n")
        heading = f"# {date_str}"
        if text.startswith(heading):
            text = text[len(heading):].lstrip("\n")
        sections.append(f"## {date_str}\n\n{text.strip() or '(no content)'}\n")

    out_path = os.path.join(root, "journal", "summaries", f"{month_label}.md")
    legacy_path = os.path.join(root, "reference", "journal-summaries", f"{month_label}.md")
    now = pc.now_iso()
    prior_path = out_path if os.path.exists(out_path) else legacy_path if os.path.exists(legacy_path) else None
    existing_fm = pc.read_entry(prior_path)[0] if prior_path else None
    if existing_fm and existing_fm.get("id"):
        entry_id, created = existing_fm["id"], existing_fm.get("created", now)
    else:
        entry_id = pc.gen_id(pc.collect_existing_ids(root), dt=datetime(year, month, 1))
        created = now

    fm = {
        "id": entry_id,
        "created": created,
        "updated": now,
        "type": "journal",
        "extension": "journal",
        "source": "manual",
        "source_id": None,
        "tags": ["journal-summary"],
        "links": day_ids,
        "title": f"Journal summary: {month_label}",
    }

    body = f"# Journal summary: {month_label}\n\n{len(days)} entries.\n"
    if tag_counts:
        top = sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        body += "tags: " + ", ".join(f"{t} ({n})" for t, n in top) + "\n"
    body += "\n---\n\n" + "\n".join(sections)

    pc.write_entry(out_path, fm, body)
    if prior_path == legacy_path:
        os.remove(legacy_path)
        with contextlib.suppress(OSError):  # only if that was the last one
            os.rmdir(os.path.dirname(legacy_path))
    pkb_entries._reindex_fast(root)
    pc.emit_result(json_out, {"id": entry_id, "path": out_path, "month": month_label, "entries": len(days)}, out_path)
    return 0
