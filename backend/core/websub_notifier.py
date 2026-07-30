#!/usr/bin/env python3
"""
YouTube WebSub (PubSubHubbub) Notifier for SuperBrain
======================================================
Provides real-time push notification subscriptions for YouTube channels via
Google's official WebSub Hub (pubsubhubbub.appspot.com).

Features:
- Subscribes SuperBrain to 200+ YouTube channel upload feeds.
- Parses OPML subscription files exported from YouTube / Google Takeout.
- Verifies WebSub GET challenges (hub.challenge response).
- Parses WebSub POST Atom XML payloads to extract new video upload URLs.
- Stores active subscriptions in SQLite superbrain.db database.
"""

import os
import re
import hmac
import hashlib
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from defusedxml import ElementTree as ET
from typing import List, Dict, Optional, Tuple, Any

GOOGLE_HUB_URL = "https://pubsubhubbub.appspot.com/subscribe"
YOUTUBE_TOPIC_PREFIX = "https://www.youtube.com/feeds/videos.xml?channel_id="
CHANNEL_ID_PATTERN = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


# ── WebSub Hub Client Operations ─────────────────────────────────────────────

def build_topic_url(channel_id: str) -> str:
    """Return the Google WebSub topic URL for a given YouTube channel ID."""
    clean_id = channel_id.strip()
    if clean_id.startswith("http"):
        # Extract channel ID from URL
        match = re.search(r"channel_id=([A-Za-z0-9_-]+)", clean_id)
        if match:
            clean_id = match.group(1)
        else:
            match_path = re.search(r"/channel/([A-Za-z0-9_-]+)", clean_id)
            if match_path:
                clean_id = match_path.group(1)
    if not CHANNEL_ID_PATTERN.fullmatch(clean_id):
        raise ValueError(f"Invalid YouTube channel ID: {clean_id!r}")
    return f"{YOUTUBE_TOPIC_PREFIX}{clean_id}"


