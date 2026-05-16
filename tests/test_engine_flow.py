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
from engine.canonical_store import load_canonical, save_canonical_atomic, validate_canonical


CANONICAL_REL = "data/canonical/resume_package_canonical.json"
BACKUP_CANONICAL_REL = "data/canonical/resume_package_canonical_backup_previous.json"
SEED_CATALOG_REL = "data/seeds/vehicle_model_seeds_il.json"
HAVAL = "haval__h6__2022__2026__il"
GMC = "gmc__yukon__2000__2026__il"
REPAIRED_NEXT = "volkswagen__polo__1990__2026__il"
# Synthetic problem-queue seeds used by the pq fixture.
# These seeds are injected into needs_retry_seed_ids so problem-queue tests
# remain valid regardless of how far the real canonical has advanced.
_SYNTH_PQ = [f"zztest__{i:02d}__2020__2026__il" for i in range(49)]
SYNTH_FIRST = _SYNTH_PQ[0]
SYNTH_SECOND = _SYNTH_PQ[1]
SYNTH_THIRD = _SYNTH_PQ[2]
# A seed guaranteed to be absent from processed_seed_ids used for catalog tests.
HAVAL_CATALOG_NEXT = "isuzu__xyz_test__2020__2026__il"

# Current canonical state after the Haval save and deterministic cursor repair.
CURRENT_VARIANTS = 1523
CURRENT_PROCESSED = 439
CURRENT_NEEDS_RETRY = 0
GOLF_VARIANT = "volkswagen__golf_variant__1993__2026__il"
# Expected counts derived from the repaired 1523-variant canonical.
EXPECTED_VERIFIED = 788
EXPECTED_PARTIAL = 735
EXPECTED_MAKES_COUNT = 38
EXPECTED_MODELS_COUNT = 437

# Problem-queue test constants (used with workspace_pq fixture):
#   54 total original problem seeds; 49 pending (49 synthetic); 5 completed.
INITIAL_VARIANTS = CURRENT_VARIANTS   # variant count is the same in both fixtures
INITIAL_PROCESSED = CURRENT_PROCESSED
INITIAL_NEEDS_RETRY = 49
INITIAL_TOTAL_PROBLEMS = 54
INITIAL_COMPLETED = 5  # = 54 - 49
INITIAL_POSITION = "6 / 54"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Copy the real canonical into a temp workspace so tests don't mutate it."""
    src = REPO_ROOT / CANONICAL_REL
    dst_dir = tmp_path / "data" / "canonical"
    dst_dir.mkdir(parents=True)
    dst = dst_dir / "resume_package_canonical.json"
    shutil.copy2(src, dst)
    seeds_dst_dir = tmp_path / "data" / "seeds"
    seeds_dst_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / SEED_CATALOG_REL, seeds_dst_dir / "vehicle_model_seeds_il.json")
    monkeypatch.setenv("CANONICAL_PATH", str(dst))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def workspace_pre_haval(tmp_path, monkeypatch):
    """Copy the unsafe pre-repair Haval snapshot into a temp workspace."""
    src = REPO_ROOT / BACKUP_CANONICAL_REL
    dst_dir = tmp_path / "data" / "canonical"
    dst_dir.mkdir(parents=True)
    dst = dst_dir / "resume_package_canonical.json"
    shutil.copy2(src, dst)
    seeds_dst_dir = tmp_path / "data" / "seeds"
    seeds_dst_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / SEED_CATALOG_REL, seeds_dst_dir / "vehicle_model_seeds_il.json")
    monkeypatch.setenv("CANONICAL_PATH", str(dst))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def workspace_pq(tmp_path, monkeypatch):
    """Copy the real canonical with 49 synthetic problem-queue seeds injected.

    Produces a state equivalent to the original mid-repair state:
    needs_retry=49, completed=5, total=54 — without requiring the real
    canonical to still be in problem_queue mode.
    """
    src = REPO_ROOT / CANONICAL_REL
    dst_dir = tmp_path / "data" / "canonical"
    dst_dir.mkdir(parents=True)
    dst = dst_dir / "resume_package_canonical.json"
    shutil.copy2(src, dst)
    seeds_dst_dir = tmp_path / "data" / "seeds"
    seeds_dst_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / SEED_CATALOG_REL, seeds_dst_dir / "vehicle_model_seeds_il.json")
    monkeypatch.setenv("CANONICAL_PATH", str(dst))
    monkeypatch.chdir(tmp_path)
    # Inject synthetic problem seeds
    data = json.loads(dst.read_text(encoding="utf-8"))
    data["batch_state"]["needs_retry_seed_ids"] = list(_SYNTH_PQ)
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp_path


