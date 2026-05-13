"""Contract tests for the clean vehicle-variant engine."""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine import queue, run_next
from engine.canonical_store import load_canonical, save_canonical_atomic


CANONICAL_REL = "data/canonical/resume_package_canonical.json"
HAVAL = "haval__h6__2022__2026__il"
GMC = "gmc__yukon__2000__2026__il"
BMW = "bmw__850i__2018__2026__il"
Z4 = "bmw__z4_sdrive20i__2019__2026__il"
DAEWOO = "daewoo__lacetti__2003__2011__il"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Copy the real canonical into a temp workspace so tests don't mutate it."""
    src = REPO_ROOT / CANONICAL_REL
    dst_dir = tmp_path / "data" / "canonical"
    dst_dir.mkdir(parents=True)
    dst = dst_dir / "resume_package_canonical.json"
    shutil.copy2(src, dst)
    monkeypatch.setenv("CANONICAL_PATH", str(dst))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _load(workspace) -> dict:
    return load_canonical()


# ---- discovery stubs ---------------------------------------------------------

def _make_fake_variant(seed_id: str, suffix: str = "test") -> dict:
    parts = seed_id.split("__")
    make = parts[0].replace("_", " ").title()
    model = parts[1].replace("_", " ")
    ys = int(parts[2])
    ye = int(parts[3])
    market = parts[4].upper()
    vid = f"{seed_id}_{suffix}_synthetic"
    return {
        "variant_id": vid,
        "make": make,
        "model": model,
        "aliases": [],
        "year_start": {"value": ys, "used_in_compare": False},
        "year_end": {"value": ye, "used_in_compare": False},
        "market": market,
        "generation": {"value": "Test Gen", "used_in_compare": False},
        "body_type": {"value": "sedan", "status": "partial", "confidence": "medium",
                      "sources_count": 1, "source_ids": ["src_test"],
                      "used_in_compare": True, "reason": "test"},
        "seats": {"value": 5, "status": "partial", "confidence": "medium",
                  "sources_count": 1, "source_ids": ["src_test"],
                  "used_in_compare": True, "reason": "test"},
        "engine": {"value": "2.0L", "status": "partial", "confidence": "medium",
                   "sources_count": 1, "source_ids": ["src_test"],
                   "used_in_compare": True, "reason": "test"},
        "transmission": {"value": "automatic", "status": "partial", "confidence": "medium",
                         "sources_count": 1, "source_ids": ["src_test"],
                         "used_in_compare": True, "reason": "test"},
        "fuel_type": {"value": "petrol", "status": "partial", "confidence": "medium",
                      "sources_count": 1, "source_ids": ["src_test"],
                      "used_in_compare": True, "reason": "test"},
        "drivetrain": {"value": "RWD", "status": "partial", "confidence": "medium",
                       "sources_count": 1, "source_ids": ["src_test"],
                       "used_in_compare": True, "reason": "test"},
        "trim": None,
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


def _runner_returning_one(seed_id: str, *, retry_hint: bool = False) -> dict:
    return {
        "ok": True,
        "seed": {"seed_id": seed_id},
        "variants": [_make_fake_variant(seed_id)],
        "no_variants_reason": None,
        "discovery": {"ok": True},
        "error": None,
    }


def _noop_push(canonical, commit_message=None) -> dict:
    return {"ok": True, "skipped": True}


def _failing_push(canonical, commit_message=None) -> dict:
    return {"ok": False, "error": "simulated push failure"}


# ---- tests -------------------------------------------------------------------

def test_clean_canonical_loads(workspace):
    canonical = _load(workspace)
    variants = canonical["accumulated_clean_export"]["variants"]
    bs = canonical["batch_state"]
    assert len(variants) == 1323
    assert len(bs["processed_seed_ids"]) == 384
    assert len(bs["needs_retry_seed_ids"]) == 54
    assert bs["needs_retry_seed_ids"][0] == BMW

    text = json.dumps(canonical)
    assert '"s1"' not in text

    vids = [v["variant_id"] for v in variants]
    assert len(vids) == len(set(vids))


def test_select_next_problem_seed(workspace):
    canonical = _load(workspace)
    selection = queue.select_next_seed(canonical)
    assert selection["mode"] == "problem_queue"
    assert selection["selected_seed_id"] == BMW
    bs = canonical["batch_state"]
    assert bs["next_seed_id"] == HAVAL
    assert bs["last_completed_seed_id"] == GMC


def test_progress_before_bmw(workspace):
    canonical = _load(workspace)
    prog = queue.compute_problem_queue_progress(canonical)
    assert prog["total"] == 54
    assert prog["completed"] == 0
    assert prog["pending"] == 54
    assert prog["current_position"] == "1 / 54"
    assert prog["current_seed"] == BMW


def test_bmw_success_persists_before_progress(workspace):
    before = _load(workspace)
    variants_before = len(before["accumulated_clean_export"]["variants"])

    result = run_next.run_selected_seed(
        BMW, run_seed_fn=_runner_returning_one, push_fn=_noop_push,
    )
    assert result["ok"] is True, result
    assert result["seed_id"] == BMW

    after = _load(workspace)
    bs = after["batch_state"]
    assert BMW not in bs["needs_retry_seed_ids"]
    assert bs["needs_retry_seed_ids"][0] == Z4
    assert len(bs["needs_retry_seed_ids"]) == 53

    prog = queue.compute_problem_queue_progress(after)
    assert prog["total"] == 54
    assert prog["pending"] == 53
    assert prog["completed"] == 1
    assert prog["current_position"] == "2 / 54"
    assert prog["current_seed"] == Z4

    assert len(after["accumulated_clean_export"]["variants"]) >= variants_before
    # Normal cursor frozen
    assert bs["next_seed_id"] == HAVAL
    assert bs["last_completed_seed_id"] == GMC


def test_failed_save_does_not_advance(workspace, monkeypatch):
    before = _load(workspace)
    variants_before = len(before["accumulated_clean_export"]["variants"])

    # Force save to fail by monkeypatching save_canonical_atomic
    def _fake_save(data, path=None):
        return {"ok": False, "path": None, "error": "simulated save failure"}

    monkeypatch.setattr("engine.run_next.save_canonical_atomic", _fake_save)

    result = run_next.run_selected_seed(
        BMW, run_seed_fn=_runner_returning_one, push_fn=_noop_push,
    )
    assert result["ok"] is False
    assert "simulated save failure" in (result.get("error") or "")

    after = _load(workspace)
    bs = after["batch_state"]
    assert bs["needs_retry_seed_ids"][0] == BMW
    assert len(bs["needs_retry_seed_ids"]) == 54
    assert len(after["accumulated_clean_export"]["variants"]) == variants_before

    prog = queue.compute_problem_queue_progress(after)
    assert prog["pending"] == 54
    assert prog["completed"] == 0
    assert prog["current_position"] == "1 / 54"


def test_z4_after_bmw(workspace):
    r1 = run_next.run_selected_seed(BMW, run_seed_fn=_runner_returning_one, push_fn=_noop_push)
    assert r1["ok"] is True
    r2 = run_next.run_selected_seed(Z4, run_seed_fn=_runner_returning_one, push_fn=_noop_push)
    assert r2["ok"] is True

    after = _load(workspace)
    bs = after["batch_state"]
    assert len(bs["needs_retry_seed_ids"]) == 52
    prog = queue.compute_problem_queue_progress(after)
    assert prog["total"] == 54
    assert prog["pending"] == 52
    assert prog["completed"] == 2
    assert prog["current_position"] == "3 / 54"
    assert bs["needs_retry_seed_ids"][0] == DAEWOO


def test_no_external_state(workspace, monkeypatch):
    # Create a stray data/output folder. It must not affect selection.
    out = workspace / "data" / "output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "scratch.json").write_text("{}")

    canonical = _load(workspace)
    selection = queue.select_next_seed(canonical)
    assert selection["selected_seed_id"] == BMW

    # Deleting data/output also must not affect anything.
    shutil.rmtree(out)
    canonical2 = _load(workspace)
    assert queue.select_next_seed(canonical2)["selected_seed_id"] == BMW


def test_stale_external_files_ignored(workspace):
    out = workspace / "data" / "output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "batch_state.json").write_text(json.dumps({
        "current": "honda_legend",
        "next_seed_id": "honda_legend__2010__2014__il",
    }))

    canonical = _load(workspace)
    selection = queue.select_next_seed(canonical)
    assert selection["mode"] == "problem_queue"
    assert selection["selected_seed_id"] == BMW
    bs = canonical["batch_state"]
    assert bs["next_seed_id"] == HAVAL


