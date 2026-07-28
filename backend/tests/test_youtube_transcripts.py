#!/usr/bin/env python3

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from analyzers import youtube_analyzer


class TestYoutubeTranscripts(unittest.TestCase):
    def test_cookie_argument_validation(self):
        self.assertEqual(
            youtube_analyzer._cookie_args("chrome:Default"),
            ["--cookies-from-browser", "chrome:Default"],
        )
        with tempfile.NamedTemporaryFile(suffix=".txt") as cookie_file:
            self.assertEqual(
                youtube_analyzer._cookie_args(cookie_file.name),
                ["--cookies", cookie_file.name],
            )
        with self.assertRaises(FileNotFoundError):
            youtube_analyzer._cookie_args("/missing/cookies.txt")

    @patch("analyzers.youtube_analyzer.subprocess.run")
    def test_native_json3_uses_browser_cookies(self, mock_run):
        def create_subtitle(command, **kwargs):
            output = command[command.index("-o") + 1]
            Path(f"{output}.en.json3").write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "segs": [
                                    {
                                        "utf8": (
                                            "one two three four five six seven "
                                            "eight nine ten eleven twelve"
                                        )
                                    }
                                ]
                            }
                        ]
                    }
                )
            )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = create_subtitle
        with contextlib.redirect_stdout(io.StringIO()):
            text, mode = youtube_analyzer.fetch_youtube_transcript(
                "https://www.youtube.com/watch?v=Mqr2wO_Vap8",
                use_native_subtitles=True,
                cookies="chrome",
            )
        self.assertEqual(mode, "native")
        self.assertIn("twelve", text)
        command = mock_run.call_args.args[0]
        self.assertIn("--cookies-from-browser", command)
        self.assertIn("chrome", command)

    @patch("analyzers.youtube_analyzer._analyze_youtube_via_groq")
    @patch("analyzers.youtube_analyzer.fetch_youtube_transcript")
    @patch("analyzers.youtube_analyzer._load_gemini_key")
    def test_transcript_options_reach_groq_fallback(
        self, mock_key, mock_fetch, mock_groq
    ):
        mock_key.return_value = ""
        mock_fetch.return_value = ("caption text", "native")
        mock_groq.return_value = {"raw_output": "ok", "error": None}
        youtube_analyzer.analyze_youtube(
            "https://www.youtube.com/watch?v=Mqr2wO_Vap8",
            use_native_subtitles=True,
            transcribe_seconds=90,
            cookies="chrome",
        )
        mock_groq.assert_called_once_with(
            "https://www.youtube.com/watch?v=Mqr2wO_Vap8",
            use_native_subtitles=True,
            transcribe_seconds=90,
            cookies="chrome",
            transcript_text="caption text",
            transcript_mode="native",
        )


if __name__ == "__main__":
    unittest.main()
