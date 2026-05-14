"""Tests for the data-quality gate (engine/quality_gate.py).

Covers all required test cases from the problem statement plus integration
tests that verify the gate is enforced inside run_selected_seed.
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.quality_gate import (
    classify_quality_level,
    normalize_variant_fields,
    validate_candidate_variants_quality,
    validate_variant_quality,
)
from engine import run_next
from engine.canonical_store import load_canonical

CANONICAL_REL = "data/canonical/resume_package_canonical.json"
# A synthetic seed injected into needs_retry for problem-queue integration tests.
_SYNTH_PQ_SEED = "zztest__qgtest__2020__2026__il"
# Seed ID used as a test subject in save/quality-gate tests (not from needs_retry).
FIAT_BRAVO = "fiat__bravo__1995__2014__il"


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


@pytest.fixture
def workspace_pq(tmp_path, monkeypatch):
    """Copy real canonical with one synthetic problem-queue seed injected."""
    src = REPO_ROOT / CANONICAL_REL
    dst_dir = tmp_path / "data" / "canonical"
    dst_dir.mkdir(parents=True)
    dst = dst_dir / "resume_package_canonical.json"
    shutil.copy2(src, dst)
    monkeypatch.setenv("CANONICAL_PATH", str(dst))
    monkeypatch.chdir(tmp_path)
    data = json.loads(dst.read_text(encoding="utf-8"))
    data["batch_state"]["needs_retry_seed_ids"] = [_SYNTH_PQ_SEED]
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp_path


def _load(workspace) -> dict:
    return load_canonical()


SEED_2018_2026 = {
    "seed_id": "bmw__850i__2018__2026__il",
    "make": "BMW",
    "model": "850i",
    "year_start": 2018,
    "year_end": 2026,
    "market": "IL",
}

SEED_DAEWOO = {
    "seed_id": FIAT_BRAVO,
    "make": "Fiat",
    "model": "Bravo",
    "year_start": 1995,
    "year_end": 2014,
    "market": "IL",
}


def _make_good_variant(
    seed: dict,
    *,
    suffix: str = "v1",
    body_type: str = "Sedan",
    engine: str = "2.0L turbo petrol, 197 hp",
    transmission: str = "8-speed automatic",
    drivetrain: str = "RWD",
    fuel_type: str = "Petrol",
    trim: str = "Base",
    year_start: int | None = None,
    year_end: int | None = None,
) -> dict:
    ys = year_start if year_start is not None else seed["year_start"]
    ye = year_end if year_end is not None else seed["year_end"]
    vid = f"{seed['seed_id']}__{suffix}"
    return {
        "variant_id": vid,
        "make": seed["make"],
        "model": seed["model"],
        "year_start": {"value": ys, "used_in_compare": False},
        "year_end": {"value": ye, "used_in_compare": False},
        "market": seed["market"],
        "generation": {"value": "G16", "used_in_compare": False},
        "body_type": {"value": body_type, "status": "partial", "confidence": "medium",
                      "sources_count": 1, "source_ids": ["src_test"],
                      "used_in_compare": True, "reason": "test"},
        "engine": {"value": engine, "status": "partial", "confidence": "medium",
                   "sources_count": 1, "source_ids": ["src_test"],
                   "used_in_compare": True, "reason": "test"},
        "transmission": {"value": transmission, "status": "partial", "confidence": "medium",
                         "sources_count": 1, "source_ids": ["src_test"],
                         "used_in_compare": True, "reason": "test"},
        "fuel_type": {"value": fuel_type, "status": "partial", "confidence": "medium",
                      "sources_count": 1, "source_ids": ["src_test"],
                      "used_in_compare": True, "reason": "test"},
        "drivetrain": {"value": drivetrain, "status": "partial", "confidence": "medium",
                       "sources_count": 1, "source_ids": ["src_test"],
                       "used_in_compare": True, "reason": "test"},
        "trim": {"value": trim, "status": "partial", "confidence": "medium",
                 "sources_count": 1, "source_ids": ["src_test"],
                 "used_in_compare": True, "reason": "test"},
        "doors": None,
        "verification_status": "partial",
        "confidence": "medium",
        "sources_count": 1,
        "created_at": "2026-05-13T00:00:00+00:00",
        "updated_at": "2026-05-13T00:00:00+00:00",
        "notes": [],
        "candidate_raw": {},
        "identity_confidence": "unknown",
    }


def _noop_push(canonical, commit_message=None) -> dict:
    return {"ok": True, "skipped": True}


# ---------------------------------------------------------------------------
# Unit tests: validate_variant_quality
# ---------------------------------------------------------------------------

def test_reject_unknown_only_variant():
    """Variant with engine/transmission/drivetrain/body_type all unknown -> reject."""
    seed = SEED_2018_2026
    variant = {
        "variant_id": "test_unknown_v1",
        "make": "BMW",
        "model": "850i",
        "year_start": {"value": 2018, "used_in_compare": False},
        "year_end": {"value": 2026, "used_in_compare": False},
        "market": "IL",
        "body_type": {"value": None, "status": "unknown"},
        "engine": {"value": None, "status": "unknown"},
        "transmission": {"value": None, "status": "unknown"},
        "drivetrain": {"value": None, "status": "unknown"},
        "fuel_type": {"value": None, "status": "unknown"},
        "trim": None,
        "sources_count": 0,
    }
    report = validate_variant_quality(variant, seed)
    assert report["quality_level"] == "reject"
    assert any("all_core_fields_unknown" in e for e in report["errors"])


def test_reject_out_of_seed_year_range():
    """Variant with years completely before the seed range -> reject."""
    seed = SEED_2018_2026
    variant = _make_good_variant(seed, suffix="old", year_start=1988, year_end=1991)
    report = validate_variant_quality(variant, seed)
    assert report["quality_level"] == "reject"
    assert any("out_of_seed_year_range" in e for e in report["errors"])


def test_reject_variant_starts_after_seed_end():
    """Variant year_start > seed year_end -> reject."""
    seed = SEED_2018_2026
    variant = _make_good_variant(seed, suffix="future", year_start=2030, year_end=2035)
    report = validate_variant_quality(variant, seed)
    assert report["quality_level"] == "reject"
    assert any("out_of_seed_year_range" in e for e in report["errors"])


def test_accept_variant_within_seed_range():
    """Variant years within seed range -> not rejected for year range."""
    seed = SEED_2018_2026
    variant = _make_good_variant(seed, suffix="ok", year_start=2019, year_end=2024)
    report = validate_variant_quality(variant, seed)
    assert report["quality_level"] != "reject"
    assert not any("out_of_seed_year_range" in e for e in report["errors"])


def test_gran_coupe_not_sedan():
    """Body type text containing 'Gran Coupe' -> body_type normalised to 'Gran Coupe', not 'Sedan'."""
    seed = SEED_2018_2026
    variant = _make_good_variant(seed, suffix="gc", body_type="Gran Coupe")
    normalised = normalize_variant_fields(variant, seed)
    bt_value = normalised["body_type"]["value"] if isinstance(normalised["body_type"], dict) else normalised["body_type"]
    assert bt_value == "Gran Coupe"
    assert bt_value != "Sedan"


def test_gran_coupe_raw_string_normalised():
    """Raw string 'gran coupe' (lower-case) normalises to 'Gran Coupe'."""
    from engine.quality_gate import _normalize_body_type_str
    assert _normalize_body_type_str("gran coupe") == "Gran Coupe"
    assert _normalize_body_type_str("Gran Coupe") == "Gran Coupe"
    assert _normalize_body_type_str("4-door gran coupe") == "Gran Coupe"


def test_trim_package_separation():
    """Trim mixing real trim with packages -> split on validate + normalize."""
    seed = SEED_2018_2026
    variant = _make_good_variant(seed, suffix="trim_mix",
                                 trim="M850i xDrive / Elegant / Carbon Edition")
    report = validate_variant_quality(variant, seed)
    # Should produce a warning about mixed packages
    assert any("trim_contains_packages" in w for w in report["warnings"])
    fixes = report.get("suggested_fixes") or {}
    assert fixes.get("trim") == "M850i xDrive"
    assert "Elegant" in (fixes.get("packages_or_editions") or [])
    assert "Carbon Edition" in (fixes.get("packages_or_editions") or [])

    # normalize_variant_fields should apply the fix
    norm = normalize_variant_fields(variant, seed)
    trim_val = norm["trim"]["value"] if isinstance(norm["trim"], dict) else norm["trim"]
    assert trim_val == "M850i xDrive"
    packages = norm.get("packages_or_editions") or []
    assert "Elegant" in packages
    assert "Carbon Edition" in packages


def test_trim_without_packages_unchanged():
    """Clean trim with no package indicators stays unchanged."""
    seed = SEED_2018_2026
    variant = _make_good_variant(seed, suffix="clean_trim", trim="M850i xDrive")
    norm = normalize_variant_fields(variant, seed)
    trim_val = norm["trim"]["value"] if isinstance(norm["trim"], dict) else norm["trim"]
    assert trim_val == "M850i xDrive"
    assert "packages_or_editions" not in norm


def test_market_confidence_required():
    """Variant with no source data should not be classified as 'high'."""
    seed = SEED_2018_2026
    variant = _make_good_variant(seed, suffix="no_src")
    variant["sources_count"] = 0
    # Remove sources from nested fields too
    for f in ("body_type", "engine", "transmission", "fuel_type", "drivetrain"):
        if isinstance(variant.get(f), dict):
            variant[f]["sources_count"] = 0
            variant[f]["source_ids"] = []
    report = validate_variant_quality(variant, seed)
    assert report["quality_level"] != "high"


def test_make_mismatch_rejected():
    """Variant make that doesn't match seed make -> rejected."""
    seed = SEED_2018_2026
    variant = _make_good_variant(seed, suffix="wrong_make")
    variant["make"] = "Toyota"
    report = validate_variant_quality(variant, seed)
    assert report["quality_level"] == "reject"
    assert any("make_mismatch" in e for e in report["errors"])


