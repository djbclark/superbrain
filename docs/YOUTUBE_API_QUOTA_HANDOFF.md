# YouTube Data API quota-extension handoff

This is the public, non-sensitive resubmission record for the completed
SuperBrain YouTube Data API audit and quota-extension form. It was captured on
**2026-07-31** after Google displayed a successful-submission confirmation.

Personal contact/address fields and the Google Cloud project identifier are
intentionally excluded. Authorized agents can find those values in the
[`site-private` companion record](https://github.com/djbclark/site-private/blob/backup-superbrain-youtube-form/memory/reference_superbrain_youtube_api_quota_form.md).

Quota and usage figures are a dated snapshot. Recheck the remaining backfill
count and requested quota before any later resubmission.

## Section 1: request type

- Reason: **Complete a compliance audit to request additional quota**

## Section 2: organization profile

- Applying as: **Individual user**
- Organization legal name: **self**
- Parent company: **self**
- Organization website:
  [SuperBrain YouTube API compliance page](https://djbclark.github.io/superbrain/youtube-api-compliance.html)
- Country: **United States**
- Category: **Technology/Software Development**
- Organization size/type: **Independent Developer/Sole Proprietor**
- Primary technical and business contacts: **Same as primary contact**
- Legal name, address, postal code, email, and project identifier: **private
  companion record**

## Section 3: business model and Google contacts

### Organization work related to YouTube

> SuperBrain is a free, open-source, self-hosted tool that helps people manage
> the videos they want to watch from their YouTube subscriptions. With
> user-granted OAuth, it reads the user's existing subscriptions for optional
> channel-update workflows and organizes user-selected videos into private
> category playlists in the same account. AI-generated summaries, tags, and
> categories let users quickly switch among topics, rediscover relevant videos,
> and choose what to watch next. The benefit to YouTube is that users can more
> easily navigate a large subscription library and return to YouTube to watch
> more of the videos they subscribed for; playback remains on YouTube.
> SuperBrain does not sell YouTube API Data, display advertising, use
> `youtube.search.list` or `youtube.videos.insert`, or provide a replacement
> playback experience. OAuth tokens and playlist mappings are stored locally,
> and users can disable synchronization, delete local data, or revoke Google
> access at any time.

- Target audiences: **Software Developers** and **General Public**
- Revenue model: **Free service (we do not charge users)**
- Google/YouTube representative: **No Google representative**
- Learned about the API from: **Google Developer Documentation**
- Content Owner ID, associated YouTube channel URL, and Google Ads Customer ID:
  **Not applicable / blank**

## Section 4: API client overview and access

- API client name: **SuperBrain**
- Client name contains the word “YouTube”: **No**
- Primary access URL:
  [SuperBrain YouTube API compliance page](https://djbclark.github.io/superbrain/youtube-api-compliance.html)
- Privacy Policy URL:
  [SuperBrain Privacy Policy](PRIVACY.md)
- Terms of Service URL:
  [SuperBrain Terms of Service](TERMS.md)
- Publicly accessible: **Yes**
- Demo username/password: **Not provided**
- API client location: **Not provided**

## Section 5: use cases and quota extension

- Number of project numbers: **1**
- Exact project number: **private companion record**
- Use-case category: **Video Uploading & Account Management**
  - Selected for its “managing playlists in bulk” component; SuperBrain does
    not upload videos.
- OAuth used: **Yes**
- Expected API volume: **1,000 to 10,000 requests per day**
- Requested quota tier: **Above Default quota**
- Total per-day quota requested: **500,000 quota units**
- Peak per-minute quota requested: **60 requests/minute**

### Selected endpoints

- `youtube.playlistItems.delete`
- `youtube.playlistItems.insert`
- `youtube.playlists.insert`
- `youtube.playlists.list`
- `youtube.subscriptions.list`

Not selected and not used: `youtube.search.list`, `youtube.videos.insert`, and
all other endpoints.

### Detailed quota justification

> SuperBrain needs additional quota for a one-time migration of approximately
> 6,627 user-selected videos into private category playlists.
> `playlistItems.insert` costs 50 units, so the remaining backfill requires
> about 331,350 units, plus limited playlist listing/creation and retry
> headroom. These playlists help users navigate videos from their subscriptions
> by topic and return to YouTube to watch them. The backfill runs sequentially
> at no more than 60 requests per minute, skips already-synchronized items, and
> pauses on quota errors; it does not call `youtube.search.list` or
> `youtube.videos.insert`. After migration, expected usage falls to
> approximately 5,000–15,000 units per day for newly organized videos and
> category changes. We request 500,000 units per day to complete the migration
> safely without repeated daily-quota interruptions; ongoing use will remain
> substantially lower.

### Evidence attached

All exact submitted screenshots are public in
[`docs/compliance-screenshots/`](compliance-screenshots/):

| Form field | Attached filename |
| --- | --- |
| Privacy Policy screenshots | [`02-privacy-policy.png`](compliance-screenshots/02-privacy-policy.png) |
| Homepage screenshot | [`01-homepage-compliance.png`](compliance-screenshots/01-homepage-compliance.png) |
| Terms of Service documentation | [`03-terms-of-service.png`](compliance-screenshots/03-terms-of-service.png) |
| Conditional OAuth evidence | [`05-oauth-revoke-hint.png`](compliance-screenshots/05-oauth-revoke-hint.png) |

## Section 6: design documents and supporting material

The exact submitted PNG files and their editable SVG sources are public in
[`docs/compliance-screenshots/supporting-materials/`](compliance-screenshots/supporting-materials/):

| Form field | Attached filename |
| --- | --- |
| Architecture Diagram | [`superbrain-youtube-architecture.png`](compliance-screenshots/supporting-materials/superbrain-youtube-architecture.png) |
| User Flow Diagrams | [`superbrain-youtube-user-flow.png`](compliance-screenshots/supporting-materials/superbrain-youtube-user-flow.png) |
| Other Supporting Materials | [`superbrain-youtube-data-handling.png`](compliance-screenshots/supporting-materials/superbrain-youtube-data-handling.png) |

## Section 7: attestations

The submitter checked the attestations for:

- YouTube API Services Terms of Service
- Google Privacy Policy
- Developer Policies and future compliance
- Termination understanding
- Demo Account Terms waiver, if applicable
- Accuracy and truthfulness of the information
- Consent to use submission data for review
- Support recording consent

## Resubmission checklist

1. Retrieve the exact project and contact fields from the private companion
   record.
2. Re-run `superbrain --category-playlists-status` and recalculate the remaining
   backfill count before reusing the quota justification.
3. Confirm the compliance, privacy, and terms URLs still resolve.
4. Reattach the seven public PNG files listed above. Editable SVG sources are
   archived beside the Section 6 PNGs.
5. Recheck current YouTube API policies and attestations.
6. Review every field and submit only with explicit operator approval.