def _load(workspace) -> dict:
    return load_canonical()


def _needs_retry_ids(workspace) -> list[str]:
    return list(_load(workspace)["batch_state"]["needs_retry_seed_ids"])


def _write_canonical(canonical: dict) -> None:
    save_result = save_canonical_atomic(canonical)
    assert save_result["ok"] is True, save_result


def _set_unsafe_haval_cursor(canonical: dict) -> dict:
    updated = copy.deepcopy(canonical)
    bs = updated["batch_state"]
    processed = list(bs.get("processed_seed_ids") or [])
    if HAVAL not in processed:
        processed.append(HAVAL)
    bs["processed_seed_ids"] = processed
    bs["processed_seeds"] = list(processed)
    bs["last_completed_seed_id"] = HAVAL
    bs["next_seed_id"] = HAVAL
    bs["needs_retry_seed_ids"] = []
    bs["active_mode"] = "normal_batch"
    return updated


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


# ---- current canonical state tests ------------------------------------------

def test_clean_canonical_loads(workspace):
    canonical = _load(workspace)
    variants = canonical["accumulated_clean_export"]["variants"]
    bs = canonical["batch_state"]
    assert len(variants) == CURRENT_VARIANTS
    assert len(bs["processed_seed_ids"]) == CURRENT_PROCESSED
    assert len(bs["needs_retry_seed_ids"]) == CURRENT_NEEDS_RETRY
    assert bs["next_seed_id"] == REPAIRED_NEXT
    assert bs["last_completed_seed_id"] == HAVAL

    text = json.dumps(canonical)
    assert '"s1"' not in text

    vids = [v["variant_id"] for v in variants]
    assert len(vids) == len(set(vids))


def test_canonical_state_valid(workspace):
    """validate_canonical must pass on the current canonical without any repair."""
    canonical = _load(workspace)
    ok, errs = validate_canonical(canonical)
    assert ok, f"validate_canonical failed: {errs}"


def test_accumulated_variants_1523(workspace):
    """Exactly 1523 variants must be in accumulated_clean_export.variants."""
    canonical = _load(workspace)
    variants = canonical["accumulated_clean_export"]["variants"]
    assert len(variants) == CURRENT_VARIANTS


def test_no_duplicate_variant_ids(workspace):
    """No duplicate variant_id must exist in accumulated_clean_export.variants."""
    canonical = _load(workspace)
    vids = [v["variant_id"] for v in canonical["accumulated_clean_export"]["variants"]]
    assert len(vids) == len(set(vids)), "Duplicate variant_id found"


def test_haval_not_in_failed_seed_ids(workspace):
    """haval__h6__2022__2026__il must not appear in failed_seed_ids after state repair."""
    canonical = _load(workspace)
    failed = canonical["batch_state"].get("failed_seed_ids") or []
    assert HAVAL not in failed, f"{HAVAL} must not be in failed_seed_ids"


def test_needs_retry_empty(workspace):
    """needs_retry_seed_ids must be empty after the problem-queue completed."""
    canonical = _load(workspace)
    assert canonical["batch_state"]["needs_retry_seed_ids"] == []


def test_mode_is_normal_batch(workspace):
    """After problem queue is cleared, mode must be normal_batch."""
    canonical = _load(workspace)
    assert queue.get_mode(canonical) == "normal_batch"


def test_selected_seed_is_haval(workspace):
    """The repaired selected next seed must match the repaired normal cursor."""
    canonical = _load(workspace)
    sel = queue.select_next_seed(canonical)
    assert sel["selected_seed_id"] == REPAIRED_NEXT
    assert sel["mode"] == "normal_batch"


