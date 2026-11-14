"""Journal entries on disk: paths, creation, listing, appending, and the look-back (on-this-day) helpers. No terminal I/O here, so kb web can share it."""
import calendar
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
    for year in _subdirs_desc(journal_dir):
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
