# Backend Codemap

**Last Updated:** 2026-07-28
**Location:** `backend/`

## Architecture

```
┌────────────────────────────────────────────────────────────────────────────┐
│                        FASTAPI BACKEND                                     │
├────────────────────────────────────────────────────────────────────────────┤
│  api.py  — REST endpoints, auth, sync, import/export, WebSub               │
│  main.py — Orchestrates Instagram / YouTube / webpage / playlist analysis  │
│                                                                            │
│  core/database.py      SQLite (WAL) persistence                            │
│  core/model_router.py  Multi-provider text/vision routing                  │
│  core/taxonomy.py      User-owned category taxonomy from categories.toml   │
│  core/classifier.py    Structured classification + local validation        │
│  core/category_manager.py  DEPRECATED — MongoDB-era; exits with guidance   │
│                                                                            │
│  analyzers/*           YouTube, webpage, visual, audio, music, text        │
│  scripts/deploy-local.sh   Allow-listed rsync → ~/.superbrain-server       │
│  scripts/recategorize.py   Backup / dry-run / apply / rollback migration   │
└────────────────────────────────────────────────────────────────────────────┘
```

## Key Modules

| Module | Purpose | Location |
|--------|---------|----------|
| `api.py` | FastAPI REST endpoints | Root |
| `main.py` | Content analysis orchestrator | Root |
| `database.py` | SQLite operations + additive migrations | `core/` |
| `taxonomy.py` | Load/validate `config/categories.toml` | `core/` |
| `classifier.py` | Taxonomy-constrained classification | `core/` |
| `model_router.py` | AI model routing | `core/` |
| `link_checker.py` | URL validation & type detection | `core/` |
| `websub_notifier.py` | YouTube WebSub hub handling | `core/` |
| `category_manager.py` | **Deprecated** — do not use | `core/` |
| `recategorize.py` | Metadata-only category migration CLI | `scripts/` |
| `deploy-local.sh` | Deploy reviewed code to runtime dir | `scripts/` |

## Category taxonomy

- Configuration lives in `config/categories.toml` (gitignored). Example:
  `config/categories.toml.example`.
- `analyses.category` remains the compatibility-facing primary category string
  for API/mobile clients.
- Additive metadata columns: `category_source`, `category_confidence`,
  `category_rationale`, `category_suggestions_json`,
  `category_taxonomy_version`, `categorized_at`.
- New analyses call `assign_category()` after parsing title/summary/tags.
- Live recategorization uses `scripts/recategorize.py` (never
  `category_manager.py`, never media redownload).

## Local deployment (this fork)

```bash
backend/scripts/deploy-local.sh [--restart]
```

Copies allow-listed application files into
`${SUPERBRAIN_RUNTIME_DIR:-$HOME/.superbrain-server}`. Does **not** copy the
database, tokens, `.env`, `config/.api_keys`, or `config/categories.toml`.

## API notes (category-related)

```
GET  /categories              Assigned category counts (soft-deleted excluded)
GET  /category/{category}     Posts for one category name
PUT  /post/{shortcode}        Manual category/title/summary update
```

## Related

- [Database Codemap](DATABASE.md)
- [Category taxonomy proposal](../CATEGORY_TAXONOMY_PROPOSAL.md)
