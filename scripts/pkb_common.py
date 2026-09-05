"""Shared helpers for pkb scripts: frontmatter parsing, id generation, config/cursor IO.

No third-party dependencies (no pyyaml) so the toolchain stays fully offline/portable
with only the stdlib.
"""
import contextlib
import copy
import fcntl
import json
import os
import re
import shlex
import shutil
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


def slugify(title):
    slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")
    slug = "-".join(filter(None, slug.split("-")))[:60] or "untitled"
    return slug


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


@contextlib.contextmanager
def entry_lock(path):
    """Advisory per-file lock so concurrent read-modify-write updates to the
    same entry's frontmatter (tags/links/content) serialize instead of
    racing and silently dropping one writer's change."""
    fd = os.open(path + ".lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


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
    # microsecond precision, not just seconds: index_fts's incremental reindex
    # treats an entry as unchanged when its `updated` string is byte-identical
    # to what's already indexed, so two writes to the same entry within the
    # same second used to look like one no-op write and silently kept the
    # index stale after the second one.
    return datetime.now().astimezone().isoformat(timespec="microseconds")


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


def existing_source_paths(root, source):
    """Map source_id -> mirror file path for every already-synced item under
    sources/<source>/.

    Keyed on source_id, not path, so a re-fetch of a changed item updates the
    same mirror file in place instead of creating a duplicate.
    """
    paths = {}
    for path in iter_markdown_files(root):
        rel = os.path.relpath(path, root)
        if not rel.startswith(f"sources{os.sep}{source}{os.sep}"):
            continue
        try:
            fm, _ = read_entry(path)
        except ValueError:
            continue
        if fm.get("source_id"):
            paths[str(fm["source_id"])] = path
    return paths


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


def _deep_merge(base, override):
    """Merge override onto a deep copy of base, recursively for nested dicts.

    Lets a data repo's config.yml override just the keys it cares about
    (e.g. only inbox_triage_days) without losing the rest of DEFAULT_CONFIG,
    such as the sync.* blocks every sync script indexes into directly.

    A deep (not shallow) copy of base is taken so the returned dict never
    shares a nested dict object with DEFAULT_CONFIG -- otherwise an untouched
    sub-dict (e.g. sync.beads, when only inbox_triage_days is overridden)
    would alias the module-level constant, and any future in-place edit of it
    would silently corrupt DEFAULT_CONFIG for the rest of the process.

    If a key that's dict-shaped in `base` (e.g. `sync`) is overridden with a
    non-dict value (e.g. `sync: null` in config.yml), the override is ignored
    for that key rather than replacing the whole structured section -- every
    caller indexes into sync.memos/gitlab/beads directly and would otherwise
    crash on a config typo that was probably meant to mean "leave it alone".
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        base_value = result.get(key)
        if isinstance(base_value, dict):
            if isinstance(value, dict):
                result[key] = _deep_merge(base_value, value)
            # else: base_value is a structured section -- ignore an
            # incompatible override instead of dropping it.
        else:
            result[key] = value
    return result


def load_config(root=None):
    root = root or get_repo_root()
    path = os.path.join(root, ".pkb", "config.yml")
    if not os.path.exists(path):
        return copy.deepcopy(DEFAULT_CONFIG)
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return _deep_merge(DEFAULT_CONFIG, _parse_simple_config(text))


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


# ---------------------------------------------------------------------------
# kb's own machine-local preferences (currently just: editor). Deliberately
# separate from a data repo's .pkb/config.yml -- that file is committed and
# shared across every machine/clone, but which editor to spawn is a per-machine
# preference that has nothing to do with any particular data repo.
# ---------------------------------------------------------------------------

def kb_config_path():
    config_dir = os.path.expanduser(os.environ.get("KB_CONFIG_DIR", "~/.config/kb"))
    return os.path.join(config_dir, "config.json")


def load_kb_config():
    path = kb_config_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_kb_config(data):
    path = kb_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def split_editor(editor):
    """Tokenize an editor command string into argv parts. Returns None if the
    string is empty/whitespace, or can't be tokenized at all (e.g. unbalanced
    quotes) -- callers should treat that the same as "no editor configured"
    rather than letting shlex.split's ValueError (or an IndexError from
    indexing an empty result) escape as a raw traceback."""
    if not editor or not editor.strip():
        return None
    try:
        parts = shlex.split(editor)
    except ValueError:
        return None
    return parts or None


def resolve_editor_argv(editor):
    """argv to spawn for `editor`, or None if it can't be resolved at all.

    Prefers shlex tokenization so multi-word commands work ('code -w'), but
    falls back to treating the whole string as one literal path when the
    tokenized first word isn't on PATH and the untouched string is itself a
    real path on disk -- handles an editor set to a single path that contains
    a literal space (e.g. a macOS .app bundle path), which shlex would
    otherwise (mis)split into two tokens.
    """
    parts = split_editor(editor)
    if parts is None:
        return None
    if shutil.which(parts[0]) is None and os.path.exists(editor):
        return [editor]
    return parts


def get_editor(prompt_if_missing=True):
    """Resolve the editor kb should spawn: $EDITOR, then the persisted
    preference in kb_config_path(), then -- only in a real terminal -- prompt
    for one and save it so this only has to happen once. Returns None if
    nothing is configured and prompting isn't possible or is declined.
    """
    editor = os.environ.get("EDITOR")
    if editor:
        return editor

    cfg = load_kb_config()
    if cfg.get("editor"):
        return cfg["editor"]

    if not prompt_if_missing or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None

    print("$EDITOR isn't set. Enter an editor command for kb to use (e.g. vim, nano, 'code -w'),")
    print("or leave blank to skip for now:")
    try:
        editor = input("editor> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not editor:
        return None
    if split_editor(editor) is None:
        print(f"'{editor}' doesn't look like a valid editor command -- not saved.")
        return None

    cfg["editor"] = editor
    save_kb_config(cfg)
    print(f"saved to {kb_config_path()} -- change it any time with `kb config editor <cmd>`.")
    warn_if_editor_missing(editor)
    return editor


def warn_if_editor_missing(editor):
    """Best-effort PATH check for a just-saved/set editor command. Only a warning,
    never blocks saving -- the shell that later runs kb may have a different PATH
    than this one (e.g. a GUI editor wrapper only present in an interactive shell)."""
    argv = resolve_editor_argv(editor)
    if argv is None:
        return  # unparsable/empty -- cmd_config already rejects this before saving
    exe = argv[0]
    if not shutil.which(exe) and not os.path.exists(exe):
        print(f"note: '{exe}' isn't on PATH right now -- kb will fail to open it until that's fixed.")
