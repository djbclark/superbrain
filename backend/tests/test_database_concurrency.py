#!/usr/bin/env python3

import contextlib
import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from core.database import Database


class TestDatabaseConcurrency(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "superbrain-test.db"
        self.output = io.StringIO()
        with contextlib.redirect_stdout(self.output):
            self.db = Database(self.db_path)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def _save(self, index):
        return self.db.save_analysis(
                shortcode=f"video-{index}",
                url=f"https://www.youtube.com/watch?v={index:011d}",
                username="channel",
                title=f"Video {index}",
                summary="summary",
                tags=[],
                music="",
                category="other",
                content_type="youtube",
                transcript_mode="native",
        )

    def test_parallel_writes_use_independent_connections(self):
        with patch("builtins.print"):
            with ThreadPoolExecutor(max_workers=16) as executor:
                results = list(executor.map(self._save, range(300)))
        count = self.db._conn.execute(
            "SELECT COUNT(*) FROM analyses"
        ).fetchone()[0]
        self.assertTrue(all(results))
        self.assertEqual(count, 300)

    def test_get_by_shortcode_preserves_transcript_mode(self):
        with patch("builtins.print"):
            self.assertTrue(self._save(1))
        row = self.db.get_by_shortcode("video-1")
        self.assertEqual(row["transcript_mode"], "native")

    def test_get_posts_since_supports_stable_page_offsets(self):
        with patch("builtins.print"):
            for index in range(3):
                self.assertTrue(self._save(index))

        first_page = self.db.get_posts_since("2000-01-01T00:00:00", limit=2)
        second_page = self.db.get_posts_since(
            "2000-01-01T00:00:00", limit=2, offset=2
        )

        self.assertEqual(len(first_page), 2)
        self.assertEqual(len(second_page), 1)
        self.assertEqual(
            {row["shortcode"] for first in [first_page] for row in first}
            .isdisjoint({row["shortcode"] for row in second_page}),
            True,
        )

    def test_websub_state_only_activates_after_verified_challenge(self):
        channel_id = "UCaaaaaaaaaaaaaaaaaaaaaa"
        topic = (
            "https://www.youtube.com/feeds/videos.xml"
            f"?channel_id={channel_id}"
        )
        self.db.upsert_websub_subscription(
            channel_id=channel_id,
            callback_url="https://example.test/api/youtube/webhook",
            topic_url=topic,
        )
        pending = self.db.get_websub_subscription(channel_id)
        self.assertEqual(pending["status"], "pending")
        self.assertTrue(
            self.db.mark_websub_verified(topic, "subscribe", lease_seconds=60)
        )
        active = self.db.get_websub_subscription(channel_id)
        self.assertEqual(active["status"], "active")
        self.assertIsNotNone(active["lease_expires_at"])

        self.db.upsert_websub_subscription(
            channel_id=channel_id,
            callback_url="https://example.test/api/youtube/webhook",
            topic_url=topic,
            status="pending",
        )
        renewing = self.db.get_websub_subscription(channel_id)
        self.assertEqual(renewing["status"], "active")
        self.assertEqual(renewing["pending_mode"], "subscribe")

        self.db.mark_websub_failed(channel_id, "temporary hub failure")
        still_active = self.db.get_websub_subscription(channel_id)
        self.assertEqual(still_active["status"], "active")
        self.assertIsNone(still_active["pending_mode"])
        self.assertEqual(still_active["last_error"], "temporary hub failure")

    def test_recovery_does_not_requeue_active_worker(self):
        self.assertEqual(
            self.db.add_to_queue(
                "YT_dQw4w9WgXcQ",
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            ),
            1,
        )
        self.assertIsNotNone(self.db.claim_next_queue_item())
        self.assertEqual(self.db.recover_interrupted_items(), 0)

        stale = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(minutes=20)
        ).isoformat()
        self.db._conn.execute(
            "UPDATE processing_queue SET started_at = ? WHERE shortcode = ?",
            (stale, "YT_dQw4w9WgXcQ"),
        )
        self.db._conn.commit()
        with patch("builtins.print"):
            self.assertEqual(self.db.recover_interrupted_items(), 1)
        self.assertEqual(
            self.db._conn.execute(
                "SELECT status FROM processing_queue WHERE shortcode = ?",
                ("YT_dQw4w9WgXcQ",),
            ).fetchone()["status"],
            "queued",
        )


if __name__ == "__main__":
    unittest.main()
