# Handoff: SuperBrain category taxonomy proposal

Start by reading
[`CATEGORY_TAXONOMY_PROPOSAL.md`](CATEGORY_TAXONOMY_PROPOSAL.md). It contains
the detailed current-state findings, proposed configuration contract, migration
design, data model, compatibility requirements, and validation plan.

This is a planning/implementation handoff for the category-taxonomy feature
only. Do not infer authorization to change unrelated import, deployment, mobile
release, credential, database, or pull-request state.

## Required first actions

Before changing code or data:

1. Independently research current best practices for structured LLM
   classification prompts, constrained taxonomy output, confidence calibration,
   and batch evaluation. Prefer authoritative primary documentation for any
   provider/model-specific claims.
2. Inspect the current fork at `/Users/djbclark/src/superbrain`, particularly
   `backend/main.py`, `backend/core/database.py`, `backend/api.py`, and the
   mobile sync/client surfaces that consume `category`.
3. Talk to the operator first. Confirm taxonomy wording, precedence, category
   names, fallback policy, confidence threshold, prompt/evaluation approach,
   alternative-category suggestion threshold, migration sample size, and
   rollback approval.
4. Present an implementation plan, migration plan, and test plan. Wait for
   operator approval before changing live category data.

## Captured operator requirements

- Categories and category prompts must live in user configuration, not source
  code.
- The configuration must support enabling/disabling default categories.
- The configuration must support single-category and multi-category modes.
- In single-category mode, a matching user category takes precedence over a
  default category.
- This deployment disables defaults and requires exactly one category per
  video.
- Requested category precedence: `Sysadmin`, `Science`, `Technology`,
  `History`, `Humanities`, `Politics`, `Other`.
- `Sysadmin` means actionable development, software, systems, tools, and
  technology the operator would functionally use. `Technology` covers
  interesting but non-actionable technology news/discussion.
- The model should record proposed categories outside the configured taxonomy
  separately and produce advisory batch-level suggestions; it must never add
  categories automatically.

## Hard boundaries

- Do not use `backend/core/category_manager.py` as the migration path; it is a
  stale MongoDB-era utility and is incompatible with active SQLite use.
- Use `backend/scripts/recategorize.py` only. Command sequences:
  [`RECATEGORIZE.md`](RECATEGORIZE.md).
- Do not perform a destructive database rebuild. Recategorize existing metadata
  only; do not redownload videos or regenerate transcripts/analyses for a
  taxonomy-only change.
- Require backup validation, dry run, operator review, bounded/resumable writes,
  and rollback capability before applying a live migration.
- Preserve existing `analyses.category` compatibility for API/mobile consumers.
- Do not commit or expose credentials, cookies, access tokens, runtime database
  files, or the user's real local category configuration.
