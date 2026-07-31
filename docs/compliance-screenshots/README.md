# YouTube API compliance assets

The completed public, non-sensitive form record and resubmission checklist are
in [`../YOUTUBE_API_QUOTA_HANDOFF.md`](../YOUTUBE_API_QUOTA_HANDOFF.md).

Public legal URLs for quota / audit forms:

| Document | URL |
|----------|-----|
| Compliance landing (YouTube attribution + legal links) | https://djbclark.github.io/superbrain/youtube-api-compliance.html **or** raw HTML below |
| Privacy Policy | https://github.com/djbclark/superbrain/blob/main/docs/PRIVACY.md |
| Terms of Service | https://github.com/djbclark/superbrain/blob/main/docs/TERMS.md |
| Raw privacy | https://raw.githubusercontent.com/djbclark/superbrain/main/docs/PRIVACY.md |
| Raw terms | https://raw.githubusercontent.com/djbclark/superbrain/main/docs/TERMS.md |
| Raw compliance HTML | https://raw.githubusercontent.com/djbclark/superbrain/main/docs/youtube-api-compliance.html |

> If GitHub Pages is not enabled on the fork, open the raw compliance HTML in a
> browser (or use the blob URLs) for screenshots. Enabling Pages for `/docs` is
> optional.

## Screenshots

Place PNG/JPG captures in `docs/compliance-screenshots/`:

| File | What to show |
|------|----------------|
| `01-homepage-compliance.png` | `youtube-api-compliance.html` with YouTube attribution and Privacy/Terms links visible |
| `02-privacy-policy.png` | Privacy Policy page showing YouTube data handling, Google Privacy Policy link, deletion/revoke |
| `03-terms-of-service.png` | Terms of Service page |
| `04-oauth-consent.png` | Google OAuth consent screen listing YouTube scopes (from `superbrain --youtube-connect`) |
| `05-oauth-revoke-hint.png` | UI or docs pointing to https://myaccount.google.com/permissions |
| `06-video-thumbnail-ui.png` | SuperBrain post detail/home showing a YouTube thumbnail + link out (this fork does not embed a YouTube IFrame player) |

Capture helper (macOS with Chrome):

```bash
docs/compliance-screenshots/capture.sh
```

## Supporting material

The `supporting-materials/` directory contains the exact design PNGs submitted
with the quota request and their editable SVG sources:

- `superbrain-youtube-architecture.{png,svg}`
- `superbrain-youtube-user-flow.{png,svg}`
- `superbrain-youtube-data-handling.{png,svg}`
