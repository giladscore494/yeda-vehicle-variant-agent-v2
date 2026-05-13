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
DAEWOO = "daewoo__lacetti__2003__2011__il"
DAIHATSU = "daihatsu__copen__2002__2012__il"
DS9 = "ds_automobiles__ds_9__2020__2026__il"
CURRENT_VARIANTS = 1331
CURRENT_PROCESSED = 386
CURRENT_NEEDS_RETRY = 52
CURRENT_TOTAL_PROBLEMS = 54
CURRENT_COMPLETED = 2
CURRENT_POSITION = "3 / 54"


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


def _needs_retry_ids(workspace) -> list[str]:
    return list(_load(workspace)["batch_state"]["needs_retry_seed_ids"])


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


def _recording_runner(calls: list[str]):
    def _runner(seed_id: str, *, retry_hint: bool = False) -> dict:
        calls.append(seed_id)
        return _runner_returning_one(seed_id, retry_hint=retry_hint)
    return _runner


# ---- tests -------------------------------------------------------------------

def test_clean_canonical_loads(workspace):
    canonical = _load(workspace)
    variants = canonical["accumulated_clean_export"]["variants"]
    bs = canonical["batch_state"]
    assert len(variants) == CURRENT_VARIANTS
    assert len(bs["processed_seed_ids"]) == CURRENT_PROCESSED
    assert len(bs["needs_retry_seed_ids"]) == CURRENT_NEEDS_RETRY
    assert bs["needs_retry_seed_ids"][0] == DAEWOO

    text = json.dumps(canonical)
    assert '"s1"' not in text

    vids = [v["variant_id"] for v in variants]
    assert len(vids) == len(set(vids))


def test_select_next_problem_seed(workspace):
    canonical = _load(workspace)
    selection = queue.select_next_seed(canonical)
    assert selection["mode"] == "problem_queue"
    assert selection["selected_seed_id"] == DAEWOO
    bs = canonical["batch_state"]
    assert bs["next_seed_id"] == HAVAL
    assert bs["last_completed_seed_id"] == GMC


def test_progress_before_current_problem_seed(workspace):
    canonical = _load(workspace)
    prog = queue.compute_problem_queue_progress(canonical)
    assert prog["total"] == CURRENT_TOTAL_PROBLEMS
    assert prog["completed"] == CURRENT_COMPLETED
    assert prog["pending"] == CURRENT_NEEDS_RETRY
    assert prog["current_position"] == CURRENT_POSITION
    assert prog["current_seed"] == DAEWOO


def test_current_problem_seed_success_persists_before_progress(workspace):
    before = _load(workspace)
    variants_before = len(before["accumulated_clean_export"]["variants"])

    result = run_next.run_selected_seed(
        DAEWOO, run_seed_fn=_runner_returning_one, push_fn=_noop_push,
    )
    assert result["ok"] is True, result
    assert result["seed_id"] == DAEWOO

    after = _load(workspace)
    bs = after["batch_state"]
    assert DAEWOO not in bs["needs_retry_seed_ids"]
    assert bs["needs_retry_seed_ids"][0] == DAIHATSU
    assert len(bs["needs_retry_seed_ids"]) == CURRENT_NEEDS_RETRY - 1

    prog = queue.compute_problem_queue_progress(after)
    assert prog["total"] == CURRENT_TOTAL_PROBLEMS
    assert prog["pending"] == CURRENT_NEEDS_RETRY - 1
    assert prog["completed"] == CURRENT_COMPLETED + 1
    assert prog["current_position"] == "4 / 54"
    assert prog["current_seed"] == DAIHATSU

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
        DAEWOO, run_seed_fn=_runner_returning_one, push_fn=_noop_push,
    )
    assert result["ok"] is False
    assert "simulated save failure" in (result.get("error") or "")

    after = _load(workspace)
    bs = after["batch_state"]
    assert bs["needs_retry_seed_ids"][0] == DAEWOO
    assert len(bs["needs_retry_seed_ids"]) == CURRENT_NEEDS_RETRY
    assert len(after["accumulated_clean_export"]["variants"]) == variants_before

    prog = queue.compute_problem_queue_progress(after)
    assert prog["pending"] == CURRENT_NEEDS_RETRY
    assert prog["completed"] == CURRENT_COMPLETED
    assert prog["current_position"] == CURRENT_POSITION