# ---- problem-queue mode tests (use workspace_pq) -----------------------------

def test_select_next_problem_seed(workspace_pq):
    canonical = _load(workspace_pq)
    selection = queue.select_next_seed(canonical)
    assert selection["mode"] == "problem_queue"
    assert selection["selected_seed_id"] == SYNTH_FIRST
    bs = canonical["batch_state"]
    assert bs["next_seed_id"] == REPAIRED_NEXT
    assert bs["last_completed_seed_id"] == HAVAL


def test_progress_before_current_problem_seed(workspace_pq):
    canonical = _load(workspace_pq)
    prog = queue.compute_problem_queue_progress(canonical)
    assert prog["total"] == INITIAL_TOTAL_PROBLEMS
    assert prog["completed"] == INITIAL_COMPLETED
    assert prog["pending"] == INITIAL_NEEDS_RETRY
    assert prog["current_position"] == INITIAL_POSITION
    assert prog["current_seed"] == SYNTH_FIRST


def test_current_problem_seed_success_persists_before_progress(workspace_pq):
    before = _load(workspace_pq)
    variants_before = len(before["accumulated_clean_export"]["variants"])

    result = run_next.run_selected_seed(
        SYNTH_FIRST, run_seed_fn=_runner_returning_one, push_fn=_noop_push,
    )
    assert result["ok"] is True, result
    assert result["seed_id"] == SYNTH_FIRST

    after = _load(workspace_pq)
    bs = after["batch_state"]
    assert SYNTH_FIRST not in bs["needs_retry_seed_ids"]
    assert bs["needs_retry_seed_ids"][0] == SYNTH_SECOND
    assert len(bs["needs_retry_seed_ids"]) == INITIAL_NEEDS_RETRY - 1

    prog = queue.compute_problem_queue_progress(after)
    assert prog["total"] == INITIAL_TOTAL_PROBLEMS
    assert prog["pending"] == INITIAL_NEEDS_RETRY - 1
    assert prog["completed"] == INITIAL_COMPLETED + 1
    assert prog["current_position"] == "7 / 54"
    assert prog["current_seed"] == SYNTH_SECOND

    assert len(after["accumulated_clean_export"]["variants"]) >= variants_before
    # Normal cursor frozen
    assert bs["next_seed_id"] == REPAIRED_NEXT
    assert bs["last_completed_seed_id"] == HAVAL


def test_failed_save_does_not_advance(workspace_pq, monkeypatch):
    before = _load(workspace_pq)
    variants_before = len(before["accumulated_clean_export"]["variants"])

    # Force save to fail by monkeypatching save_canonical_atomic
    def _fake_save(data, path=None):
        return {"ok": False, "path": None, "error": "simulated save failure"}

    monkeypatch.setattr("engine.run_next.save_canonical_atomic", _fake_save)

    result = run_next.run_selected_seed(
        SYNTH_FIRST, run_seed_fn=_runner_returning_one, push_fn=_noop_push,
    )
    assert result["ok"] is False
    assert "simulated save failure" in (result.get("error") or "")

    after = _load(workspace_pq)
    bs = after["batch_state"]
    assert bs["needs_retry_seed_ids"][0] == SYNTH_FIRST
    assert len(bs["needs_retry_seed_ids"]) == INITIAL_NEEDS_RETRY
    assert len(after["accumulated_clean_export"]["variants"]) == variants_before

    prog = queue.compute_problem_queue_progress(after)
    assert prog["pending"] == INITIAL_NEEDS_RETRY
    assert prog["completed"] == INITIAL_COMPLETED
    assert prog["current_position"] == INITIAL_POSITION


def test_second_problem_seed_after_first(workspace_pq):
    r1 = run_next.run_selected_seed(SYNTH_FIRST, run_seed_fn=_runner_returning_one, push_fn=_noop_push)
    assert r1["ok"] is True
    r2 = run_next.run_selected_seed(SYNTH_SECOND, run_seed_fn=_runner_returning_one, push_fn=_noop_push)
    assert r2["ok"] is True

    after = _load(workspace_pq)
    bs = after["batch_state"]
    assert len(bs["needs_retry_seed_ids"]) == INITIAL_NEEDS_RETRY - 2
    prog = queue.compute_problem_queue_progress(after)
    assert prog["total"] == INITIAL_TOTAL_PROBLEMS
    assert prog["pending"] == INITIAL_NEEDS_RETRY - 2
    assert prog["completed"] == INITIAL_COMPLETED + 2
    assert prog["current_position"] == "8 / 54"
    assert bs["needs_retry_seed_ids"][0] == SYNTH_THIRD


