#!/usr/bin/env python3
"""Tests for category → YouTube playlist sync (mocked API)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from core.category_playlists import (
    PlaylistSyncConfig,
    ensure_category_playlists,
    extract_youtube_video_id,
    load_playlist_sync_config,
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

    def test_ensure_creates_and_maps(self):
        client = MagicMock()
        client.list_my_playlists.return_value = []
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
        client.create_playlist.side_effect = lambda title, privacy: {
            "SB — Sysadmin": "PL_sys",
            "SB — Other": "PL_other",
        }[title]
        client.add_video.side_effect = lambda playlist_id, video_id: f"item_{playlist_id}"

        first = sync_video_category(
            self.db,
            shortcode="YT_dQw4w9WgXcQ",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            new_category="Sysadmin",
            config=self.cfg,
            client=client,
        )
        self.assertTrue(first["ok"])
        self.assertEqual(first["actions"][-1]["op"], "add")
        row = self.db.get_category_youtube_playlist_item("dQw4w9WgXcQ")
        self.assertEqual(row["category_name"], "Sysadmin")

        again = sync_video_category(
            self.db,
            shortcode="YT_dQw4w9WgXcQ",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            new_category="Sysadmin",
            config=self.cfg,
            client=client,
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
        client.create_playlist.assert_not_called()
        client.add_video.assert_not_called()


if __name__ == "__main__":
    unittest.main()
