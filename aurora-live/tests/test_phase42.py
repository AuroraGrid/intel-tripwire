from __future__ import annotations

import unittest
from pathlib import Path

from phase41_complete import Phase41Application
from phase42_complete import Phase42Application, STATIC_DIR


class Phase42Tests(unittest.TestCase):
    def test_release_is_forward_compatible(self):
        self.assertTrue(issubclass(Phase42Application, Phase41Application))

    def test_static_assets_exist(self):
        self.assertTrue((STATIC_DIR / "gop.html").is_file())
        self.assertTrue((STATIC_DIR / "aurora-live.js").is_file())
        html = (STATIC_DIR / "gop.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "aurora-live.js").read_text(encoding="utf-8")
        self.assertIn("Global Operating Picture", html)
        self.assertIn("Incident Room", html)
        self.assertIn("Source Health", html)
        self.assertIn("/api/public/product/capabilities", js)
        self.assertIn("/api/public/global-operating-picture", js)
        self.assertNotIn("HARDCODED_LIVE", html)


if __name__ == "__main__":
    unittest.main()
