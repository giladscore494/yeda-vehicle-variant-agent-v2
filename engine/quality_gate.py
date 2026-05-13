"""Data-quality gate for vehicle variants.

Runs after Gemini extraction and before canonical save to prevent inaccurate,
mixed, overbroad, hallucinated, or weakly grounded variant data from being saved.

This gate is intentionally model/make-agnostic: the same rules apply to every
vehicle variant for every make and model.
"""
from __future__ import annotations

import copy
import re
from typing import Any


# ---------------------------------------------------------------------------
# Canonical body-type values
# ---------------------------------------------------------------------------

BODY_TYPE_CANONICAL = frozenset({
    "Hatchback", "Sedan", "Wagon", "Coupe", "Convertible",
    "SUV", "Crossover", "MPV", "Pickup", "Van", "Roadster",
    "Gran Coupe", "Liftback", "Fastback",
})

# More-specific patterns must come before more-general ones.
_BODY_TYPE_PATTERNS: list[tuple[str, str]] = [
    (r"gran[\s\-]?coup[eé]", "Gran Coupe"),
    (r"cabriolet|cabrio\b|convertible|drop[\s\-]?head", "Convertible"),
    (r"roadster|spider|spyder\b", "Roadster"),
    (r"pick[\s\-]?up|pickup|truck\b", "Pickup"),
    (r"\bvan\b|cargo[\s\-]van|panel[\s\-]van", "Van"),
    (r"fastback", "Fastback"),
    (r"liftback", "Liftback"),
    (r"shooting[\s\-]brake|estate|touring\b|combi\b|avant\b|wagon|break\b|\bsw\b", "Wagon"),
    (r"\bsuv\b|sport[\s\-]utility|sport utility", "SUV"),
    (r"crossover|\bcuv\b", "Crossover"),
    (r"\bmpv\b|people[\s\-]carrier|minivan", "MPV"),
    (r"coup[eé]", "Coupe"),
    (r"hatchback|\bhatch\b", "Hatchback"),
    (r"saloon|berline|limousine|\bsedan\b", "Sedan"),
]

# Patterns that indicate a trim string mixes in package/edition names.
_PACKAGE_SPLIT_RE = re.compile(r"\s*/\s*|\s*\|\s*")
_PACKAGE_KEYWORDS_RE = re.compile(
    r"\b("
    r"elegant|exclusive|luxury|carbon\s+edition|sport\s+line|m[\s\-]sport|"
    r"executive|signature|premium|prestige|comfort|individual|excellence|"
    r"connected|sport\s+edition|edition\b|package\b|pack\b|line\b|"
    r"design[\w]*line|touring\s+line|urban\s+line|advantage|lifestyle"
    r")\b",
    re.IGNORECASE,
)

