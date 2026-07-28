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

## Why no full AI dry-run here

`recategorize.py dry-run` classifies every row with the model router and would
spend tokens across the live corpus. The test env instead:

1. Installs the example taxonomy
2. Validates config
3. Seeds a handful of fixture posts already labeled with the new categories
4. Smoke-tests `/taxonomy` and `/categories` for Android UI wiring

Full corpus dry-run/apply belongs in the **promote** step against a backed-up
live DB, after approval.

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
