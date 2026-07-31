# SuperBrain Privacy Policy

**Last updated:** 2026-07-31  
**Applies to:** the `djbclark/superbrain` fork of SuperBrain (self-hosted YouTube / content analysis software) and its use of YouTube API Services.

This privacy policy is provided so that users of this API client can understand what information is accessed, stored, and used when SuperBrain connects to YouTube. It is intended to satisfy YouTube API Services Developer Policy requirements for a clear privacy policy.

By authorizing SuperBrain to access your YouTube account (for example via `superbrain --youtube-connect`), you agree to this privacy policy for YouTube-related features.

## Who operates this software

SuperBrain in this fork is typically run as **self-hosted software on the operator’s own computer or server**. The person who installs and runs the instance is the data controller for that deployment. There is no SuperBrain cloud SaaS that receives your YouTube data unless you deliberately deploy and expose one yourself.

## Information we access via YouTube API Services

When you grant OAuth access, SuperBrain may access YouTube API Data necessary for the features you enable, including:

- Your YouTube channel identity needed to act on your behalf
- Your channel **subscriptions** (to discover channels for update notifications)
- Your **playlists** and **playlist items** (to create and update private category playlists such as “SuperBrain — Sysadmin”)
- Public video metadata needed to analyze or categorize content you ask SuperBrain to process (titles, descriptions, channel names, thumbnails, and similar fields returned by the API)

SuperBrain requests YouTube OAuth scopes required for those features (currently the full `youtube` scope for subscription discovery and playlist management). SuperBrain does **not** ask for your Google password. Authentication is handled by Google OAuth 2.0.

## Information stored locally

Depending on configuration, SuperBrain may store on the machine where it runs:

- OAuth **refresh tokens** and related secrets in the operator’s secret store (for example SecretSpec), not in public git
- Analysis results (summaries, categories, transcripts or transcript references, thumbnails, URLs) in a local database
- Mappings from SuperBrain categories to YouTube playlist IDs and playlist item IDs
- API access tokens for the local SuperBrain HTTP API

This information is stored to provide the product features (analysis, categorization, WebSub / subscription workflows, and category playlist sync). It is not uploaded to a SuperBrain corporate cloud by default.

## How we use YouTube API Data

YouTube API Data is used only to:

- Analyze and categorize videos or other content you submit
- Maintain WebSub / subscription notification workflows you enable
- Create and update **your** YouTube playlists so they mirror SuperBrain categories
- Operate and troubleshoot the self-hosted instance

We do **not** sell YouTube API Data. We do **not** use YouTube API Data for independent advertising profiling. We do **not** combine YouTube API Data with third-party data brokers for sale or surveillance.

## Sharing of information

SuperBrain does not share your YouTube API Data with third parties except:

- **Google / YouTube**, as required to perform API requests you authorize
- Service providers you yourself configure on your deployment (for example local or third-party LLM providers used for analysis). Those providers receive content you choose to analyze (such as titles, descriptions, or transcripts), not your Google password.

Human-readable analysis output may be shown in the SuperBrain UI or API to whoever can access your self-hosted instance. Protect access to that instance (API keys, network exposure) accordingly.

## Third-party policies

Use of YouTube is also subject to:

- [Google Privacy Policy](https://policies.google.com/privacy)
- [YouTube Terms of Service](https://www.youtube.com/t/terms)
- [YouTube API Services Terms of Service](https://developers.google.com/youtube/terms/api-services-terms-of-service)

## Your choices and controls

You can:

- **Revoke SuperBrain’s YouTube access** at any time in Google Account security settings:  
  [https://myaccount.google.com/permissions](https://myaccount.google.com/permissions)
- Stop playlist sync by disabling `[youtube_playlists]` in your local `categories.toml` or by not running sync commands
- Delete local SuperBrain data by removing the local database / runtime data directory on the host (for typical installs, under `~/.superbrain-server/`)
- Request deletion of Authorized Data retained by a deployment by contacting the operator of that instance (for a personal install, that is you)

After you revoke Google access, SuperBrain can no longer call YouTube on your behalf with that grant. Locally stored copies on the host should be deleted by the operator when access is revoked or when you no longer want the data retained (target: without undue delay, and in any event consistent with YouTube / Google developer policy timelines).

## Data retention

Local analysis and playlist mapping data are retained until the operator deletes them or resets the local runtime. OAuth tokens are retained until revoked or replaced. SuperBrain does not intentionally retain YouTube API Data longer than needed for the features above.

## Children

SuperBrain is not directed at children under 13, and operators should not use it to knowingly collect personal information from children.

## Changes to this policy

We may update this privacy policy in the `djbclark/superbrain` repository. The “Last updated” date at the top will change when we do. Continued use of YouTube-connected features after an update constitutes acceptance of the revised policy for those features.

## Contact

For privacy questions about this fork, open an issue on the public repository:

[https://github.com/djbclark/superbrain/issues](https://github.com/djbclark/superbrain/issues)

Or contact the repository owner via GitHub: [https://github.com/djbclark](https://github.com/djbclark)