def test_no_external_state(workspace_pq):
    # Create a stray data/output folder. It must not affect selection.
    out = workspace_pq / "data" / "output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "scratch.json").write_text("{}")

    canonical = _load(workspace_pq)
    selection = queue.select_next_seed(canonical)
    assert selection["selected_seed_id"] == SYNTH_FIRST

    # Deleting data/output also must not affect anything.
    shutil.rmtree(out)
    canonical2 = _load(workspace_pq)
    assert queue.select_next_seed(canonical2)["selected_seed_id"] == SYNTH_FIRST


def test_stale_external_files_ignored(workspace_pq):
    out = workspace_pq / "data" / "output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "batch_state.json").write_text(json.dumps({
        "current": "honda_legend",
        "next_seed_id": "honda_legend__2010__2014__il",
    }))

    canonical = _load(workspace_pq)
    selection = queue.select_next_seed(canonical)
    assert selection["mode"] == "problem_queue"
    assert selection["selected_seed_id"] == SYNTH_FIRST
    bs = canonical["batch_state"]
    assert bs["next_seed_id"] == REPAIRED_NEXT


def test_normal_cursor_frozen(workspace_pq):
    r1 = run_next.run_selected_seed(SYNTH_FIRST, run_seed_fn=_runner_returning_one, push_fn=_noop_push)
    assert r1["ok"] is True
    after_first = _load(workspace_pq)
    assert after_first["batch_state"]["next_seed_id"] == REPAIRED_NEXT
    assert after_first["batch_state"]["last_completed_seed_id"] == HAVAL

    r2 = run_next.run_selected_seed(SYNTH_SECOND, run_seed_fn=_runner_returning_one, push_fn=_noop_push)
    assert r2["ok"] is True
    after_second = _load(workspace_pq)
    assert after_second["batch_state"]["next_seed_id"] == REPAIRED_NEXT
    assert after_second["batch_state"]["last_completed_seed_id"] == HAVAL


def test_batch_size_20_sequential_success(workspace_pq, monkeypatch):
    initial_needs_retry = _needs_retry_ids(workspace_pq)
    expected_order = initial_needs_retry[:20]
    runner_calls: list[str] = []
    save_calls: list[str] = []
    push_calls: list[str] = []
    from engine.canonical_store import save_canonical_atomic as real_save

    def _save_wrapper(data, path=None):
        processed_ids = data["batch_state"].get("processed_seed_ids") or []
        save_calls.append(processed_ids[-1] if processed_ids else "<empty>")
        return real_save(data, path=path)

    def _push_wrapper(canonical, commit_message=None):
        processed_ids = canonical["batch_state"].get("processed_seed_ids") or []
        push_calls.append(processed_ids[-1] if processed_ids else "<empty>")
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
    assert result["final_state"]["pending"] == INITIAL_NEEDS_RETRY - 20
    assert result["final_state"]["completed"] == INITIAL_COMPLETED + 20
    assert result["final_state"]["position"] == "26 / 54"
    assert result["final_state"]["normal_next_seed_id"] == REPAIRED_NEXT
    assert result["final_state"]["normal_last_completed_seed_id"] == HAVAL


def test_batch_stops_on_save_failure(workspace_pq, monkeypatch):
    initial_needs_retry = _needs_retry_ids(workspace_pq)
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
    after = _load(workspace_pq)
    assert after["batch_state"]["needs_retry_seed_ids"][0] == initial_needs_retry[1]


def test_batch_stops_on_push_failure(workspace_pq):
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
    assert runner_calls == [SYNTH_FIRST]
    assert result["results"][0]["push_ok"] is False


