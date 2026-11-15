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
        found = sources.discover_sync_sources()
        fname, meta = found["github"]
        self.assertEqual(fname, "sync_github.py")
        self.assertEqual(meta.get("cli_tool"), "gh")

    def test_broken_module_gives_empty_meta_and_warning(self):
        meta = sources._LazyMeta("nope", "sync_nope.py")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(dict(meta), {})
        self.assertIn("sync_nope.py failed to load", err.getvalue())


if __name__ == "__main__":
    unittest.main()
