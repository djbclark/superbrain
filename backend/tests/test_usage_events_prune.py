#!/usr/bin/env python3
"""Tests for Database.prune_youtube_api_usage_events (retention + error storm)."""

import contextlib
import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.database import Database


def day(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


class TestPruneUsageEvents(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        with contextlib.redirect_stdout(io.StringIO()):
            self.db = Database(Path(self.tempdir.name) / "prune.db")

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def add(self, days_ago: int, result_class: str) -> None:
        self.db._conn.execute(
            "INSERT INTO youtube_api_usage_events"
            " (ts, day_key, resource, method, result_class, units, units_known, operation)"
            " VALUES (?, ?, 'playlistItems', 'list', ?, 0, 1, 'sync_video_category')",
            (f"{day(days_ago)}T00:00:00", day(days_ago), result_class),
        )
        self.db._conn.commit()

    def remaining(self):
        rows = self.db._conn.execute(
            "SELECT day_key, result_class FROM youtube_api_usage_events"
        ).fetchall()
        return sorted((r[0], r[1]) for r in rows)

    def test_old_transport_errors_go_after_a_week(self):
        self.add(30, "transport_error")
        self.add(10, "transport_error")
        self.add(3, "transport_error")
        self.assertEqual(self.db.prune_youtube_api_usage_events(), 2)
        self.assertEqual([r[0] for r in self.remaining()], [day(3)])

    def test_real_calls_are_kept_for_ninety_days(self):
        self.add(30, "ok")
        self.add(80, "not_found")
        self.add(100, "ok")
        self.assertEqual(self.db.prune_youtube_api_usage_events(), 1)
        self.assertEqual(len(self.remaining()), 2)

    def test_error_window_never_exceeds_keep_days(self):
        self.add(10, "transport_error")
        self.assertEqual(
            self.db.prune_youtube_api_usage_events(keep_days=30, error_keep_days=999), 0
        )

    def test_empty_table(self):
        self.assertEqual(self.db.prune_youtube_api_usage_events(), 0)


if __name__ == "__main__":
    unittest.main()