def test_normal_cursor_frozen(workspace):
    r1 = run_next.run_selected_seed(BMW, run_seed_fn=_runner_returning_one, push_fn=_noop_push)
    assert r1["ok"] is True
    after_bmw = _load(workspace)
    assert after_bmw["batch_state"]["next_seed_id"] == HAVAL
    assert after_bmw["batch_state"]["last_completed_seed_id"] == GMC

    r2 = run_next.run_selected_seed(Z4, run_seed_fn=_runner_returning_one, push_fn=_noop_push)
    assert r2["ok"] is True
    after_z4 = _load(workspace)
    assert after_z4["batch_state"]["next_seed_id"] == HAVAL
    assert after_z4["batch_state"]["last_completed_seed_id"] == GMC


def test_no_imports_from_legacy_reference():
    """Production files must not import legacy_reference."""
    files_to_scan: list[Path] = []
    for root_name in ("app.py",):
        files_to_scan.append(REPO_ROOT / root_name)
    for sub in ("agent", "engine", "core", "tools", "storage"):
        files_to_scan.extend((REPO_ROOT / sub).rglob("*.py"))
    offenders: list[str] = []
    for fp in files_to_scan:
        text = fp.read_text(encoding="utf-8")
        if "legacy_reference" in text:
            offenders.append(str(fp.relative_to(REPO_ROOT)))
    assert not offenders, f"legacy_reference referenced in production files: {offenders}"
