"""kb_web calls straight into the modules that own each function (no lazy hop through cli, which
imports kb_web). Its tests mock those calls via fakes.patched_kb_web, which derives the targets from
kb_web's own source -- so this checks that each one resolves, and that kb_web has no way back into cli."""
import importlib
import re
import unittest

import fakes
from pkb_cli import kb_web


class KbWebContractTest(unittest.TestCase):
    def test_every_module_function_kb_web_calls_exists(self):
        missing = [f"{mod}.{attr}" for mod, attr in sorted(fakes.kb_web_targets())
                   if not hasattr(importlib.import_module(f"pkb_cli.{mod}"), attr)]
        self.assertEqual(missing, [], f"kb_web uses names its modules no longer define: {missing}")

    def test_kb_web_does_not_import_cli(self):
        with open(kb_web.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIsNone(re.search(r"^\s*(from \. import cli\b|from \.cli import|import pkb_cli\.cli)", src, re.M))
        self.assertNotIn("_kb()", src)


if __name__ == "__main__":
    unittest.main()
