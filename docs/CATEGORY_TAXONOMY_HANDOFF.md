# Handoff: config-driven category taxonomy

Read [`CATEGORY_TAXONOMY_PROPOSAL.md`](CATEGORY_TAXONOMY_PROPOSAL.md) first.

You are taking over a proposed category-taxonomy feature for SuperBrain. Do not
start implementation immediately. First:

1. Independently research current best practices for structured LLM
   classification prompts, taxonomy-constrained output, confidence calibration,
   and batch evaluation. Use authoritative primary documentation where
   applicable.
2. Inspect the current fork at `/Users/djbclark/src/superbrain`, especially
   `backend/main.py`, `backend/core/database.py`, `backend/api.py`, and
   `superbrain-app/src/services/`.
3. Talk to the operator before changing any code. Confirm the taxonomy wording,
   precedence, category names, desired confidence/fallback policy, suggestion
   report threshold, migration scope, and rollback approval.
4. Present a concise implementation plan and test plan. Wait for approval
   before applying a migration to live data.

The operator's currently requested configuration is: defaults disabled; exactly
one category per video; precedence `Sysadmin`, `Science`, `Technology`,
`History`, `Humanities`, `Politics`, `Other`. The plan must preserve this as
configuration, not source-embedded policy. Do not copy, print, or commit API
keys, tokens, browser cookies, local database files, or personal configuration.
