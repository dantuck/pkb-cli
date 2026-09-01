"""Shared helpers for pkb scripts: frontmatter parsing, id generation, config/cursor IO.

No third-party dependencies (no pyyaml) so the toolchain stays fully offline/portable
with only the stdlib.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

CORE_TYPES = ("tutorial", "how-to", "reference", "explanation")
EXTENSION_TYPES = ("journal", "inbox", "source")
ALL_TYPES = CORE_TYPES + EXTENSION_TYPES
VALID_SOURCES = ("manual", "memos", "gitlab", "beads")

ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}(-\d+)?$")

TYPE_DIR = {
    "tutorial": "tutorials",
    "how-to": "how-to",
    "reference": "reference",
    "explanation": "explanation",
    "journal": "journal",
    "inbox": "inbox",
    "source": "sources",
}

CONTENT_DIRS = ["tutorials", "how-to", "reference", "explanation", "journal", "inbox", "sources"]


def find_repo_root(start=None):
    """Resolve the pkb data repo root for `start` (default cwd).

    The central kb lives at ~/.pkb itself -- content dirs are *inside* it
    (~/.pkb/tutorials), not siblings of it. A repo you're actually standing inside
    (a directory with its own .pkb/ subdir, sibling-style, e.g. a scratch/work repo
    elsewhere) always wins; the central kb is only a fallback for when you're not
    inside one, so kb works from anywhere without cd'ing into a specific repo first.
    """
    path = os.path.abspath(start or os.getcwd())
    home = os.path.expanduser("~")
    central = os.path.join(home, ".pkb")

    if path == central or path.startswith(central + os.sep):
        return central

    while True:
        # `home` itself is excluded here: it always "contains" .pkb/ (the central
        # kb), but that's not a sibling-style repo rooted at home -- it's handled
        # by the central-kb fallback below instead.
        if path != home and os.path.isdir(os.path.join(path, ".pkb")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent

    if os.path.isdir(central):
        return central

    raise SystemExit("error: not inside a pkb repo (no .pkb/ directory found), "
                      "and no central kb at ~/.pkb")


REPO_ROOT = None  # populated lazily via get_repo_root()


def get_repo_root():
    global REPO_ROOT
    if REPO_ROOT is None:
        REPO_ROOT = find_repo_root()
    return REPO_ROOT


# ---------------------------------------------------------------------------
# Frontmatter: minimal YAML subset (scalars, null, flow lists, quoted strings)
# ---------------------------------------------------------------------------

FRONTMATTER_FIELDS = [
    "id", "created", "updated", "type", "extension", "source", "source_id",
    "tags", "links", "title",
]


def _parse_scalar(raw):
    raw = raw.strip()
    if raw == "" or raw == "null" or raw == "~":
        return None
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        return raw[1:-1].replace('\\"', '"')
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1].replace("''", "'")
    return raw


def _parse_flow_list(raw):
    raw = raw.strip()
    if not (raw.startswith("[") and raw.endswith("]")):
        return None
    inner = raw[1:-1].strip()
    if inner == "":
        return []
    items = []
    for part in inner.split(","):
        items.append(_parse_scalar(part))
    return items


def parse_frontmatter(text):
    """Return (frontmatter_dict, body_text). Raises ValueError if malformed."""
    if not text.startswith("---"):
        raise ValueError("missing frontmatter delimiter")
    lines = text.split("\n")
    if lines[0].strip() != "---":
        raise ValueError("missing frontmatter delimiter")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise ValueError("unterminated frontmatter block")
    fm_lines = lines[1:end]
    body = "\n".join(lines[end + 1:])
    data = {}
    for raw_line in fm_lines:
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        if ":" not in raw_line:
            continue
        key, _, value = raw_line.partition(":")
        key = key.strip()
        value = value.strip()
        lst = _parse_flow_list(value)
        if lst is not None:
            data[key] = lst
        else:
            data[key] = _parse_scalar(value)
    return data, body


def _dump_scalar(value):
    if value is None:
        return "null"
    s = str(value)
    if s == "" or any(c in s for c in ':#[]{}') or s != s.strip():
        return json.dumps(s)
    return s


def dump_frontmatter(data):
    lines = ["---"]
    for key in FRONTMATTER_FIELDS:
        value = data.get(key)
        if key in ("tags", "links"):
            items = value or []
            lines.append(f"{key}: [{', '.join(str(i) for i in items)}]")
        elif key == "title":
            lines.append(f"title: {json.dumps(value or '')}")
        else:
            lines.append(f"{key}: {_dump_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def read_entry(path):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    fm, body = parse_frontmatter(text)
    return fm, body


def write_entry(path, fm, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(dump_frontmatter(fm))
        f.write("\n")
        f.write(body.lstrip("\n"))


def iter_markdown_files(root):
    # walks only start inside named content dirs (never ".pkb" itself, which isn't
    # in CONTENT_DIRS), so no exclusion check is needed here -- one used to exist
    # for a "skip nested .pkb" case that couldn't actually happen, and it broke
    # once `root` itself started being named ".pkb" (a substring match on the
    # walked path wrongly matched everything).
    for content_dir in CONTENT_DIRS:
        base = os.path.join(root, content_dir)
        if not os.path.isdir(base):
            continue
        for dirpath, _, filenames in os.walk(base):
            for name in filenames:
                if name.endswith(".md"):
                    yield os.path.join(dirpath, name)


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def cursor_lookback(ts, seconds=2):
    """Rewind an 'updated after' cursor by a small buffer before querying an API.

    Timestamp-based cursors from external APIs are often second-granularity, so two
    items updated in the same second can straddle a cursor and one gets permanently
    skipped by a strict '>' filter. Querying with a small lookback and relying on
    dedup-by-source_id (see existing_source_ids in each sync script) is the standard
    fix: it may re-fetch a couple of already-synced items, but idempotent writes make
    that a no-op, whereas the missed item without the buffer is silent data loss.
    """
    dt = parse_iso(ts)
    if dt is None:
        return ts
    shifted = dt - timedelta(seconds=seconds)
    iso = shifted.isoformat()
    if str(ts).endswith("Z") and iso.endswith("+00:00"):
        iso = iso[:-6] + "Z"
    return iso


def gen_id(existing_ids, dt=None):
    dt = dt or datetime.now()
    base = dt.strftime("%Y-%m-%d-%H%M")
    if base not in existing_ids:
        existing_ids.add(base)
        return base
    n = 1
    while f"{base}-{n}" in existing_ids:
        n += 1
    candidate = f"{base}-{n}"
    existing_ids.add(candidate)
    return candidate


def collect_existing_ids(root):
    ids = set()
    for path in iter_markdown_files(root):
        try:
            fm, _ = read_entry(path)
        except ValueError:
            continue
        if fm.get("id"):
            ids.add(fm["id"])
    return ids


# ---------------------------------------------------------------------------
# Config / cursors
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "inbox_triage_days": 14,
    "fts_default_scope": "core",
    "sync": {
        "memos": {
            "base_url_env": "PKB_MEMOS_URL",
            "token_env": "PKB_MEMOS_TOKEN",
            "inbox_min_length": 280,
        },
        "gitlab": {
            "project_env": "PKB_GITLAB_PROJECT",
            "inbox_all_issues": True,
        },
        "beads": {
            "cli": "bd",
            "inbox_all": True,
        },
    },
}


def _parse_simple_config(text):
    """Very small indented key:value config parser supporting one level of nesting."""
    result = {}
    stack = [(-1, result)]
    for raw in text.split("\n"):
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        key, _, value = raw.strip().partition(":")
        key = key.strip()
        value = value.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value == "":
            new_dict = {}
            parent[key] = new_dict
            stack.append((indent, new_dict))
        else:
            lst = _parse_flow_list(value)
            if lst is not None:
                parent[key] = lst
            else:
                v = _parse_scalar(value)
                if isinstance(v, str) and v.lower() in ("true", "false"):
                    v = v.lower() == "true"
                elif isinstance(v, str) and v.isdigit():
                    v = int(v)
                parent[key] = v
    return result


def _dump_simple_config(data, indent=0):
    lines = []
    pad = "  " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            lines.append(_dump_simple_config(value, indent + 1))
        elif isinstance(value, list):
            lines.append(f"{pad}{key}: [{', '.join(str(i) for i in value)}]")
        elif isinstance(value, bool):
            lines.append(f"{pad}{key}: {str(value).lower()}")
        else:
            lines.append(f"{pad}{key}: {value}")
    return "\n".join(lines)


def load_config(root=None):
    root = root or get_repo_root()
    path = os.path.join(root, ".pkb", "config.yml")
    if not os.path.exists(path):
        return dict(DEFAULT_CONFIG)
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return _parse_simple_config(text)


def load_cursors(root=None):
    root = root or get_repo_root()
    path = os.path.join(root, ".pkb", "cursors.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cursors(cursors, root=None):
    root = root or get_repo_root()
    path = os.path.join(root, ".pkb", "cursors.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cursors, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def fail(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)
