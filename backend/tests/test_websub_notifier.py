#!/usr/bin/env python3
"""
Unit tests for core/websub_notifier.py
======================================
Tests WebSub topic URL generation, OPML subscription parsing,
GET challenge verification, and Atom XML payload parsing.
"""

import sys
import unittest
import hashlib
import hmac
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.websub_notifier import (
    build_topic_url,
    verify_websub_challenge,
    parse_websub_atom_payload,
    parse_opml_subscriptions,
    parse_youtube_feed_entries,
    verify_websub_signature,
)


class TestWebSubNotifier(unittest.TestCase):
    """Test suite for YouTube WebSub notification module."""

    def test_build_topic_url(self):
        """Test build_topic_url generates correct YouTube Atom feed URL."""
        cid = "UC_x5XG1OV2P6uZZ5FSM9Ttw"
        url = build_topic_url(cid)
        self.assertEqual(url, f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")

    def test_build_topic_url_rejects_invalid_channel(self):
        with self.assertRaises(ValueError):
            build_topic_url("not-a-channel")

    def test_verify_websub_challenge_valid(self):
        """Test verify_websub_challenge echoes back challenge string."""
        challenge = "random_challenge_string_12345"
        resp = verify_websub_challenge("subscribe", "topic", challenge)
        self.assertEqual(resp, challenge)

    def test_verify_websub_challenge_invalid(self):
        """Test verify_websub_challenge raises ValueError on invalid mode."""
        with self.assertRaises(ValueError):
            verify_websub_challenge("invalid_mode", "topic", "challenge_str")

    def test_verify_websub_challenge_rejects_unexpected_topic(self):
        with self.assertRaises(ValueError):
            verify_websub_challenge(
                "subscribe",
                "https://example.test/unexpected",
                "challenge",
                expected_topic="https://example.test/expected",
                expected_mode="subscribe",
            )

    def test_verify_websub_signature(self):
        body = b"<feed>signed</feed>"
        secret = "test-secret"
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(
            verify_websub_signature(body, f"sha256={digest}", secret)
        )
        self.assertFalse(
            verify_websub_signature(body + b"x", f"sha256={digest}", secret)
        )

    def test_parse_websub_atom_payload(self):
        """Test parsing incoming Atom XML push payload from Google Hub."""
        atom_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>yt:video:Mqr2wO_Vap8</id>
            <yt:videoId>Mqr2wO_Vap8</yt:videoId>
            <yt:channelId>UC_x5XG1OV2P6uZZ5FSM9Ttw</yt:channelId>
            <title>Test Video Title</title>
            <link rel="alternate" href="https://www.youtube.com/watch?v=Mqr2wO_Vap8"/>
          </entry>
        </feed>
        """
        parsed = parse_websub_atom_payload(atom_xml)
        self.assertEqual(parsed["video_id"], "Mqr2wO_Vap8")
        self.assertEqual(parsed["video_url"], "https://www.youtube.com/watch?v=Mqr2wO_Vap8")
        self.assertEqual(parsed["title"], "Test Video Title")
        self.assertEqual(parsed["channel_id"], "UC_x5XG1OV2P6uZZ5FSM9Ttw")

    def test_parse_youtube_feed_entries(self):
        feed_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <yt:videoId>Mqr2wO_Vap8</yt:videoId>
            <yt:channelId>UC_x5XG1OV2P6uZZ5FSM9Ttw</yt:channelId>
            <title>Test Video Title</title>
            <published>2026-07-30T15:00:00+00:00</published>
          </entry>
        </feed>
        """
        entries = parse_youtube_feed_entries(feed_xml)
        self.assertEqual(entries, [{
            "video_id": "Mqr2wO_Vap8",
            "video_url": "https://www.youtube.com/watch?v=Mqr2wO_Vap8",
            "channel_id": "UC_x5XG1OV2P6uZZ5FSM9Ttw",
            "title": "Test Video Title",
            "published": "2026-07-30T15:00:00+00:00",
        }])

    def test_parse_opml_subscriptions(self):
        """Test parsing YouTube subscription OPML export file."""
        opml_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <opml version="1.1">
          <body>
            <outline text="YouTube Subscriptions">
              <outline title="Google Chrome" xmlUrl="https://www.youtube.com/feeds/videos.xml?channel_id=UC_x5XG1OV2P6uZZ5FSM9Ttw"/>
              <outline title="Tech Channel" xmlUrl="https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa"/>
            </outline>
          </body>
        </opml>
        """
        channels = parse_opml_subscriptions(opml_xml)
        self.assertEqual(len(channels), 2)
        self.assertEqual(channels[0]["channel_id"], "UC_x5XG1OV2P6uZZ5FSM9Ttw")
        self.assertEqual(channels[0]["title"], "Google Chrome")
        self.assertEqual(channels[1]["channel_id"], "UCaaaaaaaaaaaaaaaaaaaaaa")


if __name__ == "__main__":
    unittest.main()