# Engine strings that are too vague to be a meaningful spec.
_VAGUE_ENGINE_RE = re.compile(
    r"^(petrol\s+engine|diesel\s+engine|various\s+engines?|unknown|"
    r"engine|petrol|diesel|hybrid|electric)$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Internal field helpers
# ---------------------------------------------------------------------------

def _get_field_value(field: Any) -> Any:
    """Extract .value from a VerifiedField dict, or return the plain value."""
    if isinstance(field, dict):
        return field.get("value")
    return field


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("", "unknown", "n/a", "none", "null",
                                         "various", "various engines")
    return False


def _field_is_empty(field: Any) -> bool:
    return _is_empty(_get_field_value(field))


def _get_year(field: Any, default: int | None = None) -> int | None:
    val = _get_field_value(field)
    if val is None:
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Body-type normalization
# ---------------------------------------------------------------------------

def _normalize_body_type_str(raw: str) -> str | None:
    """Map a raw body_type string to a canonical label.

    Returns the canonical label, or None if unrecognisable.
    """
    if not raw:
        return None
    v = raw.strip()
    # Direct case-insensitive match first
    for canonical in BODY_TYPE_CANONICAL:
        if v.lower() == canonical.lower():
            return canonical
    # Pattern matching
    for pattern, label in _BODY_TYPE_PATTERNS:
        if re.search(pattern, v, re.IGNORECASE):
            return label
    return None


# ---------------------------------------------------------------------------
# Trim/package separation
# ---------------------------------------------------------------------------

def _split_trim_packages(trim_raw: str) -> tuple[str, list[str]]:
    """Split a trim string into (clean_trim, packages_list).

    E.g. "M850i xDrive / Elegant / Carbon Edition"
         -> ("M850i xDrive", ["Elegant", "Carbon Edition"])
    """
    if not trim_raw:
        return trim_raw, []

    # Split on "/" or "|" separators
    parts = _PACKAGE_SPLIT_RE.split(trim_raw.strip())
    if len(parts) > 1:
        clean_trim = parts[0].strip()
        extras = [p.strip() for p in parts[1:] if p.strip()]
        return clean_trim, extras

    # Single part: try to strip trailing package keywords
    remaining = trim_raw.strip()
    found: list[str] = [m.group(0) for m in _PACKAGE_KEYWORDS_RE.finditer(remaining)]
    if found:
        clean = _PACKAGE_KEYWORDS_RE.sub("", remaining).strip().rstrip(",;/ -")
        if clean and clean.lower() != remaining.lower():
            return clean, found

    return remaining, []


# ---------------------------------------------------------------------------
# Individual validation checks
# ---------------------------------------------------------------------------

def _check_core_fields(variant: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    # Hard reject if ALL of the four core technical fields are unknown.
    core_four = ("engine", "transmission", "drivetrain", "body_type")
    if all(_field_is_empty(variant.get(f)) for f in core_four):
        errors.append(
            "all_core_fields_unknown: engine, transmission, drivetrain, "
            "and body_type are all unknown/null"
        )
        return errors, warnings

    for field in ("make", "model", "market"):
        if _is_empty(variant.get(field)):
            warnings.append(f"missing_required_field: {field}")

    for field in ("engine", "transmission", "drivetrain", "fuel_type"):
        if _field_is_empty(variant.get(field)):
            warnings.append(f"field_is_unknown: {field}")

    return errors, warnings


def _check_year_range(variant: dict, seed: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    var_ys = _get_year(variant.get("year_start"))
    var_ye = _get_year(variant.get("year_end"))
    seed_ys = seed.get("year_start")
    seed_ye = seed.get("year_end")

    if var_ys is None or var_ye is None:
        warnings.append("year_start_or_year_end_missing")
        return errors, warnings

    if seed_ys is None or seed_ye is None:
        return errors, warnings

    if var_ye < seed_ys:
        errors.append(
            f"out_of_seed_year_range: variant year_end {var_ye} "
            f"< seed year_start {seed_ys}"
        )
    elif var_ys > seed_ye:
        errors.append(
            f"out_of_seed_year_range: variant year_start {var_ys} "
            f"> seed year_end {seed_ye}"
        )

    return errors, warnings


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _check_make_model(variant: dict, seed: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    var_make = str(variant.get("make") or "").strip()
    seed_make = str(seed.get("make") or "").strip()
    if var_make and seed_make:
        if _normalize_name(var_make) != _normalize_name(seed_make):
            errors.append(
                f"make_mismatch: variant make '{var_make}' != seed make '{seed_make}'"
            )

    var_model = str(variant.get("model") or "").strip()
    seed_model = str(seed.get("model") or "").strip()
    if var_model and seed_model:
        nm_var = _normalize_name(var_model)
        nm_seed = _normalize_name(seed_model)
        if nm_var != nm_seed and nm_seed not in nm_var and nm_var not in nm_seed:
            warnings.append(
                f"model_name_differs: variant model '{var_model}' "
                f"differs from seed model '{seed_model}'"
            )

    return errors, warnings


def _check_body_type(variant: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    body_raw = _get_field_value(variant.get("body_type"))
    if _is_empty(body_raw):
        warnings.append("field_is_unknown: body_type")
        return errors, warnings

    normalized = _normalize_body_type_str(str(body_raw))
    if normalized is None:
        warnings.append(
            f"body_type_unrecognized: '{body_raw}' is not a known canonical body type"
        )

    return errors, warnings


def _check_trim(variant: dict) -> tuple[list[str], list[str], dict]:
    errors: list[str] = []
    warnings: list[str] = []
    suggested_fixes: dict = {}

    trim_raw = _get_field_value(variant.get("trim"))
    if _is_empty(trim_raw):
        return errors, warnings, suggested_fixes

    clean_trim, packages = _split_trim_packages(str(trim_raw))
    if packages:
        warnings.append(
            f"trim_contains_packages: '{trim_raw}' -> "
            f"trim='{clean_trim}', packages={packages}"
        )
        suggested_fixes["trim"] = clean_trim
        suggested_fixes["packages_or_editions"] = packages

    return errors, warnings, suggested_fixes


def _check_engine(variant: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    engine_val = _get_field_value(variant.get("engine"))
    if _is_empty(engine_val):
        return errors, warnings

    if _VAGUE_ENGINE_RE.match(str(engine_val).strip()):
        warnings.append(f"engine_too_vague: '{engine_val}'")

    return errors, warnings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_variant_quality(variant: dict, seed: dict,
                              sources: list | None = None) -> dict:
    """Validate quality of a single variant against its seed.

    Returns a dict with keys:
        - variant_id: str | None
        - errors: list[str]      (hard-reject causes)
        - warnings: list[str]    (soft issues)
        - suggested_fixes: dict
        - quality_level: "high" | "medium" | "partial" | "reject"
    """
    if not isinstance(variant, dict):
        return {
            "variant_id": None,
            "errors": ["not_a_dict"],
            "warnings": [],
            "suggested_fixes": {},
            "quality_level": "reject",
        }

    variant_id = str(variant.get("variant_id") or "").strip() or None
    all_errors: list[str] = []
    all_warnings: list[str] = []
    all_fixes: dict = {}

    errs, warns = _check_core_fields(variant)
    all_errors.extend(errs)
    all_warnings.extend(warns)

    errs, warns = _check_year_range(variant, seed)
    all_errors.extend(errs)
    all_warnings.extend(warns)

    errs, warns = _check_make_model(variant, seed)
    all_errors.extend(errs)
    all_warnings.extend(warns)

    errs, warns = _check_body_type(variant)
    all_errors.extend(errs)
    all_warnings.extend(warns)

    errs, warns, fixes = _check_trim(variant)
    all_errors.extend(errs)
    all_warnings.extend(warns)
    all_fixes.update(fixes)

    errs, warns = _check_engine(variant)
    all_errors.extend(errs)
    all_warnings.extend(warns)

    report: dict = {
        "variant_id": variant_id,
        "errors": all_errors,
        "warnings": all_warnings,
        "suggested_fixes": all_fixes,
        "quality_level": "pending",
    }
    report["quality_level"] = classify_quality_level(variant, report)
    return report


def classify_quality_level(variant: dict, validation_report: dict) -> str:
    """Classify the quality level of a variant based on its validation report.

    Returns: "high" | "medium" | "partial" | "reject"
    """
    errors = validation_report.get("errors") or []
    warnings = validation_report.get("warnings") or []

    if errors:
        return "reject"

    core_fields = ("engine", "transmission", "drivetrain", "fuel_type", "body_type")
    known_core = sum(1 for f in core_fields if not _field_is_empty(variant.get(f)))

    sources_count = variant.get("sources_count") or 0
    has_source = int(sources_count) >= 1

    warning_count = len(warnings)

    if known_core >= 4 and has_source and warning_count <= 1:
        return "high"
    if known_core >= 3:
        return "medium"
    if known_core >= 1:
        return "partial"
    return "partial"


def normalize_variant_fields(variant: dict, seed: dict) -> dict:
    """Return a copy of *variant* with normalized body_type and trim fields.

    - body_type is mapped to a canonical label when possible.
    - trim is cleaned of package/edition names; those are stored in
      ``packages_or_editions`` on the variant dict.
    """
    out = copy.deepcopy(variant)

    # Normalize body_type
    body_field = out.get("body_type")
    body_raw = _get_field_value(body_field)
    if not _is_empty(body_raw):
        normalized_bt = _normalize_body_type_str(str(body_raw))
        if normalized_bt and normalized_bt.lower() != str(body_raw).lower():
            if isinstance(body_field, dict):
                out["body_type"] = dict(body_field)
                out["body_type"]["value"] = normalized_bt
            else:
                out["body_type"] = normalized_bt

    # Normalize trim — split packages out
    trim_field = out.get("trim")
    trim_raw = _get_field_value(trim_field)
    if not _is_empty(trim_raw):
        clean_trim, packages = _split_trim_packages(str(trim_raw))
        if packages:
            if isinstance(trim_field, dict):
                out["trim"] = dict(trim_field)
                out["trim"]["value"] = clean_trim
            else:
                out["trim"] = clean_trim
            out["packages_or_editions"] = packages

    return out


def validate_candidate_variants_quality(
    variants: list[dict],
    seed: dict,
    sources: list | None = None,
) -> dict:
    """Run the quality gate on all candidate variants.

    Returns a quality-report dict:
        - total_candidate_variants: int
        - accepted: int
        - rejected: int
        - warnings: list[str]   (de-duplicated from all accepted variants)
        - rejected_variants: list[{variant_id, reason}]
        - accepted_variants: list[{variant_id, quality_level, warnings, ...}]
        - accepted_variant_objects: list[dict]  (normalised variants ready to save)
        - all_rejected: bool
    """
    total = len(variants) if variants else 0
    seen_ids: set[str] = set()

    accepted_variants: list[dict] = []
    rejected_variants: list[dict] = []
    global_warnings: list[str] = []
    accepted_objects: list[dict] = []

    for variant in variants or []:
        if not isinstance(variant, dict):
            rejected_variants.append({"variant_id": None, "reason": "not_a_dict"})
            continue

        vid = str(variant.get("variant_id") or "").strip()

        # Duplicate variant_id check
        if vid and vid in seen_ids:
            rejected_variants.append({
                "variant_id": vid,
                "reason": f"duplicate_variant_id: {vid}",
            })
            continue
        if vid:
            seen_ids.add(vid)

        report = validate_variant_quality(variant, seed, sources=sources)
        quality_level = report["quality_level"]

        if quality_level == "reject":
            rejected_variants.append({
                "variant_id": vid or None,
                "reason": "; ".join(report.get("errors") or ["unknown"]),
            })
        else:
            normalized = normalize_variant_fields(variant, seed)
            accepted_objects.append(normalized)
            entry: dict = {
                "variant_id": vid or None,
                "quality_level": quality_level,
                "warnings": report.get("warnings") or [],
            }
            if report.get("suggested_fixes"):
                entry["suggested_fixes"] = report["suggested_fixes"]
            accepted_variants.append(entry)

    # Collect de-duplicated warnings from accepted variants
    for av in accepted_variants:
        for w in av.get("warnings") or []:
            if w not in global_warnings:
                global_warnings.append(w)

    return {
        "total_candidate_variants": total,
        "accepted": len(accepted_variants),
        "rejected": len(rejected_variants),
        "warnings": global_warnings,
        "rejected_variants": rejected_variants,
        "accepted_variants": accepted_variants,
        "accepted_variant_objects": accepted_objects,
        "all_rejected": len(accepted_variants) == 0,
    }