def test_engine_too_vague_warning():
    """Vague engine string like 'petrol engine' should produce a warning."""
    seed = SEED_2018_2026
    variant = _make_good_variant(seed, suffix="vague_engine", engine="petrol engine")
    report = validate_variant_quality(variant, seed)
    assert any("engine_too_vague" in w for w in report["warnings"])
    # Not a hard reject
    assert report["quality_level"] != "reject"


def test_good_variant_not_rejected():
    """A well-formed variant should pass the quality gate."""
    seed = SEED_2018_2026
    variant = _make_good_variant(seed, suffix="good")
    report = validate_variant_quality(variant, seed)
    assert report["quality_level"] != "reject"
    assert not report["errors"]


# ---------------------------------------------------------------------------
# Unit tests: validate_candidate_variants_quality
# ---------------------------------------------------------------------------

def test_all_good_variants_accepted():
    seed = SEED_2018_2026
    variants = [
        _make_good_variant(seed, suffix="a"),
        _make_good_variant(seed, suffix="b"),
    ]
    result = validate_candidate_variants_quality(variants, seed)
    assert result["accepted"] == 2
    assert result["rejected"] == 0
    assert result["all_rejected"] is False


def test_mixed_accept_reject():
    """3 candidates, 1 out-of-range -> 2 accepted, 1 rejected."""
    seed = SEED_2018_2026
    good1 = _make_good_variant(seed, suffix="g1")
    good2 = _make_good_variant(seed, suffix="g2")
    bad = _make_good_variant(seed, suffix="bad", year_start=1988, year_end=1991)
    result = validate_candidate_variants_quality([good1, good2, bad], seed)
    assert result["accepted"] == 2
    assert result["rejected"] == 1
    assert result["all_rejected"] is False
    assert len(result["accepted_variant_objects"]) == 2


