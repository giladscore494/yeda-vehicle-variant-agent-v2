"""Focused regression test: volkswagen__golf_variant__1993__2026__il canonical reset.

Verifies that the seed was correctly removed from processed state,
added to needs_retry / false_processed tracking, and that its
seed_accounting record is no longer marked as resolved/processed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CANONICAL_REL = "data/canonical/resume_package_canonical.json"
SEED = "volkswagen__golf_variant__1993__2026__il"


@pytest.fixture
def canonical() -> dict:
    path = REPO_ROOT / CANONICAL_REL
    return json.loads(path.read_text(encoding="utf-8"))


def test_not_in_processed_seed_ids(canonical):
    """Seed must have been removed from processed_seed_ids."""
    assert SEED not in canonical["batch_state"]["processed_seed_ids"]


def test_not_in_processed_seeds(canonical):
    """Seed must have been removed from processed_seeds."""
    assert SEED not in canonical["batch_state"].get("processed_seeds", [])


def test_in_needs_retry_seed_ids(canonical):
    """Seed must be in needs_retry_seed_ids so it will be reprocessed."""
    assert SEED in canonical["batch_state"]["needs_retry_seed_ids"]


def test_in_false_processed_seed_ids(canonical):
    """Seed must be in false_processed_seed_ids."""
    assert SEED in canonical["batch_state"].get("false_processed_seed_ids", [])


def test_seed_accounting_not_resolved(canonical):
    """seed_accounting record must not be status=resolved or marked_processed=True."""
    sa = canonical["batch_state"].get("seed_accounting", {})
    record = sa.get(SEED)
    assert record is not None, f"seed_accounting entry missing for {SEED}"
    assert record.get("status") != "resolved", (
        f"seed_accounting.status must not be 'resolved', got {record.get('status')!r}"
    )
    assert record.get("marked_processed") is not True, (
        f"seed_accounting.marked_processed must not be True, got {record.get('marked_processed')!r}"
    )


def test_not_in_no_variants_by_seed(canonical):
    """Seed must not appear in no_variants_by_seed resolved tracking."""
    nvbs = canonical["batch_state"].get("no_variants_by_seed", {})
    assert SEED not in nvbs


def test_no_canonical_variants_for_seed(canonical):
    """No accumulated variants should be attributed to this seed."""
    variants = (
        canonical.get("accumulated_clean_export", {}).get("variants") or []
    )
    seed_variants = [
        v for v in variants
        if isinstance(v, dict) and v.get("variant_id", "").startswith(SEED)
    ]
    assert seed_variants == [], (
        f"Expected 0 canonical variants for {SEED}, found {len(seed_variants)}"
    )


def test_last_completed_seed_id_reset(canonical):
    """last_completed_seed_id must be reset to the seed prior to golf_variant."""
    assert canonical["batch_state"]["last_completed_seed_id"] == (
        "volkswagen__golf_r__2010__2026__il"
    )


def test_next_seed_id_preserved(canonical):
    """next_seed_id must be preserved at volkswagen__golf_plus__2005__2020__il."""
    assert canonical["batch_state"]["next_seed_id"] == (
        "volkswagen__golf_plus__2005__2020__il"
    )
