"""Variant validators (adapted from legacy core/validators.py)."""
from __future__ import annotations

CRITICAL_FIELDS = ("body_type", "seats", "fuel_type", "engine", "transmission", "drivetrain")
ALL_FIELDS = ("body_type", "seats", "engine", "transmission", "fuel_type", "drivetrain")


def _as_dict(field):
    if field is None:
        return {}
    if isinstance(field, dict):
        return field
    if hasattr(field, "model_dump"):
        return field.model_dump()
    return {"value": field}


def field_is_trusted(field) -> bool:
    f = _as_dict(field)
    return (
        f.get("status") == "verified"
        and int(f.get("sources_count", 0) or 0) >= 1
        and bool(f.get("used_in_compare"))
    )


def validate_variant_dict(variant: dict) -> tuple[bool, list[str]]:
    """Lightweight validation that works on plain dict variants."""
    errs: list[str] = []
    if not isinstance(variant, dict):
        return False, ["not_a_dict"]
    if not str(variant.get("variant_id") or "").strip():
        errs.append("variant_id_empty")
    ys = _as_dict(variant.get("year_start")).get("value", variant.get("year_start"))
    ye = _as_dict(variant.get("year_end")).get("value", variant.get("year_end"))
    try:
        if ys is not None and ye is not None and int(ys) > int(ye):
            errs.append("invalid_year_range")
    except (TypeError, ValueError):
        errs.append("invalid_year_range")
    if not variant.get("make"):
        errs.append("missing_make")
    if not variant.get("model"):
        errs.append("missing_model")
    return (len(errs) == 0), sorted(set(errs))


def classify_variant_dict(variant: dict) -> str:
    fields = {name: _as_dict(variant.get(name)) for name in ALL_FIELDS}
    if any(fields[n].get("status") == "conflict" for n in CRITICAL_FIELDS):
        return "conflict"
    sourced_critical = sum(1 for n in CRITICAL_FIELDS if int(fields[n].get("sources_count", 0) or 0) >= 1)
    multi_sourced_critical = sum(1 for n in CRITICAL_FIELDS if int(fields[n].get("sources_count", 0) or 0) >= 2)
    verified_ready = (
        fields["body_type"].get("status") in {"verified", "partial"}
        and fields["seats"].get("status") in {"verified", "partial"}
        and fields["fuel_type"].get("status") in {"verified", "partial"}
        and (fields["engine"].get("status") == "verified" or fields["transmission"].get("status") == "verified")
        and sourced_critical >= 4
        and multi_sourced_critical >= 2
    )
    if verified_ready:
        return "verified"
    identity_values = [
        fields["engine"].get("value"),
        fields["transmission"].get("value"),
        fields["fuel_type"].get("value"),
        fields["body_type"].get("value"),
        variant.get("generation"),
    ]
    has_identity = any(v not in (None, "") for v in identity_values)
    non_null_critical = sum(1 for n in CRITICAL_FIELDS if fields[n].get("value") not in (None, ""))
    if has_identity and non_null_critical >= 2 and sourced_critical >= 1 and str(variant.get("variant_id") or "").strip():
        return "partial"
    if not has_identity:
        return "unresolved"
    return "unverified"
