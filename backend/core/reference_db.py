"""
Read-only reference database lookups.

Used by the test environment (and optional operators) to reuse transcripts,
titles, summaries, and other analysis fields from another SuperBrain SQLite
database — typically production — so playlist/recategorize work does not have
to re-download or re-transcribe content.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from core.database import Database


REFERENCE_ENV = "SUPERBRAIN_REFERENCE_DATABASE_PATH"

# Fields useful for classification / display without re-analysis.
REFERENCE_FIELDS = (
    "shortcode",
    "url",
    "username",
    "content_type",
    "title",
    "summary",
    "tags",
    "music",
    "category",
    "visual_analysis",
    "audio_transcription",
    "text_analysis",
    "transcript_mode",
    "thumbnail",
    "post_date",
    "likes",
    "analyzed_at",
    "updated_at",
)


class ReferenceDatabase:
    """Immutable SQLite view of another SuperBrain analyses table.

    Uses one read-only connection per calling thread so playlist workers can
    look up rows concurrently without sharing a single sqlite3 connection.
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        if not self.db_path.is_file():
            raise FileNotFoundError(f"Reference database not found: {self.db_path}")
        self._local = threading.local()
        self._connection_lock = threading.Lock()
        self._connections: set[sqlite3.Connection] = set()
        # Eagerly open a connection on the constructing thread so open failures
        # surface immediately rather than inside a worker.
        self._connect()

    def _connect(self) -> sqlite3.Connection:
        # Read-only URI keeps us from accidentally writing the reference DB.
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(
            uri,
            uri=True,
            timeout=float(os.getenv("DATABASE_TIMEOUT", "30")),
            # Connections are isolated per thread. Disabling SQLite's ownership
            # check only lets close() release worker connections after a
            # ThreadPoolExecutor has shut down.
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(
            f"PRAGMA busy_timeout={int(float(os.getenv('DATABASE_TIMEOUT', '30')) * 1000)}"
        )
        self._local.conn = conn
        with self._connection_lock:
            self._connections.add(conn)
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
        return conn

    def close_thread_connection(self) -> None:
        """Close only the calling thread's connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return
        try:
            conn.close()
        finally:
            with self._connection_lock:
                self._connections.discard(conn)
            self._local.conn = None

    def close(self) -> None:
        """Close every connection created by this ReferenceDatabase instance."""
        with self._connection_lock:
            connections = list(self._connections)
            self._connections.clear()
        for conn in connections:
            try:
                conn.close()
            except Exception:
                pass
        self._local.conn = None

    def get_by_shortcode(self, shortcode: str) -> Optional[dict]:
        cols = ", ".join(REFERENCE_FIELDS)
        cur = self._conn.execute(
            f"SELECT {cols} FROM analyses WHERE shortcode = ? LIMIT 1",
            (shortcode,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        tags = d.get("tags")
        if tags:
            try:
                d["tags"] = json.loads(tags)
            except Exception:
                d["tags"] = []
        else:
            d["tags"] = []
        return d

    def has_shortcode(self, shortcode: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM analyses WHERE shortcode = ? LIMIT 1",
            (shortcode,),
        )
        return cur.fetchone() is not None


def open_reference_database(path: Optional[Path | str] = None) -> Optional[ReferenceDatabase]:
    """Open reference DB from explicit path or SUPERBRAIN_REFERENCE_DATABASE_PATH."""
    raw = path or os.environ.get(REFERENCE_ENV) or ""
    if not raw:
        return None
    return ReferenceDatabase(raw)


def resolve_analysis_row(
    shortcode: str,
    *,
    primary: Database,
    reference: Optional[ReferenceDatabase] = None,
) -> tuple[Optional[dict], str]:
    """
    Prefer the primary working DB, then the reference DB.

    Returns (row, source) where source is 'primary', 'reference', or 'missing'.
    """
    row = primary.get_by_shortcode(shortcode)
    if row:
        return row, "primary"
    if reference is not None:
        # Same-file primary/reference: primary already missed; skip a second
        # connection to the identical DB.
        try:
            same_file = reference.db_path.resolve() == Path(primary.db_path).resolve()
        except Exception:
            same_file = False
        if not same_file:
            row = reference.get_by_shortcode(shortcode)
            if row:
                return row, "reference"
    return None, "missing"


def copy_reference_row_to_primary(
    row: dict,
    primary: Database,
    *,
    preserve_category: bool = True,
) -> bool:
    """
    Upsert a reference analysis into the primary DB (metadata only).

    When preserve_category is True and primary already has a category, keep it.
    """
    existing = primary.get_by_shortcode(row["shortcode"])
    category = row.get("category") or "Other"
    if preserve_category and existing and existing.get("category"):
        category = existing["category"]

    return primary.save_analysis(
        shortcode=row["shortcode"],
        url=row.get("url") or "",
        username=row.get("username") or "",
        title=row.get("title") or "",
        summary=row.get("summary") or "",
        tags=row.get("tags") or [],
        music=row.get("music") or "",
        category=category,
        visual_analysis=row.get("visual_analysis") or "",
        audio_transcription=row.get("audio_transcription") or "",
        text_analysis=row.get("text_analysis") or "",
        likes=row.get("likes") or 0,
        post_date=row.get("post_date"),
        content_type=row.get("content_type") or "youtube",
        thumbnail=row.get("thumbnail") or "",
        transcript_mode=row.get("transcript_mode") or "",
    )
