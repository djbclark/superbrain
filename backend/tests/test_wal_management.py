#!/usr/bin/env python3
"""Tests for Database WAL management (wal_stats / wal_checkpoint)."""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.database import Database


class TestWalStats(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "test-wal.db"
        self.output = io.StringIO()
        with contextlib.redirect_stdout(self.output):
            self.db = Database(self.db_path)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_wal_stats_returns_expected_keys(self):
        stats = self.db.wal_stats()
        for key in ("db_bytes", "wal_bytes", "shm_bytes", "total_bytes",
                     "wal_page_count", "threshold_bytes"):
            self.assertIn(key, stats, f"missing key: {key}")

    def test_wal_stats_db_file_exists(self):
        stats = self.db.wal_stats()
        self.assertGreater(stats["db_bytes"], 0, "db should exist after init")

    def test_wal_stats_total_equals_sum(self):
        stats = self.db.wal_stats()
        self.assertEqual(
            stats["total_bytes"],
            stats["db_bytes"] + stats["wal_bytes"] + stats["shm_bytes"],
        )

    def test_wal_stats_threshold_default(self):
        stats = self.db.wal_stats()
        # Default 200 MB unless env overridden
        self.assertEqual(stats["threshold_bytes"], self.db.WAL_CHECKPOINT_THRESHOLD_BYTES)


class TestWalCheckpoint(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "test-wal.db"
        self.output = io.StringIO()
        with contextlib.redirect_stdout(self.output):
            self.db = Database(self.db_path)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_checkpoint_below_threshold_skips(self):
        """When WAL is small, checkpoint should not run."""
        result = self.db.wal_checkpoint()
        self.assertFalse(result["checkpointed"])
        self.assertEqual(result["reason"], "below_threshold")
        self.assertIsNone(result["error"])

    def test_checkpoint_force_runs_regardless(self):
        """force=True should checkpoint even when WAL is tiny."""
        with patch("builtins.print"):
            result = self.db.wal_checkpoint(force=True)
        self.assertTrue(result["checkpointed"])
        self.assertEqual(result["reason"], "forced")
        self.assertIsNone(result["error"])

    def test_checkpoint_force_returns_expected_keys(self):
        with patch("builtins.print"):
            result = self.db.wal_checkpoint(force=True)
        for key in ("checkpointed", "wal_bytes_before", "wal_bytes_after",
                     "reason", "busy", "log_pages", "checkpointed_pages", "error"):
            self.assertIn(key, result, f"missing key: {key}")

    def test_checkpoint_after_writes_reduces_wal(self):
        """After bulk writes, a forced checkpoint should shrink (or zero) the WAL."""
        with patch("builtins.print"):
            for i in range(50):
                self.db.save_analysis(
                    shortcode=f"wal-test-{i}",
                    url=f"https://example.com/{i}",
                    username="tester",
                    title=f"Title {i}",
                    summary="x" * 500,
                    tags=[],
                    music="",
                    category="test",
                    content_type="youtube",
                )

        wal_path = Path(str(self.db_path) + "-wal")
        self.assertTrue(wal_path.exists(), "WAL file should exist after writes")
        wal_before = wal_path.stat().st_size
        self.assertGreater(wal_before, 0, "WAL file should not be empty")

        with patch("builtins.print"):
            result = self.db.wal_checkpoint(force=True)

        self.assertTrue(result["checkpointed"])
        # After TRUNCATE, WAL size should be strictly less than before, usually 0.
        self.assertLess(result["wal_bytes_after"], wal_before, "WAL size should be strictly reduced after TRUNCATE")

    def test_checkpoint_threshold_environment(self):
        """WAL_CHECKPOINT_THRESHOLD_BYTES should reflect the env var at class-load time."""
        # The class attribute is set from env at import time; verify it's an int
        self.assertIsInstance(self.db.WAL_CHECKPOINT_THRESHOLD_BYTES, int)
        self.assertGreater(self.db.WAL_CHECKPOINT_THRESHOLD_BYTES, 0)


class TestWalCheckpointWithLargeWal(unittest.TestCase):
    """Simulate a WAL that exceeds the threshold to verify auto-checkpoint triggers."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "test-wal-large.db"
        self.output = io.StringIO()
        with contextlib.redirect_stdout(self.output):
            self.db = Database(self.db_path)
        # Lower the threshold to 1 KB so we can easily exceed it
        self.db.WAL_CHECKPOINT_THRESHOLD_BYTES = 1024

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_auto_checkpoint_triggers_above_threshold(self):
        """When WAL exceeds the (lowered) threshold, checkpoint should auto-trigger."""
        with patch("builtins.print"):
            for i in range(20):
                self.db.save_analysis(
                    shortcode=f"large-wal-{i}",
                    url=f"https://example.com/{i}",
                    username="tester",
                    title=f"Title {i}",
                    summary="x" * 2000,
                    tags=[],
                    music="",
                    category="test",
                    content_type="youtube",
                )

        wal_path = Path(str(self.db_path) + "-wal")
        self.assertTrue(wal_path.exists(), "WAL file should be created")
        wal_before = wal_path.stat().st_size
        self.assertGreater(wal_before, 1024, "WAL should be > 1 KB after writes")

        with patch("builtins.print"):
            result = self.db.wal_checkpoint()

        self.assertTrue(result["checkpointed"], "Auto checkpoint should trigger")
        self.assertEqual(result["reason"], "exceeded_threshold")
        self.assertIsNone(result["error"])


if __name__ == "__main__":
    unittest.main()
