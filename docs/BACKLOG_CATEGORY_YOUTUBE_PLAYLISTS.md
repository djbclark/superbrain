# Backlog: YouTube playlists synced to user categories

**Status:** implemented — see [`CATEGORY_YOUTUBE_PLAYLISTS.md`](CATEGORY_YOUTUBE_PLAYLISTS.md).

**Priority:** last (after taxonomy cutover, recategorize completion, and live promote/teardown)

## Goal

Create YouTube playlists that mirror the operator’s configured SuperBrain
categories (from `config/categories.toml`), and keep those playlists updated
as videos are newly categorized or recategorized.

## Delivered

- One YouTube playlist per effective taxonomy category (or a configurable subset)
- Create/adopt playlists via YouTube Data API (OAuth), titled with configurable prefix
- On category assign/change (YouTube analyze, manual `PUT /post`, CLI sync):
  - add the video to the playlist for its new category
  - remove it from the previous category playlist when the category changes
- Idempotent local mapping tables; re-running sync does not duplicate intent
- Single-category deployments: syncs the resolved taxonomy name only
- Credentials stay in SecretSpec / local env; feature gated by `[youtube_playlists]`
- `dry_run` / `enabled=false` so mutations stay opt-in

## Operator follow-ups (not code)

- Re-authorize OAuth after scope upgrade from `youtube.readonly` → `youtube`
- Enable in live `categories.toml` and run dry-run → force-write backfill
