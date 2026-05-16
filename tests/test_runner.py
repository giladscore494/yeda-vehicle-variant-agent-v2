"""Tests for agent/runner.py and related schema/normalize fixes.

Covers:
- FuelType.mild_hybrid enum value
- normalize_fuel_type mild hybrid recognition
- generation field stored as full VerifiedField shape
- market_scope and confidence_level preserved as top-level fields
- identity_confidence derived (not constant) from market_scope + confidence_level + sources
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.schemas import FuelType
from core.normalize import normalize_fuel_type
from agent.runner import _candidate_to_variant, _derive_identity_confidence


# ---------------------------------------------------------------------------
# Minimal seed fixture
# ---------------------------------------------------------------------------

_SEED = {
    "seed_id": "bmw__3_series__2012__2019__IL",
    "make": "BMW",
    "model": "3 Series",
    "year_start": 2012,
    "year_end": 2019,
    "market": "IL",
}


def _make_candidate(**overrides) -> dict:
    base = {
        "make": "BMW",
        "model": "3 Series",
        "year_start": 2012,
        "year_end": 2019,
        "generation": "F30",
        "body_type": "Sedan",
        "engine": "2.0L turbo petrol, 184 hp",
        "transmission": "8-speed automatic",
        "fuel_type": "Petrol",
        "drivetrain": "RWD",
        "trim": "320i",
        "market_scope": "IL-confirmed",
        "confidence_level": "high",
        "field_sources": {
            "generation": ["src_1"],
            "body_type": ["src_1"],
            "engine": ["src_1", "src_2"],
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. mild_hybrid enum
# ---------------------------------------------------------------------------

class TestMildHybridEnum:
    def test_mild_hybrid_in_fuel_type_enum(self):
        assert FuelType.mild_hybrid == "mild_hybrid"
        assert FuelType.mild_hybrid.value == "mild_hybrid"

    def test_mild_hybrid_is_distinct_from_hybrid(self):
        assert FuelType.mild_hybrid != FuelType.hybrid

    def test_existing_values_unchanged(self):
        assert FuelType.petrol.value == "petrol"
        assert FuelType.diesel.value == "diesel"
        assert FuelType.hybrid.value == "hybrid"
        assert FuelType.plug_in_hybrid.value == "plug_in_hybrid"
        assert FuelType.electric.value == "electric"


# ---------------------------------------------------------------------------
# 2. normalize_fuel_type — mild hybrid recognition
# ---------------------------------------------------------------------------

class TestNormalizeFuelTypeMildHybrid:
    def test_mild_hybrid_string(self):
        assert normalize_fuel_type("mild hybrid") == FuelType.mild_hybrid

    def test_mild_hybrid_hyphen(self):
        assert normalize_fuel_type("mild-hybrid") == FuelType.mild_hybrid

    def test_mhev_uppercase(self):
        assert normalize_fuel_type("MHEV") == FuelType.mild_hybrid

    def test_mhev_lowercase(self):
        assert normalize_fuel_type("mhev") == FuelType.mild_hybrid

    def test_petrol_mild_hybrid_compound(self):
        # "petrol" appears in string but mild hybrid must win
        assert normalize_fuel_type("petrol mild hybrid") == FuelType.mild_hybrid

    def test_mild_hybrid_petrol_compound(self):
        assert normalize_fuel_type("Mild Hybrid Petrol") == FuelType.mild_hybrid

    def test_mild_hybrid_diesel_compound(self):
        # "diesel" appears in string but mild hybrid must win
        assert normalize_fuel_type("mild hybrid diesel") == FuelType.mild_hybrid

    def test_plain_hybrid_unaffected(self):
        assert normalize_fuel_type("hybrid") == FuelType.hybrid

    def test_petrol_unaffected(self):
        assert normalize_fuel_type("petrol") == FuelType.petrol

    def test_diesel_unaffected(self):
        assert normalize_fuel_type("diesel") == FuelType.diesel

    def test_phev_unaffected(self):
        assert normalize_fuel_type("phev") == FuelType.plug_in_hybrid

    def test_electric_unaffected(self):
        assert normalize_fuel_type("electric") == FuelType.electric


# ---------------------------------------------------------------------------
# 3. generation field shape — full VerifiedField
# ---------------------------------------------------------------------------

class TestGenerationFieldShape:
    def _build(self, **overrides):
        return _candidate_to_variant(_make_candidate(**overrides), _SEED)

    def test_generation_is_dict(self):
        v = self._build()
        assert isinstance(v["generation"], dict)

    def test_generation_has_value(self):
        v = self._build()
        assert v["generation"]["value"] == "F30"

    def test_generation_has_status(self):
        v = self._build()
        assert "status" in v["generation"]
        assert v["generation"]["status"] in {"verified", "partial", "unverified", "unknown"}

    def test_generation_has_confidence(self):
        v = self._build()
        assert "confidence" in v["generation"]
        assert v["generation"]["confidence"] in {"high", "medium", "low"}

    def test_generation_has_sources_count(self):
        v = self._build()
        assert "sources_count" in v["generation"]
        assert isinstance(v["generation"]["sources_count"], int)

    def test_generation_has_source_ids(self):
        v = self._build()
        assert "source_ids" in v["generation"]
        assert isinstance(v["generation"]["source_ids"], list)

    def test_generation_has_used_in_compare(self):
        v = self._build()
        assert "used_in_compare" in v["generation"]

    def test_generation_sources_from_field_sources(self):
        v = self._build()
        # field_sources["generation"] = ["src_1"] -> sources_count == 1
        assert v["generation"]["sources_count"] == 1
        assert "src_1" in v["generation"]["source_ids"]

    def test_generation_no_sources_when_not_in_field_sources(self):
        cand = _make_candidate()
        cand["field_sources"] = {}  # no generation sources
        v = _candidate_to_variant(cand, _SEED)
        assert v["generation"]["sources_count"] == 0

    def test_generation_none_value_stored_correctly(self):
        v = self._build(generation=None)
        assert v["generation"]["value"] is None


# ---------------------------------------------------------------------------
# 4. market_scope and confidence_level preserved as top-level fields
# ---------------------------------------------------------------------------

class TestMarketScopeAndConfidenceLevel:
    def test_market_scope_stored(self):
        v = _candidate_to_variant(_make_candidate(market_scope="IL-confirmed"), _SEED)
        assert v["market_scope"] == "IL-confirmed"

    def test_confidence_level_stored(self):
        v = _candidate_to_variant(_make_candidate(confidence_level="high"), _SEED)
        assert v["confidence_level"] == "high"

    def test_market_scope_il_likely(self):
        v = _candidate_to_variant(_make_candidate(market_scope="IL-likely"), _SEED)
        assert v["market_scope"] == "IL-likely"

    def test_market_scope_global_reference(self):
        v = _candidate_to_variant(
            _make_candidate(market_scope="global-reference-only"), _SEED
        )
        assert v["market_scope"] == "global-reference-only"

    def test_missing_market_scope_stored_as_none(self):
        cand = _make_candidate()
        del cand["market_scope"]
        v = _candidate_to_variant(cand, _SEED)
        assert v["market_scope"] is None

    def test_missing_confidence_level_stored_as_none(self):
        cand = _make_candidate()
        del cand["confidence_level"]
        v = _candidate_to_variant(cand, _SEED)
        assert v["confidence_level"] is None


# ---------------------------------------------------------------------------
# 5. identity_confidence derived, not constant
# ---------------------------------------------------------------------------

class TestIdentityConfidenceDerived:
    """Unit tests for _derive_identity_confidence."""

    def test_il_confirmed_high_with_sources_gives_high(self):
        assert _derive_identity_confidence("IL-confirmed", "high", 2) == "high"

    def test_il_confirmed_high_one_source_gives_high(self):
        assert _derive_identity_confidence("IL-confirmed", "high", 1) == "high"

    def test_il_confirmed_high_no_sources_gives_low(self):
        assert _derive_identity_confidence("IL-confirmed", "high", 0) == "low"

    def test_il_confirmed_medium_gives_medium(self):
        assert _derive_identity_confidence("IL-confirmed", "medium", 1) == "medium"

    def test_il_likely_high_gives_medium(self):
        assert _derive_identity_confidence("IL-likely", "high", 1) == "medium"

    def test_il_likely_medium_gives_medium(self):
        # IL-likely + medium confidence with sources is still worth "medium" identity
        assert _derive_identity_confidence("IL-likely", "medium", 1) == "medium"

    def test_global_reference_gives_low(self):
        assert _derive_identity_confidence("global-reference-only", "high", 5) == "low"

    def test_uncertain_gives_low(self):
        assert _derive_identity_confidence("uncertain", "high", 5) == "low"

    def test_empty_scope_gives_low(self):
        assert _derive_identity_confidence("", "high", 5) == "low"

    def test_empty_level_gives_low(self):
        assert _derive_identity_confidence("IL-confirmed", "", 1) == "low"

    def test_none_inputs_give_low(self):
        assert _derive_identity_confidence(None, None, 0) == "low"  # type: ignore[arg-type]  # testing None guard in helper

    def test_identity_confidence_not_constant_in_variant(self):
        """Verify the variant dict itself has a non-constant identity_confidence."""
        v_high = _candidate_to_variant(
            _make_candidate(market_scope="IL-confirmed", confidence_level="high"), _SEED
        )
        v_low = _candidate_to_variant(
            _make_candidate(market_scope="global-reference-only", confidence_level="medium"),
            _SEED,
        )
        # They must not both be "unknown", and they must differ
        assert v_high["identity_confidence"] != "unknown"
        assert v_low["identity_confidence"] != "unknown"
        assert v_high["identity_confidence"] != v_low["identity_confidence"]
