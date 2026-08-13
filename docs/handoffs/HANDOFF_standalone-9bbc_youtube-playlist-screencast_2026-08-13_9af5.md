---
schema_version: 1
handoff_id: 9af5
parent_handoff_ids: []
lineage: none
chain: [standalone-9bbc]
repo: superbrain
workspace: superbrain
branch: main
head_sha: 0e395b2c72fba7ab8d328758480b8292250e6343
created_at: 2026-08-13T19:21:50-04:00
writer: claude-code
---

# Handoff — fork staleness fix (done) + YouTube playlist-sync screencast for Google scope verification (planning, not started)

## The Goal

Two sequential tasks in this session, in priority order the operator set:

1. **Done**: check whether `superbrain` needed a merge from the upstream
   remote (`sidinsearch/superbrain`), and separately fix branch/staleness
   issues the operator suspected.
2. **In progress, nothing executed yet**: produce a real screen-recorded
   video (not a GIF) demonstrating SuperBrain's server-side YouTube
   category-playlist sync feature, for a Google OAuth restricted-scope
   verification submission tied to the existing quota-extension form at
   `docs/YOUTUBE_API_QUOTA_HANDOFF.md`.

## Where We Are

- Working tree is clean. `main` is at `0e395b2`, already pushed to
  `origin/main` (confirmed: `a363d22..0e395b2 main -> main`).
- `draft/android-category-sync` is untouched, still tracking upstream PR
  #7 (open). Do not merge, rebase, or delete it.
- No screen recording, browser automation, or live YouTube API call has
  been made yet for task 2 — everything so far is research and planning
  (grep/Read only, plus one read-only inspection of a private doc in
  `~/ops/site-private`, which is explicitly allowed per that repo's
  CLAUDE.md carve-out for read-only inspection).

### Task 1 detail (complete)

- `upstream/main` (2f68fff) is a full ancestor of local/origin `main` —
  confirmed via `git merge-base --is-ancestor upstream/main main`. **No
  upstream merge was or is needed.** `upstream/beta` likewise fully
  merged (0 commits ahead).
- The operator's suspicion that `draft/android-category-sync` was stale
  cruft was **wrong** — it's the live head branch of **open** upstream PR
  `sidinsearch/superbrain#7`. Confirmed via
  `gh pr list --repo sidinsearch/superbrain --state open` and
  `gh pr list --repo sidinsearch/superbrain --state all --author djbclark`.
  It must never be merged/rebased against `main` or deleted while that PR
  is open.
