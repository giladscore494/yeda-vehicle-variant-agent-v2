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

import json
from pathlib import Path
from typing import Any

ORIGINAL_PROBLEM_TOTAL_DEFAULT = 54
SEED_CATALOG_PATHS = (
    Path("data/seed_catalog_il.json"),
    Path("data/seeds/vehicle_model_seeds_il.json"),
)


def _batch_state(canonical: dict) -> dict:
    bs = canonical.get("batch_state") if isinstance(canonical, dict) else None
    if not isinstance(bs, dict):
        raise ValueError("canonical is missing batch_state")
    return bs


def _seed_catalog_ids_from_raw(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError("seed catalog must be a list")

    seed_ids: list[str] = []
    for item in raw:
        if isinstance(item, str):
            seed_id = item
        elif isinstance(item, dict):
            seed_id = item.get("seed_id")
        else:
            raise ValueError("seed catalog entries must be seed_id strings or dicts with seed_id")
        if not isinstance(seed_id, str) or not seed_id.strip():
            raise ValueError("seed catalog contains an empty seed_id")
        seed_ids.append(seed_id.strip())
    return seed_ids


def validate_seed_catalog(seed_catalog: list[str], canonical: dict, *,
                          strict_total_seeds: bool = True) -> list[str]:
    errors: list[str] = []
    if not seed_catalog:
        errors.append("seed catalog is empty")
        return errors

    seen: set[str] = set()
    duplicates: list[str] = []
    for seed_id in seed_catalog:
        if seed_id in seen and seed_id not in duplicates:
            duplicates.append(seed_id)
        seen.add(seed_id)
    if duplicates:
        errors.append(f"seed catalog contains duplicate seed_id values: {duplicates[:5]}")

    bs = _batch_state(canonical)
    next_seed_id = bs.get("next_seed_id")
    if next_seed_id is not None and next_seed_id not in seen:
        errors.append(f"seed catalog missing batch_state.next_seed_id: {next_seed_id}")

    last_completed_seed_id = bs.get("last_completed_seed_id")
    if last_completed_seed_id is not None and last_completed_seed_id not in seen:
        errors.append(
            "seed catalog missing batch_state.last_completed_seed_id: "
            f"{last_completed_seed_id}"
        )

    total_seeds = bs.get("total_seeds")
    if strict_total_seeds and total_seeds is not None and len(seed_catalog) != total_seeds:
        errors.append(
            "seed catalog length mismatch: "
            f"catalog={len(seed_catalog)} batch_state.total_seeds={total_seeds}"
        )

    return errors


def load_seed_catalog(canonical: dict, *, strict_total_seeds: bool = True) -> list[str]:
    sources: list[tuple[str, Any]] = []
    for path in SEED_CATALOG_PATHS:
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ValueError(f"failed to read seed catalog at {path}: {exc}") from exc
            sources.append((str(path), raw))

    bs = _batch_state(canonical)
    sources.extend([
        ("canonical['seed_catalog']", canonical.get("seed_catalog")),
        ("canonical['batch_state']['seed_catalog']", bs.get("seed_catalog")),
        ("canonical['batch_state']['seed_catalog_ids']", bs.get("seed_catalog_ids")),
    ])

    for source_name, raw in sources:
        if raw is None:
            continue
        seed_catalog = _seed_catalog_ids_from_raw(raw)
        errors = validate_seed_catalog(
            seed_catalog,
            canonical,
            strict_total_seeds=strict_total_seeds,
        )
        if errors:
            raise ValueError(f"invalid seed catalog from {source_name}: {'; '.join(errors)}")
        return seed_catalog

    raise ValueError(
        "seed catalog missing: expected data/seed_catalog_il.json, "
        "data/seeds/vehicle_model_seeds_il.json, canonical['seed_catalog'], "
        "batch_state.seed_catalog, or batch_state.seed_catalog_ids"
    )


def find_next_unprocessed_seed(seed_catalog: list[str], current_seed_id: str, *,
                               processed_seed_ids: list[str] | None = None,
                               skipped_seed_ids: list[str] | None = None,
                               failed_seed_ids: list[str] | None = None) -> str | None:
    processed = set(processed_seed_ids or [])
    skipped = set(skipped_seed_ids or [])
    failed = set(failed_seed_ids or [])
    try:
        current_idx = seed_catalog.index(current_seed_id)
    except ValueError as exc:
        raise ValueError(f"seed catalog is missing current seed_id: {current_seed_id}") from exc

    for candidate in seed_catalog[current_idx + 1:]:
        if candidate in processed or candidate in skipped or candidate in failed:
            continue
        return candidate
    return None


def repair_normal_batch_cursor(canonical: dict, seed_catalog: list[str]) -> dict:
    bs = _batch_state(canonical)
    old_next_seed_id = bs.get("next_seed_id")
    last_completed_seed_id = bs.get("last_completed_seed_id")
    if last_completed_seed_id is None:
        raise ValueError("cannot repair normal cursor without batch_state.last_completed_seed_id")

    repaired_next_seed_id = find_next_unprocessed_seed(
        seed_catalog,
        last_completed_seed_id,
        processed_seed_ids=bs.get("processed_seed_ids"),
        skipped_seed_ids=bs.get("skipped_seed_ids"),
        failed_seed_ids=bs.get("failed_seed_ids"),
    )
    bs["next_seed_id"] = repaired_next_seed_id
    return {
        "old_next_seed_id": old_next_seed_id,
        "repaired_next_seed_id": repaired_next_seed_id,
        "last_completed_seed_id": last_completed_seed_id,
        "changed": old_next_seed_id != repaired_next_seed_id,
    }


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
