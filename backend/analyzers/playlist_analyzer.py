#!/usr/bin/env python3
"""
SuperBrain - YouTube Playlist Analyzer & Importer
=================================================
Extracts video URLs from YouTube playlists (including private/Watch Later)
and orchestrates batch video analysis in SuperBrain.

Features:
- Validates yt-dlp dependency before execution
- Supports browser cookies (e.g. Chrome, Firefox) for Watch Later / private playlists
- Supports custom start index for resuming runs
- Integrates with SuperBrain database caching
"""

import sys
import os
import shutil
import subprocess
import threading
from typing import Optional, List, Dict
from contextlib import contextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Local SuperBrain imports
from core.link_checker import validate_link
from core.database import Database, get_db
from analyzers.youtube_analyzer import _cookie_args

RETRY_SENTINEL = "__ENQUEUED_FOR_RETRY__"


def _print_native_transcript_ratio(db, playlist_items):
    """Report native-caption coverage without hiding failed transcript attempts."""
    native_count = 0
    whisper_count = 0
    unavailable_count = 0
    unknown_count = 0

    for _, url in playlist_items:
        validation = validate_link(url)
        row = (
            db.get_by_shortcode(validation["shortcode"])
            if validation["valid"]
            else None
        )
        mode = (row.get("transcript_mode") or "").lower() if row else ""
        if mode == "native":
            native_count += 1
        elif mode.endswith("_whisper"):
            whisper_count += 1
        elif mode == "none":
            unavailable_count += 1
        else:
            # Includes failed/unpersisted items and pre-migration cached rows.
            unknown_count += 1

    measured_count = native_count + whisper_count + unavailable_count
    if not measured_count:
        print(
            "📜 No transcript method metadata was recorded for the selected items.",
            flush=True,
        )
        return

    ratio = (native_count / measured_count) * 100.0
    print(
        "📊 Transcript Method Breakdown: "
        f"Native YouTube: {native_count}/{measured_count} ({ratio:.1f}%) | "
        f"Whisper Fallback: {whisper_count}/{measured_count} | "
        f"Unavailable: {unavailable_count}/{measured_count} | "
        f"Unknown: {unknown_count}",
        flush=True,
    )
    if unknown_count:
        coverage = (measured_count / len(playlist_items)) * 100.0
        print(
            "ℹ️  Transcript-mode coverage is incomplete: "
            f"{measured_count}/{len(playlist_items)} ({coverage:.1f}%) records "
            "have metadata. The 80% native-caption target is not assessed until "
            "legacy unknown records are refreshed.",
            flush=True,
        )
        return
    if measured_count >= 5 and ratio < 80.0:
        print("=" * 80, flush=True)
        print(
            "⚠️  [WARNING] LOW NATIVE TRANSCRIPT RATE: "
            f"{ratio:.1f}% ({native_count}/{measured_count} items natively "
            "transcribed)",
            flush=True,
        )
        print(
            "   Expected >= 80% native transcripts! "
            f"({whisper_count} Whisper fallback, "
            f"{unavailable_count} unavailable)",
            flush=True,
        )
        print("=" * 80, flush=True)


# ── Dependency Checks ─────────────────────────────────────────────────────────

def check_ytdlp_installed() -> bool:
    """
    Check if the 'yt-dlp' command-line tool is installed and available in system PATH.

    Returns:
        bool: True if yt-dlp is found in PATH, False otherwise.
    """
    return shutil.which("yt-dlp") is not None


def ensure_ytdlp_or_exit() -> None:
    """
    Verify that yt-dlp is installed. If missing, prints a clear error message
    with installation instructions and exits the program with code 1.
    """
    if not check_ytdlp_installed():
        print("\n" + "=" * 80)
        print("❌ ERROR: 'yt-dlp' is required for YouTube playlist import and video analysis.")
        print("   Please install yt-dlp to proceed:")
        print("     • via pip:  pip install yt-dlp")
        print("     • via brew: brew install yt-dlp")
        print("=" * 80 + "\n")
        sys.exit(1)


# ── Playlist Extraction ───────────────────────────────────────────────────────

