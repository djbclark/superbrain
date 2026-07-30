# Category → YouTube playlists

Mirror configured SuperBrain taxonomy categories onto YouTube playlists and keep
playlist membership in sync when a video’s category is assigned or changed.

## Prerequisites

1. Taxonomy cutover complete (`config/categories.toml` in the runtime).
2. YouTube OAuth client + refresh token with the full **`youtube`** scope
   (playlist create/modify). Subscription-only installs that authorized
   `youtube.readonly` must **re-authorize** after deploying this change.

   Easiest (local API running):

   ```bash
   superbrain --youtube-connect
   ```

   That opens Google consent in your browser and waits for the localhost
   callback. Equivalently, open
   `http://127.0.0.1:5000/api/youtube/oauth/start?token=<token-from-token.txt>`
   in a browser on this machine.
3. Opt in via `[youtube_playlists]` in `categories.toml` (never committed).

## Config

```toml
[youtube_playlists]
enabled = false          # master switch
dry_run = true           # log would_create / would_add; no YouTube writes
title_prefix = "SuperBrain — "
privacy_status = "private"
# categories = ["Sysadmin", "Science"]   # optional subset; omit = all taxonomy
```

`deploy-local.sh` does **not** overwrite `categories.toml`.

## Behavior

- One playlist per synced category; title = `title_prefix + category name`
- On analyze (YouTube) and `PUT /post/{shortcode}` category edits: best-effort sync
- On category change: remove from previous playlist, add to new (idempotent)
- Skips non-YouTube, hidden, and labels outside the taxonomy
- Bulk backfill is via CLI (not automatic on every `recategorize.py apply`)

## CLI

From the runtime or checkout `backend/` (with secrets/env loaded as usual):

```bash
python scripts/sync_category_playlists.py status
python scripts/sync_category_playlists.py ensure --enable
python scripts/sync_category_playlists.py sync-one YT_xxxxxxxxxxx --enable
python scripts/sync_category_playlists.py sync-all --limit 20 --enable
# Real YouTube writes (ignores dry_run for this process):
python scripts/sync_category_playlists.py ensure --force-write
python scripts/sync_category_playlists.py sync-all --force-write --continue-on-error
```

## API

| Method | Path | Notes |
|--------|------|--------|
| GET | `/api/youtube/oauth/status` | Includes `oauth_scope` + playlist config |
| GET | `/api/youtube/category-playlists/status` | Config + local mappings |
| POST | `/api/youtube/category-playlists/ensure` | Create/adopt playlists |
| POST | `/api/youtube/category-playlists/sync/{shortcode}` | Sync one analysis |

All require the API key. Ensure/sync require `enabled=true`.

## Rollout checklist

1. Deploy code; restart LaunchAgent when the queue is in a safe state.
2. Re-authorize YouTube OAuth (new scope).
3. Set `enabled = true`, keep `dry_run = true`; run `ensure` + a small `sync-all --limit`.
4. Review dry-run actions; set `dry_run = false`; `ensure` then backfill.
5. New analyses and manual category edits sync automatically while enabled.
