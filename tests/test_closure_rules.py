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
    _looks_like_placeholder_source_id,
    merge_result_into_canonical,
    run_next_model,
    run_selected_seed,
)
from engine.canonical_store import validate_canonical


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
            source_ids=["https://www.il-importer.co.il/models/x"],
            source_basis="IL market evidence: model never listed by importer.",
            confidence="medium",
        ) is True

    def test_high_confidence_resolves(self):
        assert _proof(
            reason="model_not_sold_in_market",
            source_ids=["https://www.il-importer.co.il/x", "yad2-listing-2024"],
            source_basis="Official IL importer confirmed no listing.",
            confidence="high",
        ) is True

    def test_multiple_source_ids(self):
        assert _proof(
            reason="model_not_sold_in_market",
            source_ids=["il-importer-2024", "carbibles-il-spec-2023", "gov-il-registry"],
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
            source_ids=["https://real-source.co.il"],
            source_basis="Some evidence.",
            confidence="low",
        ) is False

    def test_partial_confidence_rejected(self):
        assert _proof(
            reason="model_not_sold_in_market",
            source_ids=["https://real-source.co.il"],
            source_basis="Some evidence.",
            confidence="partial",
        ) is False

    def test_empty_confidence_rejected(self):
        assert _proof(
            reason="model_not_sold_in_market",
            source_ids=["https://real-source.co.il"],
            source_basis="Some evidence.",
            confidence="",
        ) is False


# ---------------------------------------------------------------------------
# 7b. model_not_sold_in_market with placeholder source IDs is rejected
# ---------------------------------------------------------------------------

