# Recategorize cookbook

Metadata-only taxonomy migration. **Does not** redownload media or regenerate
transcripts. **Does not** use `category_manager.py` (MongoDB-era stub).

Canonical CLI: `backend/scripts/recategorize.py`  
Also: `python scripts/recategorize.py --help`

Run from `backend/` (or pass absolute paths). Prefer the workspace checkout’s
script even when the DB lives under `~/.superbrain-server`.

## Shared paths (this deployment)

```bash
export SB_DB="$HOME/.superbrain-server/superbrain.db"
export SB_CFG="$HOME/.superbrain-server/config/categories.toml"
export SB_REPORTS="$HOME/.superbrain-server-test/reports"
export SB_PY="$HOME/.superbrain-server/.venv/bin/python"
export SB_KEYS="$HOME/.superbrain-server-test/config/.api_keys.openrouter-prefer"

# Cheap paid OpenRouter for bulk classify (optional but recommended)
export SUPERBRAIN_API_KEYS_FILE="$SB_KEYS"
export SUPERBRAIN_PROVIDER_ORDER="openrouter,omlx,ollama,groq,gemini"
unset GROQ_API_KEY GEMINI_API_KEY GOOGLE_API_KEY
export DISABLE_OLLAMA=1
export PYTHONUNBUFFERED=1

cd /path/to/superbrain/backend
```

Copy `categories.toml.example` → live `config/categories.toml` before first run
if missing. Validate anytime:

```bash
$SB_PY scripts/recategorize.py --database "$SB_DB" --config "$SB_CFG" validate
```

---

## A. Legacy-label sweep (outside taxonomy)

Use when rows still have old labels (`other`, `product`, `software`, …) after a
playlist pass. Exact name match: `other` is **outside** `Other`.

```bash
# 1) Backup (required before writes)
$SB_PY scripts/recategorize.py --database "$SB_DB" --config "$SB_CFG" backup \
  --output "$SB_REPORTS/superbrain-pre-legacy-sweep.db"

# 2) Dry-run — classify only non-taxonomy labels; no DB writes
$SB_PY scripts/recategorize.py --database "$SB_DB" --config "$SB_CFG" dry-run \
  --out "$SB_REPORTS/legacy-sweep-dryrun.jsonl" \
  --only-outside-taxonomy \
  --progress

# Review: $SB_REPORTS/legacy-sweep-dryrun.jsonl.summary.json
# Optional: limit to named legacy labels instead of all outsiders:
#   --only-categories other,product,software,food,film

# 3) Apply from the reviewed report
$SB_PY scripts/recategorize.py --database "$SB_DB" --config "$SB_CFG" apply \
  --from-report "$SB_REPORTS/legacy-sweep-dryrun.jsonl" \
  --only-changed \
  --progress \
  --i-understand-this-writes-categories

# 4) Advisory out-of-taxonomy suggestions (no writes)
$SB_PY scripts/recategorize.py --database "$SB_DB" --config "$SB_CFG" suggestions \
  --from-report "$SB_REPORTS/legacy-sweep-dryrun.jsonl"
```

Rollback if needed:

```bash
$SB_PY scripts/recategorize.py --database "$SB_DB" rollback \
  --backup "$SB_REPORTS/superbrain-pre-legacy-sweep.db" \
  --i-understand-this-restores-database
```

---

## B. Full visible-corpus dry-run / apply

Same as A without `--only-outside-taxonomy` (classifies every non-hidden row).
More tokens; use when you intentionally want a full re-pass.

```bash
$SB_PY scripts/recategorize.py --database "$SB_DB" --config "$SB_CFG" backup \
  --output "$SB_REPORTS/superbrain-pre-full-recat.db"

$SB_PY scripts/recategorize.py --database "$SB_DB" --config "$SB_CFG" dry-run \
  --out "$SB_REPORTS/full-recat-dryrun.jsonl" --progress

$SB_PY scripts/recategorize.py --database "$SB_DB" --config "$SB_CFG" apply \
  --from-report "$SB_REPORTS/full-recat-dryrun.jsonl" \
  --only-changed --progress \
  --i-understand-this-writes-categories
```

---

## C. Playlist-scoped apply (Watch Later, etc.)

Writes categories as it goes (no separate dry-run report apply). Uses optional
reference DB for transcripts; capped yt-dlp metadata for missing rows.

```bash
$SB_PY scripts/recategorize.py --database "$SB_DB" --config "$SB_CFG" playlists \
  --playlist 'https://www.youtube.com/playlist?list=WL' \
  --playlist 'https://www.youtube.com/playlist?list=PLd7Q46DE6mVwjAKCCxPaCTkK_CffR2O1R' \
  --cookies chrome \
  --reference-database "$SB_DB" \
  --missing-ai-timeout 20 \
  --out "$SB_REPORTS/playlist-recat.jsonl" \
  --url-cache "$SB_REPORTS/playlist-recat.urls.txt" \
  --workers 4 \
  --resume \
  --progress \
  --i-understand-this-writes-categories
```

`--resume` skips shortcodes already present in `--out`.

---

## Flags worth knowing

| Flag | Command | Meaning |
|------|---------|---------|
| `--only-outside-taxonomy` | dry-run | Rows whose `category` is not an exact taxonomy name |
| `--only-categories a,b` | dry-run | Exact label allow-list |
| `--only-changed` | apply | Skip rows where `old_category == new_category` |
| `--i-understand-this-writes-categories` | apply / playlists | Required write gate |
| `--resume` | playlists | Skip shortcodes already in the JSONL report |
| `SUPERBRAIN_PROVIDER_ORDER` | env | e.g. `openrouter,omlx,…` for bulk jobs |

## Related

- Taxonomy config example: `backend/config/categories.toml.example`
- Test / promote notes: [`TEST_ENV.md`](TEST_ENV.md)
- YouTube playlists-per-category (later): [`BACKLOG_CATEGORY_YOUTUBE_PLAYLISTS.md`](BACKLOG_CATEGORY_YOUTUBE_PLAYLISTS.md)
