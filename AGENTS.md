# Agent entry point

AI coding agents working in this fork must begin with
[`docs/SESSION_HANDOFF.md`](docs/SESSION_HANDOFF.md). It records the active
upstream contribution sequence, current review links, YouTube API approval
boundary, and the next safe actions.

## Discovery map

- Current cross-session handoff: [`docs/SESSION_HANDOFF.md`](docs/SESSION_HANDOFF.md)
- Public YouTube quota/compliance record:
  [`docs/YOUTUBE_API_QUOTA_HANDOFF.md`](docs/YOUTUBE_API_QUOTA_HANDOFF.md)
- Public compliance page and evidence:
  [`docs/youtube-api-compliance.html`](docs/youtube-api-compliance.html) and
  [`docs/compliance-screenshots/`](docs/compliance-screenshots/)
- YouTube playlist behavior:
  [`docs/CATEGORY_YOUTUBE_PLAYLISTS.md`](docs/CATEGORY_YOUTUBE_PLAYLISTS.md)
- Contribution roadmap:
  [fork issue #3](https://github.com/djbclark/superbrain/issues/3)
- Proposed YouTube end state:
  [fork issue #4](https://github.com/djbclark/superbrain/issues/4) and
  [upstream proposal #6](https://github.com/sidinsearch/superbrain/issues/6)
- API quota instrumentation and optimization:
  [fork issue #5](https://github.com/djbclark/superbrain/issues/5)
- Production and multi-user work:
  [fork issue #1](https://github.com/djbclark/superbrain/issues/1) and
  [fork issue #2](https://github.com/djbclark/superbrain/issues/2)
- Sensitive quota-form supplement, for agents authorized to read the private
  companion repository:
  [`djbclark/site-private` `backup-superbrain-youtube-form` record](https://github.com/djbclark/site-private/blob/backup-superbrain-youtube-form/memory/reference_superbrain_youtube_api_quota_form.md)

## Durable boundaries

- Keep upstream PRs that do not require enhanced YouTube Data API access
  separate from work that depends on increased quota, Google/YouTube approval,
  or broad hosted-service authorization.
- Do not treat the development quota request as authorization for a shared
  multi-user service. Follow issues #1 and #2 before broad launch.
- Preserve bulk selection and ongoing bulk consent. Do not design a workflow
  that makes users approve channels or videos one at a time.
- Never commit credentials, OAuth tokens, cookies, runtime databases, personal
  contact details, or the private Google Cloud project identifier.
- Public project knowledge belongs in this repository. Keep only genuinely
  sensitive values in the private supplement, and link public and private
  records in both directions so future agents can discover the split.