class TestPlaceholderSourceIdsRejected:
    """_is_no_variants_proof_valid must reject placeholder source_ids for
    model_not_sold_in_market even when all other fields are valid."""

    # --- _looks_like_placeholder_source_id unit tests ---

    def test_src_1_is_placeholder(self):
        assert _looks_like_placeholder_source_id("src_1") is True

    def test_src_4_is_placeholder(self):
        assert _looks_like_placeholder_source_id("src_4") is True

    def test_source_1_is_placeholder(self):
        assert _looks_like_placeholder_source_id("source_1") is True

    def test_source_2_is_placeholder(self):
        assert _looks_like_placeholder_source_id("source_2") is True

    def test_citation_1_is_placeholder(self):
        assert _looks_like_placeholder_source_id("citation_1") is True

    def test_ref_1_is_placeholder(self):
        assert _looks_like_placeholder_source_id("ref_1") is True

    def test_bare_digit_1_is_placeholder(self):
        assert _looks_like_placeholder_source_id("1") is True

    def test_bare_digit_3_is_placeholder(self):
        assert _looks_like_placeholder_source_id("3") is True

    def test_url_is_not_placeholder(self):
        assert _looks_like_placeholder_source_id("https://www.il-importer.co.il/x") is False

    def test_meaningful_slug_is_not_placeholder(self):
        assert _looks_like_placeholder_source_id("yad2-listing-2024") is False

    def test_hyphenated_non_numeric_suffix_not_placeholder(self):
        assert _looks_like_placeholder_source_id("il-importer-abc") is False

    # --- proof-level rejection ---

    def test_src_1_src_2_high_confidence_rejected(self):
        """model_not_sold_in_market + ["src_1", "src_2"] + high is rejected."""
        assert _proof(
            reason="model_not_sold_in_market",
            source_ids=["src_1", "src_2"],
            source_basis="Official IL importer confirmed no listing.",
            confidence="high",
        ) is False

    def test_source_1_rejected(self):
        """model_not_sold_in_market + ["source_1"] is rejected."""
        assert _proof(
            reason="model_not_sold_in_market",
            source_ids=["source_1"],
            source_basis="No IL listing found.",
            confidence="medium",
        ) is False

    def test_empty_source_ids_rejected(self):
        """model_not_sold_in_market + [] is rejected."""
        assert _proof(
            reason="model_not_sold_in_market",
            source_ids=[],
            source_basis="No IL listing found.",
            confidence="high",
        ) is False

    def test_mixed_real_and_placeholder_rejected(self):
        """A single placeholder among otherwise real IDs still rejects."""
        assert _proof(
            reason="model_not_sold_in_market",
            source_ids=["https://real-source.co.il", "src_2"],
            source_basis="IL market evidence.",
            confidence="high",
        ) is False

    def test_realistic_url_source_id_passes(self):
        """model_not_sold_in_market + realistic URL source ID + medium passes."""
        assert _proof(
            reason="model_not_sold_in_market",
            source_ids=["https://www.il-importer.co.il/models/x"],
            source_basis="IL market evidence: model never listed by importer.",
            confidence="medium",
        ) is True

    def test_realistic_slug_source_id_passes(self):
        """model_not_sold_in_market + realistic slug source ID + high passes."""
        assert _proof(
            reason="model_not_sold_in_market",
            source_ids=["yad2-listing-2024", "gov-il-registry-2023"],
            source_basis="Multiple IL sources confirm absence.",
            confidence="high",
        ) is True

    def test_duplicate_existing_variant_only_still_requires_dedupe_proof(self):
        """duplicate_existing_variant_only must still require dedupe_proof."""
        assert _proof(
            reason="duplicate_existing_variant_only",
            dedupe_proof=[{"variant_id": "x"}],
        ) is True
        assert _proof(
            reason="duplicate_existing_variant_only",
            dedupe_proof=[],
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


# ---------------------------------------------------------------------------
# 13. Regression: hyundai__creta__2020__2026__il — placeholder source_ids bypass
#
# Root cause: `validate_canonical()` did not check for placeholder source_ids in
# `seed_accounting[*].zero_variant_resolution.source_ids`.  A direct canonical
# write (bypassing `run_selected_seed()`'s proof guard) could set
# proof_status="proven" with source_ids=["src_1","src_2","src_3"].
#
# This suite covers the FULL stack:
#   A. _is_no_variants_proof_valid() rejects ["src_1","src_2","src_3"]
#   B. run_selected_seed() returns ok=False, no save, no push, seed in needs_retry
#   C. validate_canonical() rejects a canonical that has a "proven" ZVR with only
#      placeholder source_ids (closes the direct-write bypass)
# ---------------------------------------------------------------------------

_CRETA_SEED = "hyundai__creta__2020__2026__il"
_CRETA_SOURCE_BASIS = (
    "Official Israeli importer data (Colmobil / Hyundai Israel) and local "
    "automotive databases (Autoboom, Auto.co.il) confirm that the Hyundai Creta "
    "is not officially marketed in Israel."
)


def _make_creta_pq_canonical() -> dict:
    """Canonical with Creta in needs_retry_seed_ids (problem_queue mode)."""
    return {
        "accumulated_clean_export": {"variants": []},
        "batch_state": {
            "active_mode": "rerun_queue_required",
            "next_seed_id": "volkswagen__jetta__1990__2026__il",
            "last_completed_seed_id": "volkswagen__golf_plus__2005__2020__il",
            "processed_seed_ids": [],
            "processed_seeds": [],
            "needs_retry_seed_ids": [_CRETA_SEED],
            "false_processed_seed_ids": [_CRETA_SEED],
            "skipped_seed_ids": [],
            "failed_seed_ids": [],
            "seed_accounting": {
                _CRETA_SEED: {
                    "seed_id": _CRETA_SEED,
                    "status": "reset_for_rerun",
                    "marked_processed": False,
                    "reset_reason": "placeholder_source_ids_invalidated_zero_variant_closure",
                }
            },
        },
        "counts": {"total_variants": 0},
        "metadata": {"schema_version": "3.0"},
    }


def _creta_placeholder_runner(seed_id: str, *, retry_hint: bool = False) -> dict:
    """Stub runner that returns no variants with placeholder source_ids — the exact
    shape that caused the Creta false-closure (model_not_sold_in_market +
    source_ids=["src_1","src_2","src_3"] + high confidence + non-empty basis)."""
    return {
        "ok": True,
        "seed": {
            "seed_id": seed_id,
            "make": "Hyundai",
            "model": "Creta",
            "year_start": 2020,
            "year_end": 2026,
            "market": "IL",
        },
        "variants": [],
        "no_variants_reason": "model_not_sold_in_market",
        "no_variants_evidence": [],
        "no_variants_source_ids": ["src_1", "src_2", "src_3"],
        "no_variants_source_basis": _CRETA_SOURCE_BASIS,
        "no_variants_confidence": "high",
        "no_variants_reason_detail": None,
        "discovery": {"ok": True, "data": {}, "error": None, "gemini_metadata": {}},
        "error": None,
    }


class TestCretaPlaceholderProofRejection:
    """A. _is_no_variants_proof_valid rejects ["src_1","src_2","src_3"] even when
    source_basis and confidence are otherwise valid."""

    def test_src_1_2_3_high_confidence_is_false(self):
        assert _is_no_variants_proof_valid(
            "model_not_sold_in_market",
            dedupe_proof=[],
            no_variants_source_ids=["src_1", "src_2", "src_3"],
            no_variants_source_basis=_CRETA_SOURCE_BASIS,
            no_variants_confidence="high",
        ) is False

    def test_has_dedupe_proof_with_placeholders_is_false(self):
        assert _has_dedupe_or_no_variants_proof(
            _CRETA_SEED,
            [],
            0,
            0,
            "model_not_sold_in_market",
            no_variants_source_ids=["src_1", "src_2", "src_3"],
            no_variants_source_basis=_CRETA_SOURCE_BASIS,
            no_variants_confidence="high",
        ) is False


class TestCretaPlaceholderProofPQRegression:
    """B. run_selected_seed() with problem_queue mode must block placeholder source_ids:
    ok=False, no save, no push, seed remains in needs_retry, not added to processed."""

    def _stub_load(self, canonical: dict):
        def _load():
            return copy.deepcopy(canonical)
        return _load

    def test_ok_is_false(self, monkeypatch):
        canonical = _make_creta_pq_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))
        result = run_selected_seed(
            _CRETA_SEED,
            run_seed_fn=_creta_placeholder_runner,
            push_fn=None,
        )
        assert result["ok"] is False

    def test_error_contains_zero_variant_without_proof(self, monkeypatch):
        canonical = _make_creta_pq_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))
        result = run_selected_seed(
            _CRETA_SEED,
            run_seed_fn=_creta_placeholder_runner,
            push_fn=None,
        )
        err = result.get("error") or ""
        assert "zero_variant_without_proof" in err or "invalid_placeholder_sources" in err

    def test_save_is_none(self, monkeypatch):
        canonical = _make_creta_pq_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))
        result = run_selected_seed(
            _CRETA_SEED,
            run_seed_fn=_creta_placeholder_runner,
            push_fn=None,
        )
        assert result.get("save") is None

    def test_push_is_none(self, monkeypatch):
        canonical = _make_creta_pq_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))
        result = run_selected_seed(
            _CRETA_SEED,
            run_seed_fn=_creta_placeholder_runner,
            push_fn=None,
        )
        assert result.get("push") is None

    def test_seed_remains_in_needs_retry_after_batch(self, monkeypatch):
        """run_next_model() must leave Creta in needs_retry."""
        canonical = _make_creta_pq_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))

        def _noop_push(c, commit_message=None):
            return {"ok": True, "skipped": True}

        result = run_next_model(
            batch_size=1,
            run_seed_fn=_creta_placeholder_runner,
            push_fn=_noop_push,
        )
        seed_result = result["results"][0]
        assert seed_result.get("needs_retry_after") == 1

    def test_seed_not_added_to_processed(self, monkeypatch):
        """Seed must not appear in processed after failed run."""
        canonical = _make_creta_pq_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))

        def _noop_push(c, commit_message=None):
            return {"ok": True, "skipped": True}

        result = run_next_model(
            batch_size=1,
            run_seed_fn=_creta_placeholder_runner,
            push_fn=_noop_push,
        )
        assert result.get("processed_count", 0) == 0

    def test_save_ok_false_in_batch(self, monkeypatch):
        """Per-seed save_ok must be false — no canonical written."""
        canonical = _make_creta_pq_canonical()
        monkeypatch.setattr("engine.run_next.load_canonical", self._stub_load(canonical))

        def _noop_push(c, commit_message=None):
            return {"ok": True, "skipped": True}

        result = run_next_model(
            batch_size=1,
            run_seed_fn=_creta_placeholder_runner,
            push_fn=_noop_push,
        )
        assert not result["results"][0].get("save_ok")


