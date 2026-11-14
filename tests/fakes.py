"""Shared test helpers."""
from functools import partial
from unittest import mock

from pkb_cli import health


def patch_tool_status(testcase):
    """Make health.tool_status skip the auth checks (`gh auth status`, ...): they are slow,
    and they make a test depend on whoever happens to be signed in on the machine."""
    patcher = mock.patch.object(health, "tool_status", partial(health.tool_status, check_auth=False))
    patcher.start()
    testcase.addCleanup(patcher.stop)
