# Standalone taxonomy test environment

This fork’s live runtime is `~/.superbrain-server` (LaunchAgent
`com.djbclark.superbrain`, port **5000**). Do not deploy or recategorize there
until the operator explicitly approves cutover.

## Test runtime

| Item | Value |
|------|-------|
| Dir | `~/.superbrain-server-test` |
| Port | `5055` |
| DB | `~/.superbrain-server-test/superbrain.db` (seeded fixtures only) |
| Taxonomy | `~/.superbrain-server-test/config/categories.toml` |
| Token | `~/.superbrain-server-test/token.txt` |

```bash
backend/scripts/test-env.sh setup
backend/scripts/test-env.sh validate    # no AI tokens
backend/scripts/test-env.sh test        # unit tests
backend/scripts/test-env.sh start
backend/scripts/test-env.sh smoke
backend/scripts/test-env.sh status
backend/scripts/test-env.sh promote-plan
# later, when operator says so:
backend/scripts/test-env.sh teardown
```

## Reference database (token-saving)

The test environment can read transcripts/titles/summaries from another
SuperBrain SQLite file (usually production) via:

```bash
export SUPERBRAIN_REFERENCE_DATABASE_PATH="$HOME/.superbrain-server/superbrain.db"
```

`scripts/recategorize.py playlists` uses that reference DB before spending any
capped metadata-fetch budget (`--missing-ai-timeout`, default 20s) on videos
that are not present locally.

Local omlx on port 8000 is configured through `OMLX_HOST` /
`SUPERBRAIN_API_KEYS_FILE` (see `~/.superbrain-server-test/config/`).

## Android UI

- `GET /taxonomy` returns configured category names even at zero count
- Home filter pills merge taxonomy + counts (case-insensitive)
- Post edit picker loads taxonomy and saves the display name (`Sysadmin`, …)

Point a debug build at `http://<lan-ip>:5055` with the test token to exercise
UI without touching live.

## Promote / teardown

See `backend/scripts/test-env.sh promote-plan`. Important: copy **code +
categories.toml + recategorize live metadata** — do **not** replace the live
database with the tiny seeded test DB.
