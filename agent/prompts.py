"""Prompts for Gemini discovery (adapted from legacy agent/prompts.py)."""
from __future__ import annotations
import json


def build_discovery_prompt(seed: dict, market: str = "IL") -> str:
    make = seed.get("make") if isinstance(seed, dict) else getattr(seed, "make", "")
    model = seed.get("model") if isinstance(seed, dict) else getattr(seed, "model", "")
    year_start = seed.get("year_start") if isinstance(seed, dict) else getattr(seed, "year_start", None)
    year_end = seed.get("year_end") if isinstance(seed, dict) else getattr(seed, "year_end", None)
    return f"""Return compact JSON only. JSON only. No prose. No markdown. No trailing commas. No unfinished fields.

TASK: Research vehicle variants for make={make}, model={model}, year_start={year_start}, year_end={year_end}, market={market}.

==== ACCURACY OVER SPEED ====
Do not rush.
Prefer fewer accurate variants over many weak variants.
Take the time to verify every returned field before including it.
Do not complete the task by guessing.
Returning partial/uncertain/null is better than inventing data.
Accuracy is more important than quantity or completeness of output.

==== FIELD-BY-FIELD VERIFICATION ====
For every candidate variant, verify each of the following fields independently:
  make, model, trim/variant_name, generation/platform, year_start, year_end,
  body_type, engine, power output (if known), transmission, drivetrain,
  fuel_type, seating capacity (if known), market_scope, confidence_level,
  source_basis/evidence note.

Each field must be filled only if there is supporting evidence or strong cross-source consistency.
If a field is uncertain, use null and lower confidence_level.
Do not fill a field just to make the output look complete.

==== NO INVENTED PRECISION ====
Do not invent exact trims, engines, transmissions, drivetrains, year ranges, or body types just to make the output appear complete.

BAD behavior:
  - creating a full variant whose fields have no identified source basis
  - mixing global trims with local market trims without noting the distinction
  - using regional trim names as if confirmed for the target market
  - creating "complete" variants from weak or inferred evidence

GOOD behavior:
  - return fewer variants, each well-supported
  - mark market_scope as "global-reference-only" when target-market confirmation is absent
  - lower confidence_level when evidence is thin
  - include uncertainty explicitly in source_basis/evidence notes

==== SEED BOUNDARY RULE ====
Only return variants whose year range overlaps the requested seed year range ({year_start}-{year_end}).
Do NOT return variants that fall entirely outside the seed range.
If historical data exists outside the seed range, mention it only in source_basis notes; do not return it as a candidate variant.

==== MODEL-FAMILY CONSISTENCY ====
Only return variants that belong to the requested model family: {make} {model}.
Do not return variants from a neighboring model, unrelated model, different generation family, or similarly named model unless it is clearly the same official model family.
A well-known modern marketed form of the same model (e.g., an evolution carrying a closely related name) may be included only when it is evidently the same product line.
Do not mix variants across different official model families even if they share a platform.

==== BODY TYPE ACCURACY ====
Use official body type terminology when known. Do not collapse distinct official body styles into generic or misleading labels.
  - Gran Coupe → Gran Coupe (not Sedan)
  - Roadster → Roadster (not Convertible)
  - Convertible/Cabriolet → Convertible
  - Coupe → Coupe
  - Hatchback → Hatchback (not Sedan)
  - SUV/Crossover may be used only when the exact distinction is uncertain

==== TRIM VS PACKAGE SEPARATION ====
Do not mix trim level, body style, special packages, editions, and marketing packages into one trim string.

BAD:   "trim": "SportLine / Elegant / Carbon Edition / First Edition"
GOOD:  "trim": "SportLine"
       put edition/package names in source_basis notes: "Packages/editions noted: Elegant, Carbon Edition, First Edition. May vary by market."

Keep trim clean. If the schema has no separate packages field, put package/edition names in source_basis — not in trim.

==== ENGINE VERIFICATION ====
Engine must be specific when known.
GOOD: "4.4L V8 TwinPower Turbo, 530 hp" | "2.0L turbo petrol, 197 hp" | "2.0L e:HEV hybrid"
BAD:  "petrol engine" | "various engines" | "unknown engine" | "multiple options"

If multiple distinct engine variants existed and are supported by evidence, split them into separate candidate variants only when each is actually relevant to the seed/model/market.
Do not split variants to create more output — only split when the engine difference is meaningful and evidenced.

==== TRANSMISSION VERIFICATION ====
Transmission must be normalized and evidence-backed.
Allowed values (examples): "6-speed manual", "7-speed DCT", "8-speed automatic", "e-CVT", "CVT", "single-speed EV"
Do not guess transmission based only on brand/model assumptions.

==== DRIVETRAIN VERIFICATION ====
Normalize drivetrain to one of: FWD | RWD | AWD | 4WD
Do not invent drivetrain. If multiple drivetrains exist and are supported, split variants only when each is evidenced.

==== FUEL TYPE VERIFICATION ====
Normalize fuel_type to one of: Petrol | Diesel | Hybrid | Plug-in Hybrid | Electric | Mild Hybrid | LPG
Do not mix fuel types within a single variant.

==== ISRAELI MARKETED NAME (REQUIRED FIELD) ====
The target market is Israel (IL).

For every returned variant you MUST determine the exact name under which the
model was marketed in Israel — this is not optional.

Required fields per variant:
  global_model_name         — the manufacturer's global/internal model name
  official_marketed_name_il — the exact name used in Israeli marketing/sales
  local_brand_name_il       — Israeli brand name if different from global brand
  market_scope              — see allowed values below
  confidence_level          — see confidence rules
  source_basis              — must explicitly state whether IL name was confirmed

Rules:

1. Israeli marketed name takes priority over global/internal name.
   Examples of rebadging/renaming to check:
   - Daewoo Lacetti → Chevrolet Optra / Chevrolet Optra5 in Israel
   - Old GM/Daewoo/Chevrolet models rebadged by Israeli importer
   - DS/Citroën naming changes (e.g., DS 9 vs Citroën DS9)
   - Japanese/Korean models sold under different local names
   - Models where hatchback and sedan had different marketed names
   If the Israeli name differs, use it as the main marketed name:
     official_marketed_name_il = "Chevrolet Optra"
     global_model_name = "Daewoo Lacetti"
     market_scope = "IL-confirmed"  (only when supported by Israeli evidence)

2. Do NOT assume the global name was used in Israel.
   Actively verify whether: the brand name changed, the importer marketed it
   under a different name, the model name changed, body variants had different
   names, or the vehicle was a rebadge.

3. If the exact Israeli marketed name cannot be confirmed:
   - Do NOT invent one.
   - Set official_marketed_name_il = null
   - Set market_scope = "global-reference-only" or "IL-likely"
   - Set confidence_level = "medium" or "partial"
   - In source_basis write: "Israeli marketed name not confirmed; global model
     name used as reference only."

4. Do NOT set market_scope = "IL-confirmed" unless there is actual evidence for
   the Israeli marketed name.

Allowed market_scope values:
  IL-confirmed         — Israeli marketed name confirmed from Israeli sources
  IL-likely            — strong evidence but not fully confirmed
  global-reference-only — no IL-specific confirmation found
  uncertain            — insufficient data

5. Source priority for Israeli marketed name:
  1. Israeli importer/dealer official source
  2. Israeli price list or spec sheet
  3. Israeli car catalogue/spec site
  4. Israeli used-car listings (supporting evidence only)
  5. Global manufacturer/spec data (fallback only)

6. Output discipline: source_basis must explicitly say whether the Israeli
   marketed name was confirmed.

   GOOD:
   {{
     "global_model_name": "Daewoo Lacetti",
     "official_marketed_name_il": "Chevrolet Optra",
     "market_scope": "IL-confirmed",
     "source_basis": "Israeli market sources identify the Lacetti-based sedan
       as Chevrolet Optra; specs cross-checked with global Lacetti/Optra data."
   }}

   BAD:
   {{
     "model": "Daewoo Lacetti",
     "market_scope": "IL-confirmed",
     "source_basis": "Common model name."
   }}

7. official_marketed_name_il is the primary field.
   global_model_name, alternate_names, rebadged_as, previous_name are secondary
   support fields only.

==== ISRAELI MARKET PRIORITY ====
The primary target market is Israel (IL).

Evidence priority (highest to lowest):
  1. Israeli importer/official dealer data
  2. Israeli official price lists or spec sheets
  3. Israeli listings (as supporting evidence only)
  4. Manufacturer official global/European sources
  5. Reliable international spec databases (as secondary support)

If no Israeli market confirmation exists:
  - market_scope must be "global-reference-only" or "IL-likely"
  - confidence_level cannot be "high"
  - source_basis must explicitly state that IL confirmation was not found

==== EVIDENCE PER VARIANT ====
Every returned variant must include a source_basis/evidence note that explains:
  - what was verified for this variant
  - whether the source is IL-confirmed or global reference
  - whether each key field is exact or inferred

Example evidence note:
  "Official manufacturer documentation confirms body style and powertrain with specific engine and transmission. IL-market availability not confirmed; marked IL-likely/global-reference."

==== GENERATION FIELD — ALWAYS POPULATE ====
The `generation` field must never be null or an empty string.

Rules:
1. Prefer chassis codes when widely known (E46, W204, B8, Mk7).
2. If no chassis code exists, use ordinal: "1st Gen", "2nd Gen", etc.
3. The generation must match the variant's year range.
4. If a model spans multiple generations within the seed range, produce
   SEPARATE candidates per generation with their own year_start/year_end.
5. These boundaries are guidance for generation splitting. Do not contradict them unless reliable IL-market evidence proves a local overlap/transition year.

Known IL-market generation boundaries:
  BMW 3 Series:   E30(1982-91) E36(1992-98) E46(1998-2006) E90(2005-12) F30(2012-19) G20(2019-)
  BMW 5 Series:   E34(1988-95) E39(1995-03) E60(2003-10) F10(2010-17) G30(2017-)
  BMW 7 Series:   E32(1987-94) E38(1994-01) E65(2001-08) F01(2008-15) G11(2015-)
  BMW X3:         E83(2003-10) F25(2010-17) G01(2017-)
  BMW X5:         E53(1999-06) E70(2006-13) F15(2013-18) G05(2018-)
  Audi A3:        8L(1996-03) 8P(2003-12) 8V(2012-20) 8Y(2020-)
  Audi A4:        B5(1994-01) B6(2001-05) B7(2005-08) B8(2008-16) B9(2016-)
  Audi A6:        C4(1994-97) C5(1997-04) C6(2004-11) C7(2011-18) C8(2018-)
  Audi Q5:        8R(2008-17) FY(2017-)
  VW Golf:        Mk4(1997-03) Mk5(2003-08) Mk6(2008-13) Mk7(2013-20) Mk8(2020-)
  VW Passat:      B5(1996-05) B6(2005-10) B7(2010-14) B8(2014-)
  Mercedes C:     W202(1993-00) W203(2000-07) W204(2007-14) W205(2014-21) W206(2021-)
  Mercedes E:     W210(1995-02) W211(2002-09) W212(2009-16) W213(2016-)
  Mercedes GLC:   X253(2015-22) X254(2022-)
  Toyota Corolla: E11(1997-02) E12(2002-06) E15(2006-13) E16(2013-19) E21(2019-)
  Toyota Camry:   V40(2006-11) V50(2011-17) V70(2017-)
  Toyota RAV4:    XA20(2000-05) XA30(2005-12) XA40(2012-18) XA50(2018-)
  Honda Civic:    7G(2001-05) 8G(2006-11) 9G(2012-15) 10G(2016-21) 11G(2022-)
  Honda CR-V:     1G(1996-01) 2G(2001-06) 3G(2006-11) 4G(2012-16) 5G(2017-22) 6G(2023-)
  Hyundai i30:    FD(2007-12) GD(2012-17) PD(2017-)
  Hyundai Tucson: 1G(2004-09) 2G(2009-15) 3G(2015-21) 4G(2021-)
  Kia Sportage:   SL(2010-15) QL(2016-21) NQ5(2022-)
  Kia Ceed:       1G(2006-12) 2G(2012-18) 3G(2018-)
  Mazda 3:        BK(2003-09) BL(2009-13) BM(2014-18) BP(2019-)
  Mazda 6:        GG(2002-07) GH(2008-12) GJ(2013-)
  Mazda CX-5:     1G(2012-17) 2G(2017-)
  Subaru Forester: SG(2002-07) SH(2008-12) SJ(2012-18) SK(2018-)
  Subaru Outback:  3G(2003-09) 4G(2009-14) 5G(2014-19) 6G(2019-)
  Volvo XC60:     1G(2008-17) 2G(2017-)
  Volvo V60:      1G(2010-18) 2G(2018-)

Also verify the candidate output shape explicitly includes:
- `generation`
- `market_scope`
- `confidence_level`

Ensure every candidate variant you return includes all three of these fields.

Expected candidate shape rule:
- `generation`: non-empty string
- `market_scope`: one of `IL-confirmed`, `IL-likely`, `global-reference-only`, `unknown`
- `confidence_level`: one of `high`, `medium`, `low`

==== GENERATION FIELD SOURCES (field_sources.generation) ====
Populate `field_sources.generation` with source_ids when ANY source you consulted
directly supports the generation, platform, or year-boundary:

1. Chassis code or platform code found in a source (e.g. E46, W204, B8, MQB, F30).
2. Official generation name stated in a source (e.g. "4th Generation", "Mk7").
3. Explicit generation year range stated in a source.
4. Official platform or body-generation name stated in a source.
5. Source-backed year boundary that justifies splitting two candidates at a
   generation transition point.

If generation is derived ONLY from the known IL-market generation boundary table
above (not directly backed by any consulted source):
  - still return the generation value
  - leave field_sources.generation as [] (empty list)
  - do NOT claim high confidence solely because of the table
  - treat as inferred/partial, not verified

Do NOT leave field_sources.generation empty when the source text you consulted
clearly mentions the chassis code, platform code, generation name, or year boundary.

When a candidate is split at a generation boundary, include in field_sources.generation
the source_ids that justify that split when available.

==== CONFIDENCE LEVELS ====
high:
  - core fields complete (body_type, engine, transmission, drivetrain, fuel_type)
  - source-backed with identified evidence
  - internally consistent
  - preferably IL-confirmed

medium:
  - core technical fields present
  - official or reliable global sources exist
  - IL market not fully confirmed

partial:
  - useful but incomplete
  - some core fields unknown
  - should not be treated as final

reject / no variant:
  - too vague or unknown-only
  - year range entirely outside seed
  - model family mismatch
  - no useful technical fields can be filled

==== UNKNOWN HANDLING ====
If a variant cannot be produced with at least body_type + (engine or powertrain) + (transmission or drivetrain or fuel_type), do not fabricate it.
Instead return:
  - no_variants_reason (from the allowed enum below)
  - or a low-confidence note in source_basis explaining what is missing and why

==== OUTPUT DISCIPLINE ====
Return strict JSON only.
No markdown. No prose outside JSON. No commentary. No unsupported variant expansion.

==== VARIANT COUNT DISCIPLINE ====
Do not maximize the number of variants.
Return only distinct meaningful variants.

A "distinct variant" differs by one or more meaningful dimensions:
  - generation or platform
  - body type
  - powertrain or engine
  - drivetrain
  - major trim or variant name
  - fuel type

Do not create separate variants for cosmetic packages or minor editions unless they affect core technical specifications.

==== SELF-CHECK BEFORE RETURNING ====
Before returning JSON, silently perform this checklist for every candidate variant:
  1. Does it match the seed make ({make})?
  2. Does it match the seed model family ({model})?
  3. Does its year range overlap the seed ({year_start}-{year_end})?
  4. Is body_type official or safely normalized (not collapsed into a misleading label)?
  5. Is trim clean and not mixed with packages or editions?
  6. Are engine, transmission, drivetrain, and fuel_type each individually supported by evidence?
  7. Is market_scope honest (not overclaiming IL confirmation)?
  8. Is confidence_level not overstated?
  9. Is source_basis present and meaningful?
  10. Would this variant pass a deterministic quality gate?

If the answer to any question is NO:
  - fix the offending field
  - lower confidence_level
  - remove the variant entirely
  - or return no_variants_reason

==== OUTPUT FORMAT ====
Return max 8 candidate_variants and max 5 sources.
Top-level keys: search_queries, sources, candidate_variants, no_variants_reason,
no_variants_source_ids, no_variants_source_basis, no_variants_confidence,
no_variants_reason_detail, conflicts, unresolved, unresolved_reason.

If no candidate variants can be found, return:
  candidate_variants: []
  no_variants_reason: one of:
    model_not_sold_in_market | no_reliable_sources_found | insufficient_grounded_data |
    duplicate_existing_variant_only | seed_out_of_scope | model_discontinued_before_market_period |
    source_conflict_unresolved | blocked_by_validation
Never return an empty candidate_variants list without no_variants_reason.

IMPORTANT — when candidate_variants is empty, no_variants_reason alone is NOT enough.
You MUST also return the following evidence fields:
  no_variants_source_ids: []          — list of source_id strings that support the claim
  no_variants_source_basis: ""        — explanation of why no variants exist
  no_variants_confidence: "high|medium|low"
  no_variants_reason_detail: ""       — extra detail or evidence summary

Rules for no_variants_reason:
- Use model_not_sold_in_market ONLY if you have reliable IL-market evidence
  confirming non-availability. Vague absence of results is NOT enough.
  You must supply no_variants_source_ids (non-empty), no_variants_source_basis,
  and no_variants_confidence of medium or high.
- If you only failed to find reliable sources, use no_reliable_sources_found or
  insufficient_grounded_data — NOT model_not_sold_in_market.
- For body-style derivatives such as Variant, Touring, Avant, SW, Estate, Wagon,
  Break: do NOT claim model_not_sold_in_market unless you have direct IL-market
  evidence. If global evidence confirms the body style but IL evidence is missing,
  prefer returning a global-reference-only or IL-likely candidate instead of
  empty candidates.
- Never use duplicate_existing_variant_only unless you can identify the duplicate
  existing variant_id in your response.
- model_discontinued_before_market_period requires either source_ids or
  source_basis explaining why the model's end year precedes the seed year_start.
- NEVER use placeholder source IDs such as src_1, src_2, source_1, citation_1,
  ref_1, or bare numbers ("1", "2") in no_variants_source_ids.
  Every ID in no_variants_source_ids MUST reference an actual source object from
  the sources array returned in this response. If no real source supports
  non-availability, use no_reliable_sources_found or insufficient_grounded_data
  instead of model_not_sold_in_market.

Candidate shape:
{{
  "candidate_index": 0,
  "make": "", "model": "",
  "global_model_name": "",
  "official_marketed_name_il": "",
  "local_brand_name_il": null,
  "alternate_names": [],
  "rebadged_as": null,
  "year_start": 2009, "year_end": 2014,
  "generation": "", "body_type": "", "seats": 5,
  "engine": "", "transmission": "", "fuel_type": "",
  "drivetrain": "", "trim": "",
  "market_scope": "IL-confirmed|IL-likely|global-reference-only|uncertain",
  "market_name_confidence": "confirmed|likely|unknown",
  "confidence_level": "high|medium|partial",
  "source_basis": "",
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

==== EXAMPLE: CLEAN vs DIRTY VARIANT ====
BAD (do not return this):
{{
  "trim": "SportLine / Elegant / Carbon Edition",
  "body_type": "Sedan",
  "confidence_level": "high",
  "source_basis": ""
}}

GOOD (return this instead):
{{
  "trim": "SportLine",
  "body_type": "Gran Coupe",
  "market_scope": "global-reference-only",
  "confidence_level": "medium",
  "source_basis": "Official manufacturer source confirms body style (Gran Coupe) and powertrain; IL-specific package availability not confirmed. Editions noted in documentation: Elegant, Carbon Edition — listed here for reference, not as separate variants."
}}

This example illustrates the general rules: keep trim clean, use accurate body type terminology, be honest about market scope, never overstate confidence, always include evidence notes.
"""


def build_retry_discovery_prompt(seed, market: str = "IL") -> str:
    base = build_discovery_prompt(seed, market)
    retry = (
        "\nIMPORTANT — RETRY ATTEMPT: The previous attempt returned no usable variants. "
        "You MUST either return at least one grounded candidate_variant OR return "
        "candidate_variants: [] with an explicit no_variants_reason from the allowed enum.\n"
    )
    return base + retry
