"""Clean runner: turn raw discovery candidates into variant dicts.

Minimal replacement for legacy agent/runner.py + verifier. There is no caching,
no external state, no rerun/repair logic. The only responsibility here is to:

1. Parse a seed_id into (make, model, year_start, year_end, market).
2. Call discovery.run_discovery to fetch candidates from Gemini.
3. Convert each candidate to a canonical variant dict with stable variant_id.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent.discovery import run_discovery
from core.variant_id import generate_variant_id

FIELD_NAMES = ("body_type", "seats", "engine", "transmission", "fuel_type", "drivetrain", "trim", "doors")

# Ordered from strongest to weakest market evidence.
_SCOPE_RANK = {"il-confirmed": 3, "il-likely": 2, "global-reference-only": 1, "uncertain": 0}
_LEVEL_RANK = {"high": 3, "medium": 2, "partial": 1, "low": 1}


def _derive_identity_confidence(market_scope: str, confidence_level: str,
                                 sources_count: int) -> str:
    """Map candidate market_scope + confidence_level + source support to identity_confidence.

    high   — IL-confirmed scope AND high confidence AND at least 1 source
    medium — IL-confirmed/medium OR IL-likely/high, with at least 1 source
    low    — global-reference-only, uncertain, partial confidence, or no sources
    """
    scope = _SCOPE_RANK.get((market_scope or "").lower().strip(), 0)
    level = _LEVEL_RANK.get((confidence_level or "").lower().strip(), 0)
    sc = int(sources_count or 0)

    if scope >= 3 and level >= 3 and sc >= 1:
        return "high"
    if scope >= 2 and level >= 2 and sc >= 1:
        return "medium"
    return "low"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_seed_id(seed_id: str) -> dict:
    """Parse a canonical seed id like make__model__year_start__year_end__market."""
    parts = (seed_id or "").split("__")
    if len(parts) < 5:
        raise ValueError(f"invalid seed_id: {seed_id!r}")
    make, model, ys, ye, market = parts[0], parts[1], parts[2], parts[3], parts[4]
    return {
        "seed_id": seed_id,
        "make": make.replace("_", " ").title(),
        "model": model.replace("_", " "),
        "year_start": int(ys),
        "year_end": int(ye),
        "market": market.upper(),
    }


def _field(value: Any, sources_count: int = 0, source_ids: list | None = None,
           status: str | None = None) -> dict:
    has_value = value not in (None, "")
    sc = int(sources_count or 0)
    if status:
        st = status
    elif sc >= 2:
        st = "verified"
    elif sc == 1:
        st = "partial"
    elif has_value:
        st = "unverified"
    else:
        st = "unknown"
    conf = "high" if (st == "verified" and sc >= 2) else ("medium" if st == "partial" else "low")
    return {
        "value": value,
        "status": st,
        "confidence": conf,
        "sources_count": sc,
        "source_ids": list(source_ids or []),
        "used_in_compare": st in {"verified", "partial"} and sc >= 1,
        "reason": f"{st} from {sc} source(s)",
    }


def _candidate_to_variant(candidate: dict, seed: dict) -> dict:
    cand = dict(candidate or {})
    make = cand.get("make") or seed["make"]
    model = cand.get("model") or seed["model"]
    ys = int(cand.get("year_start") or seed["year_start"])
    ye = int(cand.get("year_end") or seed["year_end"])
    market = (cand.get("market") or seed["market"]).upper()
    generation = cand.get("generation")
    engine = cand.get("engine")
    transmission = cand.get("transmission")
    body_type = cand.get("body_type")
    fuel_type = cand.get("fuel_type")
    drivetrain = cand.get("drivetrain")
    seats = cand.get("seats")
    trim = cand.get("trim")
    market_scope = str(cand.get("market_scope") or "").strip()
    confidence_level = str(cand.get("confidence_level") or "").strip()

    variant_id = generate_variant_id(
        make, model, ys, ye, market,
        generation=generation, engine=engine, transmission=transmission,
        body_type=body_type, fuel_type=fuel_type,
    )

    field_sources = cand.get("field_sources") if isinstance(cand.get("field_sources"), dict) else {}

    def fld(name, value):
        ids = field_sources.get(name) or []
        ids = ids if isinstance(ids, list) else []
        return _field(value, sources_count=len(ids), source_ids=ids)

    now = _now()
    gen_ids = field_sources.get("generation") or []
    gen_ids = gen_ids if isinstance(gen_ids, list) else []
    total_sources = sum(len(field_sources.get(n) or []) for n in FIELD_NAMES)
    variant: dict = {
        "variant_id": variant_id,
        "make": make,
        "model": model,
        "aliases": [],
        "year_start": {"value": ys, "used_in_compare": False},
        "year_end": {"value": ye, "used_in_compare": False},
        "market": market,
        "generation": _field(generation, sources_count=len(gen_ids), source_ids=gen_ids),
        "body_type": fld("body_type", body_type),
        "seats": fld("seats", seats),
        "engine": fld("engine", engine),
        "transmission": fld("transmission", transmission),
        "fuel_type": fld("fuel_type", fuel_type),
        "drivetrain": fld("drivetrain", drivetrain),
        "trim": fld("trim", trim),
        "doors": None,
        "verification_status": "partial" if body_type or engine or fuel_type else "unresolved",
        "confidence": "medium" if body_type or engine or fuel_type else "low",
        "sources_count": total_sources,
        "market_scope": market_scope or None,
        "confidence_level": confidence_level or None,
        "created_at": now,
        "updated_at": now,
        "notes": [],
        "candidate_raw": cand,
        "identity_confidence": _derive_identity_confidence(
            market_scope, confidence_level, total_sources
        ),
    }
    return variant


def build_variants_from_discovery(seed: dict, discovery_data: dict) -> list[dict]:
    """Convert candidate_variants from discovery into canonical variant dicts."""
    candidates = (discovery_data or {}).get("candidate_variants") or []
    return [_candidate_to_variant(c, seed) for c in candidates if isinstance(c, dict)]


def run_seed(seed_id: str, *, retry_hint: bool = False, discovery_fn=None) -> dict:
    """Run discovery + build variants for a single seed.

    Returns a result dict with keys: ok, seed, variants, no_variants_reason,
    discovery (raw), error.
    """
    seed = parse_seed_id(seed_id)
    fn = discovery_fn or run_discovery
    discovery = fn(seed, market=seed["market"], retry_hint=retry_hint)
    if not discovery.get("ok"):
        return {
            "ok": False,
            "seed": seed,
            "variants": [],
            "no_variants_reason": None,
            "discovery": discovery,
            "error": discovery.get("error"),
        }
    data = discovery.get("data") or {}
    variants = build_variants_from_discovery(seed, data)
    return {
        "ok": True,
        "seed": seed,
        "variants": variants,
        "no_variants_reason": data.get("no_variants_reason"),
        "discovery": discovery,
        "error": None,
    }
