"""Build a clean final export view from canonical variants.

Adapted minimal version of legacy core/final_export_builder.py. Used by the
diagnostics tab to summarise canonical contents on demand. Does NOT decide
what runs and does NOT mutate canonical.
"""
from __future__ import annotations
import copy
import json
from datetime import datetime, timezone
from typing import Any


FIELD_NAMES = ("body_type", "seats", "engine", "transmission", "fuel_type", "drivetrain", "generation", "year_start", "year_end", "trim")
CRITICAL_FIELDS = ("body_type", "seats", "engine", "transmission", "fuel_type", "drivetrain")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_field(value: Any) -> dict:
    return value if isinstance(value, dict) else {"value": value}


def build_final_export(variants: list[dict]) -> dict:
    """Return a clean summary of the canonical variants list."""
    cleaned = [copy.deepcopy(v) for v in variants if isinstance(v, dict)]

    seen: dict[str, dict] = {}
    duplicates = 0
    for v in cleaned:
        key = str(v.get("variant_id") or "").strip().lower()
        if not key:
            continue
        if key in seen:
            duplicates += 1
            continue
        seen[key] = v

    deduped = list(seen.values())
    counts = {
        "total_variants": len(deduped),
        "duplicates_removed": duplicates,
        "verified": sum(1 for v in deduped if v.get("verification_status") == "verified"),
        "partial": sum(1 for v in deduped if v.get("verification_status") == "partial"),
        "conflict": sum(1 for v in deduped if v.get("verification_status") == "conflict"),
        "unresolved": sum(1 for v in deduped if v.get("verification_status") == "unresolved"),
    }
    fields_checked = with_sources = with_ids = 0
    for v in deduped:
        for n in FIELD_NAMES:
            if n in v:
                f = _as_field(v[n])
                fields_checked += 1
                if int(f.get("sources_count", 0) or 0) > 0:
                    with_sources += 1
                    if f.get("source_ids"):
                        with_ids += 1
    audit = {
        "fields_checked": fields_checked,
        "fields_with_sources_count_gt_0": with_sources,
        "fields_with_source_ids": with_ids,
        "source_id_coverage_ratio": (with_ids / max(with_sources, 1)),
    }
    return {
        "schema_version": "vehicle_variants_final_v2_clean",
        "created_at": _now(),
        "variants": deduped,
        "counts": counts,
        "audit": audit,
    }


def export_as_json_bytes(variants: list[dict]) -> bytes:
    payload = build_final_export(variants)
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
