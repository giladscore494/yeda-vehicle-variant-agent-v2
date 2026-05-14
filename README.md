# Yeda Vehicle Variant Agent v2

Clean, minimal engine that generates and curates vehicle variants for the
Israeli car market, using Gemini for discovery. There is exactly **one**
source of truth: `data/canonical/resume_package_canonical.json`.

## Architecture

```
app.py                                 Streamlit UI (3 tabs, no repair buttons)
agent/
  runner.py                            seed -> Gemini discovery -> variant dicts
  discovery.py                         Gemini JSON parsing / candidate extraction
  prompts.py                           discovery prompt builders
engine/
  canonical_store.py                   load / validate / atomic save canonical
  queue.py                             pick next seed; compute problem progress
  merge_variants.py                    dedupe + merge new variants into canonical
  run_next.py                          full flow incl. save-then-advance rule
core/
  schemas.py                           pydantic schemas for variants
  variant_id.py                        deterministic variant_id slug
  validators.py                        variant validation + classification
  normalize.py                         field normalization helpers
  conflict_detector.py                 cross-variant conflict detection
  final_export_builder.py              clean diagnostic view
tools/
  gemini_client.py                     Gemini wrapper + JSON salvage
storage/
  github_canonical_store.py            push canonical to GitHub (only)
data/
  canonical/
    resume_package_canonical.json      THE source of truth
  seeds/
    vehicle_model_seeds_il.json        Stable IL seed catalog (993 seeds)
tests/
  test_engine_flow.py                  contract tests for the engine
```

## Invariants

- Canonical is the only state. There is no `batch_state.json`, no
  `rerun_queue.json`, no `latest_batch_result.json`, no `problem_queue.json`.
- Seed selection is decided by `engine.queue.select_next_seed(canonical)`:
  - if `batch_state.needs_retry_seed_ids` is not empty → mode is
    `problem_queue`, selected seed is `needs_retry_seed_ids[0]`.
  - otherwise → mode is `normal_batch`, selected seed is
    `batch_state.next_seed_id`.
- Normal-batch cursor advancement requires a stable seed catalog from
  `data/seeds/vehicle_model_seeds_il.json` (or the alternate explicit path
  `data/seed_catalog_il.json`) or an embedded canonical fallback.
- During problem-queue runs the normal cursor
  (`next_seed_id`, `last_completed_seed_id`) is FROZEN.
- Problem-queue progress is computed dynamically every load — never persisted
  as state. `original_problem_total` is `54` for the initial repair run.
- A seed is only resolved (removed from `needs_retry`, added to
  `processed_seed_ids`, recorded in `seed_accounting`) AFTER canonical save
  succeeds. If save fails, no progress is advanced.

## Running

```bash
pip install -r requirements.txt
python -m pytest -q
streamlit run app.py
```

## Configuration

The Gemini and GitHub clients read their credentials from Streamlit secrets
(or environment variables for `GEMINI_API_KEY`):

```
GEMINI_API_KEY = "..."
GEMINI_MODEL_FAST = "gemini-3-flash-preview"
GEMINI_MODEL_STRONG = "gemini-3-pro-preview"
GITHUB_TOKEN = "..."
GITHUB_REPO = "owner/repo"
GITHUB_BRANCH = "main"
CANONICAL_RESUME_PATH = "data/canonical/resume_package_canonical.json"
```
