# Backlog: YouTube playlists synced to user categories

**Priority:** last (after taxonomy cutover, recategorize completion, and live promote/teardown)

## Goal

Create YouTube playlists that mirror the operator’s configured SuperBrain
categories (from `config/categories.toml`), and keep those playlists updated
as videos are newly categorized or recategorized.

## Likely requirements (to confirm when picked up)

- One YouTube playlist per effective taxonomy category (or a configurable subset)
- Create missing playlists via YouTube Data API (OAuth), using category name
- On category assign/change (analyze, manual edit, `recategorize`):
  - add the video to the playlist for its new category
  - remove it from the previous category playlist when the category changes
- Idempotent: re-running sync must not duplicate playlist items
- Respect single-category mode for this deployment
- Do not put API credentials in git; use local/SecretSpec config
- Dry-run / disable flag so playlist mutation is opt-in

## Non-goals (unless operator expands scope)

- Building playlists for legacy default categories when defaults are disabled
- Syncing soft-deleted / hidden analyses into YouTube playlists
- Replacing SuperBrain collections with YouTube playlists

## Dependencies

- Stable config-driven taxonomy in production
- Completed (or stably incremental) recategorize of Watch Later lists
- YouTube OAuth credentials with playlist create/modify scope
