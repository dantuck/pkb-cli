"""kb_web calls straight into the modules that own each function (no lazy hop through cli, which
imports kb_web). Its tests patch functions by name via fakes.patched_kb_web, so a function moved
or renamed would pass every test and still break at runtime -- this checks every patch target
exists, and that kb_web has no way back into cli."""
import importlib
import re
import unittest

import fakes
from pkb_cli import kb_web


class KbWebContractTest(unittest.TestCase):
    def test_every_patch_target_exists(self):
        missing = []
        for name, (mod, attr) in fakes._KB_WEB_TARGETS.items():
            if not hasattr(importlib.import_module(f"pkb_cli.{mod}"), attr):
                missing.append(f"{mod}.{attr}")
        self.assertEqual(missing, [], f"patch targets that no longer exist: {missing}")

    def test_kb_web_does_not_import_cli(self):
        with open(kb_web.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIsNone(re.search(r"^\s*(from \. import cli\b|from \.cli import|import pkb_cli\.cli)", src, re.M))
        self.assertNotIn("_kb()", src)


if __name__ == "__main__":
    unittest.main()