def subscribe_channel(
    channel_id: str,
    callback_url: str,
    lease_seconds: int = 864000,
    secret: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Send an HTTP POST subscription request to Google's WebSub Hub.

    Args:
        channel_id (str): YouTube Channel ID (e.g., 'UC...').
        callback_url (str): Publicly reachable HTTPS endpoint of SuperBrain.
        lease_seconds (int): Lease duration in seconds (default: 864000 = 10 days).
        secret (Optional[str]): HMAC secret key for signature verification.

    Returns:
        Tuple[bool, str]: (Success, Message)
    """
    topic_url = build_topic_url(channel_id)
    if secret and len(secret.encode("utf-8")) > 200:
        return False, "WebSub hub secret must be at most 200 bytes"
    payload = {
        "hub.callback": callback_url,
        "hub.mode": "subscribe",
        "hub.topic": topic_url,
        "hub.lease_seconds": str(lease_seconds),
    }
    if secret:
        payload["hub.secret"] = secret

    encoded_data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        GOOGLE_HUB_URL,
        data=encoded_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (202, 204, 200):
                return True, f"Subscription request accepted for channel {channel_id}"
            return False, f"Unexpected response status: {resp.status}"
    except Exception as e:
        return False, f"Failed to subscribe to channel {channel_id}: {e}"


def unsubscribe_channel(channel_id: str, callback_url: str) -> Tuple[bool, str]:
    """Send an HTTP POST unsubscription request to Google's WebSub Hub."""
    topic_url = build_topic_url(channel_id)
    payload = {
        "hub.callback": callback_url,
        "hub.mode": "unsubscribe",
        "hub.topic": topic_url,
    }
    encoded_data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        GOOGLE_HUB_URL,
        data=encoded_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (202, 204, 200):
                return True, f"Unsubscription request accepted for channel {channel_id}"
            return False, f"Unexpected response status: {resp.status}"
    except Exception as e:
        return False, f"Failed to unsubscribe from channel {channel_id}: {e}"


def subscribe_channels(
    channel_ids: List[str],
    callback_url: str,
    lease_seconds: int = 864000,
    secret: Optional[str] = None,
    max_workers: int = 8,
) -> Dict[str, Any]:
    """
    Batch subscribe SuperBrain to multiple YouTube channels (e.g. 200+ channels).

    Returns summary stats dict.
    """
    unique_ids = list(dict.fromkeys(channel_ids))
    stats = {"total": len(unique_ids), "success": 0, "failed": 0, "details": []}
    if not unique_ids:
        return stats
    worker_count = max(1, min(max_workers, len(unique_ids)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                subscribe_channel,
                cid,
                callback_url,
                lease_seconds,
                secret,
            ): cid
            for cid in unique_ids
        }
        for future in as_completed(futures):
            cid = futures[future]
            try:
                ok, msg = future.result()
            except Exception as exc:
                ok, msg = False, f"Failed to subscribe to channel {cid}: {exc}"
            if ok:
                stats["success"] += 1
            else:
                stats["failed"] += 1
            stats["details"].append(
                {"channel_id": cid, "success": ok, "message": msg}
            )
    stats["details"].sort(key=lambda item: unique_ids.index(item["channel_id"]))
    return stats


# ── OPML Subscription Parser ──────────────────────────────────────────────────

def parse_opml_subscriptions(opml_data_or_path: str) -> List[Dict[str, str]]:
    """
    Parse a Google Takeout / YouTube subscription export OPML XML file.

    Returns a list of dicts containing channel_id, title, and xml_url.
    """
    content = opml_data_or_path
    if os.path.exists(opml_data_or_path):
        with open(opml_data_or_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

    channels = []
    try:
        root = ET.fromstring(content)
        for outline in root.findall(".//outline"):
            xml_url = outline.get("xmlUrl", "")
            title = outline.get("title") or outline.get("text") or ""
            if "channel_id=" in xml_url:
                match = re.search(r"channel_id=([A-Za-z0-9_-]+)", xml_url)
                if match:
                    channels.append({
                        "channel_id": match.group(1),
                        "title": title,
                        "xml_url": xml_url
                    })
    except Exception as e:
        raise ValueError(f"Failed to parse OPML XML content: {e}")

    return channels


def parse_youtube_feed_entries(xml_content: str | bytes) -> List[Dict[str, str]]:
    """Extract upload entries from a YouTube channel Atom feed."""
    namespaces = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    try:
        root = ET.fromstring(xml_content)
    except Exception as exc:
        raise ValueError(f"Failed to parse YouTube Atom feed: {exc}") from exc

    entries = []
    for entry in root.findall("atom:entry", namespaces):
        video_id = entry.findtext("yt:videoId", default="", namespaces=namespaces).strip()
        channel_id = entry.findtext("yt:channelId", default="", namespaces=namespaces).strip()
        title = entry.findtext("atom:title", default="", namespaces=namespaces).strip()
        published = entry.findtext("atom:published", default="", namespaces=namespaces).strip()
        if video_id:
            entries.append({
                "video_id": video_id,
                "video_url": f"https://www.youtube.com/watch?v={video_id}",
                "channel_id": channel_id,
                "title": title,
                "published": published,
            })
    return entries


def fetch_youtube_feed_entries(channel_id: str, timeout: int = 15) -> List[Dict[str, str]]:
    """Fetch one channel feed for the WebSub missed-delivery reconciliation."""
    topic_url = build_topic_url(channel_id)
    request = urllib.request.Request(
        topic_url,
        headers={"User-Agent": "SuperBrain-WebSub-Reconciler/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return parse_youtube_feed_entries(response.read())
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch YouTube feed for {channel_id}: {exc}") from exc


# ── Webhook Challenge Verification & Atom Payload Parser ──────────────────────

def verify_websub_challenge(
    mode: str,
    topic: str,
    challenge: str,
    lease_seconds: Optional[int] = None,
    expected_topic: Optional[str] = None,
    expected_mode: Optional[str] = None,
) -> str:
    """
    Handle Google's GET verification challenge request.
    Echoes back the hub.challenge string.
    """
    if expected_topic is not None and not hmac.compare_digest(
        topic or "", expected_topic
    ):
        raise ValueError("WebSub topic does not match a pending subscription")
    if expected_mode is not None and mode != expected_mode:
        raise ValueError("WebSub mode does not match the pending operation")
    if mode in ("subscribe", "unsubscribe") and challenge:
        return challenge
    raise ValueError("Invalid WebSub challenge request")


def verify_websub_signature(
    body: bytes,
    signature_header: str,
    secret: str,
) -> bool:
    """Verify an X-Hub-Signature header using a WebSub-recognized HMAC."""
    if not body or not signature_header or not secret:
        return False
    try:
        algorithm, supplied_digest = signature_header.split("=", 1)
    except ValueError:
        return False
    digest_functions = {
        "sha1": hashlib.sha1,
        "sha256": hashlib.sha256,
        "sha384": hashlib.sha384,
        "sha512": hashlib.sha512,
    }
    digest_function = digest_functions.get(algorithm.lower())
    if digest_function is None:
        return False
    expected_digest = hmac.new(
        secret.encode("utf-8"), body, digest_function
    ).hexdigest()
    return hmac.compare_digest(expected_digest, supplied_digest.lower())


def parse_websub_atom_payload(xml_content: str | bytes) -> Dict[str, Any]:
    """
    Parse an incoming Atom XML POST notification from Google's WebSub Hub.

    Extracts:
    - video_id: YouTube video ID (e.g. 'Mqr2wO_Vap8')
    - video_url: Full YouTube URL ('https://www.youtube.com/watch?v=...')
    - title: Video title
    - channel_id: YouTube channel ID
    """
    namespaces = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    try:
        root = ET.fromstring(xml_content.strip())
        entry = root.find("atom:entry", namespaces)
        if entry is None:
            entry = root.find("entry")

        if entry is None:
            raise ValueError("No <entry> tag found in Atom XML payload")

        video_id_elem = entry.find("yt:videoId", namespaces)
        if video_id_elem is None:
            video_id_elem = entry.find("{http://www.youtube.com/xml/schemas/2015}videoId")

        channel_id_elem = entry.find("yt:channelId", namespaces)
        if channel_id_elem is None:
            channel_id_elem = entry.find("{http://www.youtube.com/xml/schemas/2015}channelId")

        title_elem = entry.find("atom:title", namespaces)
        if title_elem is None:
            title_elem = entry.find("title")

        video_id = video_id_elem.text.strip() if video_id_elem is not None and video_id_elem.text else ""
        channel_id = channel_id_elem.text.strip() if channel_id_elem is not None and channel_id_elem.text else ""
        title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""

        if not video_id:
            raise ValueError("Missing yt:videoId in Atom XML payload")

        video_url = f"https://www.youtube.com/watch?v={video_id}"
        return {
            "video_id": video_id,
            "video_url": video_url,
            "title": title,
            "channel_id": channel_id,
        }
    except Exception as e:
        raise ValueError(f"Failed to parse WebSub Atom XML payload: {e}")
