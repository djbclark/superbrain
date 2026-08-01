# Category → YouTube playlists

> **Optional feature.** If you are not mirroring SuperBrain categories onto
> YouTube playlists, **skip this entire document.**

Mirror configured SuperBrain taxonomy categories onto YouTube playlists and keep
playlist membership in sync when a video’s category is assigned or changed.

**Operator entrypoint:** `superbrain …` (not curl, not raw `python`). HTTP
endpoints below are for the app and automation.

---

## Quick start

```bash
superbrain --youtube-connect                 # once (or after OAuth scope change)
superbrain --category-playlists-status       # config + quota + backfill state
# historic backfill — choose one:
superbrain --sync-category-playlists-start   # reboot-safe (recommended for long runs)
#   or:
superbrain --sync-category-playlists         # foreground one-shot
superbrain --sync-category-playlists-stop    # cancel reboot-safe mode only
```

See `superbrain --help` (groups + process epilog) for flag dependencies.

---

## Prerequisites

1. Taxonomy cutover complete (`config/categories.toml` in the runtime).
2. YouTube OAuth with the full **`youtube`** scope (playlist create/modify).
   Subscription-only installs that authorized `youtube.readonly` must
   re-authorize: `superbrain --youtube-connect`.
3. Playlist sync turns on automatically after a successful connect (writes
   `enabled=true` / `dry_run=false` into live `categories.toml` and
   creates/adopts category playlists). Override under `[youtube_playlists]`
   if needed:

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

---

## Behavior

- One playlist per synced category; title = `title_prefix + category name`
- On analyze (YouTube) and `PUT /post/{shortcode}` category edits: best-effort
  sync with **priority=new** (uses the new-video quota reserve)
- On category change: remove from previous playlist, add to new (idempotent)
  unless `membership_mode = "add_only"` (skips auto-delete; playlists become
  cumulative). Memberships are tracked in
  `category_youtube_playlist_memberships` (video_id, playlist_id) so add-only
  can retain multiple playlist rows; the primary `category_youtube_playlist_items`
  row still points at the current category.
- Live analyze/edit hooks debounce rapid category edits
  (`sync_debounce_seconds`, default 2s) so only the final category is synced.
- **Playlist order:** inserts use `snippet.position = 0` so watchlists read
  newest → oldest
- Skips non-YouTube, hidden, and labels outside the taxonomy
- Bulk historic backfill is via `superbrain` (not automatic on every
  recategorize apply)

---

## Quota-aware pacing

YouTube Data API daily quota resets at **Pacific midnight**. SuperBrain keeps a
local ledger (`youtube_api_quota_ledger`) and a deferred queue
(`category_youtube_playlist_pending`).

| Knob | Default | Meaning |
|------|---------|---------|
| `daily_quota_units` | 10000 | Assumed daily budget |
| `new_video_reserve_pct` | 0.90 | During the normal day, historic backfill may use only the remaining ~10% |
| `near_reset_hours` | 2 | Last N hours before PT midnight |
| `near_reset_historic_pct` | 0.90 | Near reset, spend remaining budget down to this ceiling (leave ~10% buffer) |
| `fresh_window_hours` | 24 | Unsynced analyses this new count as `priority=new` during backfill |

Estimated unit costs: `playlistItems.insert/delete` = 50, `playlists.list` =
1/page, `playlists.insert` = 50.

On Google 403/quota errors the day is marked exhausted, the video is enqueued,
and the worker sleeps until Pacific midnight (or idles until the near-reset
window). Live `priority=new` inserts that hit budget/403 are queued and drained
first when quota returns.

`superbrain --youtube-quota-stats` prints durable usage events
(`youtube_api_usage_events`) plus a local-only reconcile-vs-rebuild planner
(never automatic).

Concurrent CLI runs use flock locks under `~/.superbrain-server/locks/` for
`--youtube-connect`, `--sync-category-playlists`, `--category-playlists-status`,
and `--youtube-quota-stats`. Start/stop only toggle the enable flag.

