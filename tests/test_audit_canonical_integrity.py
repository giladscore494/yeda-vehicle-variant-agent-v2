"""Tests for tools/audit_canonical_integrity.py

Covers:
1. Processed seed with zero matching variants and no proof → reported as no_closure_candidate.
2. Processed seed with matching variants → NOT reported.
3. Completed make with zero variants → reported as false_completed.
4. Honda with coverage_by_make missing but variants present → not false-completed, needs recompute.
5. Proven no-variants closure (with source_ids) → NOT reported as false-processed.
6. Weak closure (no source_ids) → reported in weak_closure_candidates.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.audit_canonical_integrity import (
    build_audit_report,
    find_matching_variants,
    parse_seed_id,
    section_d_false_processed_seeds,
    section_e_honda_and_makes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_variant(make: str, model: str, variant_id: str | None = None) -> dict:
    vid = variant_id or f"{make.lower()}_{model.lower().replace(' ', '_')}_2020_2024_il"
    return {
        "variant_id": vid,
        "make": make,
        "model": model,
        "year_start": {"value": 2020},
        "year_end": {"value": 2024},
        "market": "IL",
        "verification_status": "partial",
        "confidence": "medium",
        "fuel_type": {"value": "petrol"},
        "transmission": {"value": "automatic"},
        "drivetrain": {"value": "FWD"},
        "body_type": {"value": "sedan"},
    }


def _minimal_canonical(
    variants: list[dict] | None = None,
    processed_seed_ids: list[str] | None = None,
    no_variants_by_seed: dict | None = None,
    seed_accounting: dict | None = None,
    coverage_by_make: dict | None = None,
) -> dict:
    return {
        "accumulated_clean_export": {"variants": variants or []},
        "batch_state": {
            "processed_seed_ids": processed_seed_ids or [],
            "needs_retry_seed_ids": [],
            "no_variants_by_seed": no_variants_by_seed or {},
            "seed_accounting": seed_accounting or {},
        },
        "coverage_by_make": coverage_by_make or {},
        "counts": {"total_variants": len(variants or [])},
    }


# ---------------------------------------------------------------------------
# 1. Zero-match seed with no closure → no_closure_candidate
# ---------------------------------------------------------------------------

class TestNoClosureCandidate:
    def test_reported_as_no_closure(self):
        canonical = _minimal_canonical(
            variants=[_make_variant("Toyota", "Corolla")],
            processed_seed_ids=["ford__ecosport__2013__2022__il"],
        )
        result = section_d_false_processed_seeds(canonical)
        ids = [item["seed_id"] for item in result["no_closure_candidates"]]
        assert "ford__ecosport__2013__2022__il" in ids

    def test_recommendation_is_reset(self):
        canonical = _minimal_canonical(
            variants=[],
            processed_seed_ids=["ford__ecosport__2013__2022__il"],
        )
        result = section_d_false_processed_seeds(canonical)
        item = next(i for i in result["no_closure_candidates"]
                    if i["seed_id"] == "ford__ecosport__2013__2022__il")
        assert item["recommendation"] == "reset_to_needs_retry"

    def test_two_false_processed_candidates(self):
        canonical = _minimal_canonical(
            variants=[_make_variant("Toyota", "Corolla")],
            processed_seed_ids=[
                "gac_-_aion__aion_v__2024__2026__il",
                "gac_-_aion__aion_y__2023__2026__il",
            ],
        )
        result = section_d_false_processed_seeds(canonical)
        assert len(result["no_closure_candidates"]) == 2


# ---------------------------------------------------------------------------
# 2. Processed seed with matching variants → NOT reported
# ---------------------------------------------------------------------------

class TestMatchingVariantNotReported:
    def test_seed_with_variant_not_in_no_closure(self):
        canonical = _minimal_canonical(
            variants=[_make_variant("Ford", "Focus")],
            processed_seed_ids=["ford__focus__2011__2018__il"],
        )
        result = section_d_false_processed_seeds(canonical)
        no_closure_ids = [i["seed_id"] for i in result["no_closure_candidates"]]
        weak_ids = [i["seed_id"] for i in result["weak_closure_candidates"]]
        assert "ford__focus__2011__2018__il" not in no_closure_ids
        assert "ford__focus__2011__2018__il" not in weak_ids

    def test_total_zero_match_is_zero_when_all_have_variants(self):
        canonical = _minimal_canonical(
            variants=[
                _make_variant("Ford", "Focus"),
                _make_variant("Toyota", "Corolla"),
            ],
            processed_seed_ids=[
                "ford__focus__2011__2018__il",
                "toyota__corolla__2019__2026__il",
            ],
        )
        result = section_d_false_processed_seeds(canonical)
        assert result["total_zero_match_processed"] == 0


# ---------------------------------------------------------------------------
# 3. Completed make with zero variants → false_completed
# ---------------------------------------------------------------------------

class TestFalseCompletedMake:
    def test_completed_make_with_zero_variants_reported(self):
        canonical = _minimal_canonical(
            variants=[],
            coverage_by_make={
                "Acme": {
                    "completed": True,
                    "processed": 3,
                    "verified_variants": 0,
                    "partial_variants": 0,
                }
            },
        )
        report = build_audit_report(canonical)
        false_makes = [m["make"] for m in report["E_honda_makes"]["false_completed_makes"]]
        assert "Acme" in false_makes

    def test_non_completed_make_not_reported(self):
        canonical = _minimal_canonical(
            variants=[],
            coverage_by_make={
                "Acme": {
                    "completed": False,
                    "processed": 0,
                    "verified_variants": 0,
                    "partial_variants": 0,
                }
            },
        )
        report = build_audit_report(canonical)
        false_makes = [m["make"] for m in report["E_honda_makes"]["false_completed_makes"]]
        assert "Acme" not in false_makes


# ---------------------------------------------------------------------------
# 4. Honda: coverage_by_make missing but variants present → not false-completed
# ---------------------------------------------------------------------------

class TestHondaState:
    def _make_honda_canonical(self, honda_variants: int = 59,
                               honda_coverage: dict | None = None) -> dict:
        variants = [_make_variant("Honda", f"Civic_{i}") for i in range(honda_variants)]
        coverage = {}
        if honda_coverage is not None:
            coverage["Honda"] = honda_coverage
        processed = [f"honda__civic__1990__2026__il"]
        return _minimal_canonical(
            variants=variants,
            processed_seed_ids=processed,
            coverage_by_make=coverage,
        )

    def test_honda_missing_coverage_not_false_completed(self):
        canonical = self._make_honda_canonical(honda_variants=59, honda_coverage=None)
        report = section_e_honda_and_makes(canonical)
        assert report["honda"]["needs_reset"] is False

    def test_honda_missing_coverage_needs_recompute(self):
        canonical = self._make_honda_canonical(honda_variants=59, honda_coverage=None)
        report = section_e_honda_and_makes(canonical)
        assert report["honda"]["needs_coverage_recompute"] is True

    def test_honda_missing_coverage_reported_as_missing(self):
        canonical = self._make_honda_canonical(honda_variants=59, honda_coverage=None)
        report = section_e_honda_and_makes(canonical)
        assert report["honda"]["coverage_missing"] is True

    def test_honda_59_variants_counted(self):
        canonical = self._make_honda_canonical(honda_variants=59, honda_coverage=None)
        report = section_e_honda_and_makes(canonical)
        assert report["honda"]["canonical_variants_count"] == 59

    def test_honda_false_completed_detected_when_actually_false(self):
        """If Honda truly had completed=True with zero variants, it would be flagged."""
        canonical = self._make_honda_canonical(
            honda_variants=0,
            honda_coverage={
                "completed": True,
                "processed": 5,
                "verified_variants": 0,
                "partial_variants": 0,
            },
        )
        report = section_e_honda_and_makes(canonical)
        assert report["honda"]["needs_reset"] is True


# ---------------------------------------------------------------------------
# 5. Proven closure → NOT reported as false-processed
# ---------------------------------------------------------------------------

class TestProvenClosureNotReported:
    def test_proven_closure_not_in_no_closure(self):
        """Seed with source-backed no_variants entry should go to proven_closures."""
        canonical = _minimal_canonical(
            variants=[],
            processed_seed_ids=["fiat__freemont__2011__2016__il"],
            no_variants_by_seed={
                "fiat__freemont__2011__2016__il": {
                    "no_variants_reason": "model_not_sold_in_market",
                    "no_variants_source_ids": ["real_source_1"],
                    "no_variants_confidence": "high",
                }
            },
            seed_accounting={
                "fiat__freemont__2011__2016__il": {
                    "status": "resolved",
                    "marked_processed": True,
                }
            },
        )
        result = section_d_false_processed_seeds(canonical)
        no_closure_ids = [i["seed_id"] for i in result["no_closure_candidates"]]
        assert "fiat__freemont__2011__2016__il" not in no_closure_ids
        proven_ids = [i["seed_id"] for i in result["proven_closures"]]
        assert "fiat__freemont__2011__2016__il" in proven_ids


# ---------------------------------------------------------------------------
# 6. Weak closure (string reason, no source_ids) → weak_closure_candidates
# ---------------------------------------------------------------------------

class TestWeakClosureReported:
    def test_string_reason_no_source_ids_is_weak(self):
        """Plain string no_variants_by_seed entry without source_ids is weak proof."""
        canonical = _minimal_canonical(
            variants=[],
            processed_seed_ids=["fiat__freemont__2011__2016__il"],
            no_variants_by_seed={
                "fiat__freemont__2011__2016__il": "model_not_sold_in_market",
            },
            seed_accounting={
                "fiat__freemont__2011__2016__il": {
                    "status": "resolved",
                    "marked_processed": True,
                }
            },
        )
        result = section_d_false_processed_seeds(canonical)
        weak_ids = [i["seed_id"] for i in result["weak_closure_candidates"]]
        assert "fiat__freemont__2011__2016__il" in weak_ids

    def test_weak_closure_recommendation(self):
        canonical = _minimal_canonical(
            variants=[],
            processed_seed_ids=["fiat__freemont__2011__2016__il"],
            no_variants_by_seed={"fiat__freemont__2011__2016__il": "model_not_sold_in_market"},
            seed_accounting={"fiat__freemont__2011__2016__il": {"status": "resolved",
                                                                  "marked_processed": True}},
        )
        result = section_d_false_processed_seeds(canonical)
        item = next(i for i in result["weak_closure_candidates"]
                    if i["seed_id"] == "fiat__freemont__2011__2016__il")
        assert item["recommendation"] == "review_manually"


# ---------------------------------------------------------------------------
# 7. parse_seed_id and find_matching_variants unit tests
# ---------------------------------------------------------------------------

class TestSeedIdParsing:
    def test_valid_seed_id(self):
        parsed = parse_seed_id("honda__civic__1990__2026__il")
        assert parsed is not None
        assert parsed["make"] == "honda"
        assert parsed["model_slug"] == "civic"
        assert parsed["year_start"] == 1990

    def test_invalid_seed_id_returns_none(self):
        assert parse_seed_id("bad__format") is None
        assert parse_seed_id("") is None

    def test_find_matching_by_model(self):
        variants = [_make_variant("Honda", "Civic")]
        matched = find_matching_variants("honda__civic__1990__2026__il", variants)
        assert len(matched) == 1

    def test_find_no_match_different_make(self):
        variants = [_make_variant("Toyota", "Corolla")]
        matched = find_matching_variants("honda__civic__1990__2026__il", variants)
        assert len(matched) == 0