def test_second_problem_seed_after_first(workspace):
    r1 = run_next.run_selected_seed(DAEWOO, run_seed_fn=_runner_returning_one, push_fn=_noop_push)
    assert r1["ok"] is True
    r2 = run_next.run_selected_seed(DAIHATSU, run_seed_fn=_runner_returning_one, push_fn=_noop_push)
    assert r2["ok"] is True

    after = _load(workspace)
    bs = after["batch_state"]
    assert len(bs["needs_retry_seed_ids"]) == CURRENT_NEEDS_RETRY - 2
    prog = queue.compute_problem_queue_progress(after)
    assert prog["total"] == CURRENT_TOTAL_PROBLEMS
    assert prog["pending"] == CURRENT_NEEDS_RETRY - 2
    assert prog["completed"] == CURRENT_COMPLETED + 2
    assert prog["current_position"] == "5 / 54"
    assert bs["needs_retry_seed_ids"][0] == DS9


def test_no_external_state(workspace, monkeypatch):
    # Create a stray data/output folder. It must not affect selection.
    out = workspace / "data" / "output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "scratch.json").write_text("{}")

    canonical = _load(workspace)
    selection = queue.select_next_seed(canonical)
    assert selection["selected_seed_id"] == DAEWOO

    # Deleting data/output also must not affect anything.
    shutil.rmtree(out)
    canonical2 = _load(workspace)
    assert queue.select_next_seed(canonical2)["selected_seed_id"] == DAEWOO


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
    assert selection["selected_seed_id"] == DAEWOO
    bs = canonical["batch_state"]
    assert bs["next_seed_id"] == HAVAL


def test_normal_cursor_frozen(workspace):
    r1 = run_next.run_selected_seed(DAEWOO, run_seed_fn=_runner_returning_one, push_fn=_noop_push)
    assert r1["ok"] is True
    after_first = _load(workspace)
    assert after_first["batch_state"]["next_seed_id"] == HAVAL
    assert after_first["batch_state"]["last_completed_seed_id"] == GMC

    r2 = run_next.run_selected_seed(DAIHATSU, run_seed_fn=_runner_returning_one, push_fn=_noop_push)
    assert r2["ok"] is True
    after_second = _load(workspace)
    assert after_second["batch_state"]["next_seed_id"] == HAVAL
    assert after_second["batch_state"]["last_completed_seed_id"] == GMC


def test_batch_size_20_sequential_success(workspace, monkeypatch):
    initial_needs_retry = _needs_retry_ids(workspace)
    expected_order = initial_needs_retry[:20]
    runner_calls: list[str] = []
    save_calls: list[str] = []
    push_calls: list[str] = []
    from engine.canonical_store import save_canonical_atomic as real_save

    def _save_wrapper(data, path=None):
        save_calls.append((data["batch_state"]["processed_seed_ids"] or [None])[-1])
        return real_save(data, path=path)

    def _push_wrapper(canonical, commit_message=None):
        push_calls.append((canonical["batch_state"]["processed_seed_ids"] or [None])[-1])
        return _noop_push(canonical, commit_message=commit_message)

    monkeypatch.setattr("engine.run_next.save_canonical_atomic", _save_wrapper)

    result = run_next.run_next_model(
        batch_size=20,
        run_seed_fn=_recording_runner(runner_calls),
        push_fn=_push_wrapper,
    )

    assert result["ok"] is True, result
    assert result["requested_batch_size"] == 20
    assert result["processed_count"] == 20
    assert result["stopped_early"] is False
    assert runner_calls == expected_order
    assert save_calls == expected_order
    assert push_calls == expected_order
    assert [item["seed_id"] for item in result["results"]] == expected_order
    assert all(item["ok"] is True for item in result["results"])
    assert result["final_state"]["pending"] == CURRENT_NEEDS_RETRY - 20
    assert result["final_state"]["completed"] == CURRENT_COMPLETED + 20
    assert result["final_state"]["position"] == "23 / 54"
    assert result["final_state"]["normal_next_seed_id"] == HAVAL
    assert result["final_state"]["normal_last_completed_seed_id"] == GMC


