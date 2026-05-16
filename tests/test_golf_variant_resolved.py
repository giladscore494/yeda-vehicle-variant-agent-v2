"""Regression test: volkswagen__golf_variant__1993__2026__il successfully reran.

Replaces the stale test_golf_variant_reset.py. Golf Variant was previously
reset to needs_retry, then reran successfully, adding 7 canonical variants.
These tests verify the resolved/completed state.
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
MIN_EXPECTED_VARIANTS = 7


@pytest.fixture
def canonical() -> dict:
    path = REPO_ROOT / CANONICAL_REL
    return json.loads(path.read_text(encoding="utf-8"))


def test_in_processed_seed_ids(canonical):
    """Golf Variant seed must be in processed_seed_ids after successful rerun."""
    assert SEED in canonical["batch_state"]["processed_seed_ids"], (
        f"{SEED} expected in processed_seed_ids"
    )


def test_not_in_needs_retry_seed_ids(canonical):
    """Golf Variant seed must NOT be in needs_retry_seed_ids."""
    assert SEED not in canonical["batch_state"]["needs_retry_seed_ids"], (
        f"{SEED} must not be in needs_retry_seed_ids after successful run"
    )


def test_seed_accounting_status_resolved(canonical):
    """seed_accounting.status must be 'resolved'."""
    sa = canonical["batch_state"].get("seed_accounting", {})
    record = sa.get(SEED)
    assert record is not None, f"seed_accounting entry missing for {SEED}"
    assert record.get("status") == "resolved", (
        f"Expected status='resolved', got {record.get('status')!r}"
    )


def test_seed_accounting_marked_processed(canonical):
    """seed_accounting.marked_processed must be True."""
    sa = canonical["batch_state"].get("seed_accounting", {})
    record = sa.get(SEED)
    assert record is not None
    assert record.get("marked_processed") is True, (
        f"Expected marked_processed=True, got {record.get('marked_processed')!r}"
    )


def test_canonical_has_golf_variant_variants(canonical):
    """Canonical must contain at least 7 Golf Variant variants."""
    variants = canonical.get("accumulated_clean_export", {}).get("variants") or []
    gv_variants = [
        v for v in variants
        if isinstance(v, dict)
        and (v.get("make") or "").lower() == "volkswagen"
        and "variant" in (v.get("model") or "").lower()
    ]
    assert len(gv_variants) >= MIN_EXPECTED_VARIANTS, (
        f"Expected >= {MIN_EXPECTED_VARIANTS} Golf Variant variants, "
        f"found {len(gv_variants)}"
    )


def test_next_seed_id(canonical):
    """next_seed_id must be volkswagen__jetta__1990__2026__il (cursor after Golf Plus)."""
    assert canonical["batch_state"]["next_seed_id"] == "volkswagen__jetta__1990__2026__il", (
        f"Expected next_seed_id=volkswagen__jetta__1990__2026__il, "
        f"got {canonical['batch_state']['next_seed_id']!r}"
    )


def test_last_completed_seed_id(canonical):
    """last_completed_seed_id must be volkswagen__golf_plus__2005__2020__il."""
    assert canonical["batch_state"]["last_completed_seed_id"] == (
        "volkswagen__golf_plus__2005__2020__il"
    ), (
        f"Expected last_completed_seed_id=volkswagen__golf_plus__2005__2020__il, "
        f"got {canonical['batch_state']['last_completed_seed_id']!r}"
    )
