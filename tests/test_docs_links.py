"""Build the documentation into a scratch folder and verify its page links."""

import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.docs.build import build_documentation
from scripts.docs.lib import REPO_ROOT, SOURCE_REL
from scripts.docs.verify import verify_documentation

RELEASE = {"commit": "a" * 40, "source_date_epoch": 0}


class DocumentationLinkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        shutil.copytree(REPO_ROOT / SOURCE_REL, self.source)

    def verify(self):
        values = {**RELEASE, "source_root": self.source, "output_root": self.root / "built"}
        build_documentation(values)
        return verify_documentation(values)

    def test_repository_documentation_links_resolve(self):
        self.assertEqual(self.verify()["product"], "adobe-mcp")

    def test_a_link_out_of_the_documentation_is_refused(self):
        page = self.source / "pages/photoshop/tools.md"
        page.write_text(page.read_text() + "\n[Results](../../reference/results-and-errors.md)\n")
        with self.assertRaisesRegex(ValueError, "link in photoshop/tools.md names no"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
