"""Focused test: invalid-placeholder-closure seeds were reset correctly.

Checks (on the real canonical, post-reset):
- 3 seeds (creta, fiat, ford) are in needs_retry_seed_ids
- those 3 seeds are NOT in processed_seed_ids
- geely__coolray is in processed_seed_ids (VALID closure — non-placeholder sources)
- variants count unchanged at 1566
- active_mode == "rerun_queue_required"

Also exercises apply_reset() against a minimal in-memory canonical so the test
does not depend on the current file state after the script has already run.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.reset_invalid_placeholder_closures import (
    INVALID_PLACEHOLDER_CLOSURE_SEEDS,
    apply_reset,
)

CANONICAL_PATH = REPO_ROOT / "data/canonical/resume_package_canonical.json"
NOW_TS = "2026-05-16T19:00:00+00:00"

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _load_canonical() -> dict:
    return json.loads(CANONICAL_PATH.read_text(encoding="utf-8"))


def _make_pre_reset_canonical(n_variants: int = 5) -> dict:
    """Build a minimal canonical with the 3 invalid seeds in processed_seed_ids
    (using placeholder source_ids src_1/src_2) plus a valid Geely seed in
    processed that must NOT be reset."""
    sa = {
        s: {
            "seed_id": s,
            "status": "resolved",
            "marked_processed": True,
            "zero_variant_resolution": {
                "proof_status": "proven",
                "source_ids": ["src_1", "src_2"],
                "reason": "model_not_sold_in_market",
            },
        }
        for s in INVALID_PLACEHOLDER_CLOSURE_SEEDS
    }
    # Geely has VALID non-placeholder sources — must remain processed after apply_reset.
    geely = "geely__coolray__2023__2026__il"
    sa[geely] = {
        "seed_id": geely,
        "status": "resolved",
        "marked_processed": True,
        "zero_variant_resolution": {
            "proof_status": "proven",
            "source_ids": ["src_walla_cars", "src_autoboom_il"],
            "reason": "model_not_sold_in_market",
            "confidence": "high",
            "source_basis": "Walla Cars and Autoboom IL confirm Geely Coolray is not sold in Israel.",
        },
    }
    return {
        "accumulated_clean_export": {
            "variants": [{"variant_id": f"v{i}"} for i in range(n_variants)],
        },
        "batch_state": {
            "processed_seed_ids": list(INVALID_PLACEHOLDER_CLOSURE_SEEDS) + [geely, "other__seed__2020__2026__il"],
            "processed_seeds": list(INVALID_PLACEHOLDER_CLOSURE_SEEDS) + [geely],
            "needs_retry_seed_ids": [],
            "false_processed_seed_ids": [],
            "seed_accounting": sa,
            "no_variants_by_seed": {s: "model_not_sold_in_market" for s in INVALID_PLACEHOLDER_CLOSURE_SEEDS},
            "active_mode": "normal_batch",
            "next_seed_id": "volkswagen__jetta__1990__2026__il",
            "last_completed_seed_id": "volkswagen__golf_plus__2005__2020__il",
        },
        "counts": {
            "total_variants": n_variants,
            "processed_seeds": len(INVALID_PLACEHOLDER_CLOSURE_SEEDS) + 2,
            "needs_retry_seeds": 0,
        },
    }


# ---------------------------------------------------------------------------
# Focused integration test against the real canonical (post-reset state)
# ---------------------------------------------------------------------------

class TestPlaceholderClosureResetApplied:
    """Verify the real canonical has the 3 invalid seeds correctly reset.

    geely__coolray__2023__2026__il is excluded: it was re-run with non-placeholder
    source_ids ["src_walla_cars", "src_autoboom_il"] and has a VALID closure.
    """

    @pytest.fixture
    def canonical(self):
        return _load_canonical()

    def test_3_seeds_in_needs_retry(self, canonical):
        ids = canonical["batch_state"]["needs_retry_seed_ids"]
        for seed in INVALID_PLACEHOLDER_CLOSURE_SEEDS:
            assert seed in ids, f"{seed} missing from needs_retry_seed_ids"

    def test_3_seeds_not_in_processed(self, canonical):
        ids = canonical["batch_state"]["processed_seed_ids"]
        for seed in INVALID_PLACEHOLDER_CLOSURE_SEEDS:
            assert seed not in ids, f"{seed} still in processed_seed_ids"

    def test_geely_valid_closure_remains_processed(self, canonical):
        """Geely was re-run with valid non-placeholder sources; must stay in processed."""
        geely = "geely__coolray__2023__2026__il"
        processed_ids = canonical["batch_state"]["processed_seed_ids"]
        needs_retry = canonical["batch_state"]["needs_retry_seed_ids"]
        assert geely in processed_ids, f"{geely} unexpectedly removed from processed_seed_ids"
        assert geely not in needs_retry, f"{geely} incorrectly placed in needs_retry_seed_ids"
        sa = canonical["batch_state"].get("seed_accounting", {}).get(geely, {})
        zvr = sa.get("zero_variant_resolution", {})
        source_ids = zvr.get("source_ids", [])
        placeholders = {"src_1", "src_2", "src_3", "source_1", "citation_1", "ref_1"}
        assert not any(s in placeholders for s in source_ids), (
            f"Geely source_ids unexpectedly contain placeholders: {source_ids}"
        )

    def test_variants_unchanged_at_1566(self, canonical):
        count = len(canonical["accumulated_clean_export"]["variants"])
        assert count == 1566, f"Expected 1566 variants, got {count}"

    def test_active_mode_rerun_queue_required(self, canonical):
        mode = canonical["batch_state"]["active_mode"]
        assert mode == "rerun_queue_required", f"Expected rerun_queue_required, got {mode!r}"


# ---------------------------------------------------------------------------
# Unit tests against apply_reset() with minimal in-memory canonical
# ---------------------------------------------------------------------------

class TestApplyResetLogic:
    def test_seeds_moved_from_processed_to_needs_retry(self):
        data = _make_pre_reset_canonical()
        apply_reset(data, NOW_TS)
        ids = data["batch_state"]["processed_seed_ids"]
        retry = data["batch_state"]["needs_retry_seed_ids"]
        for seed in INVALID_PLACEHOLDER_CLOSURE_SEEDS:
            assert seed not in ids, f"{seed} still in processed"
            assert seed in retry, f"{seed} missing from needs_retry"

    def test_geely_not_reset_by_apply_reset(self):
        """apply_reset must not touch geely__coolray (valid non-placeholder sources)."""
        data = _make_pre_reset_canonical()
        geely = "geely__coolray__2023__2026__il"
        apply_reset(data, NOW_TS)
        assert geely in data["batch_state"]["processed_seed_ids"], (
            "Geely should remain in processed after apply_reset"
        )
        assert geely not in data["batch_state"]["needs_retry_seed_ids"], (
            "Geely should not be in needs_retry after apply_reset"
        )

    def test_variants_not_modified(self):
        data = _make_pre_reset_canonical(n_variants=7)
        apply_reset(data, NOW_TS)
        assert len(data["accumulated_clean_export"]["variants"]) == 7

    def test_active_mode_set(self):
        data = _make_pre_reset_canonical()
        apply_reset(data, NOW_TS)
        assert data["batch_state"]["active_mode"] == "rerun_queue_required"

    def test_seed_accounting_updated(self):
        data = _make_pre_reset_canonical()
        apply_reset(data, NOW_TS)
        sa = data["batch_state"]["seed_accounting"]
        for seed in INVALID_PLACEHOLDER_CLOSURE_SEEDS:
            assert sa[seed]["status"] == "reset_for_rerun"
            assert sa[seed]["marked_processed"] is False
            assert sa[seed]["reset_reason"] == "placeholder_source_ids_invalidated_zero_variant_closure"

    def test_no_variants_by_seed_cleared(self):
        data = _make_pre_reset_canonical()
        apply_reset(data, NOW_TS)
        nvbs = data["batch_state"]["no_variants_by_seed"]
        for seed in INVALID_PLACEHOLDER_CLOSURE_SEEDS:
            assert seed not in nvbs

    def test_added_to_false_processed(self):
        data = _make_pre_reset_canonical()
        apply_reset(data, NOW_TS)
        fp = data["batch_state"]["false_processed_seed_ids"]
        for seed in INVALID_PLACEHOLDER_CLOSURE_SEEDS:
            assert seed in fp

    def test_counts_match_list_lengths(self):
        data = _make_pre_reset_canonical()
        apply_reset(data, NOW_TS)
        assert data["counts"]["processed_seeds"] == len(data["batch_state"]["processed_seed_ids"])
        assert data["counts"]["needs_retry_seeds"] == len(data["batch_state"]["needs_retry_seed_ids"])

    def test_cursor_fields_preserved(self):
        data = _make_pre_reset_canonical()
        apply_reset(data, NOW_TS)
        bs = data["batch_state"]
        assert bs["next_seed_id"] == "volkswagen__jetta__1990__2026__il"
        assert bs["last_completed_seed_id"] == "volkswagen__golf_plus__2005__2020__il"

    def test_zvr_proof_status_invalidated(self):
        data = _make_pre_reset_canonical()
        apply_reset(data, NOW_TS)
        sa = data["batch_state"]["seed_accounting"]
        for seed in INVALID_PLACEHOLDER_CLOSURE_SEEDS:
            zvr = sa[seed].get("zero_variant_resolution")
            if zvr is not None:
                assert zvr["proof_status"] == "invalid_placeholder_sources"

    def test_idempotent_no_duplicates(self):
        data = _make_pre_reset_canonical()
        apply_reset(data, NOW_TS)
        apply_reset(data, NOW_TS)
        retry = data["batch_state"]["needs_retry_seed_ids"]
        fp = data["batch_state"]["false_processed_seed_ids"]
        assert len(retry) == len(set(retry))
        assert len(fp) == len(set(fp))


# ---------------------------------------------------------------------------
# Focused test: invalid zero-variant closure with placeholder source_ids
# ---------------------------------------------------------------------------

class TestInvalidPlaceholderClosureReset:
    """Focused test: a seed with placeholder source_ids (src_1, src_2) closed as
    zero-variant is reset to needs_retry; key invariants are preserved."""

    PLACEHOLDER_SEED = "hyundai__creta__2020__2026__il"

    def _make_single_seed_canonical(self, n_variants: int = 5) -> dict:
        """Minimal canonical with one invalid-placeholder seed in processed."""
        return {
            "accumulated_clean_export": {
                "variants": [{"variant_id": f"v{i}"} for i in range(n_variants)],
            },
            "batch_state": {
                "processed_seed_ids": [self.PLACEHOLDER_SEED, "other__seed__2020__2026__il"],
                "processed_seeds": [self.PLACEHOLDER_SEED],
                "needs_retry_seed_ids": [],
                "false_processed_seed_ids": [],
                "seed_accounting": {
                    self.PLACEHOLDER_SEED: {
                        "seed_id": self.PLACEHOLDER_SEED,
                        "status": "resolved",
                        "marked_processed": True,
                        "no_variants_reason": "model_not_sold_in_market",
                        "zero_variant_resolution": {
                            "proof_status": "proven",
                            "source_ids": ["src_1", "src_2", "src_3"],
                            "source_basis": "Some basis text.",
                            "confidence": "high",
                            "reason": "model_not_sold_in_market",
                        },
                    }
                },
                "no_variants_by_seed": {self.PLACEHOLDER_SEED: "model_not_sold_in_market"},
                "active_mode": "normal_batch",
                "next_seed_id": "volkswagen__jetta__1990__2026__il",
                "last_completed_seed_id": "volkswagen__golf_plus__2005__2020__il",
            },
            "counts": {
                "total_variants": n_variants,
                "processed_seeds": 2,
                "needs_retry_seeds": 0,
            },
        }

    def test_placeholder_seed_moved_to_needs_retry(self):
        data = self._make_single_seed_canonical()
        apply_reset(data, NOW_TS)
        bs = data["batch_state"]
        assert self.PLACEHOLDER_SEED not in bs["processed_seed_ids"]
        assert self.PLACEHOLDER_SEED in bs["needs_retry_seed_ids"]

    def test_variants_unchanged_after_reset(self):
        n = 8
        data = self._make_single_seed_canonical(n_variants=n)
        apply_reset(data, NOW_TS)
        assert len(data["accumulated_clean_export"]["variants"]) == n

    def test_next_seed_id_preserved(self):
        data = self._make_single_seed_canonical()
        apply_reset(data, NOW_TS)
        assert data["batch_state"]["next_seed_id"] == "volkswagen__jetta__1990__2026__il"

    def test_last_completed_seed_id_preserved(self):
        data = self._make_single_seed_canonical()
        apply_reset(data, NOW_TS)
        assert data["batch_state"]["last_completed_seed_id"] == "volkswagen__golf_plus__2005__2020__il"

    def test_active_mode_rerun_queue_required(self):
        data = self._make_single_seed_canonical()
        apply_reset(data, NOW_TS)
        assert data["batch_state"]["active_mode"] == "rerun_queue_required"

    def test_seed_accounting_reset_for_rerun(self):
        data = self._make_single_seed_canonical()
        apply_reset(data, NOW_TS)
        sa = data["batch_state"]["seed_accounting"][self.PLACEHOLDER_SEED]
        assert sa["status"] == "reset_for_rerun"
        assert sa["marked_processed"] is False
        assert sa["reset_reason"] == "placeholder_source_ids_invalidated_zero_variant_closure"
        assert sa["reset_at"] == NOW_TS

    def test_zvr_proof_status_invalidated(self):
        data = self._make_single_seed_canonical()
        apply_reset(data, NOW_TS)
        zvr = data["batch_state"]["seed_accounting"][self.PLACEHOLDER_SEED]["zero_variant_resolution"]
        assert zvr["proof_status"] == "invalid_placeholder_sources"

    def test_no_variants_by_seed_removed(self):
        data = self._make_single_seed_canonical()
        apply_reset(data, NOW_TS)
        assert self.PLACEHOLDER_SEED not in data["batch_state"]["no_variants_by_seed"]

    def test_added_to_false_processed_seed_ids(self):
        data = self._make_single_seed_canonical()
        apply_reset(data, NOW_TS)
        assert self.PLACEHOLDER_SEED in data["batch_state"]["false_processed_seed_ids"]

    def test_counts_updated_correctly(self):
        data = self._make_single_seed_canonical()
        apply_reset(data, NOW_TS)
        assert data["counts"]["processed_seeds"] == len(data["batch_state"]["processed_seed_ids"])
        assert data["counts"]["needs_retry_seeds"] == len(data["batch_state"]["needs_retry_seed_ids"])
