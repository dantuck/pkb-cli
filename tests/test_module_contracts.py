"""kb_web reaches into the cli module lazily (`_kb().name`, `kb.name`), and its tests mock that
accessor -- so a function moved out of cli.py would pass every test and still break at runtime.
This checks every attribute kb_web uses actually exists."""
import re
import unittest

from pkb_cli import cli, kb_web


class KbWebContractTest(unittest.TestCase):
    def test_every_cli_attribute_kb_web_uses_exists(self):
        with open(kb_web.__file__, encoding="utf-8") as f:
            src = f.read()
        used = set(re.findall(r"_kb\(\)\.(\w+)", src)) | set(re.findall(r"(?<![\w.])kb\.(\w+)", src))
        used.discard("pc")  # `_kb().pc` is the pkb_common alias
        missing = sorted(n for n in used if not hasattr(cli, n))
        self.assertEqual(missing, [], f"kb_web uses these cli attributes, but cli has no such name: {missing}")


if __name__ == "__main__":
    unittest.main()
