#!/usr/bin/env python3
"""Tests for YouTube API quota instrumentation and cost table."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from core.database import Database
from core.youtube_quota import (
    COST_TABLE_VERSION,
    classify_result,
    estimate_units,
    instrumented_request,
    lookup_quota_cost,
    record_youtube_api_call,
    usage_summary,
)


class TestQuotaCostTable(unittest.TestCase):
    def test_known_insert_cost(self):
        cost = lookup_quota_cost("playlistItems", "insert")
        self.assertTrue(cost.known)
        self.assertEqual(cost.units, 50)
        self.assertEqual(estimate_units("playlistItems", "insert"), 50)

    def test_list_pages(self):
        self.assertEqual(estimate_units("playlists", "list", pages=3), 3)

    def test_unknown_method_visible(self):
        cost = lookup_quota_cost("playlistItems", "explode")
        self.assertFalse(cost.known)
        self.assertIsNone(cost.units)
        self.assertEqual(estimate_units("playlistItems", "explode"), 0)

    def test_classify_quota_error(self):
        self.assertEqual(classify_result(http_status=403), "quota_error")
        self.assertEqual(
            classify_result(error=Exception("403 Forbidden quotaExceeded")),
            "quota_error",
        )


class TestUsageEvents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(db_path=Path(self.tmp.name) / "u.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_and_summarize(self):
        record_youtube_api_call(
            self.db,
            resource="playlistItems",
            method="insert",
            http_status=200,
            operation="backfill_insert",
            priority="historic",
        )
        record_youtube_api_call(
            self.db,
            resource="playlists",
            method="list",
            http_status=200,
            operation="ensure_playlists",
            priority="historic",
            pages=2,
        )
        # Failed requests still charged
        record_youtube_api_call(
            self.db,
            resource="playlistItems",
            method="insert",
            http_status=403,
            operation="backfill_insert",
            priority="new",
            error=Exception("403 quotaExceeded"),
        )
        summary = usage_summary(self.db)
        self.assertEqual(summary["cost_table_version"], COST_TABLE_VERSION)
        self.assertEqual(summary["totals"]["calls"], 3)
        self.assertEqual(summary["totals"]["units"], 50 + 2 + 50)
        self.assertEqual(summary["totals"]["quota_errors"], 1)
        self.assertTrue(any(e["resource"] == "playlistItems" for e in summary["by_endpoint"]))
        ledger = self.db.get_youtube_quota_ledger(summary["day_key"])
        self.assertEqual(ledger["units_used"], 102)
        self.assertIsNotNone(ledger["exhausted_at"])

    def test_instrumented_request_success_and_error(self):
        ok = MagicMock()
        ok.status_code = 200
        ok.raise_for_status = MagicMock()
        instrumented_request(
            self.db,
            do_request=lambda: ok,
            resource="subscriptions",
            method="list",
            operation="subscription_list",
            priority="new",
        )
        bad = MagicMock()
        bad.status_code = 403
        bad.raise_for_status = MagicMock(
            side_effect=Exception("403 Client Error: Forbidden")
        )
        with self.assertRaises(Exception):
            instrumented_request(
                self.db,
                do_request=lambda: bad,
                resource="playlistItems",
                method="insert",
                operation="sync_video_category",
                priority="historic",
            )
        summary = usage_summary(self.db)
        self.assertEqual(summary["totals"]["calls"], 2)
        self.assertEqual(summary["totals"]["units"], 1 + 50)

    def test_unknown_methods_listed(self):
        record_youtube_api_call(
            self.db,
            resource="weirdResource",
            method="list",
            http_status=200,
            operation="probe",
            update_ledger=False,
        )
        summary = usage_summary(self.db)
        self.assertEqual(summary["unknown_methods"][0]["resource"], "weirdResource")


if __name__ == "__main__":
    unittest.main()
