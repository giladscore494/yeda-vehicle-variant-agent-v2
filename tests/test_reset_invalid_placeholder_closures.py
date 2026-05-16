"""Focused test: 4 invalid-placeholder-closure seeds were reset correctly.

Checks (on the real canonical, post-reset):
- 4 seeds are in needs_retry_seed_ids
- 4 seeds are NOT in processed_seed_ids
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
    """Build a minimal canonical with the 4 seeds in processed_seed_ids."""
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
    return {
        "accumulated_clean_export": {
            "variants": [{"variant_id": f"v{i}"} for i in range(n_variants)],
        },
        "batch_state": {
            "processed_seed_ids": list(INVALID_PLACEHOLDER_CLOSURE_SEEDS) + ["other__seed__2020__2026__il"],
            "processed_seeds": list(INVALID_PLACEHOLDER_CLOSURE_SEEDS),
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
            "processed_seeds": len(INVALID_PLACEHOLDER_CLOSURE_SEEDS) + 1,
            "needs_retry_seeds": 0,
        },
    }


# ---------------------------------------------------------------------------
# Focused integration test against the real canonical (post-reset state)
# ---------------------------------------------------------------------------

class TestPlaceholderClosureResetApplied:
    """Verify the real canonical has the 4 seeds correctly reset."""

    @pytest.fixture
    def canonical(self):
        return _load_canonical()

    def test_4_seeds_in_needs_retry(self, canonical):
        ids = canonical["batch_state"]["needs_retry_seed_ids"]
        for seed in INVALID_PLACEHOLDER_CLOSURE_SEEDS:
            assert seed in ids, f"{seed} missing from needs_retry_seed_ids"

    def test_4_seeds_not_in_processed(self, canonical):
        ids = canonical["batch_state"]["processed_seed_ids"]
        for seed in INVALID_PLACEHOLDER_CLOSURE_SEEDS:
            assert seed not in ids, f"{seed} still in processed_seed_ids"

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