def test_batch_does_not_move_normal_cursor(workspace_pq, monkeypatch):
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
    assert all(cursor == (REPAIRED_NEXT, HAVAL) for cursor in observed_cursors)
    assert result["final_state"]["normal_next_seed_id"] == REPAIRED_NEXT
    assert result["final_state"]["normal_last_completed_seed_id"] == HAVAL


def test_progress_after_batch(workspace_pq):
    initial_needs_retry = _needs_retry_ids(workspace_pq)

    result = run_next.run_next_model(
        batch_size=20,
        run_seed_fn=_recording_runner([]),
        push_fn=_noop_push,
    )

    assert result["ok"] is True, result
    assert result["final_state"]["completed"] == INITIAL_COMPLETED + 20
    assert result["final_state"]["pending"] == INITIAL_NEEDS_RETRY - 20
    assert result["final_state"]["position"] == "26 / 54"
    assert result["final_state"]["selected_next_seed"] == initial_needs_retry[20]


# ---- terminal problem-queue transition tests ---------------------------------

def _make_workspace_with_n_pq_seeds(tmp_path, monkeypatch, n: int) -> Path:
    """Helper: copy canonical into tmp_path with exactly *n* synthetic pq seeds."""
    src = REPO_ROOT / CANONICAL_REL
    dst_dir = tmp_path / "data" / "canonical"
    dst_dir.mkdir(parents=True)
    dst = dst_dir / "resume_package_canonical.json"
    shutil.copy2(src, dst)
    seeds_dst_dir = tmp_path / "data" / "seeds"
    seeds_dst_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / SEED_CATALOG_REL, seeds_dst_dir / "vehicle_model_seeds_il.json")
    monkeypatch.setenv("CANONICAL_PATH", str(dst))
    monkeypatch.chdir(tmp_path)
    data = json.loads(dst.read_text(encoding="utf-8"))
    data["batch_state"]["needs_retry_seed_ids"] = [
        f"zzterm__{i:02d}__2020__2026__il" for i in range(n)
    ]
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp_path


def test_terminal_problem_queue_transition(tmp_path, monkeypatch):
    """Resolving the last retry seed must produce a clean normal_batch transition."""
    ws = _make_workspace_with_n_pq_seeds(tmp_path, monkeypatch, n=1)
    last_seed = "zzterm__00__2020__2026__il"

    # Confirm we are in problem_queue mode before the run.
    before_canon = _load(ws)
    assert queue.get_mode(before_canon) == "problem_queue"
    assert before_canon["batch_state"]["needs_retry_seed_ids"] == [last_seed]

    result = run_next.run_next_model(
        batch_size=1,
        run_seed_fn=_runner_returning_one,
        push_fn=_noop_push,
    )

    assert result["ok"] is True, f"terminal transition failed: {result.get('stop_reason')}"
    assert result["stopped_early"] is False
    assert result["final_state"]["needs_retry"] == 0
    assert result["final_state"]["selected_next_seed"] == REPAIRED_NEXT
    assert result["final_state"]["normal_next_seed_id"] == REPAIRED_NEXT

    after = _load(ws)
    assert queue.get_mode(after) == "normal_batch"
    assert after["batch_state"]["needs_retry_seed_ids"] == []
    assert after["batch_state"]["next_seed_id"] == REPAIRED_NEXT


def test_after_terminal_pq_selected_seed_returns_to_normal(tmp_path, monkeypatch):
    """After the last retry seed is resolved, selected_seed_id == normal next_seed_id."""
    ws = _make_workspace_with_n_pq_seeds(tmp_path, monkeypatch, n=1)
    last_seed = "zzterm__00__2020__2026__il"

    run_next.run_selected_seed(last_seed, run_seed_fn=_runner_returning_one, push_fn=_noop_push)

    after = _load(ws)
    sel = queue.select_next_seed(after)
    assert sel["mode"] == "normal_batch"
    assert sel["selected_seed_id"] == after["batch_state"]["next_seed_id"]
    assert sel["selected_seed_id"] == REPAIRED_NEXT


# ---- normal-batch cursor advancement tests -----------------------------------