- `draft` is deliberately kept diffed against the old upstream base
  (merge-base `2f68fff`, same as `upstream/main`'s tip) so PR #7 stays a
  clean, minimal, Android-only diff — per the fork's own commit
  `2585c26 docs: record upstream PR #7 rebuilt as Android-only`.
- There is established precedent for handling this divergence: commit
  `c195a38` on `main` already **manually ported** an earlier draft fix
  (`5975a02`) into `main`'s current (differently-structured) files,
  rather than merging branches.
- Found 3 later draft-only commits that had **not** been ported:
  `878a1dc`, `d629ccf`, `3acb694` (delta-sync pagination safety /
  non-advancing-cursor detection, cursor-advance-on-incomplete-page fix,
  and a real bug: unstable `ORDER BY updated_at` OFFSET pagination when
  timestamps tie, plus a taxonomy double-fetch race in `HomeScreen.tsx`).
  Verified with `git show main:<path>` (not the working tree, which was
  on `draft` at the time) that `main` genuinely still had these bugs
  before the fix.
- Manually ported the fixes into `main`'s current file structure (not a
  git merge — `draft`'s `backend/api.py` is drastically older/smaller
  than `main`'s, a raw merge would have been a mess). Files changed:
  `backend/api.py`, `backend/core/database.py`,
  `backend/tests/test_database_concurrency.py`,
  `superbrain-app/src/screens/HomeScreen.tsx`,
  `superbrain-app/src/services/api.ts`,
  `superbrain-app/src/services/syncService.ts`.
- Verification before commit: `npm install` (node_modules was missing),
  `npx tsc --noEmit` → clean, no errors. Backend:
  `python3 -m unittest discover -s backend/tests -p "test_*.py"` → 83
  tests, 4 pre-existing errors (`test_access_token`,
  `test_pending_new_priority_and_enqueue_on_budget`,
  `test_gemini_uses_installed_google_genai_sdk`, `test_websub_notifier`)
  confirmed present **before** the change too via `git stash` +
  re-running — not a regression.
  `backend/tests/test_database_concurrency.py::test_get_posts_since_supports_stable_page_offsets`
  updated for the new `(results, has_more)` tuple return shape and
  passes.
- Reverted an incidental `superbrain-app/package-lock.json` version drift
  (`1.0.1` → `2.0.0`) that `npm install` introduced — unrelated,
  `git checkout -- superbrain-app/package-lock.json` before committing.
- Committed as `0e395b2 fix: port real /sync pagination + taxonomy dedup
  fixes from PR #7` (full message explains the port rationale and lists
  each of the 5 sub-fixes). Pushed to `origin/main` after explicit
  operator go-ahead ("yes, push.").

## What We Tried

- **Rejected approach, caught before acting**: my first instinct was to
  suggest merging/rebasing `main` into `draft/android-category-sync` to
  fix the staleness. The operator pushed back ("make sure that is the
  right thing to do first"), which is what triggered the deeper
  investigation above. That instinct was wrong — it would have dumped 57
  unrelated fork commits (including a full `backend/api.py`
  restructuring) into the open upstream PR #7 diff. Correct direction
  was the reverse: port draft's newer fixes into `main`, leave `draft`
  alone.
- Confirmed via `git blame`/`git show main:<path>` (not just reading the
  working tree) that the bugs the draft-only commits fixed were **still
  live in `main`** before porting — this was necessary because at one
  point the working tree was checked out to `draft`, and reading files
  directly (via the `Read` tool) at that moment would have silently
  shown the wrong branch's content. Always `git show <rev>:<path>` when
  comparing across branches rather than trusting the working tree.

## Key Decisions

- **Keep `draft/android-category-sync` exactly as-is.** Rejected:
  merging `main` into it (see above), deleting it (it's an open PR
  head), rebasing it onto `main` (same pollution problem).
- **Port, don't merge, the 3 unported draft fixes into `main`.** Chosen
  because it matches existing precedent (`c195a38`) and because the file
  structures have diverged too far for a clean automatic merge.
- **For the screencast (task 2), GIF format was explicitly rejected** by
  the operator's own reasoning, which I confirmed: (a) GIF has no
  efficient inter-frame compression and is capped at 256 colors/frame —
  a multi-minute terminal+browser capture would be tens–hundreds of MB
  vs. a few MB for H.264; (b) more fundamentally, the `gif_creator`
  browser-automation tool can only capture *inside a Chrome tab* — it
  physically cannot capture a terminal pane sitting next to the browser,
  which is exactly the split-screen shot the operator wants. Proposed
  instead: macOS's built-in `screencapture -v` (drivable via the Bash
  tool, produces a real `.mov`) as an alternative to the operator running
  QuickTime's screen recording manually. **Not yet decided which of
  those two the operator wants** — see Where We're Going.
- **Demo must NOT involve the Android app** — the operator explicitly
  corrected this scope assumption. The relevant feature lives entirely
  server-side: `backend/core/category_playlists.py` (historic
  migration/backfill of existing videos into per-category YouTube
  playlists) and the WebSub push-notification path in `backend/api.py`
  (auto-adding newly-uploaded subscription videos as they arrive). The
  operator wants a split screen: terminal showing server logs on one
  side, a browser showing the actual YouTube playlists being
  created/updated live on the other.
- **Interaction-style note (worth remembering for this operator)**: a
  4-question `AskUserQuestion` batch and later a 2-question batch were
  both rejected in this session — the operator preferred to explain
  their intent in free text rather than pick from a structured multi-part
  menu, and one rejection came with explicit meta-feedback ("The user
  wants to clarify these questions... take their response into account").
  Lean toward plain conversational questions (one at a time, or folded
  into prose) rather than large `AskUserQuestion` batches with this
  operator, at least for open-ended/creative-judgment planning topics
  like recording tooling choices.

## Evidence & Data

Research was done via a background `Explore` subagent
(`a5785a726a395608d`, ~112K tokens, 56 tool uses) plus direct
verification by the main session. Key facts, with file:line references,
needed to actually run the demo:

**Flow (a) — historic backfill/migration into category playlists**
(`backend/core/category_playlists.py`, CLI wiring in `backend/main.py:623-855`):
- `superbrain --youtube-connect` — already done for this operator's test
  account per their confirmation ("already authorized" in the rejected
  2-question `AskUserQuestion`, one answer of which still landed: yes,
  already authorized, backend can reach the real API now).
- `superbrain --category-playlists-status` and
  `superbrain --youtube-quota-stats` — pre-flight checks, **not yet run**.
  Recommended before recording to confirm today's quota ledger is fresh.
- `superbrain --sync-category-playlists --sync-category-playlists-limit 5`
  — foreground one-shot, best for a demo (streams output directly,
  small limit avoids the daily historic-backfill quota cap of ~1000
  units/day ≈ 20 `playlistItems.insert` calls outside the near-reset
  window — see `category_playlists.py:72-76`).
- Cleanest **single-item, individually-narratable** demo action:
  `POST /api/youtube/category-playlists/sync/{shortcode}`
  (`backend/api.py:2747`) — returns JSON showing the exact mutation
  (`"op": "add"`/`"remove"`) performed.
- Important beat: `--youtube-connect` **auto-enables** playlist sync and
  immediately creates/adopts category playlists
  (`activate_after_oauth()`, `category_playlists.py:219-229`) — playlists
  get created on YouTube the moment OAuth completes, before any backfill
  runs. Since this operator's account is already connected, playlists
  likely already exist; may need a fresh category or an incremental add
  to show a visible "before/after" rather than creation from zero.
- Getting videos into SuperBrain's local DB in the first place uses
  `superbrain --playlist <URL> [--cookies chrome]` (yt-dlp scrape, **not**
  the YouTube Data API — `analyzers/playlist_analyzer.py`). The
  OAuth-scoped API calls only start at the category-playlist-sync step,
  so that's the actual "scope in use" moment to narrate, not the import
  step.
- Best log lines to narrate on camera:
  - Per-item, real-time: `logger.info("[category-playlists] %s %s",
    shortcode, result.get("actions"))` — e.g.
    `[category-playlists] YT_xxx [{'op': 'add', 'category': 'Sysadmin',
    'playlist_id': 'PLxxxx', 'playlist_item_id': 'UExxxx', 'video_id':
    'xxx', 'position': 0}]` (`category_playlists.py:1196-1236`, fires on
    the live single-video analysis / category-edit path, debounced ~2s).
  - Batch progress (1st + every 25th item only —
    **not per-item**, plan the narration around this gap):
    `[sync] ok=3 skipped=0 failed=0 last=YT_xxx pri=new units=150 hist=0
    phase=normal` (`category_playlists.py:1899-1908`).
  - Final summary JSON at end of a `--sync-category-playlists` run:
    `category_playlists.py:1929-1944`.
- Endpoints/scopes actually called
  (`category_playlists.py:391-576`, `YouTubePlaylistClient`):
  `playlists.list` (1 unit), `playlists.insert` (50), `playlistItems.insert`
  (50), `playlistItems.delete` (50) — matches the quota-extension form's
  "Selected endpoints" exactly (`docs/YOUTUBE_API_QUOTA_HANDOFF.md:87-91`).

**Flow (b) — WebSub push → auto-add to category playlist**
(`backend/api.py:2090-2536`):
- No CLI simulate command exists. Real endpoints:
  `GET/POST /api/youtube/webhook` (Google's actual push, HMAC-verified via
  `WEBSUB_HMAC_SECRET`, `backend/core/websub_notifier.py`),
  `POST /api/youtube/subscribe` (subscribes via Google's real
  `pubsubhubbub.appspot.com` Hub).
- Two options for demoing this flow, **operator has not chosen yet**:
  - **(A) Real** — publish/upload a short unlisted video on a channel
    the connected account is subscribed to, wait for Google's real Hub
    push (near-real-time, usually well under a couple minutes). More
    credible for a Google reviewer. Log sequence to expect: `🔔 [WebSub]
    Received real-time YouTube upload push notification from Google
    Hub!` → `📹 [WebSub Push] New Video Upload: '{title}'
    ({video_url}) [Channel: {channel_id}]` (`api.py:2366,2374`) → queue
    processing lines (`api.py:242-288`) → the same
    `[category-playlists] ... 'op': 'add' ...` line as flow (a) once
    analysis finishes.
  - **(B) Simulated** — hand-craft a signed Atom XML payload (structure
    at `backend/tests/test_websub_notifier.py:75-91`) and POST it to the
    server's own `/api/youtube/webhook` with header `X-Hub-Signature:
    sha256=<hmac-sha256-hex-of-body>`. Instant/controllable, but must be
    narrated on camera as simulated if used — the `channel_id` in the
    payload must already have an `active` row in the `websub_subscriptions`
    table (`api.py:2376-2381`), i.e. a real prior
    `POST /api/youtube/subscribe` for that channel is a prerequisite
    either way.
  - I recommended (A) if a test channel is available, for credibility
    with reviewers, but left the final call to the operator.

**Reconciled a research gap**: the Explore subagent's report flagged
that `youtube.subscriptions.list` appears on the quota-extension form's
selected-endpoints list but couldn't find it called anywhere in the
WebSub code it searched. I checked directly and confirmed it **is**
called — `backend/core/youtube_oauth.py:115` `list_subscriptions()` →
`SUBSCRIPTIONS_URL` (line 24), instrumented via
`core.youtube_quota.instrumented_request` — exposed through
`POST /api/youtube/subscriptions/discover` (`backend/api.py:2642-2649`).
So all 5 form endpoints are genuinely exercised; the demo should include
hitting `/discover` to show that specific call, since it isn't triggered
automatically by anything else in the flows above.

**Existing compliance/verification artifacts** (no prior screencast plan
existed — built the script above from scratch):
- `docs/YOUTUBE_API_QUOTA_HANDOFF.md` — the quota-extension form record
  this screencast is presumably supporting. Business description text
  there ("helps people manage the videos they want to watch from their
  YouTube subscriptions...") is nearly verbatim what the operator quoted
  when introducing this task — strong signal this screencast is for that
  same or a directly-related Google review thread.
- `~/ops/site-private/memory/reference_superbrain_youtube_api_quota_form.md`
  (read read-only, per that repo's explicit carve-out) — contains only
  private contact/project-number fields, **no additional screencast
  requirement text** found there.
- No file anywhere in the repo (or the private supplement) specifies an
  exact required video format, length, or submission mechanism for
  Google's review — this needs to be confirmed with whatever Google
  correspondence prompted this request (not visible in-repo).

## Operator Feedback

- Wants git/branch decisions double-checked before execution,
  especially anything touching open PRs — "make sure that is the right
  thing to do first" led directly to catching the wrong initial
  suggestion (see Key Decisions). Apply this generally: verify branch
  purpose (check for associated open PRs via `gh pr list`) before
  proposing any merge/rebase/delete on this repo.
- Wants explicit confirmation before `git push` even after already
  approving the work that produced the commit — asked "push?" and
  waited for an explicit "yes, push." rather than pushing automatically
  once code was ready.
- Prefers free-text clarification over large `AskUserQuestion` menus for
  open-ended planning — see the interaction-style note under Key
  Decisions above.
- Corrected scope firmly and specifically ("This is not for the android
  client at all") when I defaulted to assuming the mobile app was in
  scope for the screencast — don't assume which surface (mobile vs.
  server vs. web) a feature request applies to when the repo has
  multiple; ask or infer narrowly from the specific YouTube-API code
  path being discussed.

## Where We're Going

1. **Next action**: get the operator's answer on the two still-open
   screencast logistics questions (asked, not yet answered at end of
   session):
   - Recording mechanism: drive `screencapture -v` via Bash (real
     `.mov`, I start/stop it) vs. operator runs QuickTime's screen
     recording themselves and tells me when it's rolling.
   - Segment 2 (WebSub) approach: real upload + wait for genuine Google
     Hub push (A) vs. simulated signed webhook POST with honest on-camera
     framing (B).
2. Once both are answered: run pre-flight checks
   (`superbrain --category-playlists-status`,
   `superbrain --youtube-quota-stats`) to confirm the quota ledger is
   fresh before any live recording.
3. Record Segment 1 (subscriptions → categorized playlists) per the
   script in Evidence & Data: `/discover` call → existing YouTube state
   → `--sync-category-playlists --sync-category-playlists-limit 5` →
   browser refresh showing new playlists → optional single-item
   `POST /api/youtube/category-playlists/sync/{shortcode}` for a clean
   one-call narration.
4. Record Segment 2 (WebSub auto-organize) per whichever option (A) or
   (B) the operator picked.
5. Review the resulting video with the operator before it's used in any
   Google submission — nothing here should go to Google without explicit
   operator sign-off, per the general external/high-stakes-action policy.

## Quick Start

```bash
cd /Users/djbclark/src/superbrain
git status                       # should be clean, on main, at 0e395b2
git log --oneline -3              # confirm 0e395b2 is HEAD and pushed

# Pre-flight before recording anything:
cd backend
python3 main.py --category-playlists-status
python3 main.py --youtube-quota-stats

# Segment 1 demo commands (once logistics decided):
python3 main.py --sync-category-playlists --sync-category-playlists-limit 5
# or, for a single clean call:
curl -s -X POST "http://127.0.0.1:5000/api/youtube/category-playlists/sync/<shortcode>" \
  -H "X-API-Key: $(cat backend/token.txt)"
curl -s -X POST "http://127.0.0.1:5000/api/youtube/subscriptions/discover" \
  -H "X-API-Key: $(cat backend/token.txt)"

# If driving the recording myself:
screencapture -v /path/to/output.mov &   # start; note the PID to stop later
```

Re-read this file plus `docs/YOUTUBE_API_QUOTA_HANDOFF.md` and
`docs/CATEGORY_YOUTUBE_PLAYLISTS.md` before resuming — the quota-form doc
has the exact business narrative Google already saw, and the category
playlists doc has the full CLI reference beyond what's summarized above.
