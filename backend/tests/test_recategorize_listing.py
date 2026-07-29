#!/usr/bin/env python3
"""Tests for recategorize listing filters."""

import tempfile
import unittest
from pathlib import Path

from core.database import Database


class TestRecategorizeListing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")
        for shortcode, category in [
            ("YT_a", "Other"),
            ("YT_b", "other"),
            ("YT_c", "product"),
            ("YT_d", "Sysadmin"),
        ]:
            self.db.save_analysis(
                shortcode=shortcode,
                url=f"https://www.youtube.com/watch?v={shortcode[3:]}xxxxx",
                username="u",
                title=f"t-{shortcode}",
                summary="s",
                tags=[],
                music="",
                category=category,
                content_type="youtube",
            )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_outside_taxonomy_is_exact_match(self):
        rows = self.db.list_visible_for_recategorize(
            outside_taxonomy_names=["Sysadmin", "Other"]
        )
        cats = sorted(r["category"] for r in rows)
        self.assertEqual(cats, ["other", "product"])

    def test_only_categories(self):
        rows = self.db.list_visible_for_recategorize(only_categories=["product", "other"])
        cats = sorted(r["category"] for r in rows)
        self.assertEqual(cats, ["other", "product"])


if __name__ == "__main__":
    unittest.main()
