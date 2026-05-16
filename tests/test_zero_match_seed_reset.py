"""Tests for tools/reset_zero_match_processed_seeds.py

Assertions:
- all 6 seeds are not in processed_seed_ids after reset
- all 6 seeds are not in processed_seeds after reset
- all 6 seeds are in needs_retry_seed_ids after reset
- seed_accounting status == "reset_for_rerun" and marked_processed == False
- weak no_variants_by_seed plain-string entries are removed
- counts match list lengths
- active_mode == "rerun_queue_required"
- variants count unchanged at 1564
- Honda seeds are NOT reset and remain processed
- next_seed_id and last_completed_seed_id are preserved
- no-closure seeds are added to false_processed_seed_ids
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

from tools.reset_zero_match_processed_seeds import (
    ALL_SEEDS_TO_RESET,
    NO_CLOSURE_SEEDS,
    WEAK_CLOSURE_SEEDS,
    apply_reset,
)

CANONICAL_PATH = REPO_ROOT / "data/canonical/resume_package_canonical.json"
SEED_CATALOG_PATH = REPO_ROOT / "data/seeds/vehicle_model_seeds_il.json"

NOW_TS = "2026-05-16T18:00:00+00:00"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_canonical() -> dict:
    return json.loads(CANONICAL_PATH.read_text(encoding="utf-8"))


def _load_catalog_seed_ids() -> list[str]:
    raw = json.loads(SEED_CATALOG_PATH.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return [
            item["seed_id"] if isinstance(item, dict) else str(item)
            for item in raw
        ]
    return []


def _make_minimal_canonical(
    processed_seed_ids: list[str] | None = None,
    processed_seeds: list | None = None,
    needs_retry_seed_ids: list[str] | None = None,
    no_variants_by_seed: dict | None = None,
    seed_accounting: dict | None = None,
    false_processed_seed_ids: list[str] | None = None,
    variants: list | None = None,
    active_mode: str = "normal_batch",
    next_seed_id: str = "volkswagen__jetta__1990__2026__il",
    last_completed_seed_id: str = "volkswagen__golf_plus__2005__2020__il",
) -> dict:
    all_processed = processed_seed_ids or list(ALL_SEEDS_TO_RESET)
    return {
        "accumulated_clean_export": {"variants": variants or []},
        "batch_state": {
            "processed_seed_ids": list(all_processed),
            "processed_seeds": list(processed_seeds or all_processed),
            "needs_retry_seed_ids": list(needs_retry_seed_ids or []),
            "false_processed_seed_ids": list(false_processed_seed_ids or []),
            "seed_accounting": dict(seed_accounting or {}),
            "no_variants_by_seed": dict(no_variants_by_seed or {}),
            "active_mode": active_mode,
            "next_seed_id": next_seed_id,
            "last_completed_seed_id": last_completed_seed_id,
        },
        "counts": {
            "total_variants": len(variants or []),
            "processed_seeds": len(all_processed),
            "needs_retry_seeds": len(needs_retry_seed_ids or []),
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def canonical_copy() -> dict:
    """Deep copy of the real canonical file — no side effects."""
    return copy.deepcopy(_load_canonical())


@pytest.fixture
def catalog_seed_ids() -> list[str]:
    return _load_catalog_seed_ids()


@pytest.fixture
def reset_canonical(canonical_copy, catalog_seed_ids) -> dict:
    """Apply reset to a deep copy of the real canonical."""
    apply_reset(canonical_copy, catalog_seed_ids, NOW_TS)
    return canonical_copy


# ---------------------------------------------------------------------------
# 1. All 6 seeds removed from processed_seed_ids
# ---------------------------------------------------------------------------

class TestRemovedFromProcessed:
    def test_all_6_not_in_processed_seed_ids(self, reset_canonical):
        ids = reset_canonical["batch_state"]["processed_seed_ids"]
        for seed in ALL_SEEDS_TO_RESET:
            assert seed not in ids, f"{seed} still in processed_seed_ids"

    def test_processed_seed_ids_is_list(self, reset_canonical):
        assert isinstance(reset_canonical["batch_state"]["processed_seed_ids"], list)


# ---------------------------------------------------------------------------
# 2. All 6 seeds removed from processed_seeds
# ---------------------------------------------------------------------------

class TestRemovedFromProcessedSeeds:
    def test_all_6_not_in_processed_seeds(self, reset_canonical):
        processed = reset_canonical["batch_state"]["processed_seeds"]
        for seed in ALL_SEEDS_TO_RESET:
            # processed_seeds may be list of strings or list of dicts
            ids_in_list = [
                s if isinstance(s, str) else s.get("seed_id", "")
                for s in processed
            ]
            assert seed not in ids_in_list, f"{seed} still in processed_seeds"


# ---------------------------------------------------------------------------
# 3. All 6 seeds present in needs_retry_seed_ids
# ---------------------------------------------------------------------------

class TestAddedToNeedsRetry:
    def test_all_6_in_needs_retry_seed_ids(self, reset_canonical):
        ids = reset_canonical["batch_state"]["needs_retry_seed_ids"]
        for seed in ALL_SEEDS_TO_RESET:
            assert seed in ids, f"{seed} missing from needs_retry_seed_ids"

    def test_no_duplicates_in_needs_retry(self, reset_canonical):
        ids = reset_canonical["batch_state"]["needs_retry_seed_ids"]
        assert len(ids) == len(set(ids)), "Duplicate seeds in needs_retry_seed_ids"


# ---------------------------------------------------------------------------
# 4. seed_accounting updated correctly
# ---------------------------------------------------------------------------

class TestSeedAccountingUpdated:
    def test_status_is_reset_for_rerun(self, reset_canonical):
        sa = reset_canonical["batch_state"]["seed_accounting"]
        for seed in ALL_SEEDS_TO_RESET:
            assert sa[seed]["status"] == "reset_for_rerun", (
                f"{seed} status is {sa[seed].get('status')!r}"
            )

    def test_marked_processed_is_false(self, reset_canonical):
        sa = reset_canonical["batch_state"]["seed_accounting"]
        for seed in ALL_SEEDS_TO_RESET:
            assert sa[seed]["marked_processed"] is False, (
                f"{seed} marked_processed is {sa[seed].get('marked_processed')!r}"
            )

    def test_reset_reason_set(self, reset_canonical):
        sa = reset_canonical["batch_state"]["seed_accounting"]
        for seed in ALL_SEEDS_TO_RESET:
            assert sa[seed].get("reset_reason") == "zero_match_processed_seed_reset"

    def test_reset_at_set(self, reset_canonical):
        sa = reset_canonical["batch_state"]["seed_accounting"]
        for seed in ALL_SEEDS_TO_RESET:
            assert "reset_at" in sa[seed]
            assert sa[seed]["reset_at"] == NOW_TS


# ---------------------------------------------------------------------------
# 5. Weak no_variants_by_seed entries removed
# ---------------------------------------------------------------------------

class TestWeakNoVariantsRemoved:
    def test_weak_closure_seeds_removed_from_no_variants(self, reset_canonical):
        nvbs = reset_canonical["batch_state"]["no_variants_by_seed"]
        for seed in WEAK_CLOSURE_SEEDS:
            assert seed not in nvbs, (
                f"{seed} still in no_variants_by_seed: {nvbs.get(seed)!r}"
            )

    def test_no_closure_seeds_not_in_no_variants(self, reset_canonical):
        nvbs = reset_canonical["batch_state"]["no_variants_by_seed"]
        for seed in NO_CLOSURE_SEEDS:
            assert seed not in nvbs, (
                f"{seed} in no_variants_by_seed unexpectedly: {nvbs.get(seed)!r}"
            )

    def test_proven_entry_not_removed(self):
        """A dict entry with non-empty source_ids must NOT be removed."""
        data = _make_minimal_canonical(
            processed_seed_ids=["fiat__freemont__2011__2016__il"],
            processed_seeds=["fiat__freemont__2011__2016__il"],
            no_variants_by_seed={
                "fiat__freemont__2011__2016__il": {
                    "no_variants_reason": "model_not_sold_in_market",
                    "no_variants_source_ids": ["proven_source_1"],
                    "no_variants_confidence": "high",
                }
            },
        )
        apply_reset(data, [], NOW_TS)
        # Should NOT be removed (has source_ids → proven)
        nvbs = data["batch_state"]["no_variants_by_seed"]
        assert "fiat__freemont__2011__2016__il" in nvbs


# ---------------------------------------------------------------------------
# 6. Counts match list lengths
# ---------------------------------------------------------------------------

class TestCountsMatchLengths:
    def test_processed_seeds_count_equals_list_length(self, reset_canonical):
        ids = reset_canonical["batch_state"]["processed_seed_ids"]
        counts_val = reset_canonical["counts"]["processed_seeds"]
        assert counts_val == len(ids), (
            f"counts.processed_seeds={counts_val} != len(processed_seed_ids)={len(ids)}"
        )

    def test_needs_retry_count_equals_list_length(self, reset_canonical):
        ids = reset_canonical["batch_state"]["needs_retry_seed_ids"]
        counts_val = reset_canonical["counts"]["needs_retry_seeds"]
        assert counts_val == len(ids), (
            f"counts.needs_retry_seeds={counts_val} != len(needs_retry_seed_ids)={len(ids)}"
        )


# ---------------------------------------------------------------------------
# 7. active_mode updated
# ---------------------------------------------------------------------------

class TestActiveModeUpdated:
    def test_active_mode_is_rerun_queue_required(self, reset_canonical):
        assert reset_canonical["batch_state"]["active_mode"] == "rerun_queue_required"


# ---------------------------------------------------------------------------
# 8. Variants count unchanged at 1564
# ---------------------------------------------------------------------------

class TestVariantsUnchanged:
    def test_variants_count_unchanged(self, reset_canonical):
        variants = reset_canonical.get("accumulated_clean_export", {}).get("variants", [])
        assert len(variants) == 1564, f"Expected 1564 variants, got {len(variants)}"

    def test_variants_present(self, reset_canonical):
        variants = reset_canonical.get("accumulated_clean_export", {}).get("variants", [])
        assert len(variants) > 0


# ---------------------------------------------------------------------------
# 9. Honda not reset — remains processed
# ---------------------------------------------------------------------------

class TestHondaNotReset:
    def test_honda_seeds_still_in_processed(self, reset_canonical):
        ids = reset_canonical["batch_state"]["processed_seed_ids"]
        honda_seeds = [s for s in ids if s.startswith("honda__")]
        assert len(honda_seeds) > 0, "No Honda seeds found in processed_seed_ids after reset"

    def test_honda_seed_accounting_not_touched(self, reset_canonical):
        sa = reset_canonical["batch_state"]["seed_accounting"]
        for seed_id, entry in sa.items():
            if seed_id.startswith("honda__"):
                assert entry.get("status") != "reset_for_rerun", (
                    f"Honda seed {seed_id} was incorrectly reset"
                )

    def test_honda_variants_present(self, reset_canonical):
        variants = reset_canonical.get("accumulated_clean_export", {}).get("variants", [])
        honda_variants = [
            v for v in variants
            if (v.get("make") or "").lower() == "honda"
        ]
        assert len(honda_variants) == 59, (
            f"Expected 59 Honda variants, got {len(honda_variants)}"
        )


# ---------------------------------------------------------------------------
# 10. Cursor fields preserved
# ---------------------------------------------------------------------------

class TestCursorFieldsPreserved:
    def test_next_seed_id_preserved(self, reset_canonical):
        assert reset_canonical["batch_state"]["next_seed_id"] == (
            "volkswagen__jetta__1990__2026__il"
        )

    def test_last_completed_seed_id_preserved(self, reset_canonical):
        assert reset_canonical["batch_state"]["last_completed_seed_id"] == (
            "volkswagen__golf_plus__2005__2020__il"
        )


# ---------------------------------------------------------------------------
# 11. No-closure seeds added to false_processed_seed_ids
# ---------------------------------------------------------------------------

class TestNoClosureAddedToFalseProcessed:
    def test_no_closure_seeds_in_false_processed(self, reset_canonical):
        fp = reset_canonical["batch_state"]["false_processed_seed_ids"]
        for seed in NO_CLOSURE_SEEDS:
            assert seed in fp, f"{seed} missing from false_processed_seed_ids"

    def test_no_duplicates_in_false_processed(self, reset_canonical):
        fp = reset_canonical["batch_state"]["false_processed_seed_ids"]
        assert len(fp) == len(set(fp)), "Duplicate entries in false_processed_seed_ids"


# ---------------------------------------------------------------------------
# 12. Idempotency: running twice should not duplicate entries
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_needs_retry_no_duplicates_after_double_apply(
        self, canonical_copy, catalog_seed_ids
    ):
        apply_reset(canonical_copy, catalog_seed_ids, NOW_TS)
        apply_reset(canonical_copy, catalog_seed_ids, NOW_TS)
        ids = canonical_copy["batch_state"]["needs_retry_seed_ids"]
        assert len(ids) == len(set(ids)), "needs_retry_seed_ids has duplicates after double apply"

    def test_false_processed_no_duplicates_after_double_apply(
        self, canonical_copy, catalog_seed_ids
    ):
        apply_reset(canonical_copy, catalog_seed_ids, NOW_TS)
        apply_reset(canonical_copy, catalog_seed_ids, NOW_TS)
        fp = canonical_copy["batch_state"]["false_processed_seed_ids"]
        assert len(fp) == len(set(fp))


# ---------------------------------------------------------------------------
# 13. Minimal-canonical unit tests (no file I/O)
# ---------------------------------------------------------------------------

class TestMinimalCanonical:
    def test_minimal_all_6_moved(self):
        data = _make_minimal_canonical(
            no_variants_by_seed={
                s: "model_not_sold_in_market" for s in WEAK_CLOSURE_SEEDS
            }
        )
        apply_reset(data, [], NOW_TS)
        ids = data["batch_state"]["processed_seed_ids"]
        for seed in ALL_SEEDS_TO_RESET:
            assert seed not in ids

    def test_minimal_needs_retry_populated(self):
        data = _make_minimal_canonical()
        apply_reset(data, [], NOW_TS)
        ids = data["batch_state"]["needs_retry_seed_ids"]
        for seed in ALL_SEEDS_TO_RESET:
            assert seed in ids

    def test_minimal_active_mode(self):
        data = _make_minimal_canonical()
        apply_reset(data, [], NOW_TS)
        assert data["batch_state"]["active_mode"] == "rerun_queue_required"

    def test_minimal_counts_consistent(self):
        data = _make_minimal_canonical()
        apply_reset(data, [], NOW_TS)
        assert data["counts"]["processed_seeds"] == len(
            data["batch_state"]["processed_seed_ids"]
        )
        assert data["counts"]["needs_retry_seeds"] == len(
            data["batch_state"]["needs_retry_seed_ids"]
        )

    def test_minimal_seed_accounting_entries_created(self):
        data = _make_minimal_canonical(seed_accounting={})
        apply_reset(data, [], NOW_TS)
        sa = data["batch_state"]["seed_accounting"]
        for seed in ALL_SEEDS_TO_RESET:
            assert seed in sa
            assert sa[seed]["status"] == "reset_for_rerun"
            assert sa[seed]["marked_processed"] is False

    def test_already_in_needs_retry_no_duplicate(self):
        data = _make_minimal_canonical(
            needs_retry_seed_ids=[ALL_SEEDS_TO_RESET[0]]
        )
        apply_reset(data, [], NOW_TS)
        ids = data["batch_state"]["needs_retry_seed_ids"]
        assert ids.count(ALL_SEEDS_TO_RESET[0]) == 1
