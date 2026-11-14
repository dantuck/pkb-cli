"""Shared test helpers."""
import shutil
from unittest import mock

from pkb_cli import sources
from pkb_cli import health


def patch_tool_status(testcase):
    """Replace health.tool_status with a version that only checks the PATH. The real one
    runs each tool's auth check (`gh auth status`, ...) -- slow, and it makes a test
    depend on whoever happens to be signed in on the machine running it."""
    def fast(sync_sources=None):
        if sync_sources is None:
            sync_sources = sources.discover_sync_sources()
        tools = set(health.BUILTIN_BREW_FORMULAS)
        for _n, (_f, meta) in sync_sources.items():
            if meta.get("cli_tool"):
                tools.add(meta["cli_tool"])
        status = {t: shutil.which(t) is not None for t in tools}
        for _n, (_f, meta) in sync_sources.items():
            if meta.get("cli_tool") and meta.get("auth_check"):
                status[f"{meta['cli_tool']}_auth"] = False
        return status

    patcher = mock.patch.object(health, "tool_status", fast)
    patcher.start()
    testcase.addCleanup(patcher.stop)
