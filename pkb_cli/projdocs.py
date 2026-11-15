"""`kb docs`: create, check and search a code project's Diataxis pages under docs/.

Project pages are plain markdown (no kb frontmatter), unlike entries in the personal data repo.
Functions return plain dicts and never print or exit; `cmd_docs` renders them.
"""
import argparse
import os
import re

from . import pkb_common as pc
from . import project

QUADRANT_DIR = dict(zip(pc.CORE_TYPES, project.DIATAXIS_DIRS))

SKELETONS = {
    "tutorial": "# {title}\n\nIn this tutorial you will ...\n\n## Before you start\n\n## Step 1\n\n"
                "## What you learned\n",
    "how-to": "# {title}\n\nUse this guide to ...\n\n## Prerequisites\n\n## Steps\n\n1. \n\n"
              "## Troubleshooting\n",
    "reference": "# {title}\n\nFacts to look up. Describe, do not instruct or argue.\n\n## Overview\n",
    "explanation": "# {title}\n\n## Context\n\n## Why it is this way\n\n## Trade-offs\n",
}

_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
_STEP_RE = re.compile(r"^\s*\d+\.\s+\S", re.M)


def _docs(root):
    base = os.path.join(root, project.DOCS_DIR)
    for dirpath, _, names in os.walk(base):
        for name in sorted(names):
            if name.endswith(".md"):
                yield os.path.join(dirpath, name)


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return ""


def docs_new(root, doc_type, title):
    title = (title or "").strip()
    if doc_type not in QUADRANT_DIR:
        return {"error": f"type must be one of: {', '.join(QUADRANT_DIR)}"}
    if not title:
        return {"error": "title is empty"}
    path = os.path.join(root, project.DOCS_DIR, QUADRANT_DIR[doc_type], pc.slugify(title) + ".md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return {"error": f"{os.path.relpath(path, root)} already exists"}
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(SKELETONS[doc_type].format(title=title))
    return {"type": doc_type, "title": title, "path": os.path.relpath(path, root)}


def docs_check(root):
    """Structural problems in docs/: [{path, issue}]. Heuristic, so advice rather than errors."""
    base = os.path.join(root, project.DOCS_DIR)
    findings = []
    if not os.path.isdir(base):
        return [{"path": project.DOCS_DIR, "issue": "no docs/ directory (run `kb project init`)"}]
    for q in project.DIATAXIS_DIRS:
        if not os.path.isdir(os.path.join(base, q)):
            findings.append({"path": f"{project.DOCS_DIR}/{q}", "issue": "missing Diataxis directory"})
    indexes = "".join(_read(p) for p in _docs(root) if os.path.basename(p) == "README.md")
    for path in _docs(root):
        rel = os.path.relpath(path, root)
        parent = os.path.relpath(os.path.dirname(path), base)
        name = os.path.basename(path)
        text = _read(path)
        if parent == "." and name != "README.md":
            findings.append({"path": rel, "issue": "not filed under a Diataxis quadrant"})
        if parent in ("explanation", "reference") and len(_STEP_RE.findall(text)) >= 5:
            findings.append({"path": rel, "issue": f"numbered steps in {parent}/: looks like a how-to"})
        if parent in project.DIATAXIS_DIRS and name != "README.md" and name not in indexes:
            findings.append({"path": rel, "issue": "not linked from any README index"})
        if parent in ("specs", "decisions") and name != "README.md" and not re.match(r"^\d{4}-", name):
            findings.append({"path": rel, "issue": "no NNNN- prefix, so kb spec/adr cannot see it"})
        for target in _LINK_RE.findall(text):
            if re.match(r"^(\w+:|#)", target):
                continue
            dest = os.path.normpath(os.path.join(os.path.dirname(path), target.split("#")[0]))
            if target.split("#")[0] and not os.path.exists(dest):
                findings.append({"path": rel, "issue": f"broken link: {target}"})
    for kind in project.KINDS:
        for i, paths in project.duplicate_ids(root, kind).items():
            findings.append({"path": paths[0], "issue": f"duplicate {kind} id {i} (also {', '.join(paths[1:])}); "
                                                        f"run `kb {kind} renumber <slug>`"})
    return findings


def docs_search(root, query, limit=10):
    """Case-insensitive AND search over docs/ pages, best match first."""
    terms = [t for t in re.split(r"\s+", (query or "").lower()) if t]
    if not terms:
        return []
    hits = []
    for path in _docs(root):
        text = _read(path)
        low = text.lower()
        if not all(t in low for t in terms):
            continue
        lines = text.split("\n")
        title = next((l.lstrip("# ").strip() for l in lines if l.startswith("# ")), os.path.basename(path))
        line_no = next((i for i, l in enumerate(lines, 1) if terms[0] in l.lower()), 1)
        score = sum(low.count(t) for t in terms) + 5 * sum(t in title.lower() for t in terms)
        hits.append({"path": os.path.relpath(path, root), "title": title, "score": score,
                     "line": line_no, "snippet": lines[line_no - 1].strip()[:160]})
    hits.sort(key=lambda h: -h["score"])
    return hits[:limit]


def cmd_docs(args):
    root = project._root(args)
    rest = args.rest
    if args.action == "new":
        if len(rest) != 2:
            return project._fail(args, 'usage: kb docs new <type> "<title>"')
        r = docs_new(root, rest[0], rest[1])
        if "error" in r:
            return project._fail(args, r["error"])
        project._emit(args, r, r["path"])
    elif args.action == "check":
        r = docs_check(root)
        project._emit(args, r, "\n".join(f"{f['path']}: {f['issue']}" for f in r) or "ok")
    else:
        if not rest:
            return project._fail(args, 'usage: kb docs search "<query>"')
        r = docs_search(root, " ".join(rest), args.limit)
        project._emit(args, r, "\n".join(f"{h['path']}:{h['line']}  {h['title']}\n    {h['snippet']}"
                                         for h in r) or "no matches")
    return 0


def add_parser(sub, common):
    p = sub.add_parser("docs", help="create, check and search a project's docs/ pages",
                       description="Work with a code project's Diataxis pages (plain markdown under docs/).",
                       epilog='  kb docs new how-to "Deploy to staging"\n  kb docs check\n  kb docs search "rate limit"',
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["new", "check", "search"])
    p.add_argument("rest", nargs="*", help='new: <type> "<title>"; search: the query')
    p.add_argument("--limit", type=int, default=10, help="search: max results (default 10)")
    common(p)
    p.set_defaults(func=cmd_docs)
