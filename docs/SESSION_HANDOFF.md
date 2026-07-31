# SuperBrain session handoff

Status captured **2026-07-31 afternoon ET**. Live playlist backfill is sleeping
until Pacific midnight (~03:00 ET / 00:00 PT on 2026-08-01).

## Current next action

1. **Babysit playlist backfill through PT midnight reset** (~03:00 ET 2026-08-01).
   Confirm `day_key` rolls to `2026-08-01`, usage events record, historic sync
   uses ~10% daytime cap then near-reset spend, inserts at `position=0`.
2. Wait for upstream review of PRs #4/#5 and feedback on proposal #6 (unchanged).

Babysit commands:

```bash
pgrep -lf 'sync-category-playlists'
tail -n 40 ~/Library/Logs/superbrain/category-playlist-backfill.log
superbrain --category-playlists-status
superbrain --youtube-quota-stats
```

Restart backfill only if dead (deploy refuses while sync is running):

```bash
pkill -f 'main.py --sync-category-playlists' || true
bash ~/src/superbrain/backend/scripts/deploy-local.sh
nohup superbrain --sync-category-playlists >> ~/Library/Logs/superbrain/category-playlist-backfill.log 2>&1 &
```

## Fork playlist / quota work (shipped on main)

Recent work on `djbclark/superbrain` `main`:

- Quota-aware newest-first sync (new-video reserve, near-reset historic, pending queue, position-0)
- Durable YouTube API usage events + cost table + CLI/API stats ([issue #5](https://github.com/djbclark/superbrain/issues/5))
- `membership_mode=move|add_only`, reconcile-vs-rebuild planner with savings margin
- Strict moves add-before-delete; live sync debounce; no synthetic duplicate item IDs

Runtime: `~/.superbrain-server`. Log:
`~/Library/Logs/superbrain/category-playlist-backfill.log`.

Fork #5 operator decisions (2026-07-31): playlist **rebuild execution** and **hosted per-user budgets** are deferred (later). Planner stays recommendation-only; self-hosted keeps a single-tenant local ledger until issues #1/#2.

## Upstream contribution sequence

Wait for upstream review of these ready, clean pull requests:

- [sidinsearch/superbrain#4 — isolate the live API probe](https://github.com/sidinsearch/superbrain/pull/4)
- [sidinsearch/superbrain#5 — paginate mobile delta sync](https://github.com/sidinsearch/superbrain/pull/5)

Also wait for maintainer feedback on
[sidinsearch/superbrain#6 — opt-in YouTube subscription organization and private category playlists](https://github.com/sidinsearch/superbrain/issues/6).

The [hourly upstream activity workflow](https://github.com/djbclark/superbrain/actions/workflows/track-upstream-activity.yml)
runs entirely on GitHub Actions (Telegram notifications for upstream activity).

1. Respond narrowly if the tracker reports an upstream comment, review, or
   requested change on PR #4, PR #5, or proposal #6.
2. When either current PR closes and a slot opens, rebase and submit the queued
   `prep/biome-tooling` branch (`b5301e8`).
3. Continue [fork issue #3](https://github.com/djbclark/superbrain/issues/3) for
   YouTube-related improvements independent of enhanced API access. Hold
   approval-dependent work out of upstream PRs.
4. After the delta-sync PR, prepare the generic SQLite concurrency/worker-safety
   wave in issue #3.

Mirrored proposal: [fork issue #4](https://github.com/djbclark/superbrain/issues/4).
Quota instrumentation: [fork issue #5](https://github.com/djbclark/superbrain/issues/5).

Branch tips: `dpr/test-live-api-isolation` `937704c`,
`dpr/mobile-delta-sync-pagination` `44df0a0`, `prep/biome-tooling` `b5301e8`.

## YouTube quota request

Form submitted 2026-07-31 requesting 500,000 daily units. Wait for Google email.
Public record: [`YOUTUBE_API_QUOTA_HANDOFF.md`](YOUTUBE_API_QUOTA_HANDOFF.md).
Private companion in site-private memory (authorized agents only).

With the local budget (10% historic by day / near-reset catch-up), default
quota supports ~20 historic inserts/day plus reserved new-video capacity;
~6.7k unsynced remain unless Google raises project quota.

## Broad multi-user feature work

- [Fork issue #1](https://github.com/djbclark/superbrain/issues/1) — production readiness
- [Fork issue #2](https://github.com/djbclark/superbrain/issues/2) — multi-user plan (blocked by #1)

Do not assume the development quota request authorizes a shared hosted
multi-user service.

## Suggested resume prompt

> Continue the SuperBrain handoff. Read `AGENTS.md` and `docs/SESSION_HANDOFF.md`.
> Babysit `superbrain --sync-category-playlists` through Pacific midnight
> (~03:00 ET 2026-08-01): confirm day_key roll, usage events, position-0
> inserts, and historic 10%/near-reset pacing. Check upstream PRs #4/#5 and
> proposal #6. Remaining fork #5: multi-membership add-only tracking if needed.
> Report Google quota-email reply if email access is available.
