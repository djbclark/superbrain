# Config-driven category taxonomy proposal

## Purpose

Replace SuperBrain's fixed, code-owned category list with a user-owned taxonomy
that can classify both new and existing analyses without downloading videos or
re-running transcription. This proposal is intentionally an implementation
plan, not an approved schema or prompt. The next implementation agent must do
its own current prompting research and obtain operator confirmation before
changing classification behavior.

## Operator requirements captured so far

- Categories and classification guidance belong in user configuration, never in
  application code or prompts embedded in source.
- The configuration must support either one category per video or multiple
  categories.
- The configuration must support disabling all built-in/default categories.
- When a video can only receive one category, a matching user category wins
  over any default category.
- For this deployment, default categories are disabled and every video receives
  exactly one category.
- Requested user categories, from highest to lowest precedence:
  `Sysadmin`, `Science`, `Technology`, `History`, `Humanities`, `Politics`,
  `Other`.
- `Sysadmin` means development, software, tools, and technology the operator is
  likely to use functionally. `Technology` covers interesting but non-actionable
  technology news and discussion.
- Classification should retain model-proposed alternative categories that are
  outside the requested taxonomy, then produce batch-level suggestions for
  possible future taxonomy changes.

## Current-state findings

- `backend/main.py` contains a fixed `valid_categories` allow-list and a
  keyword fallback. It cannot preserve newly configured names.
- `backend/core/category_manager.py` is a MongoDB-era utility that references
  `db.collection`; it is not compatible with the active SQLite database and
  must not be used as the migration tool.
- The SQLite `analyses.category` field already supports a simple assigned
  category, and the mobile API supports updating one post at a time.
- Existing analyses have title, summary, tags, transcript data, and current
  category values. A taxonomy migration can use those fields and must not
  redownload media or redo Whisper/Gemini analysis.

## Proposed configuration contract

Use a local, ignored configuration file such as `config/categories.toml` (or a
documented XDG/SecretSpec-compatible equivalent). Check in only an example
file. The real local file must remain outside Git and must not contain API
credentials.

Illustrative shape only:

```toml
[taxonomy]
use_default_categories = false
allow_multiple_categories = false
fallback_category = "Other"

[[categories]]
name = "Sysadmin"
precedence = 1
guidance = "Actionable development, software, systems, tooling, and workflows the operator could use."

[[categories]]
name = "Science"
precedence = 2
guidance = "Scientific concepts, research, methods, and discoveries."

[[categories]]
name = "Technology"
precedence = 3
guidance = "Technology news or discussion that is interesting but not operationally actionable."

# History, Humanities, Politics, and Other follow in the confirmed precedence.
```

The implementation should validate unique names, positive/unique precedence,
presence of the fallback category, and the one-category invariant before any
analysis or migration begins. It should reject an empty effective taxonomy.

## Classification behavior

1. Build an effective taxonomy from configured user categories plus defaults
   only when `use_default_categories` is true.
2. Ask the model for structured output, constrained to the effective taxonomy:
   primary category, confidence, short rationale, and zero or more suggested
   categories outside the taxonomy.
3. Validate returned values locally. For one-category mode, select exactly one
   configured category. Resolve ambiguity by configured precedence; never rely
   on model ordering.
4. On malformed/low-confidence output, use the configured fallback category
   and record why. Do not silently revive the old hard-coded keyword list.
5. Preserve suggestion data separately from the assigned category. Suggestions
   must never affect filtering, totals, or the one-category invariant unless an
   operator explicitly adopts them.

Prompt wording and JSON schema require independent research and evaluation by
the next agent. The prompt should ask for evidence-based classification from
the analysis content, explain the Sysadmin/Technology distinction, and make
`Other` an explicit last resort rather than a convenient default.

## Data model proposal

Keep `analyses.category` as the compatibility-facing primary category. Add
separate metadata only after confirming the mobile API's needs, for example:

- `category_source` (`model`, `migration`, `manual`, `fallback`)
- `category_confidence` (nullable numeric)
- `category_rationale` (short text, optional)
- `category_suggestions_json` (JSON array of unadopted proposed categories)
- `category_taxonomy_version` (string or hash)
- `categorized_at` (ISO timestamp)

This retains existing clients while making assignments explainable and allowing
batch suggestion reports. Do not expose private prompt text or credentials to
the mobile client.

## Migration and rebuild design

The required operation is a metadata recategorization, not a destructive
database rebuild:

1. Take a SQLite backup and validate it before writing.
2. Load and validate the taxonomy configuration.
3. Generate a dry-run report for every visible analysis: old category,
   proposed category, confidence, rationale, suggestions, and reason for any
   fallback.
4. Require explicit operator approval of aggregate counts and a representative
   sample before applying writes.
5. Apply updates in bounded transactions with resumable progress markers.
6. Produce a final report: counts by assigned category, changes from prior
   category, low-confidence/fallback count, unknown/malformed count, and the
   aggregated alternative-category suggestions.
7. Support a rollback from the validated backup and do not alter media,
   transcripts, summaries, tags, collections, or soft-delete state.

For the initial taxonomy, expect `Other` to be meaningful and reviewable. Do
not force a high-confidence categorization solely to reduce its count.

## Batch suggestion report

Aggregate model suggestions only when they are not one of the effective
taxonomy categories. Normalize case/whitespace, count frequency, retain a few
representative shortcodes/rationales, and require a minimum threshold before
presenting a candidate. The report is advisory: adding a new category remains
an operator decision followed by a new dry run.

## API and mobile compatibility

- Existing `category` consumers continue to receive one primary string.
- Add new metadata fields only as optional additive API fields.
- Ensure the mobile app's local schema/migrations tolerate the additive fields
  or deliberately omit them from its lightweight sync response until supported.
- Category listings must derive from effective assigned categories and exclude
  soft-deleted posts, preserving current behavior.

## Validation plan

- Unit tests: configuration parsing, invalid taxonomy rejection, precedence,
  one-category invariant, default-category enable/disable behavior, malformed
  model output, suggestion extraction, and fallback behavior.
- Migration tests: dry run makes no writes; apply changes only category metadata;
  resume/rollback behavior; exact backup restoration.
- Contract tests: backend `/categories`, post update, sync, and mobile local
  database compatibility.
- Prompt evaluation: a labeled fixture set covering Sysadmin vs Technology,
  Science vs Technology, History vs Humanities, Politics overlap, and genuine
  Other cases. Compare accuracy, fallback rate, and suggestion quality before
  production migration.
- Operational gate: operator reviews dry-run report before any live update.

## Explicit non-goals

- No automatic creation of new user categories from model suggestions.
- No use of category prompts as a place to store secrets.
- No media re-download, transcript regeneration, or full analysis rebuild for
  a taxonomy-only change.
- No deletion or overwrite of the user's local taxonomy configuration during
  deployment.