def test_normal_batch_cursor_advances_with_catalog(workspace_pre_haval):
    """A catalog must let an unsafe Haval normal-batch cursor advance deterministically."""
    catalog = [GMC, HAVAL, HAVAL_CATALOG_NEXT]

    result = run_next.run_next_model(
        batch_size=1,
        run_seed_fn=_runner_returning_one,
        push_fn=_noop_push,
        seed_catalog=catalog,
    )
    assert result["ok"] is True, result
    assert result["processed_count"] == 1

    after = _load(workspace_pre_haval)
    processed_ids = after["batch_state"]["processed_seed_ids"]
    assert after["batch_state"]["last_completed_seed_id"] == HAVAL
    assert after["batch_state"]["next_seed_id"] == HAVAL_CATALOG_NEXT
    assert processed_ids.count(HAVAL) == 1
    assert result["final_state"]["selected_next_seed"] == HAVAL_CATALOG_NEXT


def test_normal_batch_cursor_blocked_without_catalog(workspace, monkeypatch):
    """Without a catalog, normal_batch must stop before runner/save/push."""
    canonical = _set_unsafe_haval_cursor(_load(workspace))
    _write_canonical(canonical)

    runner_calls: list[str] = []
    save_calls: list[str] = []

    def _save_wrapper(data, path=None):
        save_calls.append("save")
        return save_canonical_atomic(data, path=path)

    monkeypatch.setattr("engine.run_next.save_canonical_atomic", _save_wrapper)

    result = run_next.run_next_model(
        batch_size=5,
        run_seed_fn=_recording_runner(runner_calls),
        push_fn=_noop_push,
        seed_catalog=None,
    )

    after = _load(workspace)
    assert result["ok"] is False
    assert result["processed_count"] == 0
    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item["seed_id"] == HAVAL
    assert item["ok"] is False
    assert item["added_count"] == 0
    assert item["merged_count"] == 0
    assert item["rejected_count"] == 0
    assert item["quality_warnings"] == []
    assert item["accepted_quality_levels"] == []
    assert item["save_ok"] is False
    assert item["push_ok"] is False
    assert item["variants_before"] == CURRENT_VARIANTS
    assert item["variants_after"] == CURRENT_VARIANTS
    assert item["needs_retry_before"] == 0
    assert item["needs_retry_after"] == 0
    assert "seed catalog is missing" in (result["stop_reason"] or "")
    assert runner_calls == []
    assert save_calls == []
    assert len(after["accumulated_clean_export"]["variants"]) == CURRENT_VARIANTS
    assert len(after["batch_state"]["processed_seed_ids"]) == CURRENT_PROCESSED
    assert after["batch_state"]["next_seed_id"] == HAVAL


def test_cursor_repair_after_processed_haval(workspace):
    """Repair must advance the unsafe Haval cursor without changing variants or processed."""
    canonical = _set_unsafe_haval_cursor(_load(workspace))
    _write_canonical(canonical)
    before = _load(workspace)
    catalog = queue.load_seed_catalog(before)

    repair = queue.repair_normal_batch_cursor(before, catalog)
    _write_canonical(before)

    after = _load(workspace)
    assert repair["old_next_seed_id"] == HAVAL
    assert repair["repaired_next_seed_id"] == REPAIRED_NEXT
    assert after["batch_state"]["last_completed_seed_id"] == HAVAL
    assert after["batch_state"]["next_seed_id"] == REPAIRED_NEXT
    assert len(after["accumulated_clean_export"]["variants"]) == CURRENT_VARIANTS
    assert len(after["batch_state"]["processed_seed_ids"]) == CURRENT_PROCESSED


def test_load_seed_catalog_from_file(workspace):
    """The repo seed catalog file must load and match batch_state.total_seeds."""
    canonical = _load(workspace)
    catalog = queue.load_seed_catalog(canonical)
    assert len(catalog) == canonical["batch_state"]["total_seeds"] == 993
    assert catalog[0] == "toyota__yaris__1999__2026__il"
    assert catalog[catalog.index(HAVAL)] == HAVAL