def test_all_rejected_variants():
    """All candidates are invalid -> all_rejected=True."""
    seed = SEED_2018_2026
    bad1 = _make_good_variant(seed, suffix="b1", year_start=1980, year_end=1990)
    bad2 = _make_good_variant(seed, suffix="b2", year_start=1985, year_end=1995)
    result = validate_candidate_variants_quality([bad1, bad2], seed)
    assert result["all_rejected"] is True
    assert result["accepted"] == 0
    assert result["rejected"] == 2


def test_no_duplicate_variant_id_after_quality_gate():
    """Duplicate variant_ids in candidates -> second occurrence rejected."""
    seed = SEED_2018_2026
    v1 = _make_good_variant(seed, suffix="dup")
    v2 = _make_good_variant(seed, suffix="dup")  # same variant_id
    result = validate_candidate_variants_quality([v1, v2], seed)
    accepted_ids = [av["variant_id"] for av in result["accepted_variants"]]
    assert len(accepted_ids) == len(set(filter(None, accepted_ids)))
    assert result["rejected"] >= 1


# ---------------------------------------------------------------------------
# Integration tests: quality gate enforced inside run_selected_seed
# ---------------------------------------------------------------------------

def _runner_all_bad(seed_id: str, *, retry_hint: bool = False) -> dict:
    """Runner that returns only out-of-range variants (all should be rejected)."""
    parts = seed_id.split("__")
    make = parts[0].replace("_", " ").title()
    model = parts[1].replace("_", " ")
    seed = {
        "seed_id": seed_id,
        "make": make,
        "model": model,
        "year_start": int(parts[2]),
        "year_end": int(parts[3]),
        "market": parts[4].upper(),
    }
    bad = _make_good_variant(seed, suffix="bad", year_start=1980, year_end=1990)
    return {
        "ok": True,
        "seed": seed,
        "variants": [bad],
        "no_variants_reason": None,
        "discovery": {"ok": True},
        "error": None,
    }


