"""One-time normalization of fuel_type, transmission, drivetrain in existing canonical.

Normalizes the `value` sub-field (or plain string) for every variant's
fuel_type, transmission, and drivetrain fields using the project's
standard normalize functions.  All metadata (status, confidence, source_ids,
sources_count, reason, used_in_compare) is preserved unchanged.

Usage:
    python tools/normalize_existing_canonical_fields.py --dry-run
    python tools/normalize_existing_canonical_fields.py
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.normalize import normalize_fuel_type, normalize_transmission, normalize_drivetrain

CANONICAL_PATH = REPO_ROOT / "data/canonical/resume_package_canonical.json"


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _apply_fn(val: str, normalize_fn) -> str | None:
    """Apply normalize_fn and return the string value, or None if result is falsy."""
    if val is None or val == "":
        return None
    result = normalize_fn(str(val))
    # Enum → .value; plain str returned as-is
    if hasattr(result, "value"):
        return result.value
    if result is None:
        return None
    return str(result)


def normalize_field(field, normalize_fn) -> tuple:
    """Normalize a VerifiedField dict or plain string.

    Returns (new_field, changed: bool, old_val, new_val).
    When field is a dict, only ``field["value"]`` is updated; all other keys
    are preserved.
    """
    if field is None:
        return field, False, None, None

    if isinstance(field, dict):
        old_val = field.get("value")
        if old_val is None or old_val == "":
            return field, False, old_val, old_val
        new_val = _apply_fn(str(old_val), normalize_fn)
        if new_val is None or new_val == old_val:
            return field, False, old_val, old_val
        new_field = dict(field)
        new_field["value"] = new_val
        return new_field, True, old_val, new_val

    # plain string
    old_val = field
    if old_val is None or old_val == "":
        return field, False, old_val, old_val
    new_val = _apply_fn(str(old_val), normalize_fn)
    if new_val is None or new_val == old_val:
        return field, False, old_val, old_val
    return new_val, True, old_val, new_val


def normalize_drivetrain_field(field) -> tuple:
    """Normalize a drivetrain VerifiedField dict or plain string.

    normalize_drivetrain can return ``None`` for falsy input; we treat that as
    "no change" to avoid overwriting good data with None.
    """
    if field is None:
        return field, False, None, None

    if isinstance(field, dict):
        old_val = field.get("value")
        if old_val is None or old_val == "":
            return field, False, old_val, old_val
        result = normalize_drivetrain(str(old_val))
        if result is None:
            return field, False, old_val, old_val
        new_val = str(result)
        if new_val == old_val:
            return field, False, old_val, old_val
        new_field = dict(field)
        new_field["value"] = new_val
        return new_field, True, old_val, new_val

    # plain string
    old_val = field
    if old_val is None or old_val == "":
        return field, False, old_val, old_val
    result = normalize_drivetrain(str(old_val))
    if result is None:
        return field, False, old_val, old_val
    new_val = str(result)
    if new_val == old_val:
        return field, False, old_val, old_val
    return new_val, True, old_val, new_val


# ---------------------------------------------------------------------------
# Core normalization pass
# ---------------------------------------------------------------------------

def run_normalization(data: dict) -> tuple[dict, dict]:
    """Normalize fuel_type / transmission / drivetrain for every variant in *data*.

    Mutates *data* in-place and returns (data, stats).
    """
    variants = (data.get("accumulated_clean_export") or {}).get("variants") or []

    fuel_changes: Counter = Counter()
    trans_changes: Counter = Counter()
    drive_changes: Counter = Counter()

    for v in variants:
        if not isinstance(v, dict):
            continue

        # --- fuel_type ---
        new_fuel, changed, old, new = normalize_field(v.get("fuel_type"), normalize_fuel_type)
        if changed:
            v["fuel_type"] = new_fuel
            fuel_changes[(old, new)] += 1

        # --- transmission ---
        new_trans, changed, old, new = normalize_field(v.get("transmission"), normalize_transmission)
        if changed:
            v["transmission"] = new_trans
            trans_changes[(old, new)] += 1

        # --- drivetrain ---
        new_drive, changed, old, new = normalize_drivetrain_field(v.get("drivetrain"))
        if changed:
            v["drivetrain"] = new_drive
            drive_changes[(old, new)] += 1

    stats = {
        "variants_scanned": len(variants),
        "fuel_type_changes": len([c for c in fuel_changes.values()]),
        "transmission_changes": len([c for c in trans_changes.values()]),
        "drivetrain_changes": len([c for c in drive_changes.values()]),
        "fuel_type_total_changed": sum(fuel_changes.values()),
        "transmission_total_changed": sum(trans_changes.values()),
        "drivetrain_total_changed": sum(drive_changes.values()),
        "fuel_changes_detail": dict(fuel_changes),
        "trans_changes_detail": dict(trans_changes),
        "drive_changes_detail": dict(drive_changes),
    }
    return data, stats


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _print_top_changes(label: str, changes: dict, limit: int = 15) -> None:
    if not changes:
        print(f"  (none)")
        return
    items = sorted(changes.items(), key=lambda x: -x[1])[:limit]
    for (old, new), cnt in items:
        print(f"  {old!r:50s} -> {new!r}  ({cnt}×)")


def print_stats(stats: dict, dry_run: bool) -> None:
    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n{prefix}=== Normalization Summary ===")
    print(f"Variants scanned          : {stats['variants_scanned']}")
    print(f"fuel_type changed         : {stats['fuel_type_total_changed']} variants "
          f"({stats['fuel_type_changes']} unique transformations)")
    print(f"transmission changed      : {stats['transmission_total_changed']} variants "
          f"({stats['transmission_changes']} unique transformations)")
    print(f"drivetrain changed        : {stats['drivetrain_total_changed']} variants "
          f"({stats['drivetrain_changes']} unique transformations)")

    print(f"\nTop fuel_type changes:")
    _print_top_changes("fuel_type", stats["fuel_changes_detail"])

    print(f"\nTop transmission changes:")
    _print_top_changes("transmission", stats["trans_changes_detail"])

    print(f"\nTop drivetrain changes:")
    _print_top_changes("drivetrain", stats["drive_changes_detail"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize fuel_type/transmission/drivetrain values in canonical"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report changes without writing anything to disk"
    )
    parser.add_argument(
        "--canonical",
        default=str(CANONICAL_PATH),
        help="Path to canonical JSON (default: data/canonical/resume_package_canonical.json)",
    )
    args = parser.parse_args()

    path = Path(args.canonical)
    if not path.exists():
        print(f"ERROR: canonical not found at {path}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading canonical from {path} ...")
    data = json.loads(path.read_text(encoding="utf-8"))

    data, stats = run_normalization(data)
    print_stats(stats, dry_run=args.dry_run)

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

    # Validate the written file
    try:
        reloaded = json.loads(path.read_text(encoding="utf-8"))
        ace = reloaded.get("accumulated_clean_export") or {}
        print(f"Post-write check: {len(ace.get('variants', []))} variants loaded OK.")
    except Exception as exc:
        print(f"WARNING: post-write validation failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
