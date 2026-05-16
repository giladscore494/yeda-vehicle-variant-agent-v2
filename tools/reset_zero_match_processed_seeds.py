"""Targeted canonical repair: reset 6 zero-match processed seeds to needs_retry.

Audit confirmed 6 processed seeds that produced zero matching canonical variants.
This script resets them so they will be re-queued for rerun.

Seeds to reset:
  - gac_-_aion__aion_v__2024__2026__il   (no-closure / false-processed)
  - gac_-_aion__aion_y__2023__2026__il   (no-closure / false-processed)
  - fiat__freemont__2011__2016__il        (weak closure: plain-string reason, no source_ids)
  - ford__ecosport__2013__2022__il        (weak closure)
  - geely__coolray__2023__2026__il        (weak closure)
  - hyundai__creta__2020__2026__il        (weak closure)

Usage:
    python tools/reset_zero_match_processed_seeds.py [--dry-run] [--canonical PATH]
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
SEED_CATALOG_PATH = REPO_ROOT / "data/seeds/vehicle_model_seeds_il.json"

# -------------------------------------------------------------------------
# Seeds to reset
# -------------------------------------------------------------------------

# Pure no-closure seeds: these were processed but produced no variants AND
# have no no_variants_by_seed entry at all.  Add to false_processed_seed_ids.
NO_CLOSURE_SEEDS = [
    "gac_-_aion__aion_v__2024__2026__il",
    "gac_-_aion__aion_y__2023__2026__il",
]

# Weak-closure seeds: these have a plain-string no_variants_by_seed entry
# (e.g. "model_not_sold_in_market") with no source_ids — unproven.
# The plain-string entry is removed so the seed can be reprocessed cleanly.
WEAK_CLOSURE_SEEDS = [
    "fiat__freemont__2011__2016__il",
    "ford__ecosport__2013__2022__il",
    "geely__coolray__2023__2026__il",
    "hyundai__creta__2020__2026__il",
]

ALL_SEEDS_TO_RESET = NO_CLOSURE_SEEDS + WEAK_CLOSURE_SEEDS


# -------------------------------------------------------------------------
# Core repair logic (testable without CLI)
# -------------------------------------------------------------------------

def _load_catalog_seed_ids(catalog_path: Path) -> list[str]:
    """Return ordered list of seed_id strings from the IL seed catalog."""
    try:
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return [
                item["seed_id"] if isinstance(item, dict) else str(item)
                for item in raw
            ]
        return []
    except Exception:
        return []


def apply_reset(data: dict, catalog_seed_ids: list[str], now_ts: str) -> dict:
    """Apply the zero-match seed reset to *data* in-place.

    Parameters
    ----------
    data : dict
        Parsed canonical JSON (mutated in-place).
    catalog_seed_ids : list[str]
        Stable seed catalog order used to sort needs_retry additions.
    now_ts : str
        ISO-8601 UTC timestamp string for reset_at fields.

    Returns
    -------
    dict
        The mutated *data* dict.
    """
    bs = data["batch_state"]

    # --- Ensure required sub-structures exist ---
    if "processed_seed_ids" not in bs:
        bs["processed_seed_ids"] = []
    if "processed_seeds" not in bs:
        bs["processed_seeds"] = []
    if "needs_retry_seed_ids" not in bs:
        bs["needs_retry_seed_ids"] = []
    if "false_processed_seed_ids" not in bs:
        bs["false_processed_seed_ids"] = []
    if "seed_accounting" not in bs:
        bs["seed_accounting"] = {}
    if "no_variants_by_seed" not in bs:
        bs["no_variants_by_seed"] = {}
    if "counts" not in data:
        data["counts"] = {}

    processed_ids: list = bs["processed_seed_ids"]
    processed_seeds: list = bs["processed_seeds"]
    needs_retry: list = bs["needs_retry_seed_ids"]
    false_processed: list = bs["false_processed_seed_ids"]
    seed_accounting: dict = bs["seed_accounting"]
    no_variants_by_seed: dict = bs["no_variants_by_seed"]

    for seed_id in ALL_SEEDS_TO_RESET:
        # 1. Remove from processed_seed_ids
        if seed_id in processed_ids:
            processed_ids.remove(seed_id)

        # 2. Remove from processed_seeds (list of seed dicts or seed id strings)
        bs["processed_seeds"] = [
            s for s in processed_seeds
            if (s if isinstance(s, str) else s.get("seed_id", "")) != seed_id
        ]
        processed_seeds = bs["processed_seeds"]

        # 3. Add to needs_retry_seed_ids if not already present
        if seed_id not in needs_retry:
            needs_retry.append(seed_id)

        # 4. Update seed_accounting
        entry = seed_accounting.get(seed_id) or {}
        entry["seed_id"] = seed_id
        entry["status"] = "reset_for_rerun"
        entry["marked_processed"] = False
        entry["reset_reason"] = "zero_match_processed_seed_reset"
        entry["reset_at"] = now_ts
        seed_accounting[seed_id] = entry

        # 5. Remove weak/unproven no_variants_by_seed entries
        #    Only remove if the value is a plain string (unproven) or a dict
        #    without source_ids.
        if seed_id in no_variants_by_seed:
            nvbs_val = no_variants_by_seed[seed_id]
            is_weak = isinstance(nvbs_val, str) or (
                isinstance(nvbs_val, dict)
                and not nvbs_val.get("no_variants_source_ids")
            )
            if is_weak:
                del no_variants_by_seed[seed_id]

    # 6. Add no-closure seeds to false_processed_seed_ids (if not already present)
    for seed_id in NO_CLOSURE_SEEDS:
        if seed_id not in false_processed:
            false_processed.append(seed_id)

    # 7. Sort needs_retry by catalog order (stable, unknowns appended at end)
    catalog_index = {sid: i for i, sid in enumerate(catalog_seed_ids)}
    unknown_base = len(catalog_seed_ids)
    needs_retry.sort(key=lambda s: catalog_index.get(s, unknown_base))

    # 8. Update counts
    data["counts"]["processed_seeds"] = len(processed_ids)
    data["counts"]["needs_retry_seeds"] = len(needs_retry)

    # 9. Set active_mode
    bs["active_mode"] = "rerun_queue_required"

    return data


# -------------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Reset 6 zero-match processed seeds back to needs_retry in canonical."
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

    # Snapshot before counts
    bs_before = data["batch_state"]
    processed_before = len(bs_before.get("processed_seed_ids", []))
    needs_retry_before = len(bs_before.get("needs_retry_seed_ids", []))
    variants_before = len(
        data.get("accumulated_clean_export", {}).get("variants", [])
    )
    active_mode_before = bs_before.get("active_mode")

    # Load catalog for stable ordering
    catalog_seed_ids = _load_catalog_seed_ids(SEED_CATALOG_PATH)

    now_ts = datetime.now(timezone.utc).isoformat()

    # Apply
    apply_reset(data, catalog_seed_ids, now_ts)

    # Snapshot after counts
    bs_after = data["batch_state"]
    processed_after = len(bs_after.get("processed_seed_ids", []))
    needs_retry_after = len(bs_after.get("needs_retry_seed_ids", []))
    variants_after = len(
        data.get("accumulated_clean_export", {}).get("variants", [])
    )
    active_mode_after = bs_after.get("active_mode")

    # --- Verification ---
    honda_seeds_in_processed = [
        s for s in bs_after.get("processed_seed_ids", [])
        if s.startswith("honda__")
    ]

    # --- Report ---
    print("\n=== Zero-Match Seed Reset Report ===")
    if args.dry_run:
        print("(DRY RUN — no changes written)")
    print(f"\nSeeds reset ({len(ALL_SEEDS_TO_RESET)}):")
    for s in ALL_SEEDS_TO_RESET:
        tag = "[no-closure]" if s in NO_CLOSURE_SEEDS else "[weak-closure]"
        print(f"  {tag}  {s}")
    print(f"\nProcessed seeds:   {processed_before} -> {processed_after}")
    print(f"needs_retry seeds: {needs_retry_before} -> {needs_retry_after}")
    print(f"active_mode:       {active_mode_before!r} -> {active_mode_after!r}")
    print(f"Variants:          {variants_before} (unchanged: {variants_before == variants_after})")
    print(f"Honda processed seeds still present: {len(honda_seeds_in_processed)}")
    print(f"next_seed_id:          {bs_after.get('next_seed_id')}")
    print(f"last_completed_seed_id:{bs_after.get('last_completed_seed_id')}")

    if args.dry_run:
        print("\n(Dry run — no changes written)")
        return

    # Create timestamped backup before writing
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"resume_package_canonical_backup_{ts}.json")
    shutil.copy2(path, backup)
    print(f"\nBackup written to: {backup}")

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Canonical updated.")

    # Post-write validation
    try:
        reloaded = json.loads(path.read_text(encoding="utf-8"))
        ace = reloaded.get("accumulated_clean_export") or {}
        print(f"Post-write check: {len(ace.get('variants', []))} variants loaded OK.")
    except Exception as exc:
        print(f"WARNING: post-write validation failed: {exc}", file=sys.stderr)

    print("\nSafe to merge: yes")


if __name__ == "__main__":
    main()
