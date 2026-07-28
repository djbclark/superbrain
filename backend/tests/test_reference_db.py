#!/usr/bin/env python3
"""Tests for read-only reference database lookups."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        for i in range(40):
            self.ref.save_analysis(
                shortcode=f"YT_bulk{i:03d}",
                url=f"https://www.youtube.com/watch?v=bulk{i:08d}xx",
                username="ch",
                title=f"Bulk title {i}",
                summary=f"Bulk summary {i}",
                tags=[],
                music="",
                category="Other",
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

    def test_same_path_skips_redundant_reference_lookup(self):
        # Primary is the only handle; reference pointing at the same file must
        # not be consulted after primary already missed.
        shared = Database(self.pri_path)
        shared.save_analysis(
            shortcode="YT_only_primary",
            url="https://www.youtube.com/watch?v=onlyprim01",
            username="ch",
            title="Only primary",
            summary="x",
            tags=[],
            music="",
            category="Other",
            content_type="youtube",
        )
        shared.close()
        primary = Database(self.pri_path)
        ref_ro = ReferenceDatabase(self.pri_path)
        row, source = resolve_analysis_row(
            "YT_only_primary", primary=primary, reference=ref_ro
        )
        self.assertEqual(source, "primary")
        row2, source2 = resolve_analysis_row(
            "YT_missing_everywhere", primary=primary, reference=ref_ro
        )
        self.assertIsNone(row2)
        self.assertEqual(source2, "missing")
        ref_ro.close()
        primary.close()

    def test_concurrent_lookups_do_not_raise(self):
        """Reproduce the playlist-worker crash: shared RO conn across threads."""
        ref_ro = ReferenceDatabase(self.ref_path)
        codes = [f"YT_bulk{i:03d}" for i in range(40)] + ["YT_ref1", "YT_nope"]

        def lookup(code: str):
            return ref_ro.get_by_shortcode(code)

        results = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(lookup, code) for code in codes * 5]
            for fut in as_completed(futures):
                results.append(fut.result())

        found = [r for r in results if r is not None]
        self.assertGreaterEqual(len(found), 40 * 5)
        ref_ro.close()


if __name__ == "__main__":
    unittest.main()