def test_load_seed_catalog_from_canonical_fallback(workspace):
    (workspace / SEED_CATALOG_REL).unlink()
    canonical = _load(workspace)
    canonical["seed_catalog"] = [GMC, HAVAL, HAVAL_CATALOG_NEXT]
    canonical["batch_state"]["total_seeds"] = 3
    canonical["batch_state"]["next_seed_id"] = HAVAL
    canonical["batch_state"]["last_completed_seed_id"] = GMC
    assert queue.load_seed_catalog(canonical) == [GMC, HAVAL, HAVAL_CATALOG_NEXT]


def test_catalog_validation_duplicate_seed_id_fails(workspace):
    canonical = _load(workspace)
    canonical["batch_state"]["next_seed_id"] = HAVAL
    canonical["batch_state"]["last_completed_seed_id"] = GMC
    errors = queue.validate_seed_catalog([GMC, HAVAL, HAVAL, HAVAL_CATALOG_NEXT], canonical)
    assert any("duplicate" in err for err in errors)


def test_catalog_validation_missing_next_seed_id_fails(workspace):
    canonical = _load(workspace)
    canonical["batch_state"]["next_seed_id"] = HAVAL
    canonical["batch_state"]["last_completed_seed_id"] = GMC
    errors = queue.validate_seed_catalog([GMC, HAVAL_CATALOG_NEXT], canonical)
    assert any("next_seed_id" in err for err in errors)


def test_catalog_validation_length_mismatch_respects_strict_mode(workspace):
    canonical = _load(workspace)
    canonical["batch_state"]["next_seed_id"] = HAVAL
    canonical["batch_state"]["last_completed_seed_id"] = GMC
    short_catalog = [GMC, HAVAL, HAVAL_CATALOG_NEXT]
    strict_errors = queue.validate_seed_catalog(short_catalog, canonical, strict_total_seeds=True)
    relaxed_errors = queue.validate_seed_catalog(short_catalog, canonical, strict_total_seeds=False)
    assert any("length mismatch" in err for err in strict_errors)
    assert all("length mismatch" not in err for err in relaxed_errors)


# ---- counts accuracy tests ---------------------------------------------------

def test_counts_partial_equals_partial_variants(workspace):
    """counts.partial must equal the number of non-verified variants."""
    canonical = _load(workspace)
    variants = canonical["accumulated_clean_export"]["variants"]
    actual_partial = sum(1 for v in variants if v.get("verification_status") != "verified")
    counts = canonical.get("counts") or {}
    assert counts.get("partial") == actual_partial, (
        f"counts.partial={counts.get('partial')} != actual={actual_partial}"
    )


def test_counts_verified_equals_verified_variants(workspace):
    """counts.verified must equal the number of verified variants."""
    canonical = _load(workspace)
    variants = canonical["accumulated_clean_export"]["variants"]
    actual_verified = sum(1 for v in variants if v.get("verification_status") == "verified")
    counts = canonical.get("counts") or {}
    assert counts.get("verified") == actual_verified, (
        f"counts.verified={counts.get('verified')} != actual={actual_verified}"
    )
    assert actual_verified == EXPECTED_VERIFIED


def test_counts_makes_count(workspace):
    """counts.makes_count must equal the number of unique makes in variants."""
    canonical = _load(workspace)
    variants = canonical["accumulated_clean_export"]["variants"]
    actual_makes = len({(v.get("make") or "").strip() for v in variants if (v.get("make") or "").strip()})
    counts = canonical.get("counts") or {}
    assert counts.get("makes_count") == actual_makes, (
        f"counts.makes_count={counts.get('makes_count')} != actual={actual_makes}"
    )
    assert actual_makes == EXPECTED_MAKES_COUNT


def test_counts_models_count(workspace):
    """counts.models_count must equal unique (make, model) pairs in variants."""
    canonical = _load(workspace)
    variants = canonical["accumulated_clean_export"]["variants"]
    actual_models = len({
        ((v.get("make") or "").strip(), (v.get("model") or "").strip())
        for v in variants
        if (v.get("make") or "").strip() or (v.get("model") or "").strip()
    })
    counts = canonical.get("counts") or {}
    assert counts.get("models_count") == actual_models, (
        f"counts.models_count={counts.get('models_count')} != actual={actual_models}"
    )
    assert actual_models == EXPECTED_MODELS_COUNT