def test_batch_stops_on_save_failure(workspace, monkeypatch):
    initial_needs_retry = _needs_retry_ids(workspace)
    runner_calls: list[str] = []
    from engine.canonical_store import save_canonical_atomic as real_save

    call_count = {"save": 0}

    def _save_wrapper(data, path=None):
        call_count["save"] += 1
        if call_count["save"] == 2:
            return {"ok": False, "path": None, "error": "simulated save failure"}
        return real_save(data, path=path)

    monkeypatch.setattr("engine.run_next.save_canonical_atomic", _save_wrapper)

    result = run_next.run_next_model(
        batch_size=20,
        run_seed_fn=_recording_runner(runner_calls),
        push_fn=_noop_push,
    )

    assert result["ok"] is False
    assert result["processed_count"] == 1
    assert result["stopped_early"] is True
    assert "simulated save failure" in (result["stop_reason"] or "")
    assert runner_calls == initial_needs_retry[:2]
    assert result["results"][1]["seed_id"] == initial_needs_retry[1]
    after = _load(workspace)
    assert after["batch_state"]["needs_retry_seed_ids"][0] == initial_needs_retry[1]


def test_batch_stops_on_push_failure(workspace):
    runner_calls: list[str] = []

    result = run_next.run_next_model(
        batch_size=20,
        run_seed_fn=_recording_runner(runner_calls),
        push_fn=_failing_push,
    )

    assert result["ok"] is False
    assert result["processed_count"] == 0
    assert result["stopped_early"] is True
    assert "ahead of GitHub" in (result["stop_reason"] or "")
    assert runner_calls == [DAEWOO]
    assert result["results"][0]["push_ok"] is False


def test_batch_does_not_move_normal_cursor(workspace, monkeypatch):
    observed_cursors: list[tuple[str | None, str | None]] = []
    from engine.canonical_store import save_canonical_atomic as real_save

    def _save_wrapper(data, path=None):
        observed_cursors.append((
            data["batch_state"].get("next_seed_id"),
            data["batch_state"].get("last_completed_seed_id"),
        ))
        return real_save(data, path=path)

    monkeypatch.setattr("engine.run_next.save_canonical_atomic", _save_wrapper)

    result = run_next.run_next_model(
        batch_size=20,
        run_seed_fn=_recording_runner([]),
        push_fn=_noop_push,
    )

    assert result["ok"] is True, result
    assert len(observed_cursors) == 20
    assert all(cursor == (HAVAL, GMC) for cursor in observed_cursors)
    assert result["final_state"]["normal_next_seed_id"] == HAVAL
    assert result["final_state"]["normal_last_completed_seed_id"] == GMC


def test_progress_after_batch(workspace):
    initial_needs_retry = _needs_retry_ids(workspace)

    result = run_next.run_next_model(
        batch_size=20,
        run_seed_fn=_recording_runner([]),
        push_fn=_noop_push,
    )

    assert result["ok"] is True, result
    assert result["final_state"]["completed"] == 22
    assert result["final_state"]["pending"] == 32
    assert result["final_state"]["position"] == "23 / 54"
    assert result["final_state"]["selected_next_seed"] == initial_needs_retry[20]


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