def extract_playlist_urls(playlist_url: str, cookies: Optional[str] = None) -> List[str]:
    """
    Extract individual YouTube video URLs from a playlist URL using yt-dlp.
    Uses --ignore-errors to ensure 100% of playlist items are extracted.

    Args:
        playlist_url (str): Full URL of the YouTube playlist or Watch Later list.
        cookies (Optional[str]): Browser name (e.g., 'chrome', 'firefox') or path to cookies.txt file.

    Returns:
        List[str]: List of canonical YouTube video URLs extracted from the playlist.

    Raises:
        RuntimeError: If yt-dlp is not installed or execution fails.
    """
    if not check_ytdlp_installed():
        raise RuntimeError(
            "yt-dlp is required for YouTube playlist extraction. "
            "Please install it via 'pip install yt-dlp' or 'brew install yt-dlp'."
        )

    cmd = [
        "yt-dlp", "--flat-playlist", "--ignore-errors", "--no-warnings",
        "--extractor-args", "youtube:player_client=web,android",
        "--print", "https://www.youtube.com/watch?v=%(id)s"
    ]

    cmd.extend(_cookie_args(cookies))

    cmd.append(playlist_url)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        urls = [
            line.strip()
            for line in proc.stdout.splitlines()
            if line.strip() and "watch?v=" in line
        ]
        urls = list(dict.fromkeys(urls))
        if proc.returncode != 0 and not urls:
            detail = (proc.stderr or "yt-dlp returned no playlist items").strip()
            raise RuntimeError(detail[-1000:])
        return urls
    except Exception as e:
        raise RuntimeError(f"Failed to extract playlist URLs via yt-dlp: {e}")


def _process_single_item(
    idx: int,
    total: int,
    url: str,
    db_path: str,
    use_native_transcripts: bool,
    transcribe_seconds: int,
    cookies: Optional[str] = None,
) -> tuple[str, str, Optional[str]]:
    """
    Process a single playlist item. Returns (status, url, detail).
    status: 'cached' | 'success' | 'failed' | 'invalid'
    """
    from main import run_youtube_analysis

    with Database(db_path, initialize_schema=False) as db:
        validation = validate_link(url)
        if not validation["valid"]:
            print(f"❌ [# {idx}/{total}] Invalid link: {url}", flush=True)
            return ("invalid", url, validation.get("error"))

        shortcode = validation["shortcode"]
        cached = db.check_cache(shortcode) if db.is_connected() else None
        if cached:
            title = cached.get("title") or "Cached"
            print(
                f"⚡ [# {idx}/{total}] Found in cache! (HTTP 200) - \"{title}\"",
                flush=True,
            )
            return ("cached", url, title)

        print(f"\n{'─' * 80}", flush=True)
        print(f"  📹 [# {idx}/{total}] Processing {url}", flush=True)
        print(f"{'─' * 80}\n", flush=True)

        res = run_youtube_analysis(
            url,
            shortcode,
            db,
            use_native_subtitles=use_native_transcripts,
            transcribe_seconds=transcribe_seconds,
            cookies=cookies,
        )
        if res == RETRY_SENTINEL:
            return ("queued", url, "Queued for provider retry")
        if res:
            return ("success", url, None)
        return ("failed", url, "Analysis failed")


class PlaylistLockUnavailable(RuntimeError):
    """Raised when another process holds the same normalized playlist lock."""


_HELD_LOCK_FILES = {}
_HELD_LOCK_FILES_GUARD = threading.Lock()


def _playlist_lock_identity(playlist_url: str) -> str:
    """Normalize equivalent playlist URLs to the same lock identity."""
    parsed = urlsplit(playlist_url.strip())
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if query.get("list"):
        return f"youtube-playlist:{query['list']}"
    normalized = urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True))),
            "",
        )
    )
    return normalized

def acquire_playlist_lock(playlist_url: str):
    """
    Acquire a process lock for a specific playlist URL.
    Allows concurrent imports of DIFFERENT playlists while preventing duplicate imports of the SAME playlist.
    """
    import hashlib
    import tempfile
    import fcntl

    identity = _playlist_lock_identity(playlist_url)
    url_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    lock_path = os.path.join(tempfile.gettempdir(), f"superbrain_playlist_{url_hash}.lock")
    lock_file = None
    try:
        lock_file = open(lock_path, "a+")
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"{os.getpid()}\n")
        lock_file.flush()
        with _HELD_LOCK_FILES_GUARD:
            _HELD_LOCK_FILES[lock_path] = lock_file
        return lock_file
    except (IOError, OSError) as exc:
        if lock_file is not None:
            lock_file.close()
        raise PlaylistLockUnavailable(
            f"Another SuperBrain process is already importing {playlist_url}"
        ) from exc


