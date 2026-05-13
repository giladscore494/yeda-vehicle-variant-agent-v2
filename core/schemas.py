"""Pydantic schemas for vehicle variants. Adapted from legacy core/schemas.py."""
from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, model_validator


class VerificationStatus(str, Enum):
    verified = "verified"
    partial = "partial"
    conflict = "conflict"
    unverified = "unverified"
    unknown = "unknown"


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class BodyType(str, Enum):
    sedan = "sedan"
    hatchback = "hatchback"
    suv = "suv"
    crossover = "crossover"
    wagon = "wagon"
    mpv = "mpv"
    pickup = "pickup"
    van = "van"
    coupe = "coupe"
    convertible = "convertible"
    minivan = "minivan"
    commercial = "commercial"
    unknown = "unknown"


class FuelType(str, Enum):
    petrol = "petrol"
    diesel = "diesel"
    hybrid = "hybrid"
    plug_in_hybrid = "plug_in_hybrid"
    electric = "electric"
    hydrogen = "hydrogen"
    lpg = "lpg"
    unknown = "unknown"


class Transmission(str, Enum):
    manual = "manual"
    automatic = "automatic"
    cvt = "cvt"
    e_cvt = "e_cvt"
    dual_clutch = "dual_clutch"
    single_speed_ev = "single_speed_ev"
    unknown = "unknown"


class Market(str, Enum):
    IL = "IL"
    EU = "EU"
    US = "US"
    GLOBAL = "GLOBAL"
    UNKNOWN = "UNKNOWN"


class VehicleModelSeed(BaseModel):
    make: str
    model_raw: str = ""
    model: str
    aliases: list[str] = Field(default_factory=list)
    year_start: int | None = None
    year_end: int | None = None
    source: str = "car_models_dict"


class VerifiedField(BaseModel):
    value: Any = None
    status: VerificationStatus = VerificationStatus.unknown
    confidence: Confidence = Confidence.low
    sources_count: int = 0
    source_ids: list[str] = Field(default_factory=list)
    used_in_compare: bool = False
    reason: str | None = None


class VehicleVariant(BaseModel):
    variant_id: str
    make: str
    model: str
    aliases: list[str] = Field(default_factory=list)
    year_start: int
    year_end: int
    market: Market
    generation: VerifiedField | str | None = None
    body_type: VerifiedField
    seats: VerifiedField
    engine: VerifiedField
    transmission: VerifiedField
    fuel_type: VerifiedField
    drivetrain: VerifiedField
    trim: VerifiedField | None = None
    doors: VerifiedField | None = None
    verification_status: VerificationStatus
    confidence: Confidence
    sources_count: int = 0
    created_at: str
    updated_at: str
    notes: list[str] = Field(default_factory=list)
    candidate_raw: dict = Field(default_factory=dict)
    identity_confidence: str = "unknown"

    @model_validator(mode="after")
    def _check_years(self):
        if self.year_start > self.year_end:
            raise ValueError("year_start must be <= year_end")
        return self
