"""Tests for count consistency (Part D/E/G) and IL marketed name checks (Part A/C/G).

Tests required by the problem statement:
  test_refresh_counts_after_merge
  test_counts_do_not_stay_anchored_after_bmw
  test_validate_rejects_stale_counts
  test_dashboard_uses_actual_variant_count
  test_il_marketed_name_required_for_high_confidence
  test_global_name_and_il_name_preserved
  test_missing_il_name_lowers_confidence
"""
from __future__ import annotations

import copy
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.canonical_store import (
    load_canonical,
    refresh_canonical_counts,
    save_canonical_atomic,
    validate_canonical,
)
from engine.merge_variants import merge_variants
from engine.quality_gate import (
    classify_quality_level,
    validate_variant_quality,
    normalize_variant_fields,
)
from engine import queue, run_next

CANONICAL_REL = "data/canonical/resume_package_canonical.json"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    src = REPO_ROOT / CANONICAL_REL
    dst_dir = tmp_path / "data" / "canonical"
    dst_dir.mkdir(parents=True)
    dst = dst_dir / "resume_package_canonical.json"
    shutil.copy2(src, dst)
    monkeypatch.setenv("CANONICAL_PATH", str(dst))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _make_stub_variant(variant_id: str, make: str = "BMW", model: str = "5 Series",
                       year_start: int = 2020, year_end: int = 2024) -> dict:
    return {
        "variant_id": variant_id,
        "make": make,
        "model": model,
        "year_start": {"value": year_start},
        "year_end": {"value": year_end},
        "market": "IL",
        "body_type": {"value": "Sedan", "sources_count": 1, "source_ids": ["s1_test"]},
        "engine": {"value": "2.0L turbo petrol, 190 hp", "sources_count": 1,
                   "source_ids": ["s1_test"]},
        "transmission": {"value": "8-speed automatic", "sources_count": 1,
                         "source_ids": ["s1_test"]},
        "fuel_type": {"value": "Petrol", "sources_count": 1, "source_ids": ["s1_test"]},
        "drivetrain": {"value": "RWD", "sources_count": 1, "source_ids": ["s1_test"]},
        "trim": None,
        "sources_count": 1,
        "verification_status": "partial",
        "confidence": "medium",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def _noop_push(canonical, commit_message=None) -> dict:
    return {"ok": True, "skipped": True}


def _make_runner_returning(variants: list[dict]):
    def _runner(seed_id: str, *, retry_hint: bool = False) -> dict:
        return {
            "ok": True,
            "seed": {"seed_id": seed_id},
            "variants": variants,
            "no_variants_reason": None,
            "discovery": {"ok": True},
            "error": None,
        }
    return _runner


# ---------------------------------------------------------------------------
# Part D/E: count consistency tests
# ---------------------------------------------------------------------------

def test_refresh_counts_after_merge(workspace):
    """Refreshing counts after adding 3 variants must reflect the new total."""
    canonical = load_canonical()
    variants_before = len(canonical["accumulated_clean_export"]["variants"])

    # Manually simulate stale counts at 1323
    canonical.setdefault("counts", {})["total_variants"] = 1323
    assert canonical["counts"]["total_variants"] == 1323  # confirm stale

    # Add 3 new variants
    new_variants = [
        _make_stub_variant(f"bmw__5series__test_{i}") for i in range(3)
    ]
    ace = canonical["accumulated_clean_export"]
    merge_res = merge_variants(ace["variants"], new_variants)
    ace["variants"] = merge_res["merged_variants"]
    assert merge_res["added_count"] == 3

    # Refresh counts
    refresh_canonical_counts(canonical)

    expected = variants_before + 3
    assert canonical["counts"]["total_variants"] == expected, (
        f"Expected total_variants={expected}, got {canonical['counts']['total_variants']}"
    )


def test_counts_do_not_stay_anchored_after_bmw(workspace):
    """After simulating BMW batch of 3 added variants, final canonical counts must update."""
    canonical = load_canonical()
    variants_before = len(canonical["accumulated_clean_export"]["variants"])

    # Simulate stale anchor
    canonical.setdefault("counts", {})["total_variants"] = 1323

    seed_id = "bmw__530i__2020__2024__il"
    bmw_variants = [_make_stub_variant(f"bmw__530i__2020__2024__il__v{i}") for i in range(3)]

    runner = _make_runner_returning(bmw_variants)
    result = run_next.run_selected_seed(seed_id, run_seed_fn=runner, push_fn=_noop_push)
    assert result["ok"] is True, result

    # Reload from disk (what was actually saved)
    saved = load_canonical()
    actual_variants = len(saved["accumulated_clean_export"]["variants"])
    expected = variants_before + 3

    assert actual_variants == expected, (
        f"Expected {expected} variants on disk, got {actual_variants}"
    )
    # counts must match actual
    stored_count = saved.get("counts", {}).get("total_variants")
    assert stored_count == actual_variants, (
        f"Stored counts.total_variants={stored_count} does not match "
        f"actual len(variants)={actual_variants}"
    )


def test_validate_rejects_stale_counts(workspace):
    """validate_canonical must report a mismatch when counts.total_variants is wrong."""
    canonical = load_canonical()
    actual = len(canonical["accumulated_clean_export"]["variants"])

    # Inject a deliberately wrong count
    wrong_count = actual + 999
    canonical.setdefault("counts", {})["total_variants"] = wrong_count

    ok, errs = validate_canonical(canonical)
    assert not ok
    assert any("counts_total_variants_mismatch" in e for e in errs), (
        f"Expected mismatch error, got: {errs}"
    )


def test_validate_auto_refresh_on_save(workspace):
    """save_canonical_atomic must refresh counts so they match actual before writing."""
    canonical = load_canonical()
    actual = len(canonical["accumulated_clean_export"]["variants"])

    # Inject stale count — save should fix it
    canonical.setdefault("counts", {})["total_variants"] = 1323

    result = save_canonical_atomic(canonical)
    assert result["ok"] is True, f"save failed: {result.get('error')}"

    saved = load_canonical()
    assert saved["counts"]["total_variants"] == actual


def test_dashboard_uses_actual_variant_count(workspace):
    """summarize_state must report len(variants), not stale counts.total_variants."""
    canonical = load_canonical()
    actual = len(canonical["accumulated_clean_export"]["variants"])

    # Set a stale count
    canonical.setdefault("counts", {})["total_variants"] = actual - 500

    summary = queue.summarize_state(canonical)
    # The dashboard metric must reflect actual list length
    assert summary["variants_count"] == actual, (
        f"Dashboard reported {summary['variants_count']} but actual is {actual}"
    )


# ---------------------------------------------------------------------------
# Part A/C: IL marketed name quality tests
# ---------------------------------------------------------------------------

SEED_BMW = {
    "seed_id": "bmw__530i__2020__2024__il",
    "make": "BMW",
    "model": "530i",
    "year_start": 2020,
    "year_end": 2024,
    "market": "IL",
}


def _make_good_variant(seed: dict, suffix: str = "v1", **overrides) -> dict:
    base = {
        "variant_id": f"{seed['seed_id']}__{suffix}",
        "make": seed["make"],
        "model": seed["model"],
        "year_start": {"value": seed["year_start"]},
        "year_end": {"value": seed["year_end"]},
        "market": seed["market"],
        "body_type": {"value": "Sedan", "sources_count": 1, "source_ids": ["s1_test"]},
        "engine": {"value": "2.0L turbo petrol, 197 hp", "sources_count": 1,
                   "source_ids": ["s1_test"]},
        "transmission": {"value": "8-speed automatic", "sources_count": 1,
                         "source_ids": ["s1_test"]},
        "fuel_type": {"value": "Petrol", "sources_count": 1, "source_ids": ["s1_test"]},
        "drivetrain": {"value": "RWD", "sources_count": 1, "source_ids": ["s1_test"]},
        "trim": None,
        "sources_count": 1,
        "verification_status": "partial",
        "confidence": "medium",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_il_marketed_name_required_for_high_confidence():
    """market_scope=IL-confirmed without official_marketed_name_il must not yield 'high'."""
    seed = SEED_BMW
    variant = _make_good_variant(
        seed,
        suffix="il_confirmed_no_name",
        market_scope="IL-confirmed",
        # official_marketed_name_il intentionally absent / null
        official_marketed_name_il=None,
        source_basis="Israeli importer data confirms availability.",
    )
    report = validate_variant_quality(variant, seed)
    # Must produce a warning about missing IL name
    assert any("il_confirmed_but_no_official_marketed_name_il" in w
               for w in report["warnings"]), (
        f"Expected IL name warning, got warnings: {report['warnings']}"
    )
    # Quality must NOT be high
    assert report["quality_level"] != "high", (
        f"Expected quality != 'high', got '{report['quality_level']}'"
    )


def test_global_name_and_il_name_preserved():
    """Both global_model_name and official_marketed_name_il must survive normalization."""
    seed = SEED_BMW
    variant = _make_good_variant(
        seed,
        suffix="rebadge",
        global_model_name="Daewoo Lacetti",
        official_marketed_name_il="Chevrolet Optra",
        market_scope="IL-confirmed",
        source_basis="Israeli importer sources confirm Chevrolet Optra branding.",
    )
    normalized = normalize_variant_fields(variant, seed)
    assert normalized.get("global_model_name") == "Daewoo Lacetti", (
        "global_model_name must be preserved after normalization"
    )
    assert normalized.get("official_marketed_name_il") == "Chevrolet Optra", (
        "official_marketed_name_il must be preserved after normalization"
    )


def test_missing_il_name_lowers_confidence():
    """Variant with official_marketed_name_il=null and market_scope=global-reference-only
    must not receive quality 'high'."""
    seed = SEED_BMW
    variant = _make_good_variant(
        seed,
        suffix="no_il_name",
        market_scope="global-reference-only",
        official_marketed_name_il=None,
        source_basis="Israeli marketed name not confirmed; global model name used as reference only.",
    )
    report = validate_variant_quality(variant, seed)
    # Since market_scope is not IL-confirmed, no hard warning expected for this specific check
    # but the quality should not be high because we have no IL confirmation.
    # (The broader rule: IL-confirmed + no name -> capped at medium.)
    # For global-reference-only: no additional cap, but let's verify no high confidence.
    # Actually the problem statement says: "If official_marketed_name_il is missing:
    # quality_level cannot be high, max quality_level = medium or partial"
    # Our implementation caps only when market_scope == IL-confirmed.
    # For global-reference-only, there's no explicit cap (the variant just references global data).
    # This test verifies the logical bound: global-reference-only shouldn't be 'high' either
    # because it doesn't have IL evidence, which is enforced by the existing rule that
    # IL-unconfirmed variants cannot be 'high'.
    assert report["quality_level"] in ("medium", "partial"), (
        f"Expected medium/partial for global-reference-only without IL name, "
        f"got '{report['quality_level']}'"
    )


def test_il_confirmed_with_name_can_be_high():
    """Variant with IL-confirmed + official_marketed_name_il should be eligible for high."""
    seed = SEED_BMW
    variant = _make_good_variant(
        seed,
        suffix="il_with_name",
        market_scope="IL-confirmed",
        official_marketed_name_il="BMW 530i",
        source_basis="Israeli importer confirms BMW 530i is sold under this name in Israel.",
    )
    report = validate_variant_quality(variant, seed)
    # With IL name confirmed, no IL-name-related warning → quality can be 'high'
    il_warn = [w for w in report["warnings"] if "il_confirmed_but_no_official_marketed_name_il" in w]
    assert not il_warn, f"Unexpected IL name warning: {il_warn}"
    # The quality gate warning cap should not apply
    assert report["quality_level"] == "high", (
        f"Expected 'high' when IL name is confirmed, got '{report['quality_level']}'"
    )
