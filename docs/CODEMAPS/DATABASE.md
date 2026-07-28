# Database Codemap

**Last Updated:** 2026-07-28
**Database:** SQLite (file-based, self-hosted)

## Database File

- **Source checkout default:** `backend/superbrain.db`
- **Local runtime (this fork):** `~/.superbrain-server/superbrain.db`
- **Override:** `DATABASE_PATH` env var
- **Type:** SQLite with WAL mode for concurrent reads
- **Features:** Foreign keys enabled, additive column migrations via
  `_add_column_if_missing`

> Older documentation that mentioned MongoDB is obsolete. The active code path
> is SQLite only (`core/database.py`).

## Tables

### 1. Analyses Table

Stores analyzed content from Instagram, YouTube, and webpages.

```sql
CREATE TABLE analyses (
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
    tags                TEXT,          -- JSON array
    music               TEXT,
    category            TEXT,          -- primary assigned category (API/mobile)
    visual_analysis     TEXT,
    audio_transcription TEXT,
    text_analysis       TEXT,
    is_hidden           INTEGER DEFAULT 0,  -- soft delete
    transcript_mode     TEXT DEFAULT '',
    -- Additive taxonomy metadata (optional; clients may ignore)
    category_source     TEXT,
    category_confidence REAL,
    category_rationale  TEXT,
    category_suggestions_json TEXT,
    category_taxonomy_version TEXT,
    categorized_at      TEXT
);
```

**Indexes:**
- `idx_analyses_category` - Category lookups
- `idx_analyses_analyzed_at` - Recent posts sorting
- `idx_analyses_content_type` - Content type filtering

### 2. Processing Queue Table

Manages pending and processing analyses (`queued`, `processing`, `retry`).

### 3. Collections Table

User-created collections for organizing saved posts (`post_ids` JSON array).

### 4. WebSub + deleted_log

YouTube channel subscription state and soft-delete sync tombstones.

## Category operations

```python
db.save_analysis(..., category=..., category_source=..., category_confidence=...)
db.update_category_metadata(shortcode, category=..., ...)
db.update_post(shortcode, {"category": "Sysadmin"})  # marks source=manual
db.get_by_category(category, limit)
db.get_stats()["categories"]  # counts for visible posts only
db.list_visible_for_recategorize()  # migration helper
```

Taxonomy migration must update category metadata only. Do not rebuild the
database or redownload media for a taxonomy-only change. Use
`scripts/recategorize.py`.

## Related

- [Backend Codemap](BACKEND.md)
- [Category taxonomy proposal](../CATEGORY_TAXONOMY_PROPOSAL.md)
