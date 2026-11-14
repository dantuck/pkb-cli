"""kb validate: each class of frontmatter problem is reported, valid repos pass."""
import os
import tempfile
import unittest

from pkb_cli import validate_frontmatter as vf

VALID = {"id": "2026-01-01-0001", "created": "2026-01-01T00:00:00Z", "updated": "2026-01-01T00:00:00Z",
         "type": "how-to", "extension": "null", "source": "manual", "source_id": "null",
         "tags": "[]", "links": "[]", "title": "T"}


def write(root, rel, **over):
    fm = {**VALID, **over}
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("---\n" + "".join(f"{k}: {v}\n" for k, v in fm.items() if v is not None) + "---\n\n# T\n")


class ValidateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        os.makedirs(os.path.join(self.root, ".pkb"))

    def messages(self):
        return [m for _p, m in vf.validate_repo(self.root)]

    def test_valid_entry_passes(self):
        write(self.root, "how-to/a.md")
        self.assertEqual(vf.validate_repo(self.root), [])

    def test_missing_field(self):
        write(self.root, "how-to/a.md", title=None)
        self.assertTrue(any("missing required field 'title'" in m for m in self.messages()))

    def test_wrong_directory_for_type(self):
        write(self.root, "reference/a.md")
        self.assertTrue(any("must live under 'how-to/'" in m for m in self.messages()))

    def test_bad_id_and_duplicate_id(self):
        write(self.root, "how-to/a.md", id="nope")
        self.assertTrue(any("does not match pattern" in m for m in self.messages()))
        os.remove(os.path.join(self.root, "how-to", "a.md"))
        write(self.root, "how-to/a.md")
        write(self.root, "how-to/b.md")
        self.assertTrue(any("duplicate id" in m for m in self.messages()))

    def test_broken_link(self):
        write(self.root, "how-to/a.md", links="[2030-01-01-0001]")
        self.assertTrue(any("broken link" in m for m in self.messages()))

    def test_core_type_must_not_have_extension(self):
        write(self.root, "how-to/a.md", extension="memos")
        self.assertTrue(any("extension must be null" in m for m in self.messages()))

    def test_invalid_source(self):
        write(self.root, "how-to/a.md", source="carrier-pigeon")
        self.assertTrue(any("invalid source" in m for m in self.messages()))


if __name__ == "__main__":
    unittest.main()