class TestValidateCanonicalBlocksPlaceholderZVR:
    """C. validate_canonical() must reject a canonical where seed_accounting
    has proof_status='proven' with only placeholder source_ids — this closes
    the direct-write bypass that allowed the Creta false-closure."""

    def _canonical_with_proven_placeholder_zvr(self) -> dict:
        """Build a canonical that has the exact bad state: proven ZVR with
        source_ids=["src_1","src_2","src_3"]."""
        return {
            "accumulated_clean_export": {"variants": []},
            "batch_state": {
                "active_mode": "rerun_queue_required",
                "next_seed_id": "volkswagen__jetta__1990__2026__il",
                "last_completed_seed_id": "volkswagen__golf_plus__2005__2020__il",
                "processed_seed_ids": [_CRETA_SEED],
                "needs_retry_seed_ids": [],
                "skipped_seed_ids": [],
                "failed_seed_ids": [],
                "seed_accounting": {
                    _CRETA_SEED: {
                        "seed_id": _CRETA_SEED,
                        "status": "resolved",
                        "marked_processed": True,
                        "zero_variant_resolution": {
                            "resolved": True,
                            "reason": "model_not_sold_in_market",
                            "proof_status": "proven",
                            "source_ids": ["src_1", "src_2", "src_3"],
                            "source_basis": _CRETA_SOURCE_BASIS,
                            "confidence": "high",
                        },
                    }
                },
            },
            "counts": {"total_variants": 0},
            "metadata": {"schema_version": "3.0"},
        }

    def test_validate_rejects_proven_placeholder_zvr(self):
        """validate_canonical() must return ok=False when a proven ZVR has only
        placeholder source_ids."""
        data = self._canonical_with_proven_placeholder_zvr()
        ok, errs = validate_canonical(data)
        assert ok is False
        assert any("invalid_placeholder_sources_in_proven_zvr" in e for e in errs)

    def test_error_names_the_seed(self):
        """The error must name the offending seed_id for traceability."""
        data = self._canonical_with_proven_placeholder_zvr()
        _, errs = validate_canonical(data)
        assert any(_CRETA_SEED in e for e in errs)

    def test_valid_zvr_with_real_source_passes(self):
        """A proven ZVR with real (non-placeholder) source_ids must still pass."""
        data = self._canonical_with_proven_placeholder_zvr()
        # Replace placeholder with real URL
        data["batch_state"]["seed_accounting"][_CRETA_SEED]["zero_variant_resolution"][
            "source_ids"
        ] = ["https://www.colmobil.co.il/il-models", "yad2-creta-2024"]
        # Fix processed/needs_retry count to match
        ok, errs = validate_canonical(data)
        placeholder_errors = [e for e in errs if "invalid_placeholder_sources_in_proven_zvr" in e]
        assert not placeholder_errors, f"Unexpected placeholder errors: {placeholder_errors}"

    def test_unproven_zvr_with_placeholders_passes(self):
        """A ZVR with proof_status='invalid_placeholder_sources' (not 'proven') must
        NOT be flagged by the new check."""
        data = self._canonical_with_proven_placeholder_zvr()
        data["batch_state"]["seed_accounting"][_CRETA_SEED]["zero_variant_resolution"][
            "proof_status"
        ] = "invalid_placeholder_sources"
        ok, errs = validate_canonical(data)
        placeholder_errors = [e for e in errs if "invalid_placeholder_sources_in_proven_zvr" in e]
        assert not placeholder_errors
