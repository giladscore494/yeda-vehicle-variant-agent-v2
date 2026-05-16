"""Targeted canonical repair: reset invalid zero-variant closure seeds to needs_retry.

These seeds were processed during a problem_queue run but their
zero_variant_resolution.source_ids contained only placeholder IDs (src_1, src_2, …),
making the "proven" proof_status invalid.

Audit results (2026-05-16, guard: proof_guard_v3_validate_canonical_placeholder_check):
  - hyundai__creta__2020__2026__il  → ALREADY RESET (in needs_retry, status=reset_for_rerun)
  - geely__coolray__2023__2026__il  → VALID closure — re-run with non-placeholder
                                       source_ids ["src_walla_cars", "src_autoboom_il"];
                                       proof_status=proven, confidence=high. EXCLUDED.
  - fiat__freemont__2011__2016__il  → INVALID (placeholder sources) — reset to needs_retry
  - ford__ecosport__2013__2022__il  → INVALID (placeholder sources) — reset to needs_retry

Seeds handled by this script (idempotent — safe to re-run):
  - hyundai__creta__2020__2026__il
  - fiat__freemont__2011__2016__il
  - ford__ecosport__2013__2022__il

Usage:
    python tools/reset_invalid_placeholder_closures.py [--dry-run] [--canonical PATH]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CANONICAL_PATH = REPO_ROOT / "data/canonical/resume_package_canonical.json"

# -------------------------------------------------------------------------
# Seeds to repair
# -------------------------------------------------------------------------

# geely__coolray__2023__2026__il is intentionally excluded: it was re-run with
# non-placeholder source_ids ["src_walla_cars", "src_autoboom_il"] (proof_status=proven,
# confidence=high) and is a VALID zero-variant closure.  Do not reset it.
INVALID_PLACEHOLDER_CLOSURE_SEEDS = [
    "hyundai__creta__2020__2026__il",
    "fiat__freemont__2011__2016__il",
    "ford__ecosport__2013__2022__il",
]


# -------------------------------------------------------------------------
# Core repair logic (testable without CLI)
# -------------------------------------------------------------------------

def apply_reset(data: dict, now_ts: str) -> dict:
    """Apply the placeholder-closure reset to *data* in-place.

    For each of the 4 invalid seeds:
    - removes from processed_seed_ids and processed_seeds
    - adds to needs_retry_seed_ids (if not already present)
    - updates seed_accounting: status, marked_processed, reset_reason, reset_at
    - marks zero_variant_resolution.proof_status = "invalid_placeholder_sources"
    - removes from no_variants_by_seed
    - adds to false_processed_seed_ids

    Updates counts.processed_seeds and counts.needs_retry_seeds to match.
    Sets active_mode = "rerun_queue_required".

    Parameters
    ----------
    data : dict
        Parsed canonical JSON (mutated in-place).
    now_ts : str
        ISO-8601 UTC timestamp string for reset_at fields.

    Returns
    -------
    dict
        The mutated *data* dict.
    """
    bs = data.setdefault("batch_state", {})

    # Ensure required sub-structures exist.
    bs.setdefault("processed_seed_ids", [])
    bs.setdefault("processed_seeds", [])
    bs.setdefault("needs_retry_seed_ids", [])
    bs.setdefault("false_processed_seed_ids", [])
    bs.setdefault("seed_accounting", {})
    bs.setdefault("no_variants_by_seed", {})
    data.setdefault("counts", {})

    processed_ids: list = bs["processed_seed_ids"]
    needs_retry: list = bs["needs_retry_seed_ids"]
    false_processed: list = bs["false_processed_seed_ids"]
    seed_accounting: dict = bs["seed_accounting"]
    no_variants_by_seed: dict = bs["no_variants_by_seed"]

    for seed_id in INVALID_PLACEHOLDER_CLOSURE_SEEDS:
        # 1. Remove from processed_seed_ids.
        if seed_id in processed_ids:
            processed_ids.remove(seed_id)

        # 2. Remove from processed_seeds (list of strings or dicts).
        bs["processed_seeds"] = [
            s for s in bs["processed_seeds"]
            if (s if isinstance(s, str) else s.get("seed_id", "")) != seed_id
        ]

        # 3. Add to needs_retry_seed_ids (idempotent).
        if seed_id not in needs_retry:
            needs_retry.append(seed_id)

        # 4. Update seed_accounting.
        entry: dict = seed_accounting.get(seed_id) or {}
        entry["seed_id"] = seed_id
        entry["status"] = "reset_for_rerun"
        entry["marked_processed"] = False
        entry["reset_reason"] = "placeholder_source_ids_invalidated_zero_variant_closure"
        entry["reset_at"] = now_ts
        # Mark the ZVR as invalid rather than deleting it (preserves audit trail).
        zvr = entry.get("zero_variant_resolution")
        if isinstance(zvr, dict):
            zvr["proof_status"] = "invalid_placeholder_sources"
        seed_accounting[seed_id] = entry

        # 5. Remove from no_variants_by_seed.
        no_variants_by_seed.pop(seed_id, None)

        # 6. Add to false_processed_seed_ids (idempotent).
        if seed_id not in false_processed:
            false_processed.append(seed_id)

    # 7. Update counts.
    data["counts"]["processed_seeds"] = len(bs["processed_seed_ids"])
    data["counts"]["needs_retry_seeds"] = len(bs["needs_retry_seed_ids"])

    # 8. Set active_mode.
    bs["active_mode"] = "rerun_queue_required"

    return data


# -------------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Reset invalid placeholder-closure seeds back to needs_retry in canonical. "
            "Geely Coolray is excluded (valid non-placeholder sources confirmed)."
        )
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without writing anything to disk.",
    )
    parser.add_argument(
        "--canonical", default=str(CANONICAL_PATH),
        help="Path to canonical JSON (default: data/canonical/resume_package_canonical.json).",
    )
    args = parser.parse_args()

    path = Path(args.canonical)
    if not path.exists():
        print(f"ERROR: canonical not found at {path}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(path.read_text(encoding="utf-8"))

    # Snapshot before counts.
    bs_before = data["batch_state"]
    processed_before = len(bs_before.get("processed_seed_ids", []))
    needs_retry_before = len(bs_before.get("needs_retry_seed_ids", []))
    variants_before = len(
        data.get("accumulated_clean_export", {}).get("variants", [])
    )
    active_mode_before = bs_before.get("active_mode")

    now_ts = datetime.now(timezone.utc).isoformat()

    apply_reset(data, now_ts)

    # Snapshot after counts.
    bs_after = data["batch_state"]
    processed_after = len(bs_after.get("processed_seed_ids", []))
    needs_retry_after = len(bs_after.get("needs_retry_seed_ids", []))
    variants_after = len(
        data.get("accumulated_clean_export", {}).get("variants", [])
    )
    active_mode_after = bs_after.get("active_mode")

    # --- Report ---
    print("\n=== Invalid Placeholder Closure Reset Report ===")
    if args.dry_run:
        print("(DRY RUN — no changes written)")
    print(f"\nSeeds handled ({len(INVALID_PLACEHOLDER_CLOSURE_SEEDS)}):")
    for s in INVALID_PLACEHOLDER_CLOSURE_SEEDS:
        print(f"  [placeholder_closure]  {s}")
    print(f"\nProcessed seeds:   {processed_before} -> {processed_after}")
    print(f"needs_retry seeds: {needs_retry_before} -> {needs_retry_after}")
    print(f"active_mode:       {active_mode_before!r} -> {active_mode_after!r}")
    print(f"Variants:          {variants_before} (unchanged: {variants_before == variants_after})")
    print(f"next_seed_id:           {bs_after.get('next_seed_id')}")
    print(f"last_completed_seed_id: {bs_after.get('last_completed_seed_id')}")

    if args.dry_run:
        print("\n(Dry run — no changes written)")
        return

    # Create timestamped backup before writing.
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"resume_package_canonical_backup_{ts}.json")
    shutil.copy2(path, backup)
    print(f"\nBackup written to: {backup}")

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Canonical updated.")

    # Post-write validation.
    try:
        reloaded = json.loads(path.read_text(encoding="utf-8"))
        ace = reloaded.get("accumulated_clean_export") or {}
        print(f"Post-write check: {len(ace.get('variants', []))} variants loaded OK.")
    except Exception as exc:
        print(f"WARNING: post-write validation failed: {exc}", file=sys.stderr)

    print("\nSafe to continue: problem_queue rerun required for the 3 reset seeds. "
          "geely__coolray__2023__2026__il has a valid non-placeholder closure and "
          "remains processed.")


if __name__ == "__main__":
    main()
