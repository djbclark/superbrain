#!/usr/bin/env python3
"""Tests for read-only reference database lookups."""

import tempfile
import unittest
from pathlib import Path

from core.database import Database
from core.reference_db import (
    ReferenceDatabase,
    copy_reference_row_to_primary,
    resolve_analysis_row,
)


class TestReferenceDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ref_path = Path(self.tmp.name) / "ref.db"
        self.pri_path = Path(self.tmp.name) / "pri.db"
        self.ref = Database(self.ref_path)
        self.pri = Database(self.pri_path)
        self.ref.save_analysis(
            shortcode="YT_ref1",
            url="https://www.youtube.com/watch?v=ref1ref1ref",
            username="ch",
            title="Nginx guide",
            summary="How to configure nginx",
            tags=["#nginx"],
            music="",
            category="software",
            audio_transcription="full transcript text",
            content_type="youtube",
        )

    def tearDown(self):
        self.ref.close()
        self.pri.close()
        self.tmp.cleanup()

    def test_resolve_prefers_primary_then_reference(self):
        ref_ro = ReferenceDatabase(self.ref_path)
        row, source = resolve_analysis_row(
            "YT_ref1", primary=self.pri, reference=ref_ro
        )
        self.assertEqual(source, "reference")
        self.assertEqual(row["title"], "Nginx guide")
        self.assertIn("transcript", row["audio_transcription"])

        copy_reference_row_to_primary(row, self.pri)
        row2, source2 = resolve_analysis_row(
            "YT_ref1", primary=self.pri, reference=ref_ro
        )
        self.assertEqual(source2, "primary")
        ref_ro.close()

    def test_missing(self):
        ref_ro = ReferenceDatabase(self.ref_path)
        row, source = resolve_analysis_row(
            "YT_nope", primary=self.pri, reference=ref_ro
        )
        self.assertIsNone(row)
        self.assertEqual(source, "missing")
        ref_ro.close()


if __name__ == "__main__":
    unittest.main()