def release_playlist_lock(lock_file):
    """Release a playlist lock and its persistent registry reference."""
    if lock_file is None:
        return
    import fcntl

    lock_path = lock_file.name
    try:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
    finally:
        lock_file.close()
        with _HELD_LOCK_FILES_GUARD:
            _HELD_LOCK_FILES.pop(lock_path, None)


@contextmanager
def playlist_lock(playlist_url: str):
    lock_file = acquire_playlist_lock(playlist_url)
    try:
        yield lock_file
    finally:
        release_playlist_lock(lock_file)


def run_playlist_import(
    playlist_url: str,
    cookies: Optional[str] = None,
    start_index: int = 1,
    use_native_transcripts: bool = False,
    transcribe_seconds: int = 0,
    workers: int = 1,
) -> Dict[str, int]:
    """Run an import while holding a per-playlist process lock."""
    try:
        with playlist_lock(playlist_url):
            return _run_playlist_import_locked(
                playlist_url=playlist_url,
                cookies=cookies,
                start_index=start_index,
                use_native_transcripts=use_native_transcripts,
                transcribe_seconds=transcribe_seconds,
                workers=workers,
            )
    except PlaylistLockUnavailable as exc:
        print("\n" + "=" * 80, flush=True)
        print("  ⚠️  DUPLICATE PLAYLIST IMPORT PREVENTED", flush=True)
        print("=" * 80, flush=True)
        print(f"  {exc}", flush=True)
        print("=" * 80 + "\n", flush=True)
        raise


