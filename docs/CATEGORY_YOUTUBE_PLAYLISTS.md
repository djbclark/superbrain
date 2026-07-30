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
3. Playlist sync turns on automatically after a successful
   `superbrain --youtube-connect` (writes `enabled=true` / `dry_run=false`
   into live `categories.toml` and creates/adopts category playlists).
   You can still override settings by hand if needed:

```toml
[youtube_playlists]
enabled = true
dry_run = false
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

With the local API/runtime available:

```bash
superbrain --youtube-connect              # OAuth + auto-enable playlists
superbrain --category-playlists-status    # config + mapping counts
superbrain --sync-category-playlists      # backfill all YouTube analyses
superbrain --sync-category-playlists --sync-category-playlists-limit 20
```

`superbrain --sync-category-playlists` reloads SecretSpec when needed,
skips videos already synced, and **waits for the Pacific-midnight YouTube
quota reset** on 403/quota errors before continuing.

Concurrent accidental runs are blocked with exclusive flock locks under
`~/.superbrain-server/locks/` for `--youtube-connect`,
`--sync-category-playlists`, and `--category-playlists-status`.

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
2. Run `superbrain --youtube-connect` (re-authorize + auto-enable playlist sync + ensure playlists).
3. New YouTube analyses and manual category edits sync going forward.
4. Optional: backfill history with `superbrain --sync-category-playlists`.
