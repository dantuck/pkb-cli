"""Shared test helpers."""
import os
from functools import partial
from unittest import mock

from pkb_cli import health
from pkb_cli import pkb_common as pc


def patch_tool_status(testcase):
    """Make health.tool_status skip the auth checks (`gh auth status`, ...): they are slow,
    and they make a test depend on whoever happens to be signed in on the machine."""
    patcher = mock.patch.object(health, "tool_status", partial(health.tool_status, check_auth=False))
    patcher.start()
    testcase.addCleanup(patcher.stop)


def write_test_entry(root, rel_path, body="body\n", **fm_overrides):
    """Write a valid entry at root/rel_path (frontmatter overridable by keyword); returns its path."""
    fm = {
        "id": "2026-01-01-0000", "created": "2026-01-01T00:00:00", "updated": "2026-01-01T00:00:00",
        "type": "how-to", "extension": None, "source": "manual", "source_id": None,
        "tags": [], "links": [], "title": "t",
    }
    fm.update(fm_overrides)
    path = os.path.join(root, rel_path)
    pc.write_entry(path, fm, body)
    return path
