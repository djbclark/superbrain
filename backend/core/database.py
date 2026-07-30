#!/usr/bin/env python3
"""
SQLite Database Manager for SuperBrain
Handles caching and retrieval of Instagram analysis results
Self-hosted, zero-config, file-based database
"""

import sqlite3
import json
import os
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Database file path can be overridden for Docker deployments
DB_PATH = Path(os.getenv("DATABASE_PATH", str(Path(__file__).resolve().parent.parent / 'superbrain.db')))


def _utcnow():
    """Return naive UTC for compatibility with existing timestamp strings."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Database:
    """SQLite database manager with one WAL connection per calling thread."""

    def __init__(self, db_path=None, initialize_schema=True):
        self.db_path = Path(db_path) if db_path is not None else DB_PATH
        self._local = threading.local()
        self._schema_lock = threading.RLock()
        self._connection_lock = threading.Lock()
        self._connections = set()
        self._connect()
        if initialize_schema:
            with self._schema_lock:
                self._create_tables()
        print(f"[OK] Connected to SQLite database: {self.db_path}")

    def _connect(self):
        """Create and register the current thread's SQLite connection."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=float(os.getenv("DATABASE_TIMEOUT", "30")),
            # Connections are still isolated per thread. Disabling SQLite's
            # ownership check only lets close() release worker connections
            # after a ThreadPoolExecutor has shut down.
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={int(float(os.getenv('DATABASE_TIMEOUT', '30')) * 1000)}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        self._local.conn = conn
        with self._connection_lock:
            self._connections.add(conn)
        return conn

    @property
    def _conn(self):
        """Compatibility accessor returning the connection for this thread."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
        return conn

    def close_thread_connection(self):
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

    def close(self):
        """Close every connection created by this Database instance."""
        with self._connection_lock:
            connections = list(self._connections)
            self._connections.clear()
        for conn in connections:
            try:
                conn.close()
            except Exception:
                pass
        self._local.conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close_thread_connection()

    def _has_column(self, table, column):
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row["name"] == column for row in rows)

    def _add_column_if_missing(self, table, column, declaration):
        if self._has_column(table, column):
            return
        try:
            self._conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
            )
            self._conn.commit()
        except sqlite3.OperationalError:
            # Another Uvicorn process may have completed the same additive
            # migration after our initial PRAGMA check. Suppress only that
            # confirmed race; every other migration error must fail startup.
            self._conn.rollback()
            if not self._has_column(table, column):
                raise

    def _create_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS analyses (
                shortcode           TEXT PRIMARY KEY,
                url                 TEXT,
                username            TEXT,
                content_type        TEXT DEFAULT 'instagram',
                analyzed_at         TEXT,
                updated_at          TEXT,
                post_date           TEXT,
                likes               INTEGER DEFAULT 0,
                thumbnail           TEXT DEFAULT '',
                title               TEXT,
                summary             TEXT,
                tags                TEXT,
                music               TEXT,
                category            TEXT,
                visual_analysis     TEXT,
                audio_transcription TEXT,
                transcript_mode     TEXT DEFAULT '',
                text_analysis       TEXT
            );

            CREATE TABLE IF NOT EXISTS processing_queue (
                shortcode   TEXT PRIMARY KEY,
                url         TEXT,
                status      TEXT DEFAULT 'queued',
                position    INTEGER,
                added_at    TEXT,
                started_at  TEXT,
                updated_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS websub_subscriptions (
                channel_id    TEXT PRIMARY KEY,
                channel_title TEXT DEFAULT '',
                callback_url  TEXT,
                topic_url     TEXT,
                subscribed_at TEXT,
                lease_seconds INTEGER DEFAULT 864000,
                lease_expires_at TEXT,
                verified_at   TEXT,
                pending_mode  TEXT DEFAULT 'subscribe',
                last_error    TEXT DEFAULT '',
                status        TEXT DEFAULT 'pending'
            );

            CREATE INDEX IF NOT EXISTS idx_analyses_category    ON analyses (category);
            CREATE INDEX IF NOT EXISTS idx_analyses_analyzed_at ON analyses (analyzed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_queue_status         ON processing_queue (status);
            CREATE INDEX IF NOT EXISTS idx_queue_position       ON processing_queue (position);
        """)
        self._conn.commit()

        # Additive migrations for databases created by earlier releases.
        self._add_column_if_missing(
            "analyses", "content_type", "TEXT DEFAULT 'instagram'"
        )
        self._add_column_if_missing(
            "analyses", "thumbnail", "TEXT DEFAULT ''"
        )
        self._add_column_if_missing(
            "analyses", "transcript_mode", "TEXT DEFAULT ''"
        )

        # Migration: add retry columns to processing_queue
        for _col, _dflt in [
            ("retry_after",  "TEXT"),
            ("attempts",     "INTEGER DEFAULT 0"),
            ("reason",       "TEXT"),
            ("content_type", "TEXT"),
        ]:
            self._add_column_if_missing("processing_queue", _col, _dflt)

        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_queue_retry "
            "ON processing_queue (status, retry_after)"
        )
        self._conn.commit()

        # Create content_type index only after the column is guaranteed to exist
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_analyses_content_type "
            "ON analyses (content_type)"
        )
        self._conn.commit()

        self._add_column_if_missing(
            "analyses", "is_hidden", "INTEGER DEFAULT 0"
        )

        # Category taxonomy metadata (additive; analyses.category remains canonical)
        for _col, _decl in [
            ("category_source", "TEXT"),
            ("category_confidence", "REAL"),
            ("category_rationale", "TEXT"),
            ("category_suggestions_json", "TEXT"),
            ("category_taxonomy_version", "TEXT"),
            ("categorized_at", "TEXT"),
        ]:
            self._add_column_if_missing("analyses", _col, _decl)

        for _col, _dflt in [
            ("topic_url", "TEXT"),
            ("lease_expires_at", "TEXT"),
            ("verified_at", "TEXT"),
            ("pending_mode", "TEXT DEFAULT 'subscribe'"),
            ("last_error", "TEXT DEFAULT ''"),
        ]:
            self._add_column_if_missing("websub_subscriptions", _col, _dflt)

        # Collections table
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS collections (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                icon        TEXT DEFAULT '📁',
                post_ids    TEXT DEFAULT '[]',
                created_at  TEXT,
                updated_at  TEXT
            );
        """)
        self._conn.commit()
        # Seed default Watch Later if missing
        cur = self._conn.cursor()
        cur.execute("SELECT id FROM collections WHERE id = 'default_watch_later'")
        if cur.fetchone() is None:
            now = _utcnow().isoformat()
            self._conn.execute(
                "INSERT INTO collections (id, name, icon, post_ids, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ('default_watch_later', 'Watch Later', 'time', '[]', now, now)
            )
            self._conn.commit()

        # Migration: normalize Watch Later icon to Ionicons-safe name
        try:
            self._conn.execute(
                "UPDATE collections SET icon = 'time' WHERE id = 'default_watch_later' AND (icon IS NULL OR icon = '' OR icon = '⏰' OR icon = 'clock')"
            )
            self._conn.commit()
        except sqlite3.OperationalError:
            pass

        # Deleted-log table — tracks when posts are soft-deleted so the
        # mobile app can sync deletions via /sync/deleted.
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS deleted_log (
                shortcode   TEXT PRIMARY KEY,
                deleted_at  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_deleted_log_at ON deleted_log (deleted_at);
        """)
        self._conn.commit()

        # YouTube playlists mirrored from configured taxonomy categories
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS category_youtube_playlists (
                category_name TEXT PRIMARY KEY,
                playlist_id   TEXT NOT NULL UNIQUE,
                title         TEXT NOT NULL,
                created_at    TEXT,
                updated_at    TEXT
            );
            CREATE TABLE IF NOT EXISTS category_youtube_playlist_items (
                video_id          TEXT PRIMARY KEY,
                shortcode         TEXT,
                category_name     TEXT NOT NULL,
                playlist_id       TEXT NOT NULL,
                playlist_item_id  TEXT,
                updated_at        TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_cat_yt_items_category
                ON category_youtube_playlist_items (category_name);
            CREATE INDEX IF NOT EXISTS idx_cat_yt_items_shortcode
                ON category_youtube_playlist_items (shortcode);
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # Columns safe to send to the mobile app (excludes heavy analysis blobs)
    LIGHT_COLUMNS = (
        "shortcode, url, username, content_type, analyzed_at, updated_at, "
        "post_date, likes, thumbnail, title, summary, tags, music, category, is_hidden"
    )

    def _row_to_dict(self, row):
        if row is None:
            return None
        d = dict(row)
        if d.get('tags'):
            try:
                d['tags'] = json.loads(d['tags'])
            except Exception:
                d['tags'] = []
        else:
            d['tags'] = []
        return d

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def is_connected(self):
        try:
            self._conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Cache / Analyses
    # ------------------------------------------------------------------

    def check_cache(self, shortcode):
        """Return cached analysis dict or None."""
        if not self.is_connected():
            return None
        try:
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM analyses WHERE shortcode = ?", (shortcode,))
            return self._row_to_dict(cur.fetchone())
        except Exception as e:
            print(f"[WARNING]  Cache lookup error: {e}")
            return None

    def get_by_shortcode(self, shortcode):
        """Return cached analysis dict or None by shortcode."""
        return self.check_cache(shortcode)

    def save_analysis(self, shortcode, url, username, title, summary, tags, music, category,
                      visual_analysis="", audio_transcription="", text_analysis="",
                      likes=0, post_date=None, content_type="instagram", thumbnail="",
                      transcript_mode="", category_source=None, category_confidence=None,
                      category_rationale=None, category_suggestions_json=None,
                      category_taxonomy_version=None, categorized_at=None):
        """Insert or update an analysis record. Returns True on success."""
        if not self.is_connected():
            print("[WARNING] Database not connected. Analysis not saved.")
            return False
        try:
            print(f"📝 Saving to database with shortcode: {shortcode}")
            now = _utcnow().isoformat()
            tags_json = json.dumps(tags if isinstance(tags, list) else tags.split())
            cat_at = categorized_at or (now if category_source else None)
            suggestions_json = category_suggestions_json
            if suggestions_json is not None and not isinstance(suggestions_json, str):
                suggestions_json = json.dumps(suggestions_json, ensure_ascii=False)

            self._conn.execute("""
                INSERT INTO analyses
                    (shortcode, url, username, content_type, analyzed_at, updated_at, post_date, likes,
                     thumbnail, title, summary, tags, music, category,
                     visual_analysis, audio_transcription, transcript_mode, text_analysis,
                     category_source, category_confidence, category_rationale,
                     category_suggestions_json, category_taxonomy_version, categorized_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(shortcode) DO UPDATE SET
                    url                 = excluded.url,
                    username            = excluded.username,
                    content_type        = excluded.content_type,
                    updated_at          = excluded.updated_at,
                    post_date           = excluded.post_date,
                    likes               = excluded.likes,
                    thumbnail           = excluded.thumbnail,
                    title               = excluded.title,
                    summary             = excluded.summary,
                    tags                = excluded.tags,
                    music               = excluded.music,
                    category            = excluded.category,
                    visual_analysis     = excluded.visual_analysis,
                    audio_transcription = excluded.audio_transcription,
                    transcript_mode     = excluded.transcript_mode,
                    text_analysis       = excluded.text_analysis,
                    category_source     = COALESCE(excluded.category_source, analyses.category_source),
                    category_confidence = COALESCE(excluded.category_confidence, analyses.category_confidence),
                    category_rationale  = COALESCE(excluded.category_rationale, analyses.category_rationale),
                    category_suggestions_json = COALESCE(excluded.category_suggestions_json, analyses.category_suggestions_json),
                    category_taxonomy_version = COALESCE(excluded.category_taxonomy_version, analyses.category_taxonomy_version),
                    categorized_at      = COALESCE(excluded.categorized_at, analyses.categorized_at)
            """, (shortcode, url, username, content_type, now, now, post_date, likes,
                  thumbnail, title, summary, tags_json, music, category,
                  visual_analysis, audio_transcription, transcript_mode, text_analysis,
                  category_source, category_confidence, category_rationale,
                  suggestions_json, category_taxonomy_version, cat_at))
            self._conn.commit()
            print(f"[OK] Analysis saved to database ({shortcode})")
            return True
        except Exception as e:
            try:
                self._conn.rollback()
            except Exception:
                pass
            print(f"[WARNING]  Error saving to database: {e}")
            import traceback
            traceback.print_exc()
            return False

    def update_category_metadata(self, shortcode, *, category, category_source=None,
                                 category_confidence=None, category_rationale=None,
                                 category_suggestions_json=None,
                                 category_taxonomy_version=None, categorized_at=None):
        """Update only category + taxonomy metadata for a post. Returns True if updated."""
        if not self.is_connected():
            return False
        try:
            now = _utcnow().isoformat()
            suggestions_json = category_suggestions_json
            if suggestions_json is not None and not isinstance(suggestions_json, str):
                suggestions_json = json.dumps(suggestions_json, ensure_ascii=False)
            cur = self._conn.execute(
                """
                UPDATE analyses SET
                    category = ?,
                    category_source = ?,
                    category_confidence = ?,
                    category_rationale = ?,
                    category_suggestions_json = ?,
                    category_taxonomy_version = ?,
                    categorized_at = ?,
                    updated_at = ?
                WHERE shortcode = ?
                """,
                (
                    category,
                    category_source,
                    category_confidence,
                    category_rationale,
                    suggestions_json,
                    category_taxonomy_version,
                    categorized_at or now,
                    now,
                    shortcode,
                ),
            )
            self._conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            try:
                self._conn.rollback()
            except Exception:
                pass
            print(f"[WARNING]  Error updating category metadata: {e}")
            return False

    def list_visible_for_recategorize(
        self,
        limit=None,
        offset=0,
        *,
        only_categories=None,
        outside_taxonomy_names=None,
    ):
        """Return light rows used by taxonomy migration (excludes soft-deleted).

        Filters (optional, may combine):
          only_categories: exact category string matches (e.g. legacy labels)
          outside_taxonomy_names: keep rows whose category is not in this set
            (comparison is exact; legacy ``other`` is outside ``Other``)
        """
        if not self.is_connected():
            return []
        try:
            cur = self._conn.cursor()
            sql = (
                "SELECT shortcode, title, summary, tags, category, "
                "audio_transcription, text_analysis, visual_analysis "
                "FROM analyses WHERE (is_hidden IS NULL OR is_hidden = 0)"
            )
            params: list = []
            if only_categories:
                cats = [str(c) for c in only_categories if str(c)]
                if not cats:
                    return []
                placeholders = ",".join("?" for _ in cats)
                sql += f" AND category IN ({placeholders})"
                params.extend(cats)
            if outside_taxonomy_names is not None:
                names = [str(n) for n in outside_taxonomy_names]
                if names:
                    placeholders = ",".join("?" for _ in names)
                    sql += (
                        f" AND (category IS NULL OR category NOT IN ({placeholders}))"
                    )
                    params.extend(names)
                else:
                    # Empty taxonomy → every row is "outside"
                    pass
            sql += " ORDER BY shortcode"
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                params.extend([int(limit), int(offset)])
            cur.execute(sql, params)
            return [self._row_to_dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[WARNING]  Error listing analyses for recategorize: {e}")
            return []

    def get_recent(self, limit=10):
        """Return the most recently analysed posts (excludes soft-deleted)."""
        if not self.is_connected():
            return []
        try:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM analyses WHERE (is_hidden IS NULL OR is_hidden = 0) ORDER BY analyzed_at DESC LIMIT ?", (limit,)
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[WARNING]  Error retrieving recent: {e}")
            return []

    def get_recent_light(self, limit=50, offset=0):
        """Return recent posts with only UI-essential fields (no analysis blobs)."""
        if not self.is_connected():
            return []
        try:
            cur = self._conn.cursor()
            cur.execute(
                f"SELECT {self.LIGHT_COLUMNS} FROM analyses "
                "WHERE (is_hidden IS NULL OR is_hidden = 0) "
                "ORDER BY analyzed_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[WARNING]  Error retrieving recent (light): {e}")
            return []

    def get_posts_since(self, updated_after: str, limit=1000, offset=0):
        """Return posts updated after the given ISO timestamp (delta sync).
        Includes soft-deleted posts so the app knows to hide them."""
        if not self.is_connected():
            return []
        try:
            cur = self._conn.cursor()
            cur.execute(
                f"SELECT {self.LIGHT_COLUMNS} FROM analyses "
                "WHERE updated_at > ? "
                "ORDER BY updated_at ASC LIMIT ? OFFSET ?",
                (updated_after, limit, offset)
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[WARNING]  Error getting posts since {updated_after}: {e}")
            return []

    def get_deleted_since(self, since: str):
        """Return shortcodes of posts deleted after the given ISO timestamp."""
        if not self.is_connected():
            return []
        try:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT shortcode, deleted_at FROM deleted_log WHERE deleted_at > ? ORDER BY deleted_at ASC",
                (since,)
            )
            return [{"shortcode": r["shortcode"], "deleted_at": r["deleted_at"]} for r in cur.fetchall()]
        except Exception as e:
            print(f"[WARNING]  Error getting deleted since {since}: {e}")
            return []

    def get_total_count(self):
        """Return total number of visible (non-hidden) posts."""
        if not self.is_connected():
            return 0
        try:
            cur = self._conn.cursor()
            cur.execute("SELECT COUNT(*) FROM analyses WHERE (is_hidden IS NULL OR is_hidden = 0)")
            return cur.fetchone()[0]
        except Exception as e:
            print(f"[WARNING]  Error getting total count: {e}")
            return 0

    def get_by_category(self, category, limit=20):
        """Return all analyses for a given category (excludes soft-deleted)."""
        if not self.is_connected():
            return []
        try:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM analyses WHERE category = ? AND (is_hidden IS NULL OR is_hidden = 0) ORDER BY analyzed_at DESC LIMIT ?",
                (category, limit)
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[WARNING]  Error retrieving by category: {e}")
            return []

    def search_tags(self, tags, limit=20):
        """
        Search analyses by one or more tags (case-insensitive substring match
        against the JSON-encoded tags column).

        Args:
            tags: str or list[str]
            limit: int
        """
        if not self.is_connected():
            return []
        try:
            if isinstance(tags, str):
                tags = [tags]
            cur = self._conn.cursor()
            conditions = " OR ".join(["LOWER(tags) LIKE ?" for _ in tags])
            params = [f"%{t.lower()}%" for t in tags] + [limit]
            cur.execute(
                f"SELECT * FROM analyses WHERE ({conditions}) AND (is_hidden IS NULL OR is_hidden = 0) ORDER BY analyzed_at DESC LIMIT ?",
                params
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[WARNING]  Error searching tags: {e}")
            return []

    def get_stats(self):
        """Return basic statistics about the database."""
        if not self.is_connected():
            return {
                "document_count": 0,
                "total_posts": 0,
                "total_collections": 0,
                "storage_mb": 0,
                "categories": {},
                "capacity_used": "N/A",
            }
        try:
            cur = self._conn.cursor()

            cur.execute("SELECT COUNT(*) FROM analyses WHERE (is_hidden IS NULL OR is_hidden = 0)")
            total = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM collections")
            total_collections = cur.fetchone()[0]

            cur.execute(
                "SELECT COALESCE(category,'Uncategorized') as cat, COUNT(*) as cnt "
                "FROM analyses WHERE (is_hidden IS NULL OR is_hidden = 0) GROUP BY cat"
            )
            category_counts = {r["cat"]: r["cnt"] for r in cur.fetchall()}

            storage_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
            storage_mb = round(storage_bytes / (1024 * 1024), 2)

            return {
                "document_count": total,
                "total_posts": total,
                "total_collections": total_collections,
                "storage_mb": storage_mb,
                "categories": category_counts,
                "capacity_used": "N/A (local SQLite)"
            }
        except Exception as e:
            print(f"[WARNING]  Error getting stats: {e}")
            return {
                "document_count": 0,
                "total_posts": 0,
                "total_collections": 0,
                "storage_mb": 0,
                "categories": {},
                "capacity_used": "N/A",
            }

    def get_all_posts(self, limit: int = 50000, offset: int = 0) -> list:
        """Return all posts for export (excludes soft-deleted)."""
        if not self.is_connected():
            return []
        try:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM analyses WHERE (is_hidden IS NULL OR is_hidden = 0) ORDER BY analyzed_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[WARNING]  Error getting all posts for export: {e}")
            return []

    def get_all_collections(self) -> list:
        """Return all collections for export."""
        if not self.is_connected():
            return []
        try:
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM collections ORDER BY created_at DESC")
            return [self._row_to_dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[WARNING]  Error getting collections for export: {e}")
            return []

    # ==================== RETRY QUEUE ====================

    def queue_for_retry(self, shortcode: str, url: str, content_type: str,
                        reason: str, retry_hours: float = 24.0) -> bool:
        """
        Schedule an item to be retried after `retry_hours` from now.
        Sets status='retry' and populates retry_after, reason, content_type.
        Returns True on success.
        """
        if not self.is_connected():
            return False
        try:
            now      = _utcnow()
            retry_at = (now + timedelta(hours=retry_hours)).isoformat()
            now_str  = now.isoformat()

            # Get current attempts count
            cur = self._conn.cursor()
            cur.execute(
                "SELECT attempts FROM processing_queue WHERE shortcode = ?", (shortcode,)
            )
            row = cur.fetchone()
            attempts = (row["attempts"] or 0) + 1 if row else 1

            self._conn.execute("""
                INSERT INTO processing_queue
                    (shortcode, url, content_type, status, position,
                     added_at, updated_at, retry_after, attempts, reason)
                VALUES (?, ?, ?, 'retry', 0, ?, ?, ?, ?, ?)
                ON CONFLICT(shortcode) DO UPDATE SET
                    url          = excluded.url,
                    content_type = excluded.content_type,
                    status       = 'retry',
                    updated_at   = excluded.updated_at,
                    retry_after  = excluded.retry_after,
                    attempts     = excluded.attempts,
                    reason       = excluded.reason
            """, (shortcode, url, content_type, now_str, now_str,
                  retry_at, attempts, reason))
            self._conn.commit()
            print(f"⏰ Queued for retry in {retry_hours:.0f}h: {shortcode} ({reason})")
            return True
        except Exception as e:
            print(f"[WARNING]  Error queuing for retry: {e}")
            return False

    def get_retry_ready(self):
        """Return retry items whose retry_after time has passed."""
        if not self.is_connected():
            return []
        try:
            now = _utcnow().isoformat()
            cur = self._conn.cursor()
            cur.execute("""
                SELECT shortcode, url, content_type, reason, attempts, retry_after
                FROM processing_queue
                WHERE status = 'retry' AND retry_after <= ?
                ORDER BY retry_after
            """, (now,))
            return [
                {
                    "shortcode":    r["shortcode"],
                    "url":          r["url"],
                    "content_type": r["content_type"],
                    "reason":       r["reason"],
                    "attempts":     r["attempts"],
                    "retry_after":  r["retry_after"],
                }
                for r in cur.fetchall()
            ]
        except Exception as e:
            print(f"[WARNING]  Error getting retry-ready items: {e}")
            return []

    def get_retry_queue(self):
        """Return all items currently awaiting retry (status='retry')."""
        if not self.is_connected():
            return []
        try:
            cur = self._conn.cursor()
            cur.execute("""
                SELECT shortcode, url, content_type, reason, attempts,
                       retry_after, added_at
                FROM processing_queue
                WHERE status = 'retry'
                ORDER BY retry_after
            """)
            return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[WARNING]  Error getting retry queue: {e}")
            return []

    # ==================== QUEUE MANAGEMENT ====================

    def add_to_queue(self, shortcode, url):
        """Add item to processing queue. Returns queue position (1-based), or -1 on error."""
        if not self.is_connected():
            return -1
        try:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT status, position FROM processing_queue WHERE shortcode = ?", (shortcode,)
            )
            existing = cur.fetchone()
            if existing:
                if existing["status"] == "queued":
                    return existing["position"]
                if existing["status"] == "processing":
                    return 0

            cur.execute(
                "SELECT MAX(position) FROM processing_queue WHERE status = 'queued'"
            )
            row = cur.fetchone()
            position = (row[0] + 1) if row[0] is not None else 1

            now = _utcnow().isoformat()
            self._conn.execute("""
                INSERT INTO processing_queue (shortcode, url, status, position, added_at, updated_at)
                VALUES (?, ?, 'queued', ?, ?, ?)
                ON CONFLICT(shortcode) DO UPDATE SET
                    url        = excluded.url,
                    status     = 'queued',
                    position   = excluded.position,
                    updated_at = excluded.updated_at
            """, (shortcode, url, position, now, now))
            self._conn.commit()
            return position
        except Exception as e:
            print(f"[WARNING]  Error adding to queue: {e}")
            return -1

    def get_queue(self):
        """Return list of queued items ordered by position."""
        if not self.is_connected():
            return []
        try:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT shortcode, url, position FROM processing_queue "
                "WHERE status = 'queued' ORDER BY position"
            )
            return [
                {"shortcode": r["shortcode"], "url": r["url"], "position": r["position"]}
                for r in cur.fetchall()
            ]
        except Exception as e:
            print(f"[WARNING]  Error getting queue: {e}")
            return []

    def claim_next_queue_item(self, max_concurrent=1):
        """Atomically claim the next queued item across threads/processes."""
        conn = self._conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            processing_count = conn.execute(
                "SELECT COUNT(*) FROM processing_queue WHERE status = 'processing'"
            ).fetchone()[0]
            if processing_count >= max_concurrent:
                conn.commit()
                return None
            row = conn.execute(
                """
                SELECT shortcode, url, position
                FROM processing_queue
                WHERE status = 'queued'
                ORDER BY position
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            now = _utcnow().isoformat()
            updated = conn.execute(
                """
                UPDATE processing_queue
                SET status = 'processing', started_at = ?, updated_at = ?
                WHERE shortcode = ? AND status = 'queued'
                """,
                (now, now, row["shortcode"]),
            )
            conn.commit()
            return dict(row) if updated.rowcount == 1 else None
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"[WARNING]  Error claiming queue item: {e}")
            return None

    def get_processing(self):
        """Return list of shortcodes currently being processed."""
        if not self.is_connected():
            return []
        try:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT shortcode FROM processing_queue WHERE status = 'processing'"
            )
            return [r["shortcode"] for r in cur.fetchall()]
        except Exception as e:
            print(f"[WARNING]  Error getting processing items: {e}")
            return []

    def mark_processing(self, shortcode):
        """Mark a queued item as currently processing."""
        if not self.is_connected():
            return False
        try:
            now = _utcnow().isoformat()
            self._conn.execute("""
                UPDATE processing_queue
                SET status = 'processing', started_at = ?, updated_at = ?
                WHERE shortcode = ?
            """, (now, now, shortcode))
            self._conn.commit()
            return True
        except Exception as e:
            print(f"[WARNING]  Error marking as processing: {e}")
            return False

    def remove_from_queue(self, shortcode):
        """Remove an item from the queue and compact positions."""
        if not self.is_connected():
            return False
        try:
            self._conn.execute(
                "DELETE FROM processing_queue WHERE shortcode = ?", (shortcode,)
            )
            self._conn.commit()

            cur = self._conn.cursor()
            cur.execute(
                "SELECT shortcode FROM processing_queue "
                "WHERE status = 'queued' ORDER BY position"
            )
            for idx, item in enumerate(cur.fetchall(), 1):
                self._conn.execute(
                    "UPDATE processing_queue SET position = ? WHERE shortcode = ?",
                    (idx, item["shortcode"])
                )
            self._conn.commit()
            return True
        except Exception as e:
            print(f"[WARNING]  Error removing from queue: {e}")
            return False

    def recover_interrupted_items(self, stale_after_seconds=900):
        """
        Move stale processing items back to queued after a worker crash.

        A fresh processing row is left alone so another Uvicorn worker or a
        rolling restart cannot duplicate an analysis that is still running.
        Returns the number of items recovered.
        """
        if not self.is_connected():
            return 0
        try:
            now = _utcnow().isoformat()
            cutoff = (
                _utcnow() - timedelta(seconds=max(1, int(stale_after_seconds)))
            ).isoformat()
            cur = self._conn.cursor()
            cur.execute("""
                UPDATE processing_queue
                SET status = 'queued', started_at = NULL, updated_at = ?
                WHERE status = 'processing'
                  AND (started_at IS NULL OR started_at <= ?)
            """, (now, cutoff))
            count = cur.rowcount
            self._conn.commit()

            cur.execute(
                "SELECT shortcode FROM processing_queue "
                "WHERE status = 'queued' ORDER BY added_at"
            )
            for idx, item in enumerate(cur.fetchall(), 1):
                self._conn.execute(
                    "UPDATE processing_queue SET position = ? WHERE shortcode = ?",
                    (idx, item["shortcode"])
                )
            self._conn.commit()

            if count > 0:
                print(f"[RECOVERED] Recovered {count} interrupted items")
            return count
        except Exception as e:
            print(f"[WARNING]  Error recovering items: {e}")
            return 0

    # ------------------------------------------------------------------
    # Post management
    # ------------------------------------------------------------------

    def delete_post(self, shortcode):
        """Soft-delete a post (is_hidden=1). Data kept for re-add reuse. Returns True if updated."""
        if not self.is_connected():
            return False
        try:
            now = _utcnow().isoformat()
            cur = self._conn.execute(
                "UPDATE analyses SET is_hidden = 1, updated_at = ? WHERE shortcode = ?",
                (now, shortcode)
            )
            if cur.rowcount > 0:
                # Record in deleted_log so mobile app can sync deletions
                self._conn.execute(
                    "INSERT OR REPLACE INTO deleted_log (shortcode, deleted_at) VALUES (?, ?)",
                    (shortcode, now)
                )
            self._conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            print(f"[WARNING]  Error soft-deleting post: {e}")
            return False

    def hard_delete_post(self, shortcode):
        """Permanently remove a post row — used for force re-analysis. Returns True if deleted."""
        if not self.is_connected():
            return False
        try:
            cur = self._conn.execute(
                "DELETE FROM analyses WHERE shortcode = ?",
                (shortcode,)
            )
            self._conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            print(f"[WARNING]  Error hard-deleting post: {e}")
            return False

    def restore_post(self, shortcode):
        """Restore a soft-deleted post (is_hidden=0). Returns True if updated."""
        if not self.is_connected():
            return False
        try:
            cur = self._conn.execute(
                "UPDATE analyses SET is_hidden = 0, updated_at = ? WHERE shortcode = ?",
                (_utcnow().isoformat(), shortcode)
            )
            self._conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            print(f"[WARNING]  Error restoring post: {e}")
            return False

    def update_post(self, shortcode, updates):
        """
        Update specific fields of a post.

        Args:
            shortcode: Instagram post shortcode
            updates: dict of allowed fields (category, title, summary, and optional
                     category metadata columns)

        Returns:
            bool: True if updated
        """
        if not self.is_connected():
            return False
        try:
            allowed = {
                "category",
                "title",
                "summary",
                "category_source",
                "category_confidence",
                "category_rationale",
                "category_suggestions_json",
                "category_taxonomy_version",
                "categorized_at",
            }
            filtered = {k: v for k, v in updates.items() if k in allowed}
            if not filtered:
                return False
            if "category" in filtered and "category_source" not in filtered:
                filtered["category_source"] = "manual"
            if "category" in filtered and "categorized_at" not in filtered:
                filtered["categorized_at"] = _utcnow().isoformat()
            filtered["updated_at"] = _utcnow().isoformat()
            set_clause = ", ".join(f"{k} = ?" for k in filtered)
            values = list(filtered.values()) + [shortcode]
            cur = self._conn.execute(
                f"UPDATE analyses SET {set_clause} WHERE shortcode = ?", values
            )
            self._conn.commit()
            if cur.rowcount == 0:
                print(f"[WARNING]  Post not found: {shortcode}")
                return False
            print(f"[OK] Updated post: {shortcode}")
            return True
        except Exception as e:
            print(f"[WARNING]  Error updating post: {e}")
            return False

    # ------------------------------------------------------------------
    # Collections
    # ------------------------------------------------------------------

    def _collection_row_to_dict(self, row):
        if row is None:
            return None
        d = dict(row)
        try:
            d['post_ids'] = json.loads(d.get('post_ids') or '[]')
        except Exception:
            d['post_ids'] = []
        return d

    def get_collections(self):
        """Return all collections ordered by created_at."""
        if not self.is_connected():
            return []
        try:
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM collections ORDER BY created_at ASC")
            return [self._collection_row_to_dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[WARNING]  Error getting collections: {e}")
            return []

    def get_collection(self, collection_id):
        """Return a single collection by id."""
        if not self.is_connected():
            return None
        try:
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM collections WHERE id = ?", (collection_id,))
            return self._collection_row_to_dict(cur.fetchone())
        except Exception as e:
            print(f"[WARNING]  Error getting collection: {e}")
            return None

    def upsert_collection(self, collection_id, name, icon, post_ids, created_at=None, updated_at=None):
        """Insert or fully replace a collection. Returns the saved dict."""
        if not self.is_connected():
            return None
        try:
            now = _utcnow().isoformat()
            self._conn.execute("""
                INSERT INTO collections (id, name, icon, post_ids, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name       = excluded.name,
                    icon       = excluded.icon,
                    post_ids   = excluded.post_ids,
                    updated_at = excluded.updated_at
            """, (
                collection_id, name, icon,
                json.dumps(post_ids if isinstance(post_ids, list) else []),
                created_at or now, updated_at or now
            ))
            self._conn.commit()
            return self.get_collection(collection_id)
        except Exception as e:
            print(f"[WARNING]  Error upserting collection: {e}")
            return None

    def update_collection_posts(self, collection_id, post_ids):
        """Replace the post_ids list for a collection."""
        if not self.is_connected():
            return False
        try:
            now = _utcnow().isoformat()
            cur = self._conn.execute(
                "UPDATE collections SET post_ids = ?, updated_at = ? WHERE id = ?",
                (json.dumps(post_ids), now, collection_id)
            )
            self._conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            print(f"[WARNING]  Error updating collection posts: {e}")
            return False

    def delete_collection(self, collection_id):
        """Delete a collection. Returns True if deleted."""
        if not self.is_connected():
            return False
        try:
            cur = self._conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
            self._conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            print(f"[WARNING]  Error deleting collection: {e}")
            return False

    # ------------------------------------------------------------------
    # YouTube WebSub subscriptions
    # ------------------------------------------------------------------

    def upsert_websub_subscription(
        self,
        channel_id,
        callback_url,
        topic_url,
        channel_title="",
        lease_seconds=864000,
        status="pending",
        pending_mode="subscribe",
        last_error="",
    ):
        """Create or update WebSub state without treating HTTP 202 as verified."""
        now = _utcnow().isoformat()
        self._conn.execute(
            """
            INSERT INTO websub_subscriptions
                (channel_id, channel_title, callback_url, topic_url,
                 subscribed_at, lease_seconds, pending_mode, last_error, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                channel_title = excluded.channel_title,
                callback_url = excluded.callback_url,
                topic_url = excluded.topic_url,
                subscribed_at = excluded.subscribed_at,
                lease_seconds = excluded.lease_seconds,
                pending_mode = excluded.pending_mode,
                last_error = excluded.last_error,
                status = CASE
                    WHEN websub_subscriptions.status = 'active'
                         AND excluded.status = 'pending'
                    THEN 'active'
                    ELSE excluded.status
                END
            """,
            (
                channel_id,
                channel_title,
                callback_url,
                topic_url,
                now,
                int(lease_seconds),
                pending_mode,
                last_error,
                status,
            ),
        )
        self._conn.commit()

    def get_websub_subscription_by_topic(self, topic_url):
        row = self._conn.execute(
            "SELECT * FROM websub_subscriptions WHERE topic_url = ?",
            (topic_url,),
        ).fetchone()
        return dict(row) if row else None

    def get_websub_subscription(self, channel_id):
        row = self._conn.execute(
            "SELECT * FROM websub_subscriptions WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        return dict(row) if row else None

    def mark_websub_verified(self, topic_url, mode, lease_seconds=None):
        """Apply a verified subscribe/unsubscribe intent challenge."""
        if mode == "unsubscribe":
            cur = self._conn.execute(
                """
                UPDATE websub_subscriptions
                SET status = 'inactive', pending_mode = NULL,
                    lease_expires_at = NULL, verified_at = ?
                WHERE topic_url = ? AND pending_mode = 'unsubscribe'
                """,
                (_utcnow().isoformat(), topic_url),
            )
            self._conn.commit()
            return cur.rowcount > 0

        lease = int(lease_seconds or 864000)
        now = _utcnow()
        cur = self._conn.execute(
            """
            UPDATE websub_subscriptions
            SET status = 'active', pending_mode = NULL, last_error = '',
                lease_seconds = ?, verified_at = ?, lease_expires_at = ?
            WHERE topic_url = ? AND pending_mode = 'subscribe'
            """,
            (
                lease,
                now.isoformat(),
                (now + timedelta(seconds=lease)).isoformat(),
                topic_url,
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def mark_websub_failed(self, channel_id, error):
        self._conn.execute(
            """
            UPDATE websub_subscriptions
            SET status = CASE
                    WHEN status = 'active' THEN 'active'
                    ELSE 'failed'
                END,
                pending_mode = CASE
                    WHEN status = 'active' THEN NULL
                    ELSE pending_mode
                END,
                last_error = ?
            WHERE channel_id = ?
            """,
            (str(error)[:500], channel_id),
        )
        self._conn.commit()

    def list_websub_subscriptions(self, status=None):
        if status:
            rows = self._conn.execute(
                """
                SELECT * FROM websub_subscriptions
                WHERE status = ?
                ORDER BY subscribed_at DESC
                """,
                (status,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM websub_subscriptions ORDER BY subscribed_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_websub_renewal_candidates(self, within_hours=48):
        cutoff = (_utcnow() + timedelta(hours=within_hours)).isoformat()
        rows = self._conn.execute(
            """
            SELECT * FROM websub_subscriptions
            WHERE status = 'active'
              AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
            ORDER BY lease_expires_at
            """,
            (cutoff,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Category ↔ YouTube playlist mappings
    # ------------------------------------------------------------------

    def upsert_category_youtube_playlist(self, category_name, playlist_id, title):
        now = _utcnow().isoformat()
        self._conn.execute(
            """
            INSERT INTO category_youtube_playlists
                (category_name, playlist_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(category_name) DO UPDATE SET
                playlist_id = excluded.playlist_id,
                title = excluded.title,
                updated_at = excluded.updated_at
            """,
            (category_name, playlist_id, title, now, now),
        )
        self._conn.commit()

    def get_category_youtube_playlist(self, category_name):
        row = self._conn.execute(
            "SELECT * FROM category_youtube_playlists WHERE category_name = ?",
            (category_name,),
        ).fetchone()
        return dict(row) if row else None

    def list_category_youtube_playlists(self):
        rows = self._conn.execute(
            "SELECT * FROM category_youtube_playlists ORDER BY category_name"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_category_youtube_playlist_item(self, video_id):
        row = self._conn.execute(
            "SELECT * FROM category_youtube_playlist_items WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        return dict(row) if row else None

    def upsert_category_youtube_playlist_item(
        self, *, video_id, shortcode, category_name, playlist_id, playlist_item_id
    ):
        now = _utcnow().isoformat()
        self._conn.execute(
            """
            INSERT INTO category_youtube_playlist_items
                (video_id, shortcode, category_name, playlist_id, playlist_item_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                shortcode = excluded.shortcode,
                category_name = excluded.category_name,
                playlist_id = excluded.playlist_id,
                playlist_item_id = excluded.playlist_item_id,
                updated_at = excluded.updated_at
            """,
            (video_id, shortcode, category_name, playlist_id, playlist_item_id, now),
        )
        self._conn.commit()

    def delete_category_youtube_playlist_item(self, video_id):
        cur = self._conn.execute(
            "DELETE FROM category_youtube_playlist_items WHERE video_id = ?",
            (video_id,),
        )
        self._conn.commit()
        return cur.rowcount > 0


# ------------------------------------------------------------------
# Singleton accessor
# ------------------------------------------------------------------

_db_instance = None
_db_instance_lock = threading.Lock()


def get_db():
    """Get or create the shared Database instance."""
    global _db_instance
    if _db_instance is None:
        with _db_instance_lock:
            if _db_instance is None:
                _db_instance = Database()
    return _db_instance
