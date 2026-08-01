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
    is_uncharged_transport_failure,
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

    def test_classify_transport_error(self):
        err = ConnectionError(
            "Failed to resolve 'oauth2.googleapis.com' "
            "([Errno 8] nodename nor servname provided, or not known)"
        )
        self.assertTrue(is_uncharged_transport_failure(error=err))
        self.assertEqual(classify_result(error=err), "transport_error")
        # HTTP responses from Google are never treated as transport misses
        self.assertFalse(
            is_uncharged_transport_failure(http_status=503, error=err)
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

    def test_dns_outage_does_not_charge_ledger(self):
        """Reproduce 2026-08-01 incident: OAuth DNS failures must not burn local quota."""
        dns_err = ConnectionError(
            "HTTPSConnectionPool(host='oauth2.googleapis.com', port=443): "
            "Max retries exceeded with url: /token (Caused by NameResolutionError("
            "\"Failed to resolve 'oauth2.googleapis.com' "
            "([Errno 8] nodename nor servname provided, or not known)\"))"
        )

        def _boom():
            raise dns_err

        with self.assertRaises(ConnectionError):
            instrumented_request(
                self.db,
                do_request=_boom,
                resource="playlistItems",
                method="insert",
                operation="sync_video_category",
                priority="new",
            )
        # Burst of transport failures (same as the 200x incident)
        for _ in range(5):
            with self.assertRaises(ConnectionError):
                instrumented_request(
                    self.db,
                    do_request=_boom,
                    resource="playlistItems",
                    method="insert",
                    operation="sync_video_category",
                    priority="new",
                )

        summary = usage_summary(self.db)
        self.assertEqual(summary["totals"]["calls"], 6)
        self.assertEqual(summary["totals"]["units"], 0)
        self.assertEqual(summary["totals"]["failed"], 6)
        ledger = self.db.get_youtube_quota_ledger(summary["day_key"])
        self.assertTrue(
            ledger is None or int(ledger.get("units_used") or 0) == 0,
            f"transport failures must not spend local quota; ledger={ledger}",
        )
        event = record_youtube_api_call(
            self.db,
            resource="playlistItems",
            method="insert",
            http_status=None,
            priority="new",
            error=dns_err,
        )
        self.assertEqual(event["result_class"], "transport_error")
        self.assertEqual(event["units"], 0)
        ledger2 = self.db.get_youtube_quota_ledger(summary["day_key"])
        self.assertTrue(
            ledger2 is None or int(ledger2.get("units_used") or 0) == 0,
        )
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
