"""Read-only audit of canonical integrity.

Reports:
  A. General state (counts, cursors)
  B. Non-normalized field values (fuel_type, transmission, drivetrain)
  C. Generation field quality breakdown
  D. False-processed seeds (zero matching variants, no proven closure)
  E. False-completed makes / Honda state
  F. Recommended reset list

This script never writes any file.

Usage:
    python tools/audit_canonical_integrity.py
    python tools/audit_canonical_integrity.py --json   # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CANONICAL_PATH = REPO_ROOT / "data/canonical/resume_package_canonical.json"

# Fully normalised vocabulary sets
_NORM_FUEL = {"petrol", "diesel", "mild_hybrid", "hybrid", "plug_in_hybrid",
              "electric", "hydrogen", "lpg", "unknown"}
_NORM_TRANS = {"manual", "automatic", "cvt", "e_cvt", "dual_clutch",
               "single_speed_ev", "unknown"}
_NORM_DRIVE = {"FWD", "RWD", "AWD", "4WD", "unknown"}


# ---------------------------------------------------------------------------
# Seed-id parsing + variant matching
# ---------------------------------------------------------------------------

def parse_seed_id(seed_id: str) -> dict | None:
    """Return parsed components or None if the format is unrecognised."""
    parts = (seed_id or "").split("__")
    if len(parts) < 5:
        return None
    try:
        return {
            "make": parts[0].replace("_", " ").lower(),
            "model_slug": parts[1].replace("_", " ").lower(),
            "model_slug_underscored": parts[1].lower(),
            "year_start": int(parts[2]),
            "year_end": int(parts[3]),
            "market": parts[4].upper(),
        }
    except (ValueError, IndexError):
        return None


def find_matching_variants(seed_id: str, variants: list[dict]) -> list[dict]:
    """Return variants that match the seed_id by make + model heuristic."""
    parsed = parse_seed_id(seed_id)
    if not parsed:
        return []

    make = parsed["make"]
    model_slug = parsed["model_slug"]
    model_slug_us = parsed["model_slug_underscored"]

    matched = []
    for v in variants:
        if not isinstance(v, dict):
            continue
        v_make = (v.get("make") or "").lower()
        if v_make != make:
            continue
        v_model = (v.get("model") or "").lower()
        v_id = (v.get("variant_id") or "").lower()

        if (v_model == model_slug
                or model_slug_us in v_id
                or model_slug in v_id):
            matched.append(v)

    return matched


# ---------------------------------------------------------------------------
# Section A — general state
# ---------------------------------------------------------------------------

def section_a_general_state(canonical: dict) -> dict:
    bs = canonical.get("batch_state") or {}
    ace = canonical.get("accumulated_clean_export") or {}
    variants = ace.get("variants") or []
    counts = canonical.get("counts") or {}

    return {
        "total_variants": len(variants),
        "processed_seeds": len(bs.get("processed_seed_ids") or []),
        "needs_retry": len(bs.get("needs_retry_seed_ids") or []),
        "next_seed_id": bs.get("next_seed_id"),
        "last_completed_seed_id": bs.get("last_completed_seed_id"),
        "active_mode": bs.get("active_mode"),
        "total_seeds": bs.get("total_seeds"),
        "stored_counts": counts,
    }


# ---------------------------------------------------------------------------
# Section B — non-normalised field values
# ---------------------------------------------------------------------------

def _get_field_value(field):
    if isinstance(field, dict):
        return field.get("value")
    return field


def section_b_nonnormalized_fields(canonical: dict) -> dict:
    variants = (canonical.get("accumulated_clean_export") or {}).get("variants") or []

    fuel_counter: Counter = Counter()
    trans_counter: Counter = Counter()
    drive_counter: Counter = Counter()

    for v in variants:
        if not isinstance(v, dict):
            continue
        fv = _get_field_value(v.get("fuel_type"))
        tv = _get_field_value(v.get("transmission"))
        dv = _get_field_value(v.get("drivetrain"))
        if fv is not None:
            fuel_counter[str(fv)] += 1
        if tv is not None:
            trans_counter[str(tv)] += 1
        if dv is not None:
            drive_counter[str(dv)] += 1

    non_norm_fuel = {k: v for k, v in fuel_counter.items() if k not in _NORM_FUEL}
    non_norm_trans = {k: v for k, v in trans_counter.items() if k not in _NORM_TRANS}
    non_norm_drive = {k: v for k, v in drive_counter.items() if k not in _NORM_DRIVE}

    return {
        "fuel_type": dict(fuel_counter.most_common()),
        "transmission": dict(trans_counter.most_common(30)),
        "drivetrain": dict(drive_counter.most_common()),
        "non_normalized_fuel": non_norm_fuel,
        "non_normalized_transmission": non_norm_trans,
        "non_normalized_drivetrain": non_norm_drive,
        "non_normalized_fuel_count": sum(non_norm_fuel.values()),
        "non_normalized_transmission_count": sum(non_norm_trans.values()),
        "non_normalized_drivetrain_count": sum(non_norm_drive.values()),
    }


# ---------------------------------------------------------------------------
# Section C — generation quality
# ---------------------------------------------------------------------------

def section_c_generation_quality(canonical: dict) -> dict:
    variants = (canonical.get("accumulated_clean_export") or {}).get("variants") or []

    missing = 0
    old_shape = 0      # plain string or {value, used_in_compare} only
    partial_shape = 0  # has source_ids/sources_count but missing status/confidence
    full_shape = 0     # full VerifiedField: value + status + confidence + source_ids

    by_make: dict[str, Counter] = defaultdict(Counter)

    for v in variants:
        if not isinstance(v, dict):
            continue
        make = v.get("make") or "unknown"
        gen = v.get("generation")

        if gen is None or gen == "":
            missing += 1
            by_make[make]["missing"] += 1
        elif isinstance(gen, str):
            old_shape += 1
            by_make[make]["old_shape"] += 1
        elif isinstance(gen, dict):
            has_status = "status" in gen and gen.get("status") not in (None, "")
            has_conf = "confidence" in gen and gen.get("confidence") not in (None, "")
            has_sources = "source_ids" in gen or "sources_count" in gen
            keys = set(gen.keys())
            only_basic = keys <= {"value", "used_in_compare"}

            if only_basic:
                old_shape += 1
                by_make[make]["old_shape"] += 1
            elif has_sources and not (has_status and has_conf):
                partial_shape += 1
                by_make[make]["partial_shape"] += 1
            elif has_status and has_conf and has_sources:
                full_shape += 1
                by_make[make]["full_shape"] += 1
            else:
                old_shape += 1
                by_make[make]["old_shape"] += 1
        else:
            old_shape += 1
            by_make[make]["old_shape"] += 1

    # Build per-make summary for makes with notable data
    makes_summary = {
        make: dict(ctr) for make, ctr in sorted(by_make.items())
        if ctr.get("missing", 0) + ctr.get("old_shape", 0) > 0
    }

    return {
        "missing_or_empty": missing,
        "old_shape": old_shape,
        "partial_shape": partial_shape,
        "full_verified_field_shape": full_shape,
        "by_make_sample": dict(list(makes_summary.items())[:10]),
    }


# ---------------------------------------------------------------------------
# Section D — false-processed seeds
# ---------------------------------------------------------------------------

def section_d_false_processed_seeds(canonical: dict) -> dict:
    bs = canonical.get("batch_state") or {}
    variants = (canonical.get("accumulated_clean_export") or {}).get("variants") or []
    processed_ids = bs.get("processed_seed_ids") or []
    no_variants_by_seed = bs.get("no_variants_by_seed") or {}
    seed_accounting = bs.get("seed_accounting") or {}

    no_closure_candidates = []    # zero variants + no closure at all
    weak_closure_candidates = []  # zero variants + closure reason but no source_ids proof
    proven_closures = []          # zero variants + full closure with source proof

    for sid in processed_ids:
        matched = find_matching_variants(sid, variants)
        if matched:
            continue  # has variants → not false-processed

        nvbs_entry = no_variants_by_seed.get(sid)
        sa = seed_accounting.get(sid) or {}

        has_any_closure = nvbs_entry is not None
        sa_resolved = sa.get("status") == "resolved" and sa.get("marked_processed") is True

        # Check for source-backed proof
        has_source_proof = False
        if isinstance(nvbs_entry, dict):
            has_source_proof = bool(nvbs_entry.get("no_variants_source_ids"))

        if not has_any_closure and not sa_resolved:
            no_closure_candidates.append({
                "seed_id": sid,
                "status": "no_closure",
                "no_variants_by_seed": None,
                "seed_accounting": sa or None,
                "recommendation": "reset_to_needs_retry",
            })
        elif has_any_closure and not has_source_proof:
            weak_closure_candidates.append({
                "seed_id": sid,
                "status": "weak_closure",
                "no_variants_by_seed": nvbs_entry,
                "seed_accounting_status": sa.get("status"),
                "recommendation": "review_manually",
            })
        elif has_source_proof:
            proven_closures.append({
                "seed_id": sid,
                "status": "proven_closure",
                "no_variants_by_seed": nvbs_entry,
            })

    return {
        "no_closure_candidates": no_closure_candidates,
        "weak_closure_candidates": weak_closure_candidates,
        "proven_closures": proven_closures,
        "total_zero_match_processed": (
            len(no_closure_candidates)
            + len(weak_closure_candidates)
            + len(proven_closures)
        ),
    }


# ---------------------------------------------------------------------------
# Section E — false-completed makes / Honda state
# ---------------------------------------------------------------------------

def section_e_honda_and_makes(canonical: dict) -> dict:
    bs = canonical.get("batch_state") or {}
    variants = (canonical.get("accumulated_clean_export") or {}).get("variants") or []
    coverage = canonical.get("coverage_by_make") or {}
    processed_ids = bs.get("processed_seed_ids") or []

    # Honda analysis
    honda_seeds_processed = [s for s in processed_ids if s.lower().startswith("honda__")]
    honda_variants = [v for v in variants
                      if isinstance(v, dict) and (v.get("make") or "").lower() == "honda"]
    honda_coverage = coverage.get("Honda")

    honda_state: dict = {
        "processed_seeds_count": len(honda_seeds_processed),
        "processed_seed_ids": honda_seeds_processed,
        "canonical_variants_count": len(honda_variants),
        "coverage_by_make_entry": honda_coverage,
        "coverage_missing": honda_coverage is None,
    }

    if honda_coverage is None:
        honda_state["conclusion"] = (
            "coverage_by_make['Honda'] is missing/empty. "
            "Honda has {} variants and {} processed seeds. "
            "This is NOT false-completed — variants exist. "
            "Recommend: recompute coverage_by_make to populate Honda entry."
        ).format(len(honda_variants), len(honda_seeds_processed))
        honda_state["needs_reset"] = False
        honda_state["needs_coverage_recompute"] = True
    else:
        completed = honda_coverage.get("completed", False)
        processed = honda_coverage.get("processed", 0)
        vv = honda_coverage.get("verified_variants", 0)
        pv = honda_coverage.get("partial_variants", 0)
        total_v = vv + pv + len(honda_variants)

        if completed and processed > 0 and total_v == 0:
            honda_state["conclusion"] = "FALSE-COMPLETED: coverage shows completed but zero variants."
            honda_state["needs_reset"] = True
        else:
            honda_state["conclusion"] = (
                f"Honda appears OK: completed={completed}, "
                f"coverage_variants={vv + pv}, canonical_variants={len(honda_variants)}."
            )
            honda_state["needs_reset"] = False
        honda_state["needs_coverage_recompute"] = True  # coverage_by_make should always be synced

    # Scan all makes in coverage_by_make for false-completed pattern
    false_completed_makes = []
    for make_name, cov in coverage.items():
        if not isinstance(cov, dict):
            continue
        if cov.get("completed") and cov.get("processed", 0) > 0:
            # Count actual canonical variants for this make
            make_variants = [v for v in variants
                             if isinstance(v, dict)
                             and (v.get("make") or "").lower() == make_name.lower()]
            cov_vv = cov.get("verified_variants", 0) + cov.get("partial_variants", 0)
            if cov_vv == 0 and len(make_variants) == 0:
                false_completed_makes.append({
                    "make": make_name,
                    "coverage": cov,
                    "canonical_variants_count": 0,
                })

    return {
        "honda": honda_state,
        "coverage_by_make_total_entries": len(coverage),
        "false_completed_makes": false_completed_makes,
    }


# ---------------------------------------------------------------------------
# Section F — recommended reset list
# ---------------------------------------------------------------------------

def section_f_recommended_resets(d_report: dict, e_report: dict) -> dict:
    reset_seeds = []
    review_seeds = []

    for item in d_report.get("no_closure_candidates") or []:
        reset_seeds.append({
            "seed_id": item["seed_id"],
            "reason": "zero_variants_no_closure",
            "action": "reset_to_needs_retry",
        })

    for item in d_report.get("weak_closure_candidates") or []:
        review_seeds.append({
            "seed_id": item["seed_id"],
            "reason": f"zero_variants_weak_closure:{item.get('no_variants_by_seed')}",
            "action": "review_manually_consider_reset",
        })

    reset_makes = []
    for item in e_report.get("false_completed_makes") or []:
        reset_makes.append({
            "make": item["make"],
            "reason": "false_completed_zero_variants",
            "action": "reset_coverage_and_seeds",
        })

    honda = e_report.get("honda") or {}
    honda_rec = None
    if honda.get("needs_coverage_recompute"):
        honda_rec = {
            "make": "Honda",
            "action": "recompute_coverage_by_make",
            "reason": honda.get("conclusion", ""),
        }

    return {
        "seeds_to_reset": reset_seeds,
        "seeds_to_review": review_seeds,
        "makes_to_reset": reset_makes,
        "honda_recommendation": honda_rec,
    }


# ---------------------------------------------------------------------------
# Full report builder
# ---------------------------------------------------------------------------

def build_audit_report(canonical: dict) -> dict:
    a = section_a_general_state(canonical)
    b = section_b_nonnormalized_fields(canonical)
    c = section_c_generation_quality(canonical)
    d = section_d_false_processed_seeds(canonical)
    e = section_e_honda_and_makes(canonical)
    f = section_f_recommended_resets(d, e)
    return {"A_general": a, "B_fields": b, "C_generation": c,
            "D_false_processed": d, "E_honda_makes": e, "F_recommendations": f}


# ---------------------------------------------------------------------------
# Human-readable print
# ---------------------------------------------------------------------------

def _sep(title: str = "") -> None:
    line = "=" * 60
    if title:
        print(f"\n{line}\n  {title}\n{line}")
    else:
        print(line)


def print_report(report: dict) -> None:
    a = report["A_general"]
    b = report["B_fields"]
    c = report["C_generation"]
    d = report["D_false_processed"]
    e = report["E_honda_makes"]
    f = report["F_recommendations"]

    _sep("A. GENERAL STATE")
    print(f"  total_variants           : {a['total_variants']}")
    print(f"  processed_seeds          : {a['processed_seeds']}")
    print(f"  needs_retry              : {a['needs_retry']}")
    print(f"  next_seed_id             : {a['next_seed_id']}")
    print(f"  last_completed_seed_id   : {a['last_completed_seed_id']}")
    print(f"  active_mode              : {a['active_mode']}")

    _sep("B. NON-NORMALIZED FIELD VALUES")
    print(f"  Non-normalized fuel_type variants    : {b['non_normalized_fuel_count']}")
    print(f"  Non-normalized transmission variants : {b['non_normalized_transmission_count']}")
    print(f"  Non-normalized drivetrain variants   : {b['non_normalized_drivetrain_count']}")
    if b["non_normalized_fuel"]:
        print("  Non-normalized fuel_type values (top 10):")
        for k, cnt in sorted(b["non_normalized_fuel"].items(), key=lambda x: -x[1])[:10]:
            print(f"    {k!r:40s} {cnt}")
    if b["non_normalized_drivetrain"]:
        print("  Non-normalized drivetrain values:")
        for k, cnt in sorted(b["non_normalized_drivetrain"].items(), key=lambda x: -x[1]):
            print(f"    {k!r:30s} {cnt}")

    _sep("C. GENERATION QUALITY")
    print(f"  missing/empty            : {c['missing_or_empty']}")
    print(f"  old shape (str or basic) : {c['old_shape']}")
    print(f"  partial shape            : {c['partial_shape']}")
    print(f"  full VerifiedField shape : {c['full_verified_field_shape']}")

    _sep("D. FALSE-PROCESSED SEEDS")
    print(f"  Total zero-match processed seeds : {d['total_zero_match_processed']}")
    print(f"  No-closure candidates (reset): {len(d['no_closure_candidates'])}")
    for item in d["no_closure_candidates"]:
        print(f"    - {item['seed_id']}")
    print(f"  Weak-closure candidates (review): {len(d['weak_closure_candidates'])}")
    for item in d["weak_closure_candidates"]:
        print(f"    - {item['seed_id']} | reason={item.get('no_variants_by_seed')!r}")
    print(f"  Proven closures (OK): {len(d['proven_closures'])}")

    _sep("E. HONDA STATE & FALSE-COMPLETED MAKES")
    hd = e["honda"]
    print(f"  Honda processed seeds    : {hd['processed_seeds_count']}")
    print(f"  Honda canonical variants : {hd['canonical_variants_count']}")
    print(f"  Honda coverage entry     : {'MISSING' if hd['coverage_missing'] else 'present'}")
    print(f"  Honda conclusion         : {hd['conclusion']}")
    print(f"  Honda needs_reset        : {hd['needs_reset']}")
    print(f"  coverage_by_make entries : {e['coverage_by_make_total_entries']}")
    if e["false_completed_makes"]:
        print(f"  FALSE-COMPLETED makes    : {[m['make'] for m in e['false_completed_makes']]}")
    else:
        print("  False-completed makes    : none found")

    _sep("F. RECOMMENDATIONS")
    if f["seeds_to_reset"]:
        print("  Seeds recommended for reset to needs_retry:")
        for item in f["seeds_to_reset"]:
            print(f"    - {item['seed_id']} | {item['reason']}")
    else:
        print("  No seeds require reset.")
    if f["seeds_to_review"]:
        print("  Seeds recommended for manual review:")
        for item in f["seeds_to_review"]:
            print(f"    - {item['seed_id']} | {item['reason']}")
    if f["honda_recommendation"]:
        h = f["honda_recommendation"]
        print(f"  Honda recommendation: {h['action']}")
        print(f"    {h['reason']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only audit of canonical integrity"
    )
    parser.add_argument("--json", action="store_true",
                        help="Output machine-readable JSON instead of human-readable text")
    parser.add_argument(
        "--canonical",
        default=str(CANONICAL_PATH),
        help="Path to canonical JSON",
    )
    args = parser.parse_args()

    path = Path(args.canonical)
    if not path.exists():
        print(f"ERROR: canonical not found at {path}", file=sys.stderr)
        sys.exit(1)

    canonical = json.loads(path.read_text(encoding="utf-8"))
    report = build_audit_report(canonical)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
