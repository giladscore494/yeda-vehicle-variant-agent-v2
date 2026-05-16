"""Field normalization helpers. Adapted from legacy core/normalize.py."""
from __future__ import annotations
import re
from core.schemas import BodyType, FuelType, Transmission, Market


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def normalize_make(make: str) -> str:
    return clean_text(make).title()


def normalize_model(model: str) -> str:
    return clean_text(model)


def _contains(value: str, *needles: str) -> bool:
    value = value.lower()
    return any(n in value for n in needles)


def normalize_body_type(value: str) -> BodyType:
    v = clean_text(value).lower()
    if _contains(v, "sport utility", "jeep", "suv"):
        return BodyType.suv
    if _contains(v, "crossover", "cuv"):
        return BodyType.crossover
    if _contains(v, "estate", "touring"):
        return BodyType.wagon
    if "saloon" in v:
        return BodyType.sedan
    if "people carrier" in v:
        return BodyType.mpv
    if "minivan" in v:
        return BodyType.minivan
    if "commercial" in v:
        return BodyType.commercial
    if "van" in v:
        return BodyType.van
    return BodyType(v) if v in BodyType._value2member_map_ else BodyType.unknown


def normalize_fuel_type(value: str) -> FuelType:
    v = clean_text(value).lower()
    # mild hybrid must be checked BEFORE petrol/diesel/hybrid because compound phrases like
    # "petrol mild hybrid" or "mild hybrid diesel" contain those substrings
    if _contains(v, "mild hybrid", "mild-hybrid", "mhev"):
        return FuelType.mild_hybrid
    if _contains(v, "gasoline", "benzine", "petrol"):
        return FuelType.petrol
    if "diesel" in v:
        return FuelType.diesel
    if _contains(v, "phev", "plug-in hybrid", "plug in hybrid"):
        return FuelType.plug_in_hybrid
    if _contains(v, "hev", "hybrid"):
        return FuelType.hybrid
    if _contains(v, "ev", "bev", "electric"):
        return FuelType.electric
    if _contains(v, "hydrogen", "fuel cell"):
        return FuelType.hydrogen
    if _contains(v, "lpg", "gas"):
        return FuelType.lpg
    return FuelType.unknown


def normalize_transmission(value: str) -> Transmission:
    v = clean_text(value).lower()
    if _contains(v, "dct", "dsg", "dual clutch"):
        return Transmission.dual_clutch
    if _contains(v, "e-cvt", "ecvt"):
        return Transmission.e_cvt
    if "cvt" in v:
        return Transmission.cvt
    if _contains(v, "single speed", "single-speed", "ev reduction"):
        return Transmission.single_speed_ev
    if _contains(v, "manual", " mt"):
        return Transmission.manual
    if _contains(v, "automatic", "auto", "a/t"):
        return Transmission.automatic
    return Transmission.unknown


def normalize_market(value: str) -> Market:
    v = clean_text(value).lower()
    if v in {"israel", "il"}:
        return Market.IL
    if v in {"europe", "eu"}:
        return Market.EU
    if v in {"usa", "us", "america"}:
        return Market.US
    if v in {"global", "worldwide"}:
        return Market.GLOBAL
    return Market.UNKNOWN


def normalize_drivetrain(value):
    if not value:
        return None
    v = clean_text(value).lower()
    # Branded AWD systems -> AWD
    if _contains(v, "xdrive", "quattro", "4motion", "4matic", "all4",
                 "h-trac", "htrac", "i-activ awd", "iactiv awd",
                 "symmetrical awd", "e-awd", "eawd", "e4wd"):
        return "AWD"
    # 4x4 -> 4WD
    if "4x4" in v:
        return "4WD"
    # Standard codes (preserve canonical casing)
    if "fwd" in v:
        return "FWD"
    if "rwd" in v:
        return "RWD"
    if "awd" in v:
        return "AWD"
    if "4wd" in v:
        return "4WD"
    return clean_text(value)
