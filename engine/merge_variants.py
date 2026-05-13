"""Merge new variants into the canonical variants list.

Rules:
- Primary dedupe key is ``variant_id``.
- Never create duplicate variant_id.
- Never reduce variant count.
- Never overwrite rich existing data with empty values.
- For an existing variant, only fill missing fields from the new candidate.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any


FIELD_FALLBACK_NAMES = (
    "body_type", "seats", "engine", "transmission", "fuel_type",
    "drivetrain", "trim", "doors", "generation",
)


def _is_empty_field(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, dict):
        v = value.get("value")
        return v in (None, "")
    return value in ("", [], {})


def _merge_field(existing: Any, incoming: Any) -> Any:
    """Prefer existing rich data; otherwise take incoming."""
    if not _is_empty_field(existing):
        return existing
    return incoming


def _merge_variant(existing: dict, incoming: dict) -> dict:
    out = copy.deepcopy(existing)
    for name in FIELD_FALLBACK_NAMES:
        if name in incoming:
            out[name] = _merge_field(out.get(name), incoming.get(name))
    # Top-level optional fields
    for name in ("year_start", "year_end", "market", "aliases", "notes"):
        if name in incoming and _is_empty_field(out.get(name)):
            out[name] = incoming[name]
    if not out.get("created_at"):
        out["created_at"] = incoming.get("created_at") or out.get("created_at")
    out["updated_at"] = datetime.now(timezone.utc).isoformat()
    return out


def merge_variants(canonical_variants: list[dict], new_variants: list[dict]) -> dict:
    """Merge ``new_variants`` into ``canonical_variants``.

    Returns a result dict:
        - merged_variants: new list to assign back into canonical
        - added_count: number of new variant_ids appended
        - merged_count: number of existing variant_ids enriched
        - skipped_count: number of incoming entries dropped (missing variant_id)
        - dedupe_proof: list of {incoming_variant_id, matched_existing_variant_id}
    """
    index: dict[str, int] = {}
    out: list[dict] = []
    for v in canonical_variants or []:
        if not isinstance(v, dict):
            continue
        vid = str(v.get("variant_id") or "").strip()
        if not vid:
            out.append(v)
            continue
        if vid in index:
            # de-dup any pre-existing duplicates (defence in depth)
            continue
        index[vid] = len(out)
        out.append(v)

    added = 0
    merged = 0
    skipped = 0
    dedupe_proof: list[dict] = []

    for incoming in new_variants or []:
        if not isinstance(incoming, dict):
            skipped += 1
            continue
        vid = str(incoming.get("variant_id") or "").strip()
        if not vid:
            skipped += 1
            continue
        if vid in index:
            idx = index[vid]
            out[idx] = _merge_variant(out[idx], incoming)
            merged += 1
            dedupe_proof.append({
                "incoming_variant_id": vid,
                "matched_existing_variant_id": vid,
                "action": "merged",
            })
        else:
            index[vid] = len(out)
            out.append(copy.deepcopy(incoming))
            added += 1

    # Hard invariant: never reduce variant count
    if len(out) < len(canonical_variants or []):
        raise RuntimeError("merge_variants would reduce variant count")

    return {
        "merged_variants": out,
        "added_count": added,
        "merged_count": merged,
        "skipped_count": skipped,
        "dedupe_proof": dedupe_proof,
    }