---

## CLI reference

| Flag | Role | Depends on / notes |
|------|------|--------------------|
| `--youtube-connect` | OAuth + auto-enable playlists | First step |
| `--category-playlists-status` | Status JSON (includes `backfill`) | After connect |
| `--sync-category-playlists-start` | Enable reboot-safe historic backfill | After connect; alternative to foreground sync |
| `--sync-category-playlists-stop` | Cancel reboot-safe backfill | Only needed if start was used |
| `--sync-category-playlists` | Foreground one-shot backfill | Does **not** enable reboot-safe mode |
| `--sync-category-playlists-limit N` | Cap for foreground sync | Requires `--sync-category-playlists` |
| `--youtube-quota-stats` | Usage rollup | Optional |
| `--youtube-quota-stats-days N` | Window for stats | Requires `--youtube-quota-stats` |

Foreground `--sync-category-playlists` scheduler:

1. Ensures playlists once (budget-aware; skips redundant `playlists.list`).
2. Drains pending **new**, then pending historic.
3. Syncs unsynced rows newest-first.
4. Caps historic spend early in the day; idles until near-reset or midnight.

---

## API (app / automation)

Prefer `superbrain` for operator tasks. These endpoints mirror the same
behavior for clients:

| Method | Path | Same as |
|--------|------|---------|
| GET | `/api/youtube/oauth/status` | — |
| GET | `/api/youtube/category-playlists/status` | `--category-playlists-status` |
| POST | `/api/youtube/category-playlists/backfill/start` | `--sync-category-playlists-start` |
| POST | `/api/youtube/category-playlists/backfill/stop` | `--sync-category-playlists-stop` |
| GET | `/api/youtube/quota/stats` | `--youtube-quota-stats` |
| POST | `/api/youtube/category-playlists/ensure` | (maintenance) |
| POST | `/api/youtube/category-playlists/sync/{shortcode}` | (single item) |

All require the API key. Ensure/sync require `enabled=true`.

---

## Rollout checklist

1. Deploy reviewed code into the runtime (see [Local fork runtime](#local-fork-runtime-skip-if-uninterested) if applicable).
2. `superbrain --youtube-connect`
3. New YouTube analyses and manual category edits sync going forward.
4. Optional historic backfill: `--sync-category-playlists-start` or
   `--sync-category-playlists`. Cancel reboot-safe mode with
   `--sync-category-playlists-stop`.

---

<a id="local-fork-runtime-skip-if-uninterested"></a>

## Local fork runtime (skip if uninterested)

> **This section is only for the fork’s LaunchAgent install** at
> `~/.superbrain-server` (`com.djbclark.superbrain`). Stock upstream /
> `superbrain-server` npm installs can ignore everything below.

### Reboot-safe historic backfill

Same pattern as upstream optional features (`config/ngrok_enabled.txt`):

- Enable file: `~/.superbrain-server/config/category_playlist_backfill_enabled.txt`
- While present, the API process supervises `superbrain --sync-category-playlists`
  (including after reboot).
- Log: `~/Library/Logs/superbrain/category-playlist-backfill.log`

```bash
superbrain --sync-category-playlists-start
superbrain --category-playlists-status    # "backfill": {enabled, process_running, …}
superbrain --sync-category-playlists-stop
```

### Deploy code into the runtime

```bash
superbrain --deploy-local                 # checkout → ~/.superbrain-server
superbrain --deploy-local --restart       # also restart the API service
```

`--restart` requires `--deploy-local`. Source defaults to `~/src/superbrain`
(or set `SUPERBRAIN_SOURCE_DIR`). Deploy does **not** overwrite
`categories.toml`.

If reboot-safe backfill is running, stop it first (deploy refuses while a
playlist worker is active):

```bash
superbrain --sync-category-playlists-stop
superbrain --deploy-local
superbrain --sync-category-playlists-start
```
