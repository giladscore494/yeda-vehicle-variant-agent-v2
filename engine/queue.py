"""Seed queue: decides which seed runs next.

The canonical batch_state is the ONLY source of truth. No external state file
may decide what runs.

Modes:
- "problem_queue": ``batch_state.needs_retry_seed_ids`` is not empty.
  The next seed is ``needs_retry_seed_ids[0]``. The normal cursor
  (``next_seed_id`` / ``last_completed_seed_id``) MUST NOT be moved while
  in this mode.
- "normal_batch": ``needs_retry_seed_ids`` is empty. The next seed is
  ``batch_state.next_seed_id``.

Problem-queue progress is computed dynamically; we never persist it as state.
The "original_problem_total" is resolved from a priority list of immutable
fields and defaults to 54 for the initial run.
"""
from __future__ import annotations

from typing import Any

ORIGINAL_PROBLEM_TOTAL_DEFAULT = 54


def _batch_state(canonical: dict) -> dict:
    bs = canonical.get("batch_state") if isinstance(canonical, dict) else None
    if not isinstance(bs, dict):
        raise ValueError("canonical is missing batch_state")
    return bs


def select_next_seed(canonical: dict) -> dict:
    """Return the seed selection result.

    Keys: mode, selected_seed_id, needs_retry_seed_ids, next_seed_id,
    last_completed_seed_id.
    """
    bs = _batch_state(canonical)
    needs_retry = list(bs.get("needs_retry_seed_ids") or [])
    next_seed = bs.get("next_seed_id")
    last_completed = bs.get("last_completed_seed_id")
    if needs_retry:
        return {
            "mode": "problem_queue",
            "selected_seed_id": needs_retry[0],
            "needs_retry_seed_ids": needs_retry,
            "next_seed_id": next_seed,
            "last_completed_seed_id": last_completed,
        }
    return {
        "mode": "normal_batch",
        "selected_seed_id": next_seed,
        "needs_retry_seed_ids": needs_retry,
        "next_seed_id": next_seed,
        "last_completed_seed_id": last_completed,
    }


def _resolve_original_problem_total(canonical: dict) -> int:
    """Find the immutable total of problem seeds for the original repair set.

    Priority:
      1. problem_repair_state.original_problem_seed_ids
      2. batch_state.zero_variant_repair_audit.original_false_processed_seed_ids
      3. batch_state.false_processed_seed_ids_original
      4. batch_state.false_processed_seed_ids (only in clean initial state)
    Fallback: ORIGINAL_PROBLEM_TOTAL_DEFAULT (54).
    """
    prs = canonical.get("problem_repair_state")
    if isinstance(prs, dict):
        ids = prs.get("original_problem_seed_ids")
        if isinstance(ids, list):
            return len(ids)

    bs = canonical.get("batch_state") or {}
    audit = bs.get("zero_variant_repair_audit") or {}
    if isinstance(audit, dict):
        ids = audit.get("original_false_processed_seed_ids")
        if isinstance(ids, list) and ids:
            return len(ids)

    orig = bs.get("false_processed_seed_ids_original")
    if isinstance(orig, list) and orig:
        return len(orig)

    initial = bs.get("false_processed_seed_ids")
    if isinstance(initial, list) and initial:
        return len(initial)

    return ORIGINAL_PROBLEM_TOTAL_DEFAULT


def compute_problem_queue_progress(canonical: dict) -> dict:
    """Compute problem-queue progress dynamically from the canonical state."""
    bs = _batch_state(canonical)
    pending = len(bs.get("needs_retry_seed_ids") or [])
    total = _resolve_original_problem_total(canonical)
    # Guard against the rare case where pending exceeds total (e.g. broken state).
    if pending > total:
        total = pending
    completed = total - pending
    if pending > 0:
        position = f"{completed + 1} / {total}"
    else:
        position = f"{total} / {total}"
    percent = (completed / total) if total > 0 else 0.0
    current_seed = (bs.get("needs_retry_seed_ids") or [None])[0] if pending > 0 else None
    return {
        "total": total,
        "completed": completed,
        "pending": pending,
        "current_position": position,
        "percent": percent,
        "current_seed": current_seed,
        "normal_paused_at": bs.get("next_seed_id"),
    }


def get_mode(canonical: dict) -> str:
    bs = _batch_state(canonical)
    return "problem_queue" if (bs.get("needs_retry_seed_ids") or []) else "normal_batch"


def summarize_state(canonical: dict) -> dict:
    bs = _batch_state(canonical)
    variants = (canonical.get("accumulated_clean_export") or {}).get("variants") or []
    sel = select_next_seed(canonical)
    info: dict[str, Any] = {
        "mode": sel["mode"],
        "selected_seed_id": sel["selected_seed_id"],
        "next_seed_id": bs.get("next_seed_id"),
        "last_completed_seed_id": bs.get("last_completed_seed_id"),
        "total_seeds": bs.get("total_seeds"),
        "processed_seed_count": len(bs.get("processed_seed_ids") or []),
        "needs_retry_count": len(bs.get("needs_retry_seed_ids") or []),
        "variants_count": len(variants),
    }
    if sel["mode"] == "problem_queue":
        info["problem_queue_progress"] = compute_problem_queue_progress(canonical)
    return info
