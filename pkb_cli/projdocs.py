"""`kb docs`: create, check and search a code project's Diataxis pages under docs/.

Project pages are plain markdown (no kb frontmatter), unlike entries in the personal data repo.
Functions return plain dicts and never print or exit; `cmd_docs` renders them.
"""
import argparse
import os
import re

from . import agentfiles, project, specs
from . import pkb_common as pc

QUADRANT_DIR = {"tutorial": "tutorials", "how-to": "how-to", "reference": "reference",
                "explanation": "explanation"}
DEFAULT_LIMIT = 10

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
_SCHEME_RE = re.compile(r"^(\w+:|#)")
_NUMBERED_RE = re.compile(r"^\d{4}-")


def _docs(root):
    base = os.path.join(root, specs.DOCS_DIR)
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
    path = os.path.join(root, specs.DOCS_DIR, QUADRANT_DIR[doc_type], pc.slugify(title) + ".md")
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
    base = os.path.join(root, specs.DOCS_DIR)
    if not os.path.isdir(base):
        return [{"path": specs.DOCS_DIR, "issue": "no docs/ directory (run `kb project init`)"}]
    findings = [{"path": f"{specs.DOCS_DIR}/{q}", "issue": "missing Diataxis directory"}
                for q in project.DIATAXIS_DIRS if not os.path.isdir(os.path.join(base, q))]
    texts = {path: _read(path) for path in _docs(root)}  # each page read once
    indexes = "".join(t for path, t in texts.items() if os.path.basename(path) == "README.md")
    for path, text in texts.items():
        rel = os.path.relpath(path, root)
        parent = os.path.relpath(os.path.dirname(path), base)
        name = os.path.basename(path)
        if parent == "." and name != "README.md":
            findings.append({"path": rel, "issue": "not filed under a Diataxis quadrant"})
        if parent in ("explanation", "reference") and len(_STEP_RE.findall(text)) >= 5:
            findings.append({"path": rel, "issue": f"numbered steps in {parent}/: looks like a how-to"})
        if parent in project.DIATAXIS_DIRS and name != "README.md" and name not in indexes:
            findings.append({"path": rel, "issue": "not linked from any README index"})
        if parent in ("specs", "decisions") and name != "README.md" and not _NUMBERED_RE.match(name):
            findings.append({"path": rel, "issue": "no NNNN- prefix, so kb spec/adr cannot see it"})
        for target in _LINK_RE.findall(text):
            file_part = target.split("#")[0]
            if file_part and not _SCHEME_RE.match(target) \
                    and not os.path.exists(os.path.normpath(os.path.join(os.path.dirname(path), file_part))):
                findings.append({"path": rel, "issue": f"broken link: {target}"})
    for kind in specs.KINDS:
        for i, paths in specs.duplicate_ids(root, kind).items():
            findings.append({"path": paths[0], "issue": f"duplicate {kind} id {i} (also {', '.join(paths[1:])}); "
                                                        f"run `kb {kind} renumber <slug>`"})
    return findings


def doctor_lines(start=None):
    """([ok], [todo]) for `kb doctor`, only when the cwd's project actually uses kb's project
    workflow (docs/specs/ or a kb block in AGENTS.md); otherwise ([], []) so non-project use is
    never flagged."""
    root = project.find_project_root(start)
    uses_kb = os.path.isdir(specs.kind_dir(root, "spec")) or agentfiles.MARK_BEGIN in _read(os.path.join(root, "AGENTS.md"))
    if not uses_kb:
        return [], []
    todo = [f"{f['issue']} (fix: {f['fix']})" for f in agentfiles.agents_check(root)["findings"]]
    todo += [f"{f['path']}: {f['issue']}" for f in docs_check(root)]
    ok = [f"project {root} uses kb specs/AGENTS.md"]
    return ok + ([] if todo else ["project docs, specs and agent files look consistent"]), todo


def docs_search(root, query, limit=DEFAULT_LIMIT):
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
        title_low = title.lower()
        line_no = next((i for i, l in enumerate(lines, 1) if terms[0] in l.lower()), 1)
        score = sum(low.count(t) for t in terms) + 5 * sum(t in title_low for t in terms)
        hits.append({"path": os.path.relpath(path, root), "title": title, "score": score,
                     "line": line_no, "snippet": lines[line_no - 1].strip()[:160]})
    hits.sort(key=lambda h: -h["score"])
    return hits[:limit]


def cmd_docs(args):
    root = project.root_for(args)
    if args.action == "new":
        r = docs_new(root, args.type, args.title)
        text = r.get("path")
    elif args.action == "check":
        r = docs_check(root)
        text = "\n".join(f"{f['path']}: {f['issue']}" for f in r) or "ok"
    else:  # search
        r = docs_search(root, " ".join(args.query), args.limit)
        text = "\n".join(f"{h['path']}:{h['line']}  {h['title']}\n    {h['snippet']}" for h in r) or "no matches"
    if isinstance(r, dict) and "error" in r:
        return project.fail(args, r["error"])
    project.emit(args, r, text)
    return 0


def add_parser(sub):
    p = sub.add_parser("docs", help="create, check and search a project's docs/ pages",
                       description="Work with a code project's Diataxis pages (plain markdown under docs/).",
                       epilog='  kb docs new how-to "Deploy to staging"\n  kb docs check\n  kb docs search "rate limit"',
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = p.add_subparsers(dest="action", metavar="<action>")
    project.show_help_for(p)
    np_ = actions.add_parser("new", help="add a page from a skeleton", description="Add a page in the right quadrant.")
    np_.add_argument("type", choices=list(QUADRANT_DIR), help="Diataxis type")
    np_.add_argument("title", help="page title")
    actions.add_parser("check", help="misfiled, unlinked or step-heavy pages, broken links, duplicate ids")
    sp = actions.add_parser("search", help="AND-search docs/ pages, best match first")
    sp.add_argument("query", nargs="+", help="search terms")
    sp.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"max results (default {DEFAULT_LIMIT})")
    for leaf in (np_, sp, actions.choices["check"]):
        project.add_common_args(leaf)
        leaf.set_defaults(func=cmd_docs)
