"""Discovery: call Gemini, parse candidate variants (adapted from legacy agent/discovery.py)."""
from __future__ import annotations

from agent.prompts import build_discovery_prompt, build_retry_discovery_prompt
from tools.gemini_client import (
    GeminiClient,
    parse_json_from_gemini_text,
    salvage_candidate_variants_from_raw,
)

NORMALIZED_KEYS = (
    "year_start", "year_end", "generation", "body_type", "seats",
    "engine", "transmission", "fuel_type", "drivetrain", "trim",
    "source_ids", "field_sources",
)

# New optional IL-marketed-name fields — passed through when present, not forced.
_IL_PASSTHROUGH_KEYS = (
    "global_model_name",
    "official_marketed_name_il",
    "local_brand_name_il",
    "alternate_names",
    "rebadged_as",
    "market_name_confidence",
)


def _inherit_variant_data(variant: dict, parent: dict) -> dict:
    merged = dict(variant or {})
    for key in ("generation", "year_start", "year_end", "source_ids"):
        if merged.get(key) in (None, "") and isinstance(parent, dict) and parent.get(key) not in (None, ""):
            merged[key] = parent[key]
    return merged


def _normalize_candidate(candidate: dict) -> dict:
    cand = dict(candidate or {})
    aliases = {"fuel": "fuel_type", "trans": "transmission", "gearbox": "transmission"}
    for src, dst in aliases.items():
        if dst not in cand and src in cand:
            cand[dst] = cand[src]
    for key in NORMALIZED_KEYS:
        cand.setdefault(key, [] if key == "source_ids" else None)
    if not isinstance(cand.get("field_sources"), dict):
        cand["field_sources"] = {}
    # Pass through IL marketed-name fields when present; do not force-set to None.
    for key in _IL_PASSTHROUGH_KEYS:
        if key in candidate:
            cand[key] = candidate[key]
    return cand


def extract_candidate_variants(parsed: dict):
    if not isinstance(parsed, dict):
        return [], "none", "discovery payload not a dict", 0

    raw = parsed.get("candidate_variants")
    if isinstance(raw, list) and raw:
        return [_normalize_candidate(c) for c in raw if isinstance(c, dict)], "candidate_variants", None, len(raw)

    alt_paths = (
        ("variants", parsed.get("variants")),
        ("vehicle_variants", parsed.get("vehicle_variants")),
    )
    for path, value in alt_paths:
        if isinstance(value, list) and value:
            return [_normalize_candidate(c) for c in value if isinstance(c, dict)], path, None, len(value)

    generations = parsed.get("generations")
    if isinstance(generations, list) and generations:
        flattened = []
        for gen in generations:
            if not isinstance(gen, dict):
                continue
            for var in gen.get("variants") or []:
                if isinstance(var, dict):
                    flattened.append(_normalize_candidate(_inherit_variant_data(var, gen)))
        if flattened:
            return flattened, "generations[].variants", None, len(flattened)

    return [], "none", "no candidate list found", 0


def run_discovery(seed: dict, market: str = "IL", model_override: str | None = None, retry_hint: bool = False) -> dict:
    prompt = build_retry_discovery_prompt(seed, market) if retry_hint else build_discovery_prompt(seed, market)
    res = GeminiClient().grounded_generate_json(prompt=prompt, model_override=model_override)
    if not isinstance(res, dict):
        return {"ok": False, "data": None, "error": f"non-dict Gemini response: {type(res).__name__}", "gemini_metadata": {}}

    payload = res.get("data") if isinstance(res.get("data"), (dict, list)) else None
    if payload is None:
        payload = res.get("parsed_json") if isinstance(res.get("parsed_json"), (dict, list)) else None
    parse_error = res.get("parse_error")
    if payload is None and res.get("raw_text"):
        payload, fallback_err = parse_json_from_gemini_text(res.get("raw_text"))
        parse_error = parse_error or fallback_err
    salvage_used = bool(res.get("json_salvage_used"))
    if payload is None and res.get("raw_text"):
        salvaged = salvage_candidate_variants_from_raw(res.get("raw_text"))
        if salvaged:
            payload = salvaged
            salvage_used = True

    if payload is None:
        return {
            "ok": False, "data": None,
            "error": "Failed to parse Gemini discovery JSON",
            "gemini_metadata": {
                "model": res.get("model"), "raw_text": res.get("raw_text"),
                "parse_error": parse_error, "json_salvage_used": salvage_used,
            },
        }

    if isinstance(payload, list):
        payload = {"candidate_variants": payload}

    extracted, extraction_path, extraction_warning, raw_count = extract_candidate_variants(payload)
    no_variants_reason = payload.get("no_variants_reason") if isinstance(payload, dict) else None
    if not extracted and not no_variants_reason:
        extraction_warning = extraction_warning or "empty_candidate_variants_without_no_variants_reason"

    data = {
        "search_queries": payload.get("search_queries") or [],
        "sources": payload.get("sources") or [],
        "candidate_variants": extracted,
        "no_variants_reason": no_variants_reason,
        "conflicts": payload.get("conflicts") or [],
        "unresolved": bool(payload.get("unresolved", False)),
        "unresolved_reason": payload.get("unresolved_reason"),
    }
    return {
        "ok": True, "data": data, "error": None,
        "gemini_metadata": {
            "model": res.get("model"),
            "candidate_extraction_path": extraction_path,
            "candidate_extraction_warning": extraction_warning,
            "raw_candidates_count_before_normalization": raw_count,
            "candidate_variants_count_after_extraction": len(extracted),
            "json_salvage_used": salvage_used,
        },
    }
