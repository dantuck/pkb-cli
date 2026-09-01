#!/usr/bin/env python3
"""Validate frontmatter across the pkb repo. Exit non-zero if any file fails.

Run in CI / as a pre-commit hook (installed by `kb setup`).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import pkb_common as pc


def relpath(root, path):
    return os.path.relpath(path, root)


def validate_repo(root):
    errors = []  # list of (path, message)
    all_ids = {}  # id -> path (first seen), to detect duplicates
    entries = []  # (path, fm)

    for path in pc.iter_markdown_files(root):
        rel = relpath(root, path)
        try:
            fm, _ = pc.read_entry(path)
        except ValueError as e:
            errors.append((rel, f"malformed frontmatter: {e}"))
            continue
        entries.append((path, fm))

        required = ["id", "created", "updated", "type", "extension", "source", "tags", "links", "title"]
        for field in required:
            if field not in fm:
                errors.append((rel, f"missing required field '{field}'"))
        if "source_id" not in fm:
            errors.append((rel, "missing required field 'source_id' (use null for manual entries)"))

        entry_type = fm.get("type")
        if entry_type not in pc.ALL_TYPES:
            errors.append((rel, f"invalid type '{entry_type}', must be one of {pc.ALL_TYPES}"))
        else:
            extension = fm.get("extension")
            if entry_type in pc.CORE_TYPES:
                if extension is not None:
                    errors.append((rel, f"type '{entry_type}' is core Diataxis content, extension must be null, got '{extension}'"))
            else:
                if extension != entry_type:
                    errors.append((rel, f"type '{entry_type}' requires extension == '{entry_type}', got '{extension}'"))

            expected_dir = pc.TYPE_DIR.get(entry_type)
            if expected_dir:
                rel_parts = rel.split(os.sep)
                if not rel_parts or rel_parts[0] != expected_dir:
                    errors.append((rel, f"type '{entry_type}' must live under '{expected_dir}/', found in '{rel_parts[0] if rel_parts else '?'}/'"))

        source = fm.get("source")
        if source not in pc.VALID_SOURCES:
            errors.append((rel, f"invalid source '{source}', must be one of {pc.VALID_SOURCES}"))

        entry_id = fm.get("id")
        if not entry_id or not pc.ID_RE.match(str(entry_id)):
            errors.append((rel, f"id '{entry_id}' does not match pattern YYYY-MM-DD-HHMM[-n]"))
        elif entry_id in all_ids:
            errors.append((rel, f"duplicate id '{entry_id}' (also used by {relpath(root, all_ids[entry_id])})"))
        else:
            all_ids[entry_id] = path

    # broken-link check, second pass now that all ids are known
    for path, fm in entries:
        rel = relpath(root, path)
        for target in (fm.get("links") or []):
            if target and target not in all_ids:
                errors.append((rel, f"broken link: '{target}' does not resolve to any entry id"))

    return errors


def main():
    root = pc.get_repo_root()
    errors = validate_repo(root)
    if not errors:
        print("validate: OK — all frontmatter valid")
        return 0
    print(f"validate: {len(errors)} error(s) found\n")
    for rel, msg in errors:
        print(f"  {rel}: {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
