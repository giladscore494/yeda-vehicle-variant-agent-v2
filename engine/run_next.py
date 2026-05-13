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
import json
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    return {
        "ok": overall_ok,
        "save": save_result,
        "push": push_result,
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
            "error": save_push.get("error"),
            "save": save_push.get("save"),
            "push": save_push.get("push"),
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
        "error": None,
    }


def run_next_model(*, run_seed_fn: Callable | None = None,
                   push_fn: Callable | None = None,
                   retry_hint: bool = False) -> dict:
    """Run whichever seed the queue picks next."""
    canonical = load_canonical()
    selection = queue.select_next_seed(canonical)
    seed_id = selection.get("selected_seed_id")
    if not seed_id:
        return {"ok": False, "error": "no seed available", "mode": selection.get("mode")}
    return run_selected_seed(seed_id, run_seed_fn=run_seed_fn, push_fn=push_fn, retry_hint=retry_hint)