def _run_playlist_import_locked(
    playlist_url: str,
    cookies: Optional[str] = None,
    start_index: int = 1,
    use_native_transcripts: bool = False,
    transcribe_seconds: int = 0,
    workers: int = 1
) -> Dict[str, int]:
    """
    Import and process all videos from a YouTube playlist or Watch Later list.
    """
    # Ensure real-time line buffering for playlist import output
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)

    # Ensure yt-dlp is installed before starting
    ensure_ytdlp_or_exit()

    if start_index < 1:
        raise ValueError("start_index must be at least 1")
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if transcribe_seconds < 0:
        raise ValueError("transcribe_seconds cannot be negative")

    print("\n" + "=" * 80, flush=True)
    print("  📋 SUPERBRAIN - PLAYLIST IMPORTER", flush=True)
    print("=" * 80 + "\n", flush=True)
    print(f"🔗 Playlist URL: {playlist_url}", flush=True)
    if cookies:
        print(f"🍪 Cookies: {cookies}", flush=True)
    if start_index > 1:
        print(f"⏩ Starting at index: #{start_index}", flush=True)

    mode_label = "Sequential (one at a time)" if workers <= 1 else f"Parallel ({workers} workers at once)"
    print(f"⚡ Execution Mode: {mode_label}", flush=True)

    print("\n🔍 Fetching playlist video list with yt-dlp...", flush=True)
    try:
        urls = extract_playlist_urls(playlist_url, cookies=cookies)
    except Exception as e:
        print(f"❌ Failed to fetch playlist: {e}", flush=True)
        sys.exit(1)

    if not urls:
        print("⚠️  No videos found in playlist.", flush=True)
        print("   (If this is Watch Later or a private playlist, pass '--cookies chrome')", flush=True)
        sys.exit(1)

    print(f"✓ Found {len(urls)} videos in playlist\n", flush=True)

    db = get_db()
    db_path = db.db_path if hasattr(db, "db_path") else os.path.join(os.path.dirname(__file__), "..", "superbrain.db")

    items_to_process = [(idx, url) for idx, url in enumerate(urls, start=1) if idx >= start_index]

    stats = {
        "total": len(urls),
        "selected": len(items_to_process),
        "processed": 0,
        "cached": 0,
        "queued": 0,
        "failed": 0,
    }

    if not items_to_process:
        print(
            f"⚠️  Start index #{start_index} is beyond the {len(urls)} playlist items.",
            flush=True,
        )
        return stats

    item_status = {}
    if workers <= 1:
        # Sequential processing (one at a time)
        for idx, url in items_to_process:
            status, _, _ = _process_single_item(
                idx,
                len(urls),
                url,
                db_path,
                use_native_transcripts,
                transcribe_seconds,
                cookies,
            )
            item_status[url] = status
            if status in ("cached", "success"):
                stats["processed"] += 1
                if status == "cached":
                    stats["cached"] += 1
            elif status == "queued":
                stats["queued"] += 1
            else:
                stats["failed"] += 1
    else:
        # Parallel processing (all at once / concurrent workers)
        from concurrent.futures import ThreadPoolExecutor, as_completed
        max_w = min(workers, len(items_to_process))
        print(f"🚀 Launching {max_w} concurrent workers...", flush=True)
        with ThreadPoolExecutor(max_workers=max_w) as executor:
            futures = {
                executor.submit(
                    _process_single_item,
                    idx,
                    len(urls),
                    url,
                    db_path,
                    use_native_transcripts,
                    transcribe_seconds,
                    cookies,
                ): (idx, url)
                for idx, url in items_to_process
            }
            for future in as_completed(futures):
                try:
                    status, _, _ = future.result()
                    item_status[futures[future][1]] = status
                    if status in ("cached", "success"):
                        stats["processed"] += 1
                        if status == "cached":
                            stats["cached"] += 1
                    elif status == "queued":
                        stats["queued"] += 1
                    else:
                        stats["failed"] += 1
                except Exception as e:
                    print(f"❌ Worker error: {e}", flush=True)
                    item_status[futures[future][1]] = "failed"
                    stats["failed"] += 1

    print("\n" + "=" * 80, flush=True)
    print(
        f"✅ Playlist Processing Complete! Total: {stats['total']} | "
        f"Selected: {stats['selected']} | Processed: {stats['processed']} "
        f"(Cached: {stats['cached']}) | Queued: {stats['queued']} | "
        f"Failed: {stats['failed']}",
        flush=True
    )

    print("=" * 80 + "\n", flush=True)

    # ── Post-Import Database Verification & Auto-Retry Loop ────────────────────
    print("🔍 Running Post-Import Database Verification & Auto-Retry...", flush=True)

    target_count = len(items_to_process)

    # Auto-retry loop: up to 3 focused retry passes for missing items
    for retry_pass in range(1, 4):
        missing_items = []
        for idx, url in items_to_process:
            v = validate_link(url)
            if v["valid"]:
                sc = v["shortcode"]
                if not db.check_cache(sc):
                    # Provider-rate-limited items already have a durable retry time.
                    if item_status.get(url) != "queued":
                        missing_items.append((idx, url))

        if not missing_items:
            break

        print(f"\n🔄 [AUTO-RETRY Pass #{retry_pass}] Retrying {len(missing_items)} missing items to ensure 100% database persistence...", flush=True)
        from main import run_youtube_analysis
        for m_idx, m_url in missing_items:
            v = validate_link(m_url)
            if v["valid"]:
                sc = v["shortcode"]
                res = run_youtube_analysis(
                    m_url, sc, db,
                    use_native_subtitles=use_native_transcripts,
                    transcribe_seconds=transcribe_seconds,
                    cookies=cookies,
                )
                if res == RETRY_SENTINEL:
                    item_status[m_url] = "queued"
                elif res:
                    item_status[m_url] = "success"

    # Final Verification Assessment
    final_missing = []
    for idx, url in items_to_process:
        v = validate_link(url)
        if v["valid"]:
            sc = v["shortcode"]
            if not db.check_cache(sc):
                final_missing.append((idx, url))

    verified_count = target_count - len(final_missing)
    stats["processed"] = verified_count
    stats["queued"] = sum(
        1 for _, url in final_missing if item_status.get(url) == "queued"
    )
    stats["failed"] = len(final_missing) - stats["queued"]

    print("\n" + "=" * 80, flush=True)
    if not final_missing:
        print(f"🎉 VERIFICATION SUCCESS: 100% of playlist items ({target_count}/{target_count}) are verified in SuperBrain Database!", flush=True)
    else:
        print(f"⚠️  VERIFICATION SUMMARY: {verified_count}/{target_count} items verified in Database ({len(final_missing)} unverified/private):", flush=True)
        for m_idx, m_url in final_missing[:10]:
            print(f"   ❌ Missing [# {m_idx}]: {m_url}", flush=True)
        if len(final_missing) > 10:
            print(f"   ... and {len(final_missing) - 10} more unverified items.", flush=True)
    print("=" * 80 + "\n", flush=True)

    # Assess after the retry/verification pass so the denominator reflects the
    # final persisted state. A failed native+Whisper attempt counts as non-native.
    if use_native_transcripts:
        _print_native_transcript_ratio(db, items_to_process)

    return stats
