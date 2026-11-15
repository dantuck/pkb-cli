"""discover_sync_sources lists sync_<name>.py files without importing them."""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pkb_cli import sources  # noqa: E402


class DiscoverSyncSourcesTest(unittest.TestCase):
    def test_listing_does_not_import_sync_modules(self):
        with mock.patch.object(sources.importlib, "import_module") as imp:
            found = sources.discover_sync_sources()
        self.assertIn("memos", found)
        imp.assert_not_called()

    def test_meta_loads_on_first_read(self):
        self.assertEqual(sources.discover_sync_sources()["github"], "sync_github.py")
        self.assertEqual(sources.source_meta("github").get("cli_tool"), "gh")

    def test_broken_module_gives_empty_meta_and_warning(self):
        sources.source_meta.cache_clear()
        self.addCleanup(sources.source_meta.cache_clear)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(sources.source_meta("nope"), {})
            sources.source_meta("nope")  # cached: warned once
        self.assertEqual(err.getvalue().count("sync_nope.py failed to load"), 1)


if __name__ == "__main__":
    unittest.main()
