"""Guards against documentation drift: every kb subcommand is listed in the
CLI reference, every config key is in the config reference, and every
relative markdown link in the repo resolves."""
import os
import re
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from pkb_cli import pkb_common as pc  # noqa: E402


def _read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


class DocsTest(unittest.TestCase):
    def test_every_subcommand_is_in_cli_reference(self):
        commands = set(re.findall(r'sub\.add_parser\(\s*"([a-z-]+)"', _read("pkb_cli", "cli.py")))
        self.assertGreater(len(commands), 10)
        reference = _read("docs", "reference", "cli.md")
        missing = sorted(c for c in commands - {"help"} if not re.search(rf"^kb {re.escape(c)}\b", reference, re.M))
        self.assertEqual(missing, [], "subcommands missing from docs/reference/cli.md")

    def test_every_config_key_is_in_config_reference(self):
        reference = _read("docs", "reference", "config.md")
        missing = []

        def walk(d, path):
            for key, value in d.items():
                if isinstance(value, dict):
                    walk(value, path + [key])
                elif f"`{'.'.join(path + [key])}`" not in reference and f"`{key}`" not in reference \
                        and not (path and f"`{'.'.join(path)}.{key}`" in reference.replace("` / `", "`/`")):
                    missing.append(".".join(path + [key]))

        walk(pc.DEFAULT_CONFIG, [])
        self.assertEqual(missing, [], "config keys missing from docs/reference/config.md")

    def test_relative_markdown_links_resolve(self):
        link_re = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
        broken = []
        for base in ("README.md", "CONTRIBUTING.md", "docs", "skills"):
            full = os.path.join(REPO, base)
            if os.path.isfile(full):
                files = [full]
            elif os.path.isdir(full):
                files = [os.path.join(d, f) for d, _, fs in os.walk(full) for f in fs if f.endswith(".md")]
            else:
                continue
            for path in files:
                with open(path, encoding="utf-8") as f:
                    text = re.sub(r"```.*?```", "", f.read(), flags=re.S)
                for target in link_re.findall(text):
                    if re.match(r"[a-z]+:", target) or target.startswith("#"):
                        continue
                    if not os.path.exists(os.path.join(os.path.dirname(path), target.split("#")[0])):
                        broken.append(f"{os.path.relpath(path, REPO)} -> {target}")
        self.assertEqual(broken, [])


if __name__ == "__main__":
    unittest.main()
