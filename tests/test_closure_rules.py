"""Tests for the strict no-variants closure rules introduced to fix the
false-processed zero-variant bug (volkswagen__golf_variant__1993__2026__il).

Covers:
- plain no_variants_reason is rejected
- added_count > 0 resolves
- merged_count > 0 resolves
- duplicate_existing_variant_only: requires dedupe_proof
- retry reasons never close a seed
- model_not_sold_in_market: needs source_ids + source_basis + medium/high confidence
- model_not_sold_in_market with low confidence: rejected
- regression test for volkswagen__golf_variant__1993__2026__il (normal_batch path)
- regression test for volkswagen__golf_variant__1993__2026__il (problem_queue/needs_retry path)
- defensive guard: merge_result_into_canonical raises on proof_valid=False + zero variants
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

from engine.run_next import (
    _has_dedupe_or_no_variants_proof,
    _is_no_variants_proof_valid,
    merge_result_into_canonical,
    run_next_model,
    run_selected_seed,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _proof(added=0, merged=0, dedupe_proof=None, reason=None, *,
           source_ids=None, source_basis=None, confidence=None):
    return _has_dedupe_or_no_variants_proof(
        "test_seed",
        dedupe_proof or [],
        added,
        merged,
        reason,
        no_variants_source_ids=source_ids or [],
        no_variants_source_basis=source_basis,
        no_variants_confidence=confidence,
    )


# ---------------------------------------------------------------------------
# 1. Plain no_variants_reason without proof is rejected
# ---------------------------------------------------------------------------

class TestPlainNoVariantsReasonRejected:
    def test_model_not_sold_no_proof(self):
        assert _proof(reason="model_not_sold_in_market") is False

    def test_model_not_sold_source_ids_only(self):
        assert _proof(reason="model_not_sold_in_market",
                      source_ids=["src_1"]) is False

    def test_model_not_sold_basis_only(self):
        assert _proof(reason="model_not_sold_in_market",
                      source_basis="Some basis") is False

    def test_model_not_sold_confidence_only(self):
        assert _proof(reason="model_not_sold_in_market",
                      confidence="high") is False

    def test_empty_reason(self):
        assert _proof() is False

    def test_none_reason(self):
        assert _proof(reason=None) is False

    def test_whitespace_reason(self):
        assert _proof(reason="   ") is False


# ---------------------------------------------------------------------------
# 2. added_count > 0 always resolves
# ---------------------------------------------------------------------------

class TestAddedCountResolves:
    def test_added_one(self):
        assert _proof(added=1) is True

    def test_added_ten(self):
        assert _proof(added=10) is True

    def test_added_zero_does_not_resolve_alone(self):
        assert _proof(added=0) is False


# ---------------------------------------------------------------------------
# 3. merged_count > 0 always resolves
# ---------------------------------------------------------------------------

class TestMergedCountResolves:
    def test_merged_one(self):
        assert _proof(merged=1) is True

    def test_merged_zero_does_not_resolve_alone(self):
        assert _proof(merged=0) is False


# ---------------------------------------------------------------------------
# 4. duplicate_existing_variant_only requires dedupe_proof
# ---------------------------------------------------------------------------

class TestDuplicateExistingVariantOnly:
    def test_with_dedupe_proof_resolves(self):
        assert _proof(reason="duplicate_existing_variant_only",
                      dedupe_proof=[{"variant_id": "x"}]) is True

    def test_without_dedupe_proof_rejected(self):
        assert _proof(reason="duplicate_existing_variant_only",
                      dedupe_proof=[]) is False

    def test_none_dedupe_proof_rejected(self):
        assert _proof(reason="duplicate_existing_variant_only",
                      dedupe_proof=None) is False


# ---------------------------------------------------------------------------
# 5. Retry reasons never close a seed
# ---------------------------------------------------------------------------

class TestRetryReasonsNeverClose:
    def test_no_reliable_sources_found(self):
        assert _proof(reason="no_reliable_sources_found") is False

    def test_insufficient_grounded_data(self):
        assert _proof(reason="insufficient_grounded_data") is False

    def test_source_conflict_unresolved(self):
        assert _proof(reason="source_conflict_unresolved") is False

    def test_blocked_by_validation(self):
        assert _proof(reason="blocked_by_validation") is False

    # Even with source_ids/basis/confidence, retry reasons must not resolve.
    def test_no_reliable_sources_with_proof_still_rejected(self):
        assert _proof(
            reason="no_reliable_sources_found",
            source_ids=["src_1"],
            source_basis="some basis",
            confidence="high",
        ) is False


# ---------------------------------------------------------------------------
# 6. model_not_sold_in_market with full proof resolves
# ---------------------------------------------------------------------------

class TestModelNotSoldWithProof:
    def test_medium_confidence_resolves(self):
        assert _proof(
            reason="model_not_sold_in_market",
            source_ids=["src_1"],
            source_basis="IL market evidence: model never listed by importer.",
            confidence="medium",
        ) is True

    def test_high_confidence_resolves(self):
        assert _proof(
            reason="model_not_sold_in_market",
            source_ids=["src_1", "src_2"],
            source_basis="Official IL importer confirmed no listing.",
            confidence="high",
        ) is True

    def test_multiple_source_ids(self):
        assert _proof(
            reason="model_not_sold_in_market",
            source_ids=["src_1", "src_2", "src_3"],
            source_basis="Multiple IL sources confirm absence.",
            confidence="medium",
        ) is True


# ---------------------------------------------------------------------------
# 7. model_not_sold_in_market with low confidence is rejected
# ---------------------------------------------------------------------------

class TestModelNotSoldLowConfidenceRejected:
    def test_low_confidence_rejected(self):
        assert _proof(
            reason="model_not_sold_in_market",
            source_ids=["src_1"],
            source_basis="Some evidence.",
            confidence="low",
        ) is False

    def test_partial_confidence_rejected(self):
        assert _proof(
            reason="model_not_sold_in_market",
            source_ids=["src_1"],
            source_basis="Some evidence.",
            confidence="partial",
        ) is False

    def test_empty_confidence_rejected(self):
        assert _proof(
            reason="model_not_sold_in_market",
            source_ids=["src_1"],
            source_basis="Some evidence.",
            confidence="",
        ) is False


# ---------------------------------------------------------------------------
# 8. seed_out_of_scope always resolves
# ---------------------------------------------------------------------------

class TestSeedOutOfScope:
    def test_seed_out_of_scope_resolves(self):
        assert _proof(reason="seed_out_of_scope") is True


# ---------------------------------------------------------------------------
# 9. model_discontinued_before_market_period
# ---------------------------------------------------------------------------

class TestModelDiscontinuedBeforeMarketPeriod:
    def test_with_source_ids_resolves(self):
        assert _proof(
            reason="model_discontinued_before_market_period",
            source_ids=["src_1"],
        ) is True

    def test_with_source_basis_resolves(self):
        assert _proof(
            reason="model_discontinued_before_market_period",
            source_basis="Model ended 2010; seed starts 2015.",
        ) is True

    def test_without_any_proof_rejected(self):
        assert _proof(reason="model_discontinued_before_market_period") is False


# ---------------------------------------------------------------------------
# 10. Regression: volkswagen__golf_variant__1993__2026__il
# ---------------------------------------------------------------------------

def _make_minimal_canonical() -> dict:
    """Build the smallest valid canonical that run_selected_seed will accept."""
    return {
        "accumulated_clean_export": {"variants": []},
        "batch_state": {
            "active_mode": "normal_batch",
            "next_seed_id": "volkswagen__golf_variant__1993__2026__il",
            "last_completed_seed_id": None,
            "processed_seed_ids": [],
            "needs_retry_seed_ids": [],
            "skipped_seed_ids": [],
            "failed_seed_ids": [],
        },
        "counts": {"total_variants": 0},
        "metadata": {"schema_version": "3.0"},
    }


class TestGolfVariantRegression:
    """Simulate the exact runner result that caused the false-processed seed."""

    _SEED_ID = "volkswagen__golf_variant__1993__2026__il"

    def _stubbed_runner(self, seed_id: str, *, retry_hint: bool = False) -> dict:
        return {
            "ok": True,
            "seed": {
                "seed_id": seed_id,
                "make": "Volkswagen",
                "model": "Golf Variant",
                "year_start": 1993,
                "year_end": 2026,
                "market": "IL",
            },
            "variants": [],
            "no_variants_reason": "model_not_sold_in_market",
            "no_variants_evidence": [],
            "no_variants_source_ids": [],
            "no_variants_source_basis": None,
            "no_variants_confidence": None,
            "no_variants_reason_detail": None,
            "discovery": {"ok": True, "data": {}, "error": None, "gemini_metadata": {}},
            "error": None,
        }

    def _make_stub_load(self, canonical: dict):
        def _load():
            return copy.deepcopy(canonical)
        return _load

    def test_run_selected_seed_returns_ok_false(self, monkeypatch):
        canonical = _make_minimal_canonical()
        monkeypatch.setattr(
            "engine.run_next.load_canonical",
            self._make_stub_load(canonical),
        )
        result = run_selected_seed(
            self._SEED_ID,
            run_seed_fn=self._stubbed_runner,
            push_fn=None,
            seed_catalog=[self._SEED_ID],
        )
        assert result["ok"] is False

    def test_error_contains_zero_variant_without_proof(self, monkeypatch):
        canonical = _make_minimal_canonical()
        monkeypatch.setattr(
            "engine.run_next.load_canonical",
            self._make_stub_load(canonical),
        )
        result = run_selected_seed(
            self._SEED_ID,
            run_seed_fn=self._stubbed_runner,
            push_fn=None,
            seed_catalog=[self._SEED_ID],
        )
        assert "zero_variant_without_proof" in (result.get("error") or "")

    def test_save_is_none(self, monkeypatch):
        canonical = _make_minimal_canonical()
        monkeypatch.setattr(
            "engine.run_next.load_canonical",
            self._make_stub_load(canonical),
        )
        result = run_selected_seed(
            self._SEED_ID,
            run_seed_fn=self._stubbed_runner,
            push_fn=None,
            seed_catalog=[self._SEED_ID],
        )
        assert result.get("save") is None

    def test_push_is_none(self, monkeypatch):
        canonical = _make_minimal_canonical()
        monkeypatch.setattr(
            "engine.run_next.load_canonical",
            self._make_stub_load(canonical),
        )
        result = run_selected_seed(
            self._SEED_ID,
            run_seed_fn=self._stubbed_runner,
            push_fn=None,
            seed_catalog=[self._SEED_ID],
        )
        assert result.get("push") is None

    def test_closure_decision_not_resolved(self, monkeypatch):
        canonical = _make_minimal_canonical()
        monkeypatch.setattr(
            "engine.run_next.load_canonical",
            self._make_stub_load(canonical),
        )
        result = run_selected_seed(
            self._SEED_ID,
            run_seed_fn=self._stubbed_runner,
            push_fn=None,
            seed_catalog=[self._SEED_ID],
        )
        assert result.get("closure_decision") == "not_resolved"


# ---------------------------------------------------------------------------
# 11. Regression: problem_queue / needs_retry path — zero variants without proof
#
# Simulates the EXACT canonical state that existed before the bad engine commit
# (5ee0f0d) that incorrectly re-resolved the Golf Variant seed while it was in
# needs_retry_seed_ids (problem_queue mode).
#
# Input state mirrors the real pre-bad-commit canonical:
#   needs_retry_seed_ids = ["volkswagen__golf_variant__1993__2026__il"]
#   false_processed_seed_ids contains the seed
#   last_completed_seed_id = "volkswagen__golf_r__2010__2026__il"
#   next_seed_id = "volkswagen__golf_plus__2005__2020__il"
#
# Stub runner returns zero variants with no validated proof:
#   no_variants_reason = "model_not_sold_in_market"
#   no_variants_source_ids = []
#   no_variants_source_basis = None
#   no_variants_confidence = None
# ---------------------------------------------------------------------------

_GOLF_VARIANT_SEED = "volkswagen__golf_variant__1993__2026__il"
_GOLF_R_SEED = "volkswagen__golf_r__2010__2026__il"
_GOLF_PLUS_SEED = "volkswagen__golf_plus__2005__2020__il"


def _make_problem_queue_canonical() -> dict:
    """Build a canonical that mirrors the real pre-bad-commit state:
    Golf Variant is in needs_retry (problem_queue mode), not processed."""
    return {
        "accumulated_clean_export": {"variants": []},
        "batch_state": {
            "active_mode": "rerun_queue_required",
            "next_seed_id": _GOLF_PLUS_SEED,
            "last_completed_seed_id": _GOLF_R_SEED,
            "processed_seed_ids": [_GOLF_R_SEED],
            "processed_seeds": [_GOLF_R_SEED],
            "needs_retry_seed_ids": [_GOLF_VARIANT_SEED],
            "false_processed_seed_ids": [_GOLF_VARIANT_SEED],
            "skipped_seed_ids": [],
            "failed_seed_ids": [],
            "seed_accounting": {
                _GOLF_VARIANT_SEED: {
                    "seed_id": _GOLF_VARIANT_SEED,
                    "variants_added_to_canonical": 0,
                    "variants_deduped_or_merged": 0,
                    "dedupe_proof": [],
                    "no_variants_reason": "model_not_sold_in_market",
                    "marked_processed": False,
                    "status": "false_processed",
                }
            },
        },
        "counts": {"total_variants": 0},
        "metadata": {"schema_version": "3.0"},
    }


def _zero_proof_golf_variant_runner(seed_id: str, *, retry_hint: bool = False) -> dict:
    """Stub runner: returns zero variants with NO validated proof — exactly the
    result shape that should trigger the strict closure guard."""
    return {
        "ok": True,
        "seed": {
            "seed_id": seed_id,
            "make": "Volkswagen",
            "model": "Golf Variant",
            "year_start": 1993,
            "year_end": 2026,
            "market": "IL",
        },
        "variants": [],
        "no_variants_reason": "model_not_sold_in_market",
        "no_variants_evidence": [],
        "no_variants_source_ids": [],
        "no_variants_source_basis": None,
        "no_variants_confidence": None,
        "no_variants_reason_detail": None,
        "discovery": {"ok": True, "data": {}, "error": None, "gemini_metadata": {}},
        "error": None,
    }


class TestGolfVariantProblemQueuePathGuard:
    """Guard test: problem_queue/needs_retry path must NOT resolve a zero-variant
    seed that has no validated no_variants proof.

    Covers run_selected_seed() called directly with mode=problem_queue.
    """

    def _stub_load(self, canonical: dict):
        def _load():
            return copy.deepcopy(canonical)
        return _load

    def test_run_selected_seed_pq_ok_is_false(self, monkeypatch):
        canonical = _make_problem_queue_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))
        result = run_selected_seed(
            _GOLF_VARIANT_SEED,
            run_seed_fn=_zero_proof_golf_variant_runner,
            push_fn=None,
        )
        assert result["ok"] is False

    def test_run_selected_seed_pq_error_contains_zero_variant_without_proof(self, monkeypatch):
        canonical = _make_problem_queue_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))
        result = run_selected_seed(
            _GOLF_VARIANT_SEED,
            run_seed_fn=_zero_proof_golf_variant_runner,
            push_fn=None,
        )
        assert "zero_variant_without_proof" in (result.get("error") or "")

    def test_run_selected_seed_pq_closure_decision_not_resolved(self, monkeypatch):
        canonical = _make_problem_queue_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))
        result = run_selected_seed(
            _GOLF_VARIANT_SEED,
            run_seed_fn=_zero_proof_golf_variant_runner,
            push_fn=None,
        )
        assert result.get("closure_decision") == "not_resolved"

    def test_run_selected_seed_pq_save_is_none(self, monkeypatch):
        canonical = _make_problem_queue_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))
        result = run_selected_seed(
            _GOLF_VARIANT_SEED,
            run_seed_fn=_zero_proof_golf_variant_runner,
            push_fn=None,
        )
        assert result.get("save") is None

    def test_run_selected_seed_pq_push_is_none(self, monkeypatch):
        canonical = _make_problem_queue_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))
        result = run_selected_seed(
            _GOLF_VARIANT_SEED,
            run_seed_fn=_zero_proof_golf_variant_runner,
            push_fn=None,
        )
        assert result.get("push") is None


class TestGolfVariantBatchProblemQueuePathGuard:
    """Guard test: run_next_model() batch path in problem_queue mode must NOT
    resolve a zero-variant seed that has no validated proof.

    This is the EXACT path that the bad engine commit exploited (the commit that
    re-resolved Golf Variant by calling the engine while it was in needs_retry,
    producing ok=true / added_count=0 / needs_retry_after=0 with Gemini-supplied
    proof that should have been rejected at the source-ID quality level).

    Verifies the full batch pipeline: run_next_model → run_selected_seed →
    closure guard fires → no save/push → seed remains in needs_retry.
    """

    def _stub_load(self, canonical: dict):
        def _load():
            return copy.deepcopy(canonical)
        return _load

    def _noop_push(self, canonical, commit_message=None):
        return {"ok": True, "skipped": True}

    def test_batch_ok_is_false(self, monkeypatch):
        """run_next_model must return ok=False when guard fires."""
        canonical = _make_problem_queue_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))
        result = run_next_model(
            batch_size=1,
            run_seed_fn=_zero_proof_golf_variant_runner,
            push_fn=self._noop_push,
        )
        assert result["ok"] is False

    def test_batch_stop_reason_contains_zero_variant_without_proof(self, monkeypatch):
        """stop_reason must propagate the closure guard error message."""
        canonical = _make_problem_queue_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))
        result = run_next_model(
            batch_size=1,
            run_seed_fn=_zero_proof_golf_variant_runner,
            push_fn=self._noop_push,
        )
        assert "zero_variant_without_proof" in (result.get("stop_reason") or "")

    def test_batch_processed_count_is_zero(self, monkeypatch):
        """No seed may be counted as processed when the guard fires."""
        canonical = _make_problem_queue_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))
        result = run_next_model(
            batch_size=1,
            run_seed_fn=_zero_proof_golf_variant_runner,
            push_fn=self._noop_push,
        )
        assert result["processed_count"] == 0

    def test_batch_result_save_ok_false(self, monkeypatch):
        """Per-seed result save_ok must be false — no canonical was written."""
        canonical = _make_problem_queue_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))
        result = run_next_model(
            batch_size=1,
            run_seed_fn=_zero_proof_golf_variant_runner,
            push_fn=self._noop_push,
        )
        seed_result = result["results"][0]
        assert not seed_result.get("save_ok")

    def test_batch_result_push_ok_false(self, monkeypatch):
        """Per-seed result push_ok must be false — no push was made."""
        canonical = _make_problem_queue_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))
        result = run_next_model(
            batch_size=1,
            run_seed_fn=_zero_proof_golf_variant_runner,
            push_fn=self._noop_push,
        )
        seed_result = result["results"][0]
        assert not seed_result.get("push_ok")

    def test_batch_needs_retry_unchanged(self, monkeypatch):
        """needs_retry_after must still be 1 — seed was not removed."""
        canonical = _make_problem_queue_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))
        result = run_next_model(
            batch_size=1,
            run_seed_fn=_zero_proof_golf_variant_runner,
            push_fn=self._noop_push,
        )
        seed_result = result["results"][0]
        assert seed_result.get("needs_retry_after") == 1

    def test_batch_normal_next_seed_id_unchanged(self, monkeypatch):
        """Normal batch cursor (next_seed_id) must not advance."""
        canonical = _make_problem_queue_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))
        result = run_next_model(
            batch_size=1,
            run_seed_fn=_zero_proof_golf_variant_runner,
            push_fn=self._noop_push,
        )
        assert result["final_state"]["normal_next_seed_id"] == _GOLF_PLUS_SEED


# ---------------------------------------------------------------------------
# 12. Defensive guard: merge_result_into_canonical raises on proof_valid=False
#
# Verifies that the centralised invariant in merge_result_into_canonical()
# surfaces loudly rather than silently corrupting canonical state when a future
# caller bypasses the pre-merge closure check.
# ---------------------------------------------------------------------------

class TestMergeResultCentralisedGuard:
    """merge_result_into_canonical must raise ValueError when called with
    proof_valid=False and zero variants — ensuring the defensive invariant
    catches any future bypass of the pre-merge closure guard.
    """

    def _minimal_canonical(self) -> dict:
        return {
            "accumulated_clean_export": {"variants": []},
            "batch_state": {
                "active_mode": "normal_batch",
                "next_seed_id": "some__seed__2020__2026__il",
                "last_completed_seed_id": None,
                "processed_seed_ids": [],
                "needs_retry_seed_ids": [],
                "skipped_seed_ids": [],
                "failed_seed_ids": [],
            },
            "counts": {"total_variants": 0},
            "metadata": {"schema_version": "3.0"},
        }

    def test_raises_on_proof_valid_false_zero_variants(self):
        canonical = self._minimal_canonical()
        with pytest.raises(ValueError, match="zero_variant_without_proof"):
            merge_result_into_canonical(
                canonical,
                "volkswagen__golf_variant__1993__2026__il",
                [],   # zero variants
                "model_not_sold_in_market",
                "normal_batch",
                proof_valid=False,
            )

    def test_does_not_raise_when_proof_valid_true(self):
        """proof_valid=True with zero variants is a valid no-variants closure."""
        canonical = self._minimal_canonical()
        # Should not raise — the pre-merge guard already validated the proof
        result = merge_result_into_canonical(
            canonical,
            "some__seed__2020__2026__il",
            [],
            "model_not_sold_in_market",
            "normal_batch",
            proof_valid=True,
        )
        assert result["added_count"] == 0

    def test_does_not_raise_when_variants_present(self):
        """proof_valid=False with variants present must NOT raise — proof comes
        from the variants themselves."""
        canonical = self._minimal_canonical()
        fake_variant = {
            "variant_id": "some__seed__2020__2026__il_v1",
            "make": "Some", "model": "Seed",
            "year_start": {"value": 2020, "used_in_compare": False},
            "year_end": {"value": 2026, "used_in_compare": False},
            "market": "IL",
            "verification_status": "partial",
        }
        result = merge_result_into_canonical(
            canonical,
            "some__seed__2020__2026__il",
            [fake_variant],
            None,
            "normal_batch",
            proof_valid=False,   # irrelevant when variants exist
        )
        assert result["added_count"] == 1
