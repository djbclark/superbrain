#!/usr/bin/env python3
"""Tests for CLI locks and YouTube quota helpers."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core.cli_locks import CliLockUnavailable, exclusive_cli_lock
from core.category_playlists import (
    is_youtube_quota_error,
    seconds_until_youtube_quota_reset,
)


class TestCliLocks(unittest.TestCase):
    def test_exclusive_lock_blocks_second_holder(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"SUPERBRAIN_RUNTIME_DIR": tmp}):
                with exclusive_cli_lock("demo-cmd"):
                    with self.assertRaises(CliLockUnavailable):
                        with exclusive_cli_lock("demo-cmd"):
                            pass
                # Released — can acquire again
                with exclusive_cli_lock("demo-cmd"):
                    self.assertTrue((Path(tmp) / "locks" / "demo-cmd.lock").is_file())


class TestQuotaHelpers(unittest.TestCase):
    def test_is_youtube_quota_error(self):
        self.assertTrue(is_youtube_quota_error(Exception("403 Client Error: Forbidden")))
        self.assertTrue(is_youtube_quota_error(Exception("quotaExceeded")))
        self.assertFalse(is_youtube_quota_error(Exception("404 Not Found")))

    def test_seconds_until_reset_positive(self):
        now = datetime(2026, 7, 30, 16, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        secs = seconds_until_youtube_quota_reset(now=now)
        # 8 hours to midnight + 2 minutes
        self.assertGreater(secs, 8 * 3600)
        self.assertLess(secs, 9 * 3600)


if __name__ == "__main__":
    unittest.main()
