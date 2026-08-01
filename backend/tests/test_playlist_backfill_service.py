#!/usr/bin/env python3
"""Tests for category playlist backfill enable flag (ngrok-style config file)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import playlist_backfill_service as svc


class TestPlaylistBackfillService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.tmp.name) / "runtime"
        (self.runtime / "config").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_enable_flag_uses_enabled_txt_name(self):
        flag = svc.enable_flag_path(self.runtime)
        self.assertTrue(flag.name.endswith("_enabled.txt"))
        self.assertFalse(svc.is_backfill_enabled(self.runtime))
        flag.write_text("enabled\n", encoding="utf-8")
        self.assertTrue(svc.is_backfill_enabled(self.runtime))

    def test_migrates_legacy_dot_enabled_flag(self):
        legacy = self.runtime / "config" / "category_playlist_backfill.enabled"
        legacy.write_text("on\n", encoding="utf-8")
        self.assertTrue(svc.is_backfill_enabled(self.runtime))
        self.assertTrue(svc.enable_flag_path(self.runtime).is_file())
        self.assertFalse(legacy.exists())

    def test_stop_removes_enable_flag(self):
        flag = svc.enable_flag_path(self.runtime)
        flag.write_text("enabled\n", encoding="utf-8")
        with (
            patch.object(svc, "remove_legacy_backfill_launch_agent"),
            patch.object(svc, "_stop_backfill_processes"),
            patch.object(svc, "_backfill_process_running", return_value=False),
            patch.object(svc, "time") as mock_time,
        ):
            mock_time.sleep = lambda _s: None
            status = svc.stop_category_playlist_backfill(runtime=self.runtime)
        self.assertFalse(flag.exists())
        self.assertEqual(status["action"], "stopped")
        self.assertFalse(status["enabled"])

    def test_start_writes_enabled_content(self):
        with (
            patch.object(svc, "remove_legacy_backfill_launch_agent"),
            patch.object(svc, "migrate_launch_agent_off_service_py", return_value=False),
            patch.object(svc, "_backfill_process_running", return_value=True),
            patch.object(svc, "time") as mock_time,
        ):
            mock_time.monotonic = lambda: 0.0
            mock_time.sleep = lambda _s: None
            status = svc.start_category_playlist_backfill(runtime=self.runtime)
        flag = svc.enable_flag_path(self.runtime)
        self.assertEqual(flag.read_text(encoding="utf-8").strip(), "enabled")
        self.assertEqual(status["action"], "started")

    def test_supervisor_respects_flag(self):
        supervisor = svc.PlaylistBackfillSupervisor(runtime=self.runtime)
        with patch.object(supervisor, "_terminate_child") as terminate:
            supervisor._reconcile()
            terminate.assert_called_once()
        svc.enable_flag_path(self.runtime).write_text("enabled\n", encoding="utf-8")
        with patch.object(supervisor, "_spawn_child") as spawn:
            supervisor._reconcile()
            spawn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
