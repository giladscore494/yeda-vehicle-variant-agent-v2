"""Prompts for Gemini discovery (adapted from legacy agent/prompts.py)."""
from __future__ import annotations
import json


def build_discovery_prompt(seed: dict, market: str = "IL") -> str:
    make = seed.get("make") if isinstance(seed, dict) else getattr(seed, "make", "")
    model = seed.get("model") if isinstance(seed, dict) else getattr(seed, "model", "")
    year_start = seed.get("year_start") if isinstance(seed, dict) else getattr(seed, "year_start", None)
    year_end = seed.get("year_end") if isinstance(seed, dict) else getattr(seed, "year_end", None)
    return f"""Return compact JSON only. JSON only. No prose. No markdown.
Research make={make}, model={model}, year_start={year_start}, year_end={year_end}, market={market}.
Return valid minified JSON only. No markdown. No prose. No trailing commas. No unfinished fields.
Do not include notes, explanations, or reason strings.
Return max 8 candidate_variants and max 5 sources.
Top-level keys: search_queries, sources, candidate_variants, no_variants_reason, conflicts, unresolved, unresolved_reason.
If no candidate variants can be found, return:
  candidate_variants: []
  no_variants_reason: one of:
    model_not_sold_in_market | no_reliable_sources_found | insufficient_grounded_data |
    duplicate_existing_variant_only | seed_out_of_scope | model_discontinued_before_market_period |
    source_conflict_unresolved | blocked_by_validation
Never return an empty candidate_variants list without no_variants_reason.
Candidate shape:
{{
  "candidate_index": 0,
  "make": "", "model": "",
  "year_start": 2009, "year_end": 2014,
  "generation": "", "body_type": "", "seats": 5,
  "engine": "", "transmission": "", "fuel_type": "",
  "drivetrain": "", "trim": "",
  "source_ids": [],
  "field_sources": {{
    "body_type": [], "seats": [], "engine": [], "transmission": [], "fuel_type": [],
    "drivetrain": [], "generation": [], "year_start": [], "year_end": [], "trim": []
  }}
}}
Source shape:
{{
  "source_id": "src_1", "url": "", "title": "",
  "source_type": "official_importer|israeli_specs|israeli_review|price_list|global_fallback|unknown",
  "market_scope": "IL|EU|GLOBAL|UNKNOWN", "fields_supported": []
}}
"""


def build_retry_discovery_prompt(seed, market: str = "IL") -> str:
    base = build_discovery_prompt(seed, market)
    retry = (
        "\nIMPORTANT — RETRY ATTEMPT: The previous attempt returned no usable variants. "
        "You MUST either return at least one grounded candidate_variant OR return "
        "candidate_variants: [] with an explicit no_variants_reason from the allowed enum.\n"
    )
    return base + retry