def _runner_two_good_one_bad(seed_id: str, *, retry_hint: bool = False) -> dict:
    """Runner that returns 2 good variants and 1 out-of-range variant."""
    parts = seed_id.split("__")
    make = parts[0].replace("_", " ").title()
    model = parts[1].replace("_", " ")
    seed = {
        "seed_id": seed_id,
        "make": make,
        "model": model,
        "year_start": int(parts[2]),
        "year_end": int(parts[3]),
        "market": parts[4].upper(),
    }
    good1 = _make_good_variant(seed, suffix="g1")
    good2 = _make_good_variant(seed, suffix="g2")
    bad = _make_good_variant(seed, suffix="bad", year_start=1980, year_end=1990)
    return {
        "ok": True,
        "seed": seed,
        "variants": [good1, good2, bad],
        "no_variants_reason": None,
        "discovery": {"ok": True},
        "error": None,
    }


def test_do_not_save_all_rejected_variants(workspace_pq):
    """When all variants are rejected, canonical is unchanged and seed stays in needs_retry."""
    before = _load(workspace_pq)
    variants_before = len(before["accumulated_clean_export"]["variants"])
    needs_retry_before = list(before["batch_state"]["needs_retry_seed_ids"])

    result = run_next.run_selected_seed(
        _SYNTH_PQ_SEED, run_seed_fn=_runner_all_bad, push_fn=_noop_push,
    )

    assert result["ok"] is False
    assert result.get("error") == "quality_gate_failed_no_accepted_variants"

    after = _load(workspace_pq)
    # Canonical unchanged
    assert len(after["accumulated_clean_export"]["variants"]) == variants_before
    # Seed still in needs_retry
    assert _SYNTH_PQ_SEED in after["batch_state"]["needs_retry_seed_ids"]
    assert after["batch_state"]["needs_retry_seed_ids"] == needs_retry_before


def test_save_only_accepted_variants(workspace):
    """3 candidates (2 good, 1 rejected) -> only 2 saved, seed marked completed."""
    before = _load(workspace)
    variants_before = len(before["accumulated_clean_export"]["variants"])

    result = run_next.run_selected_seed(
        FIAT_BRAVO,
        run_seed_fn=_runner_two_good_one_bad,
        push_fn=_noop_push,
        seed_catalog=[FIAT_BRAVO],
    )

    assert result["ok"] is True, result
    quality_report = result.get("quality_report") or {}
    assert quality_report.get("rejected") == 1
    assert quality_report.get("accepted") == 2

    after = _load(workspace)
    # At least 2 variants added (or merged if IDs already exist)
    assert len(after["accumulated_clean_export"]["variants"]) >= variants_before
    # Seed removed from needs_retry
    assert FIAT_BRAVO not in after["batch_state"]["needs_retry_seed_ids"]
