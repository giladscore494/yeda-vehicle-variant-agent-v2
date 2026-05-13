"""Conflict detection over a list of variant dicts (adapted from legacy core/conflict_detector.py)."""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone


def _val(field):
    if isinstance(field, dict):
        return field.get("value")
    return field


def detect_conflicts(variants: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for v in variants:
        ys = _val(v.get("year_start"))
        ye = _val(v.get("year_end"))
        key = (v.get("make"), v.get("model"), ys, ye)
        groups[key].append(v)

    now = datetime.now(timezone.utc).isoformat()
    out: list[dict] = []
    for (make, model, ys, ye), items in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        seats = {_val(i.get("seats")) for i in items if _val(i.get("seats")) is not None}
        bodies = {_val(i.get("body_type")) for i in items if _val(i.get("body_type")) is not None}
        engines = {_val(i.get("engine")) for i in items if _val(i.get("engine")) is not None}
        if len(seats) > 1:
            out.append({
                "conflict_id": f"{make}_{model}_{ys}_{ye}_seats",
                "make": make, "model": model, "year_start": ys, "year_end": ye,
                "field_name": "seats", "values_found": sorted(seats, key=str),
                "source_ids": [], "reason": "Conflicting seats",
                "recommended_action": "review", "created_at": now,
            })
        if len(bodies) > 1:
            out.append({
                "conflict_id": f"{make}_{model}_{ys}_{ye}_body_type",
                "make": make, "model": model, "year_start": ys, "year_end": ye,
                "field_name": "body_type", "values_found": sorted(bodies, key=str),
                "source_ids": [], "reason": "Conflicting body type",
                "recommended_action": "review", "created_at": now,
            })
        if len(engines) > 1:
            out.append({
                "conflict_id": f"{make}_{model}_{ys}_{ye}_engine",
                "make": make, "model": model, "year_start": ys, "year_end": ye,
                "field_name": "engine", "values_found": sorted(engines, key=str),
                "source_ids": [], "reason": "Multiple engines without split",
                "recommended_action": "split_variants", "created_at": now,
            })
    return out
