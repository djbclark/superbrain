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
daily_quota_units = 10000
new_video_reserve_pct = 0.90
near_reset_hours = 2
near_reset_historic_pct = 0.90
fresh_window_hours = 24
idle_sleep_seconds = 180
membership_mode = "move"   # or "add_only"
# categories = ["Sysadmin", "Science"]   # optional subset; omit = all taxonomy
```

`deploy-local.sh` does **not** overwrite `categories.toml`.

## Behavior

- One playlist per synced category; title = `title_prefix + category name`
- On analyze (YouTube) and `PUT /post/{shortcode}` category edits: best-effort
  sync with **priority=new** (uses the new-video quota reserve)
- On category change: remove from previous playlist, add to new (idempotent)
  unless `membership_mode = "add_only"` (skips auto-delete; playlists become
  cumulative — local mapping still tracks the primary category only). Strict
  **move** mode adds the new membership **before** deleting the old one.
- Live analyze/edit hooks debounce rapid category edits
  (`sync_debounce_seconds`, default 2s) so only the final category is synced.
- **Playlist order:** inserts use `snippet.position = 0` so watchlists read
  newest → oldest
- Skips non-YouTube, hidden, and labels outside the taxonomy
- Bulk backfill is via CLI (not automatic on every `recategorize.py apply`)

## Quota-aware pacing

YouTube Data API daily quota resets at **Pacific midnight**. SuperBrain keeps a
local ledger (`youtube_api_quota_ledger`) and a deferred queue
(`category_youtube_playlist_pending`).

Defaults (override under `[youtube_playlists]`):

| Knob | Default | Meaning |
|------|---------|---------|
| `daily_quota_units` | 10000 | Assumed daily budget |
| `new_video_reserve_pct` | 0.90 | During the normal day, historic backfill may use only the remaining ~10% |
| `near_reset_hours` | 2 | Last N hours before PT midnight |
| `near_reset_historic_pct` | 0.90 | Near reset, spend remaining budget down to this ceiling (leave ~10% buffer) |
| `fresh_window_hours` | 24 | Unsynced analyses this new count as `priority=new` during backfill |

Estimated unit costs tracked locally: `playlistItems.insert/delete` = 50,
`playlists.list` = 1/page, `playlists.insert` = 50.

On Google 403/quota errors the day is marked exhausted, the video is enqueued
with the correct priority, and the worker sleeps until Pacific midnight (or
idles in short chunks while waiting for the near-reset historic window). Live
`priority=new` inserts that hit budget/403 are queued and drained first when
quota returns.

This aligns with fork issue #5 (quota-aware pacing) while keeping add-only +
position-0 as the default playlist UX.

## CLI

With the local API/runtime available:

```bash
superbrain --youtube-connect              # OAuth + auto-enable playlists
superbrain --category-playlists-status    # config + quota ledger + pending
superbrain --sync-category-playlists      # quota-aware newest-first backfill
superbrain --sync-category-playlists --sync-category-playlists-limit 20
```

`superbrain --sync-category-playlists`:

1. Ensures playlists once (budget-aware).
2. Drains pending **new** first, then pending historic.
3. Syncs unsynced rows **newest-first** (`analyzed_at`, then `categorized_at`,
   then `updated_at`).
4. Caps historic spend early in the day; idles in short sleeps until the
   near-reset window (or midnight), instead of a single blind 24h sleep.

```bash
superbrain --youtube-quota-stats           # durable API usage for today (PT)
superbrain --youtube-quota-stats --youtube-quota-stats-days 7
```

Usage events are stored in `youtube_api_usage_events` (no tokens/headers/payloads)
with a versioned cost table (`core/youtube_quota.py`, currently dated
`2026-07-31`). Failed calls are still charged when the method cost is known.
`--youtube-quota-stats` also prints a local-only reconcile-vs-rebuild planner
(rebuild is never automatic; cheaper only when `deletions > retained + 2` per
category under equal 50-unit write costs).

Concurrent accidental runs are blocked with exclusive flock locks under
`~/.superbrain-server/locks/` for `--youtube-connect`,
`--sync-category-playlists`, `--category-playlists-status`, and
`--youtube-quota-stats`.

## API

| Method | Path | Notes |
|--------|------|--------|
| GET | `/api/youtube/oauth/status` | Includes `oauth_scope` + playlist config |
| GET | `/api/youtube/category-playlists/status` | Config + mappings + quota/usage |
| GET | `/api/youtube/quota/stats` | Durable usage rollup (`?days=1`) |
| POST | `/api/youtube/category-playlists/ensure` | Create/adopt playlists |
| POST | `/api/youtube/category-playlists/sync/{shortcode}` | Sync one analysis |

All require the API key. Ensure/sync require `enabled=true`.

## Rollout checklist

1. Deploy code; restart LaunchAgent when the queue is in a safe state.
2. Run `superbrain --youtube-connect` (re-authorize + auto-enable playlist sync + ensure playlists).
3. New YouTube analyses and manual category edits sync going forward.
4. Optional: backfill history with `superbrain --sync-category-playlists`
   (stop any old sleeper first so the new scheduler takes the lock).
