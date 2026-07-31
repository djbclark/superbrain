# SuperBrain session handoff

Status captured **2026-07-31**. There are no known blockers or immediate
upstream replies required.

## Current next action

Wait for upstream review of these ready, clean pull requests:

- [sidinsearch/superbrain#4 — isolate the live API probe](https://github.com/sidinsearch/superbrain/pull/4)
- [sidinsearch/superbrain#5 — paginate mobile delta sync](https://github.com/sidinsearch/superbrain/pull/5)

Also wait for maintainer feedback on
[sidinsearch/superbrain#6 — opt-in YouTube subscription organization and private category playlists](https://github.com/sidinsearch/superbrain/issues/6).

At the time of this handoff, both PRs were open, mergeable, and had no issue or
review comments. Proposal #6 had three comments, all from `djbclark`; there was
no maintainer response to answer.

The [hourly upstream activity workflow](https://github.com/djbclark/superbrain/actions/workflows/track-upstream-activity.yml)
runs entirely on GitHub Actions. It sends Telegram notifications for upstream
PR and proposal comments, reviews, requested changes, state changes, changed
next actions, three-day PR reminders, and seven-day proposal reminders. Its
expanded proposal-aware version passed
[run 30638150707](https://github.com/djbclark/superbrain/actions/runs/30638150707).

## Contribution sequence

1. Respond narrowly if the tracker reports an upstream comment, review, or
   requested change on PR #4, PR #5, or proposal #6.
2. When either current PR closes and a slot opens, rebase and submit the queued
   `prep/biome-tooling` branch. It is pushed at `b5301e8`, is one commit based
   on upstream `main`, and passed `npm ci` plus `npm run check` across 28 source
   files. The tracker will automatically detect the resulting upstream PR.
3. Continue the contribution plan in
   [fork issue #3](https://github.com/djbclark/superbrain/issues/3) for
   YouTube-related improvements that are independent of enhanced YouTube Data
   API access. Keep at most two independent upstream PRs open. Hold work that
   depends on enhanced access, increased quota, or related Google/YouTube
   approval out of those PRs.
4. After the delta-sync PR, prepare the generic SQLite
   concurrency/worker-safety wave described in issue #3. Before configurable
   taxonomy work, open an upstream design issue and obtain maintainer direction.

The YouTube API end-state proposal is mirrored in
[fork issue #4](https://github.com/djbclark/superbrain/issues/4). Quota
instrumentation and API-use optimization—including add-only versus strict
moves and individual reconciliation versus explicit playlist rebuild—are
tracked in [fork issue #5](https://github.com/djbclark/superbrain/issues/5).

Current pushed branch tips:

- `dpr/test-live-api-isolation` — `937704c`
- `dpr/mobile-delta-sync-pagination` — `44df0a0`
- `prep/biome-tooling` — `b5301e8`

## YouTube quota request

The YouTube Data API audit/quota-extension form was submitted successfully on
2026-07-31, requesting 500,000 daily quota units for the development project.
There is no action until Google replies by email. Email is not part of the
GitHub/Telegram upstream monitor.

The reusable non-sensitive form record and all safe evidence are public in
[`YOUTUBE_API_QUOTA_HANDOFF.md`](YOUTUBE_API_QUOTA_HANDOFF.md). Personal
contact/address fields and the Google Cloud project identifier are isolated in
the
[private companion record](https://github.com/djbclark/site-private/blob/backup-superbrain-youtube-form/memory/reference_superbrain_youtube_api_quota_form.md),
which is available only to authorized agents.

Current quota analysis: listing subscriptions is cheap; playlist writes drive
usage. Default quota supports roughly 175–190 new playlist items per day after
normal overhead, while a strict category move costs approximately twice an
insertion. Ordinary self-hosted use can pace work under default quota; the
large historical backfill is what makes enhanced quota useful. Issue #5 will
add durable statistics rather than relying on estimates.

Before any resubmission, recalculate the remaining playlist backfill, verify
the public URLs and current policies, and submit only with explicit operator
approval.

## Broad multi-user feature work

- [Fork issue #1](https://github.com/djbclark/superbrain/issues/1) tracks the
  production/distribution decision, Google approvals, OAuth verification,
  privacy, quota, security, and rollout requirements.
- [Fork issue #2](https://github.com/djbclark/superbrain/issues/2) is the
  code-derived hosted multi-user implementation plan and is explicitly blocked
  by issue #1. It preserves bulk selection and ongoing bulk consent; users must
  not approve each channel or video separately.

Do not assume the development quota request authorizes a shared hosted
multi-user service. Broad launch requires a production project, revised
audit/quota request, OAuth verification, tenant isolation, accurate hosted
privacy terms, and the required approvals.

## Suggested resume prompt

> Continue the SuperBrain handoff. Read `AGENTS.md`, then check upstream PRs #4
> and #5, upstream proposal #6, and fork roadmap #3. If there is maintainer
> feedback, address it narrowly. If a PR slot has opened, rebase and submit
> `prep/biome-tooling`. Keep work that depends on enhanced YouTube Data API
> access, increased quota, or related approval out of upstream PRs until
> upstream has accepted the proposal and the required Google/YouTube approval
> is ready. Use fork issue #5 for quota instrumentation and optimization. Also
> report whether Google has replied to the 2026-07-31 quota request if email
> access is available.
