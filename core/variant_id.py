"""Deterministic variant_id generation. Adapted from legacy core/variant_id.py."""
from __future__ import annotations
import re
import unicodedata


def _slug(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def generate_variant_id(
    make,
    model,
    year_start,
    year_end,
    market,
    generation=None,
    engine=None,
    transmission=None,
    body_type=None,
    fuel_type=None,
) -> str:
    parts = [
        make,
        model,
        str(year_start),
        str(year_end),
        str(market),
        generation or "unknown_generation",
        engine or "unknown_engine",
        transmission or "unknown_transmission",
        body_type or "unknown_body",
        fuel_type or "unknown_fuel",
    ]
    return _slug("_".join(parts))