def test_processed_count_uses_processed_seed_ids(workspace):
    """counts.processed_seeds must equal len(processed_seed_ids), not stale processed_seeds list."""
    canonical = _load(workspace)
    bs = canonical["batch_state"]
    counts = canonical.get("counts") or {}
    actual = len(bs.get("processed_seed_ids") or [])
    assert counts.get("processed_seeds") == actual, (
        f"counts.processed_seeds={counts.get('processed_seeds')} != len(processed_seed_ids)={actual}"
    )
    assert actual == CURRENT_PROCESSED


def test_active_mode_is_normal_batch(workspace):
    """batch_state.active_mode must be 'normal_batch' when needs_retry_seed_ids is empty."""
    canonical = _load(workspace)
    bs = canonical["batch_state"]
    assert bs.get("needs_retry_seed_ids") == [], "precondition: needs_retry must be empty"
    assert bs.get("active_mode") == "normal_batch", (
        f"active_mode={bs.get('active_mode')} but needs_retry is empty; expected normal_batch"
    )


def test_empty_needs_retry_forces_normal_batch_dynamic(workspace):
    """queue.get_mode must return normal_batch whenever needs_retry_seed_ids is empty."""
    canonical = _load(workspace)
    # Inject a synthetic active_mode to confirm dynamic calculation ignores it.
    canonical["batch_state"]["active_mode"] = "rerun_queue_required"
    canonical["batch_state"]["needs_retry_seed_ids"] = []
    assert queue.get_mode(canonical) == "normal_batch"


def test_processed_seeds_list_synced_to_processed_seed_ids(workspace):
    """batch_state.processed_seeds list must have same length as processed_seed_ids."""
    canonical = _load(workspace)
    bs = canonical["batch_state"]
    seed_ids = list(bs.get("processed_seed_ids") or [])
    seeds_list = list(bs.get("processed_seeds") or [])
    assert len(seeds_list) == len(seed_ids), (
        f"len(processed_seeds)={len(seeds_list)} != len(processed_seed_ids)={len(seed_ids)}"
    )


def test_acceptance_diagnostics(workspace):
    """All acceptance criteria must be satisfied simultaneously."""
    canonical = _load(workspace)
    variants = canonical["accumulated_clean_export"]["variants"]
    bs = canonical["batch_state"]
    counts = canonical.get("counts") or {}

    vids = [v["variant_id"] for v in variants]
    duplicate_count = len(vids) - len(set(vids))
    verified_count = sum(1 for v in variants if v.get("verification_status") == "verified")
    partial_count = len(variants) - verified_count
    processed_count = len(bs.get("processed_seed_ids") or [])
    needs_retry_count = len(bs.get("needs_retry_seed_ids") or [])
    mode = queue.get_mode(canonical)
    next_seed = bs.get("next_seed_id")
    last_completed_seed = bs.get("last_completed_seed_id")
    haval_in_failed = HAVAL in (bs.get("failed_seed_ids") or [])

    # Print acceptance diagnostics
    print(f"\n=== Acceptance Diagnostics ===")
    print(f"variants = {len(variants)}")
    print(f"processed_seed_ids = {processed_count}")
    print(f"needs_retry_seed_ids = {needs_retry_count}")
    print(f"active_mode = {mode}")
    print(f"last_completed_seed_id = {last_completed_seed}")
    print(f"old_next_seed_id = {HAVAL}")
    print(f"repaired_next_seed_id = {next_seed}")
    print(f"duplicate_variant_id_count = {duplicate_count}")
    print(f"haval_in_failed_seed_ids = {haval_in_failed}")

    assert len(variants) == CURRENT_VARIANTS
    assert verified_count == EXPECTED_VERIFIED
    assert partial_count == EXPECTED_PARTIAL
    assert counts.get("partial") == EXPECTED_PARTIAL
    assert processed_count == CURRENT_PROCESSED
    assert needs_retry_count == CURRENT_NEEDS_RETRY
    assert mode == "normal_batch"
    assert last_completed_seed == HAVAL
    assert next_seed == REPAIRED_NEXT
    assert duplicate_count == 0
    assert haval_in_failed is False


# ---- legacy reference test ---------------------------------------------------

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
