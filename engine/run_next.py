"""End-to-end "run next" flow.

Implements:
- run_next_model()
- run_selected_seed(seed_id)
- merge_result_into_canonical()
- save_and_push_canonical()

Critical rules:
- A seed is only marked resolved AFTER canonical save succeeds.
- During problem_queue runs, the normal cursor
  (``next_seed_id`` / ``last_completed_seed_id``) is FROZEN.
- The only state mutated lives inside canonical itself.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Callable

from agent.runner import run_seed as _default_run_seed
from engine import queue
from engine.canonical_store import (
    load_canonical,
    save_canonical_atomic,
    validate_canonical,
)
from engine.merge_variants import merge_variants

MAX_BATCH_SIZE = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _problem_progress(summary: dict) -> dict:
    return summary.get("problem_queue_progress") or {}


def _state_snapshot(canonical: dict) -> dict:
    summary = queue.summarize_state(canonical)
    progress = _problem_progress(summary)
    needs_retry_seed_ids = list((canonical.get("batch_state") or {}).get("needs_retry_seed_ids") or [])
    return {
        "mode": summary["mode"],
        "selected_seed_id": summary["selected_seed_id"],
        "next_seed_id": summary["next_seed_id"],
        "last_completed_seed_id": summary["last_completed_seed_id"],
        "variants": summary["variants_count"],
        "processed": summary["processed_seed_count"],
        "needs_retry": summary["needs_retry_count"],
        "progress": progress.get("current_position"),
        "problem_total": progress.get("total"),
        "problem_completed": progress.get("completed"),
        "problem_pending": progress.get("pending"),
        "needs_retry_seed_ids": needs_retry_seed_ids,
    }


def _result_progress(snapshot: dict) -> str | None:
    return snapshot.get("progress")


def _build_seed_result(seed_id: str, *, ok: bool, before: dict, after: dict,
                       run_result: dict) -> dict:
    return {
        "seed_id": seed_id,
        "ok": ok,
        "added_count": run_result.get("added_count", 0),
        "merged_count": run_result.get("merged_count", 0),
        "save_ok": bool((run_result.get("save") or {}).get("ok")),
        "push_ok": bool((run_result.get("push") or {}).get("ok")),
        "variants_before": before["variants"],
        "variants_after": after["variants"],
        "needs_retry_before": before["needs_retry"],
        "needs_retry_after": after["needs_retry"],
        "progress_before": _result_progress(before),
        "progress_after": _result_progress(after),
    }


def _final_state_from_canonical(canonical: dict | None) -> dict | None:
    if canonical is None:
        return None
    summary = queue.summarize_state(canonical)
    progress = _problem_progress(summary)
    return {
        "variants": summary["variants_count"],
        "processed": summary["processed_seed_count"],
        "needs_retry": summary["needs_retry_count"],
        "completed": progress.get("completed"),
        "pending": progress.get("pending"),
        "position": progress.get("current_position"),
        "selected_next_seed": summary["selected_seed_id"],
        "normal_next_seed_id": summary["next_seed_id"],
        "normal_last_completed_seed_id": summary["last_completed_seed_id"],
    }


def _reload_canonical_checked() -> tuple[dict | None, str | None]:
    try:
        canonical = load_canonical()
    except Exception as exc:
        return None, f"canonical reload failed: {exc}"
    ok, errs = validate_canonical(canonical)
    if not ok:
        return canonical, f"canonical reload validation failed: {errs}"
    return canonical, None


def _expected_progress_position(*, total: int | None, completed: int | None,
                                pending: int | None) -> str | None:
    if total is None or completed is None or pending is None:
        return None
    if pending > 0:
        return f"{completed + 1} / {total}"
    return f"{total} / {total}"


def _validate_successful_transition(before: dict, after: dict) -> list[str]:
    errors: list[str] = []
    if after["variants"] < before["variants"]:
        errors.append("variants count decreased")
    if before["mode"] == "problem_queue":
        if after["needs_retry"] >= before["needs_retry"]:
            errors.append("needs_retry count did not decrease after a successful problem_queue seed")
        if before["next_seed_id"] != after["next_seed_id"]:
            errors.append("normal next_seed_id changed while needs_retry is not empty")
        if before["last_completed_seed_id"] != after["last_completed_seed_id"]:
            errors.append("normal last_completed_seed_id changed while needs_retry is not empty")
        if after["problem_total"] != before["problem_total"]:
            errors.append("problem queue total changed")
        if after["problem_completed"] != (before["problem_completed"] + 1):
            errors.append("problem queue completed count did not advance correctly")
        if after["problem_pending"] != (before["problem_pending"] - 1):
            errors.append("problem queue pending count did not advance correctly")
        expected_position = _expected_progress_position(
            total=after["problem_total"],
            completed=after["problem_completed"],
            pending=after["problem_pending"],
        )
        if after["progress"] != expected_position:
            errors.append("progress did not advance correctly")
        if after["needs_retry"] > 0:
            expected_ids = after.get("needs_retry_seed_ids") or []
            expected_selected = expected_ids[0] if expected_ids else None
            if after["selected_seed_id"] != expected_selected:
                errors.append("selected next seed is not needs_retry_seed_ids[0]")
            if after["selected_seed_id"] == after["next_seed_id"]:
                errors.append("selected seed became normal next_seed_id while needs_retry is not empty")
    return errors


def _has_dedupe_or_no_variants_proof(seed_id: str, dedupe_proof: list[dict],
                                     added_count: int, merged_count: int,
                                     no_variants_reason: str | None) -> bool:
    """Closure rule: seed is resolved when one of these conditions holds."""
    if added_count > 0:
        return True
    if merged_count > 0:
        return True
    if dedupe_proof:
        return True
    if isinstance(no_variants_reason, str) and no_variants_reason.strip():
        return True
    return False


def _record_seed_accounting(canonical: dict, seed_id: str, *,
                            added: int, merged: int,
                            dedupe_proof: list[dict],
                            no_variants_reason: str | None) -> None:
    bs = canonical["batch_state"]
    accounting = bs.setdefault("seed_accounting", {})
    accounting[seed_id] = {
        "seed_id": seed_id,
        "variants_added_to_canonical": added,
        "variants_deduped_or_merged": merged,
        "dedupe_proof": dedupe_proof,
        "no_variants_reason": no_variants_reason,
        "marked_processed": True,
        "status": "resolved",
        "resolved_at": _now(),
    }
    if no_variants_reason:
        nvbs = bs.setdefault("no_variants_by_seed", {})
        nvbs[seed_id] = no_variants_reason


def merge_result_into_canonical(canonical: dict, seed_id: str, variants: list[dict],
                                no_variants_reason: str | None,
                                mode: str) -> dict:
    """Merge a runner result into a COPY of canonical and return a candidate
    canonical + merge metadata. Does NOT save anything.
    """
    candidate = copy.deepcopy(canonical)
    ace = candidate.setdefault("accumulated_clean_export", {})
    existing = ace.get("variants") or []
    merge_res = merge_variants(existing, variants or [])
    ace["variants"] = merge_res["merged_variants"]

    bs = candidate.setdefault("batch_state", {})
    bs["updated_at"] = _now()

    if mode == "problem_queue":
        # Remove the seed from needs_retry (only first occurrence)
        needs_retry = list(bs.get("needs_retry_seed_ids") or [])
        if needs_retry and needs_retry[0] == seed_id:
            needs_retry = needs_retry[1:]
        else:
            needs_retry = [s for s in needs_retry if s != seed_id]
        bs["needs_retry_seed_ids"] = needs_retry
        # Normal cursor MUST NOT move during problem_queue runs.
        # next_seed_id / last_completed_seed_id are intentionally untouched.
        # Add to processed if not already there.
        processed = bs.setdefault("processed_seed_ids", [])
        if seed_id not in processed:
            processed.append(seed_id)
    else:
        # Normal batch: advance the cursor. last_completed = seed_id.
        bs["last_completed_seed_id"] = seed_id
        processed = bs.setdefault("processed_seed_ids", [])
        if seed_id not in processed:
            processed.append(seed_id)
        # next_seed_id advancement requires the car-models catalog, which
        # this engine does not own. We leave next_seed_id untouched here;
        # the catalog/cursor module (if/when added) is responsible for it.

    _record_seed_accounting(
        candidate, seed_id,
        added=merge_res["added_count"],
        merged=merge_res["merged_count"],
        dedupe_proof=merge_res["dedupe_proof"],
        no_variants_reason=no_variants_reason,
    )

    return {
        "candidate_canonical": candidate,
        "added_count": merge_res["added_count"],
        "merged_count": merge_res["merged_count"],
        "dedupe_proof": merge_res["dedupe_proof"],
        "skipped_count": merge_res["skipped_count"],
    }


def save_and_push_canonical(candidate: dict, *, push_fn: Callable | None = None,
                            commit_message: str | None = None) -> dict:
    """Validate, atomically save canonical to disk, then push to GitHub.

    Returns a dict with keys: ok, save, push, error. If save fails the push is
    NOT attempted. If push fails, the on-disk canonical remains the new state
    but the caller may treat the overall operation as failed.
    """
    ok, errs = validate_canonical(candidate)
    if not ok:
        return {"ok": False, "save": None, "push": None,
                "error": f"validation failed: {errs}"}

    save_result = save_canonical_atomic(candidate)
    if not save_result.get("ok"):
        return {"ok": False, "save": save_result, "push": None,
                "error": save_result.get("error")}

    push_result: dict = {"ok": True, "skipped": True, "reason": "no push_fn provided"}
    if push_fn is not None:
        try:
            push_result = push_fn(candidate, commit_message=commit_message)
        except TypeError:
            push_result = push_fn(candidate)
        if not isinstance(push_result, dict):
            push_result = {"ok": False, "error": "push returned non-dict"}

    overall_ok = bool(save_result.get("ok")) and bool(push_result.get("ok", True))
    warning = None
    if save_result.get("ok") and not bool(push_result.get("ok", True)):
        warning = "Push failed after local save; the local canonical is ahead of GitHub."
    return {
        "ok": overall_ok,
        "save": save_result,
        "push": push_result,
        "warning": warning,
        "error": (None if overall_ok else (push_result.get("error") or save_result.get("error"))),
    }


def _default_push_fn(canonical: dict, commit_message: str | None = None) -> dict:
    try:
        from storage.github_canonical_store import push_canonical
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "error": f"github_canonical_store import failed: {exc}"}
    return push_canonical(canonical, commit_message=commit_message)


def run_selected_seed(seed_id: str, *,
                      run_seed_fn: Callable | None = None,
                      push_fn: Callable | None = None,
                      retry_hint: bool = False) -> dict:
    """Run a specific seed end-to-end.

    Flow: load -> select-mode -> run -> merge -> validate -> save -> push ->
    only then mark seed resolved (the merge already produced the candidate
    canonical with the seed removed from needs_retry; if save/push fail we
    drop that candidate and return an error so progress does not advance).
    """
    canonical = load_canonical()
    selection = queue.select_next_seed(canonical)
    mode = selection["mode"]

    runner = run_seed_fn or _default_run_seed
    run_result = runner(seed_id, retry_hint=retry_hint)
    if not run_result.get("ok"):
        return {
            "ok": False,
            "seed_id": seed_id,
            "mode": mode,
            "error": run_result.get("error") or "runner failed",
            "run_result": run_result,
            "save": None,
            "push": None,
        }

    variants = run_result.get("variants") or []
    no_variants_reason = run_result.get("no_variants_reason")
    merge_res = merge_result_into_canonical(
        canonical, seed_id, variants, no_variants_reason, mode,
    )

    resolved = _has_dedupe_or_no_variants_proof(
        seed_id,
        merge_res["dedupe_proof"],
        merge_res["added_count"],
        merge_res["merged_count"],
        no_variants_reason,
    )
    if not resolved:
        # No proof of resolution: do not save, do not advance.
        return {
            "ok": False,
            "seed_id": seed_id,
            "mode": mode,
            "error": "closure_rule_failed: no variants added/merged and no no_variants_reason",
            "added_count": merge_res["added_count"],
            "merged_count": merge_res["merged_count"],
            "save": None,
            "push": None,
        }

    save_push = save_and_push_canonical(
        merge_res["candidate_canonical"],
        push_fn=push_fn if push_fn is not None else _default_push_fn,
        commit_message=f"engine: resolve seed {seed_id}",
    )

    if not save_push.get("ok"):
        # Hard rule: save/push failed -> do not advance progress at all.
        return {
            "ok": False,
            "seed_id": seed_id,
            "mode": mode,
            "error": save_push.get("warning") or save_push.get("error"),
            "save": save_push.get("save"),
            "push": save_push.get("push"),
            "warning": save_push.get("warning"),
            "added_count": merge_res["added_count"],
            "merged_count": merge_res["merged_count"],
        }

    return {
        "ok": True,
        "seed_id": seed_id,
        "mode": mode,
        "added_count": merge_res["added_count"],
        "merged_count": merge_res["merged_count"],
        "dedupe_proof": merge_res["dedupe_proof"],
        "no_variants_reason": no_variants_reason,
        "save": save_push.get("save"),
        "push": save_push.get("push"),
        "warning": save_push.get("warning"),
        "error": None,
    }


def run_next_model(*, run_seed_fn: Callable | None = None,
                   batch_size: int = 1,
                   push_fn: Callable | None = None,
                   retry_hint: bool = False) -> dict:
    """Run up to ``batch_size`` seeds sequentially."""
    try:
        requested_batch_size = int(batch_size)
    except (TypeError, ValueError):
        requested_batch_size = 0
    if requested_batch_size < 1 or requested_batch_size > MAX_BATCH_SIZE:
        return {
            "ok": False,
            "requested_batch_size": batch_size,
            "processed_count": 0,
            "stopped_early": True,
            "stop_reason": f"batch_size must be between 1 and {MAX_BATCH_SIZE}",
            "results": [],
            "final_state": _final_state_from_canonical(load_canonical()),
        }

    results: list[dict] = []
    stop_reason: str | None = None

    for _ in range(requested_batch_size):
        canonical_before = load_canonical()
        before = _state_snapshot(canonical_before)
        seed_id = before.get("selected_seed_id")
        if not seed_id:
            break

        run_result = run_selected_seed(
            seed_id,
            run_seed_fn=run_seed_fn,
            push_fn=push_fn,
            retry_hint=retry_hint,
        )
        canonical_after, reload_error = _reload_canonical_checked()
        after = _state_snapshot(canonical_after or canonical_before)
        seed_result = _build_seed_result(
            seed_id,
            ok=False,
            before=before,
            after=after,
            run_result=run_result,
        )

        if not run_result.get("ok"):
            results.append(seed_result)
            stop_reason = run_result.get("warning") or run_result.get("error") or "seed run failed"
            break

        if reload_error:
            results.append(seed_result)
            stop_reason = reload_error
            break

        transition_errors = _validate_successful_transition(before, after)
        if transition_errors:
            results.append(
                _build_seed_result(
                    seed_id,
                    ok=False,
                    before=before,
                    after=after,
                    run_result=run_result,
                )
            )
            stop_reason = "; ".join(transition_errors)
            break

        results.append(
            _build_seed_result(
                seed_id,
                ok=True,
                before=before,
                after=after,
                run_result=run_result,
            )
        )

    final_canonical, final_reload_error = _reload_canonical_checked()
    processed_count = sum(1 for item in results if item.get("ok"))
    if stop_reason is None and final_reload_error:
        stop_reason = final_reload_error
    return {
        "ok": stop_reason is None,
        "requested_batch_size": requested_batch_size,
        "processed_count": processed_count,
        "stopped_early": stop_reason is not None,
        "stop_reason": stop_reason,
        "results": results,
        "final_state": _final_state_from_canonical(final_canonical),
    }
