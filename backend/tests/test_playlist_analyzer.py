#!/usr/bin/env python3
"""
Unit tests for analyzers/playlist_analyzer.py
=============================================
Tests yt-dlp availability checks, playlist URL extraction, and playlist import handling.
"""

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to sys.path for test execution
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analyzers.playlist_analyzer import (
    check_ytdlp_installed,
    ensure_ytdlp_or_exit,
    extract_playlist_urls,
    acquire_playlist_lock,
    release_playlist_lock,
    PlaylistLockUnavailable,
    _print_native_transcript_ratio,
)


class TestPlaylistAnalyzer(unittest.TestCase):
    """Test suite for YouTube playlist analyzer functions."""

    @patch("shutil.which")
    def test_check_ytdlp_installed_present(self, mock_which):
        """Test check_ytdlp_installed returns True when yt-dlp is in PATH."""
        mock_which.return_value = "/usr/local/bin/yt-dlp"
        self.assertTrue(check_ytdlp_installed())
        mock_which.assert_called_once_with("yt-dlp")

    @patch("shutil.which")
    def test_check_ytdlp_installed_missing(self, mock_which):
        """Test check_ytdlp_installed returns False when yt-dlp is missing."""
        mock_which.return_value = None
        self.assertFalse(check_ytdlp_installed())
        mock_which.assert_called_once_with("yt-dlp")

    @patch("analyzers.playlist_analyzer.check_ytdlp_installed")
    def test_ensure_ytdlp_or_exit_raises(self, mock_check):
        """Test ensure_ytdlp_or_exit calls sys.exit(1) when yt-dlp is missing."""
        mock_check.return_value = False
        with (
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as cm,
        ):
            ensure_ytdlp_or_exit()
        self.assertEqual(cm.exception.code, 1)

    @patch("analyzers.playlist_analyzer.check_ytdlp_installed")
    def test_extract_playlist_urls_missing_ytdlp(self, mock_check):
        """Test extract_playlist_urls raises RuntimeError if yt-dlp is not installed."""
        mock_check.return_value = False
        with self.assertRaises(RuntimeError) as cm:
            extract_playlist_urls("https://www.youtube.com/playlist?list=WL")
        self.assertIn("yt-dlp is required", str(cm.exception))

    @patch("subprocess.run")
    @patch("analyzers.playlist_analyzer.check_ytdlp_installed")
    def test_extract_playlist_urls_success(self, mock_check, mock_run):
        """Test extract_playlist_urls parses video URLs correctly from yt-dlp output."""
        mock_check.return_value = True
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = (
            "https://www.youtube.com/watch?v=video1\n"
            "https://www.youtube.com/watch?v=video2\n"
        )
        mock_run.return_value = mock_proc

        urls = extract_playlist_urls("https://www.youtube.com/playlist?list=PL123")
        self.assertEqual(len(urls), 2)
        self.assertEqual(urls[0], "https://www.youtube.com/watch?v=video1")
        self.assertEqual(urls[1], "https://www.youtube.com/watch?v=video2")

    @patch("subprocess.run")
    @patch("analyzers.playlist_analyzer.check_ytdlp_installed")
    def test_extract_playlist_urls_surfaces_ytdlp_failure(
        self, mock_check, mock_run
    ):
        mock_check.return_value = True
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="private playlist requires authentication",
        )
        with self.assertRaisesRegex(RuntimeError, "requires authentication"):
            extract_playlist_urls("https://www.youtube.com/playlist?list=PL123")

    def test_playlist_lock_normalizes_playlist_id_and_releases(self):
        playlist_id = f"PLtest{os.getpid()}abcdefghijk"
        first_url = f"https://www.youtube.com/playlist?list={playlist_id}"
        equivalent_url = (
            f"https://youtube.com/playlist?index=4&list={playlist_id}"
        )
        lock_file = acquire_playlist_lock(first_url)
        try:
            with self.assertRaises(PlaylistLockUnavailable):
                acquire_playlist_lock(equivalent_url)
        finally:
            release_playlist_lock(lock_file)

        reacquired = acquire_playlist_lock(equivalent_url)
        release_playlist_lock(reacquired)

    def test_native_ratio_counts_unavailable_transcripts(self):
        modes = ["native", "60s_whisper", "none", "none", "none"]
        ids = ["dQw4w9WgXcQ", "AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC", "DDDDDDDDDDD"]
        rows = {
            f"YT_{video_id}": {"transcript_mode": mode}
            for video_id, mode in zip(ids, modes)
        }
        fake_db = MagicMock()
        fake_db.get_by_shortcode.side_effect = rows.get
        items = [
            (index, f"https://www.youtube.com/watch?v={video_id}")
            for index, video_id in enumerate(ids, 1)
        ]

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            _print_native_transcript_ratio(fake_db, items)

        self.assertIn("Native YouTube: 1/5 (20.0%)", output.getvalue())
        self.assertIn("Unavailable: 3/5", output.getvalue())
        self.assertIn("LOW NATIVE TRANSCRIPT RATE", output.getvalue())

    def test_native_ratio_defers_threshold_when_legacy_metadata_is_unknown(self):
        rows = {"YT_dQw4w9WgXcQ": {"transcript_mode": "native"}}
        fake_db = type("FakeDb", (), {"get_by_shortcode": lambda _, code: rows.get(code)})()
        items = [
            (1, "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            (2, "https://www.youtube.com/watch?v=AAAAAAAAAAA"),
        ]

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            _print_native_transcript_ratio(fake_db, items)

        self.assertIn("coverage is incomplete", output.getvalue())
        self.assertNotIn("LOW NATIVE TRANSCRIPT RATE", output.getvalue())


if __name__ == "__main__":
    unittest.main()
