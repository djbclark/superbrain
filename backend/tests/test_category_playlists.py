#!/usr/bin/env python3
"""Tests for category → YouTube playlist sync (mocked API)."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

from core.category_playlists import (
    PlaylistSyncConfig,
    can_spend_quota,
    ensure_category_playlists,
    extract_youtube_video_id,
    load_playlist_sync_config,
    pacific_day_key,
    priority_for_analysis_row,
    record_quota_spend,
    sync_video_category,
)
from core.database import Database
from core.taxonomy import clear_taxonomy_cache


TOML = """
[taxonomy]
use_default_categories = false
allow_multiple_categories = false
fallback_category = "Other"
confidence_threshold = 0.55
suggestion_min_count = 5

[[categories]]
name = "Sysadmin"
precedence = 1
guidance = "Tools."

[[categories]]
name = "Other"
precedence = 2
guidance = "Fallback."

[youtube_playlists]
enabled = true
dry_run = false
title_prefix = "SB — "
privacy_status = "private"
daily_quota_units = 10000
new_video_reserve_pct = 0.90
near_reset_hours = 2
near_reset_historic_pct = 0.90
"""


class TestExtractVideoId(unittest.TestCase):
    def test_shortcode(self):
        self.assertEqual(extract_youtube_video_id("YT_dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_watch_url(self):
        self.assertEqual(
            extract_youtube_video_id(
                "", "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1"
            ),
            "dQw4w9WgXcQ",
        )

    def test_youtu_be(self):
        self.assertEqual(
            extract_youtube_video_id("", "https://youtu.be/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )


class TestPlaylistSync(unittest.TestCase):
    def setUp(self):
        clear_taxonomy_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg_path = Path(self.tmp.name) / "categories.toml"
        self.cfg_path.write_text(TOML, encoding="utf-8")
        self.db_path = Path(self.tmp.name) / "t.db"
        self.db = Database(db_path=self.db_path)
        self.db.save_analysis(
            shortcode="YT_dQw4w9WgXcQ",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            username="u",
            title="t",
            summary="s",
            tags=[],
            music="",
            category="Sysadmin",
            content_type="youtube",
        )
        self.cfg = load_playlist_sync_config(self.cfg_path)

    def tearDown(self):
        clear_taxonomy_cache()
        self.tmp.cleanup()

    def test_load_config(self):
        self.assertTrue(self.cfg.enabled)
        self.assertFalse(self.cfg.dry_run)
        self.assertEqual(self.cfg.title_prefix, "SB — ")
        self.assertEqual(self.cfg.config_path, self.cfg_path)
        self.assertEqual(self.cfg.daily_quota_units, 10000)
        self.assertEqual(self.cfg.historic_normal_cap, 1000)

    def test_ensure_creates_and_maps(self):
        client = MagicMock()
        client.list_my_playlists.return_value = []
        client.last_list_pages = 1
        client.create_playlist.side_effect = lambda title, privacy: f"PL_{title}"
        out = ensure_category_playlists(self.db, config=self.cfg, client=client)
        self.assertTrue(out["ok"])
        mapped = {a["category"]: a for a in out["actions"]}
        self.assertEqual(mapped["Sysadmin"]["action"], "created")
        self.assertEqual(
            self.db.get_category_youtube_playlist("Sysadmin")["playlist_id"],
            "PL_SB — Sysadmin",
        )

    def test_sync_add_and_move(self):
        client = MagicMock()
        client.list_my_playlists.return_value = []
        client.last_list_pages = 1
        client.create_playlist.side_effect = lambda title, privacy: {
            "SB — Sysadmin": "PL_sys",
            "SB — Other": "PL_other",
        }[title]
        client.add_video.side_effect = (
            lambda playlist_id, video_id, position=0: f"item_{playlist_id}"
        )

        first = sync_video_category(
            self.db,
            shortcode="YT_dQw4w9WgXcQ",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            new_category="Sysadmin",
            config=self.cfg,
            client=client,
            priority="new",
        )
        self.assertTrue(first["ok"])
        self.assertEqual(first["actions"][-1]["op"], "add")
        self.assertEqual(first["actions"][-1]["position"], 0)
        client.add_video.assert_called_with("PL_sys", "dQw4w9WgXcQ", position=0)
        row = self.db.get_category_youtube_playlist_item("dQw4w9WgXcQ")
        self.assertEqual(row["category_name"], "Sysadmin")

        again = sync_video_category(
            self.db,
            shortcode="YT_dQw4w9WgXcQ",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            new_category="Sysadmin",
            config=self.cfg,
            client=client,
            priority="new",
        )
        self.assertEqual(again["skipped"], "already_synced")

        moved = sync_video_category(
            self.db,
            shortcode="YT_dQw4w9WgXcQ",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            new_category="Other",
            old_category="Sysadmin",
            config=self.cfg,
            client=client,
            priority="new",
        )
        self.assertTrue(moved["ok"])
        ops = [a["op"] for a in moved["actions"]]
        self.assertIn("remove", ops)
        self.assertIn("add", ops)
        client.remove_playlist_item.assert_called()
        row = self.db.get_category_youtube_playlist_item("dQw4w9WgXcQ")
        self.assertEqual(row["category_name"], "Other")

    def test_dry_run_does_not_call_api_mutate(self):
        client = MagicMock()
        cfg = PlaylistSyncConfig(
            enabled=True,
            dry_run=True,
            title_prefix="SB — ",
            config_path=self.cfg_path,
        )
        out = sync_video_category(
            self.db,
            shortcode="YT_dQw4w9WgXcQ",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            new_category="Sysadmin",
            config=cfg,
            client=client,
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["actions"][0]["op"], "would_add")
        self.assertEqual(out["actions"][0]["position"], 0)
        client.create_playlist.assert_not_called()
        client.add_video.assert_not_called()

    def test_enable_playlist_sync_in_config(self):
        from core.category_playlists import enable_playlist_sync_in_config

        # Start from disabled section
        self.cfg_path.write_text(
            TOML.replace("enabled = true", "enabled = false").replace(
                "dry_run = false", "dry_run = true"
            ),
            encoding="utf-8",
        )
        cfg = enable_playlist_sync_in_config(self.cfg_path, dry_run=False)
        self.assertTrue(cfg.enabled)
        self.assertFalse(cfg.dry_run)
        text = self.cfg_path.read_text(encoding="utf-8")
        self.assertIn("enabled = true", text)
        self.assertIn("dry_run = false", text)
        self.assertIn('title_prefix = "SB — "', text)
        self.assertIn("daily_quota_units = 10000", text)


class TestQuotaBudget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(db_path=Path(self.tmp.name) / "q.db")
        self.cfg = PlaylistSyncConfig(
            enabled=True,
            dry_run=False,
            daily_quota_units=10000,
            new_video_reserve_pct=0.90,
            near_reset_hours=2.0,
            near_reset_historic_pct=0.90,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_pacific_day_key_boundary(self):
        # 2026-07-31 23:30 PT → same calendar day; 00:30 PT next day → rolls.
        try:
            from zoneinfo import ZoneInfo

            pt = ZoneInfo("America/Los_Angeles")
        except Exception:
            self.skipTest("zoneinfo unavailable")
        late = datetime(2026, 7, 31, 23, 30, tzinfo=pt)
        early = datetime(2026, 8, 1, 0, 30, tzinfo=pt)
        self.assertEqual(pacific_day_key(late), "2026-07-31")
        self.assertEqual(pacific_day_key(early), "2026-08-01")

    def test_historic_capped_at_ten_percent_normal_phase(self):
        try:
            from zoneinfo import ZoneInfo

            pt = ZoneInfo("America/Los_Angeles")
        except Exception:
            self.skipTest("zoneinfo unavailable")
        # Mid-day PT — not near reset.
        noon = datetime(2026, 7, 31, 12, 0, tzinfo=pt)
        self.assertTrue(
            can_spend_quota(
                self.db, self.cfg, priority="historic", units=50, now=noon
            )
        )
        record_quota_spend(
            self.db, self.cfg, priority="historic", units=1000, now=noon
        )
        self.assertFalse(
            can_spend_quota(
                self.db, self.cfg, priority="historic", units=50, now=noon
            )
        )
        # New videos still allowed within daily total.
        self.assertTrue(
            can_spend_quota(self.db, self.cfg, priority="new", units=50, now=noon)
        )

    def test_near_reset_allows_historic_up_to_ninety_percent(self):
        try:
            from zoneinfo import ZoneInfo

            pt = ZoneInfo("America/Los_Angeles")
        except Exception:
            self.skipTest("zoneinfo unavailable")
        near = datetime(2026, 7, 31, 23, 0, tzinfo=pt)  # 1h to midnight
        record_quota_spend(
            self.db, self.cfg, priority="historic", units=1000, now=near
        )
        self.assertTrue(
            can_spend_quota(
                self.db, self.cfg, priority="historic", units=50, now=near
            )
        )
        record_quota_spend(
            self.db, self.cfg, priority="historic", units=8000, now=near
        )
        # 9000 used → at near_reset_total_cap; another 50 would exceed.
        self.assertFalse(
            can_spend_quota(
                self.db, self.cfg, priority="historic", units=50, now=near
            )
        )
        # Buffer remains for new.
        self.assertTrue(
            can_spend_quota(self.db, self.cfg, priority="new", units=50, now=near)
        )

    def test_pending_new_priority_and_enqueue_on_budget(self):
        clear_taxonomy_cache()
        cfg_path = Path(self.tmp.name) / "categories.toml"
        cfg_path.write_text(TOML, encoding="utf-8")
        cfg = load_playlist_sync_config(cfg_path)
        self.db.save_analysis(
            shortcode="YT_abcdefghijk",
            url="https://www.youtube.com/watch?v=abcdefghijk",
            username="u",
            title="t",
            summary="s",
            tags=[],
            music="",
            category="Sysadmin",
            content_type="youtube",
        )
        self.db.upsert_category_youtube_playlist(
            "Sysadmin", "PL_sys", "SB — Sysadmin"
        )
        try:
            from zoneinfo import ZoneInfo

            pt = ZoneInfo("America/Los_Angeles")
        except Exception:
            self.skipTest("zoneinfo unavailable")
        noon = datetime(2026, 7, 31, 12, 0, tzinfo=pt)
        # Exhaust full daily budget so even new cannot spend.
        record_quota_spend(self.db, cfg, priority="new", units=10000, now=noon)
        client = MagicMock()
        out = sync_video_category(
            self.db,
            shortcode="YT_abcdefghijk",
            url="https://www.youtube.com/watch?v=abcdefghijk",
            new_category="Sysadmin",
            config=cfg,
            client=client,
            priority="new",
            ensure_playlists=False,
        )
        self.assertEqual(out["skipped"], "quota_budget")
        pending = self.db.list_category_youtube_playlist_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["priority"], "new")
        client.add_video.assert_not_called()

        # Historic budget deny does not enqueue.
        self.db.delete_category_youtube_playlist_pending("YT_abcdefghijk")
        # Reset ledger for a fresh day key simulation: mark only historic capped.
        day = pacific_day_key(noon)
        self.db._conn.execute(
            "UPDATE youtube_api_quota_ledger SET units_used=1000, new_units_used=0, historic_units_used=1000 WHERE day_key=?",
            (day,),
        )
        self.db._conn.commit()
        out2 = sync_video_category(
            self.db,
            shortcode="YT_abcdefghijk",
            url="https://www.youtube.com/watch?v=abcdefghijk",
            new_category="Sysadmin",
            config=cfg,
            client=client,
            priority="historic",
            ensure_playlists=False,
        )
        self.assertEqual(out2["skipped"], "quota_budget")
        self.assertEqual(self.db.list_category_youtube_playlist_pending(), [])

    def test_pending_drain_order_new_before_historic(self):
        self.db.upsert_category_youtube_playlist_pending(
            shortcode="YT_oldhistoric1",
            priority="historic",
            category="Sysadmin",
        )
        self.db.upsert_category_youtube_playlist_pending(
            shortcode="YT_brandnewvid",
            priority="new",
            category="Sysadmin",
        )
        self.db.upsert_category_youtube_playlist_pending(
            shortcode="YT_oldhistoric2",
            priority="historic",
            category="Other",
        )
        ordered = self.db.list_category_youtube_playlist_pending()
        self.assertEqual(ordered[0]["shortcode"], "YT_brandnewvid")
        self.assertEqual(ordered[0]["priority"], "new")
        self.assertTrue(all(p["priority"] == "historic" for p in ordered[1:]))

    def test_fresh_window_priority(self):
        cfg = PlaylistSyncConfig(fresh_window_hours=24.0)
        now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
        fresh = {
            "analyzed_at": (now - timedelta(hours=1)).isoformat(),
        }
        old = {
            "analyzed_at": (now - timedelta(hours=48)).isoformat(),
        }
        self.assertEqual(priority_for_analysis_row(cfg, fresh, now=now), "new")
        self.assertEqual(priority_for_analysis_row(cfg, old, now=now), "historic")


if __name__ == "__main__":
    unittest.main()
