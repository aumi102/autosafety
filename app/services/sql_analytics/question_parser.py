"""
Deterministic question parser for Phase 2.

Extracts vehicle entities and classifies intent using keyword/pattern matching.
No LLM calls. Simple, predictable, testable.

Supported intents:
- top_complaint_components_by_vehicle
- complaint_count_by_vehicle
- recalls_by_vehicle
- recall_count_by_vehicle
- vehicles_by_complaint_count
- unknown / clarification_needed
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Literal

from app.services.ingestion.normalization import normalize_make, normalize_model, models_equivalent


@dataclass
class VehicleEntity:
    """Extracted vehicle from question."""
    make: str          # Original as written in question
    model: str         # Original as written in question
    normalized_make: str
    normalized_model: str
    model_year: Optional[int] = None

    def __post_init__(self):
        self.normalized_make = normalize_make(self.make)
        self.normalized_model = normalize_model(self.model)


@dataclass
class ParsedQuestion:
    """Result of parsing a natural language question."""
    intent: Literal[
        "top_complaint_components_by_vehicle",
        "complaint_count_by_vehicle",
        "recalls_by_vehicle",
        "recall_count_by_vehicle",
        "vehicles_by_complaint_count",
        "unknown",
        "clarification_needed",
    ]
    vehicle: Optional[VehicleEntity] = None
    limit: Optional[int] = None
    component: Optional[str] = None  # Only for component-specific questions
    make_filter: Optional[str] = None  # For vehicles_by_complaint_count
    raw: str = ""
    confidence: float = 0.5  # 0.0-1.0


# Known make aliases
MAKE_ALIASES = {
    "ford": "Ford",
    "chevrolet": "Chevrolet",
    "chevy": "Chevrolet",
    "honda": "Honda",
    "toyota": "Toyota",
    "tesla": "Tesla",
    "nissan": "Nissan",
    "bmw": "BMW",
    "audi": "Audi",
    "hyundai": "Hyundai",
    "kia": "Kia",
}


# Known model normalization targets (raw → canonical model for display)
# The normalized_model is always stored uppercase/trimmed
KNOWN_F150_VARIANTS = {"f-150", "f150", "f 150", "f 150", "f-150"}
MODEL_CANONICAL = {
    "f-150": "F-150",
    "f150": "F-150",
    "f 150": "F-150",
    "f 150": "F-150",
    "f-150": "F-150",
    "accord": "Accord",
    "camry": "Camry",
}


def _extract_make(text: str) -> Optional[str]:
    """Extract and normalize make from text."""
    text_lower = text.lower()
    for alias, canonical in MAKE_ALIASES.items():
        pattern = r'\b' + re.escape(alias) + r'\b'
        if re.search(pattern, text_lower):
            return canonical
    return None


def _extract_model(text: str) -> Optional[str]:
    """Extract model name from text."""
    text_lower = text.lower()

    # F-150 variants
    for variant in KNOWN_F150_VARIANTS:
        pattern = r'\bf-? ?150\b'
        if re.search(pattern, text_lower):
            return "F-150"

    # Accord
    if re.search(r'\baccord\b', text_lower):
        return "Accord"

    # Camry
    if re.search(r'\bcamry\b', text_lower):
        return "Camry"

    # Tacoma
    if re.search(r'\btacoma\b', text_lower):
        return "Tacoma"

    # Silverado
    if re.search(r'\bsilverado\b', text_lower):
        return "Silverado"

    return None


def _extract_year(text: str) -> Optional[int]:
    """Extract model year (4-digit year 1990-2030)."""
    # Look for 4-digit year in context near vehicle info
    for match in re.finditer(r'\b(19[9]\d|20[0-2]\d|2030)\b', text):
        year = int(match.group(1))
        # Verify it's near vehicle keywords by looking around the year
        # Extend window both before year end and after
        end = min(len(text), match.end() + 20)
        context = text[max(0, end - 60):end].lower()
        if any(kw in context for kw in ['ford', 'honda', 'toyota', 'chevy', 'tacoma', 'camry', 'accord', 'f-150', 'f150', 'vehicle', 'model', 'year']):
            return year
    return None


def _extract_limit(text: str) -> Optional[int]:
    """Extract LIMIT hint from question."""
    # "top 5", "top 10", "list top 3"
    match = re.search(r'\btop\s+(\d+)\b', text, re.IGNORECASE)
    if match:
        limit = int(match.group(1))
        if 1 <= limit <= 100:
            return limit

    # "show me 20", "list 15"
    match = re.search(r'\b(list|show|return)\s+(\d+)\b', text, re.IGNORECASE)
    if match:
        limit = int(match.group(2))
        if 1 <= limit <= 100:
            return limit
    return None


def _classify_intent(text: str, vehicle: Optional[VehicleEntity]) -> str:
    """Classify question intent from keywords."""
    text_lower = text.lower()

    # Recall patterns (check before complaint to avoid overlap)
    if any(kw in text_lower for kw in ['recall', 'campaign']):
        if any(kw in text_lower for kw in ['how many', 'count', 'number of recall']):
            return "recall_count_by_vehicle"
        return "recalls_by_vehicle"

    # "Which vehicles have the most complaints" type — check first to avoid
    # "complaint component" matching "vehicles" containing "component"
    if any(kw in text_lower for kw in ['most complaint', 'complaint ranking', 'vehicles with most', 'most complaints', 'complaints overall', 'vehicles with highest']):
        return "vehicles_by_complaint_count"

    # Specific complaint patterns by vehicle
    if any(kw in text_lower for kw in ['top complaint', 'complaint component']):
        return "top_complaint_components_by_vehicle"

    # "by component" takes priority over generic complaint count
    if any(kw in text_lower for kw in ['by component', 'component breakdown', 'group by component', 'per component']):
        if vehicle:
            return "complaint_count_by_component_for_vehicle"
        return "vehicles_by_complaint_count"

    if any(kw in text_lower for kw in ['how many complaint', 'number of complaint', 'total complaint', 'complaint count', 'how many complaints']):
        if vehicle:
            return "complaint_count_by_vehicle"
        return "vehicles_by_complaint_count"

    if any(kw in text_lower for kw in ['complaint', 'recall']):
        if vehicle:
            if 'recall' in text_lower:
                return "recalls_by_vehicle"
            return "top_complaint_components_by_vehicle"
        return "vehicles_by_complaint_count"

    # Fallback
    return "unknown"


def parse_question(text: str) -> ParsedQuestion:
    """
    Parse a natural language question into structured intent + entities.

    Deterministic: same input always produces same output.
    No LLM calls.
    """
    raw = text.strip()
    raw_lower = raw.lower()

    # Extract entities
    make = _extract_make(raw)
    model = _extract_model(raw)
    year = _extract_year(raw)
    limit = _extract_limit(raw)

    # Build vehicle entity if we have make+model
    vehicle = None
    if make and model:
        vehicle = VehicleEntity(
            make=make,
            model=model,
            normalized_make=normalize_make(make),
            normalized_model=normalize_model(model),
            model_year=year,
        )
    elif make:
        # Make only - for vehicles_by_complaint_count
        vehicle = VehicleEntity(
            make=make,
            model="",
            normalized_make=normalize_make(make),
            normalized_model="",
        )

    # Classify intent
    intent = _classify_intent(raw, vehicle)

    # Validation: certain intents require vehicle with full make+model+year
    if intent in ("top_complaint_components_by_vehicle", "complaint_count_by_vehicle",
                   "recalls_by_vehicle", "recall_count_by_vehicle",
                   "complaint_count_by_component_for_vehicle"):
        if not vehicle or not vehicle.model_year:
            intent = "clarification_needed"
    elif intent == "vehicles_by_complaint_count":
        # vehicles_by_complaint_count works with make-only or no vehicle filter
        # Only require clarification if a vehicle partial was detected but year is missing
        if vehicle and vehicle.model and not vehicle.model_year:
            intent = "clarification_needed"
        # If no vehicle at all, that's OK for this intent

    # Confidence: higher when entity extraction succeeded
    confidence = 0.3
    if intent != "unknown" and intent != "clarification_needed":
        if vehicle and vehicle.model_year:
            confidence = 0.9
        elif vehicle:
            confidence = 0.6

    return ParsedQuestion(
        intent=intent,
        vehicle=vehicle,
        limit=limit,
        make_filter=normalize_make(make) if make else None,
        raw=raw,
        confidence=confidence,
    )


def build_clarification_response(question: ParsedQuestion) -> str:
    """Build a clarification prompt based on what's missing."""
    parts = []
    if not question.vehicle:
        parts.append("Please specify a vehicle make and model")
    elif not question.vehicle.model_year:
        parts.append("Please include the model year")
    return ". ".join(parts) if parts else "I need more information to answer this question."


def extract_vehicle_slots(text: str) -> dict:
    """Public deterministic vehicle-slot extraction.

    Thin, additive accessor over the existing Phase 2 extractors so later
    phases can reuse entity recognition without duplicating patterns or
    changing Phase 2 parsing semantics.
    """
    return {
        "make": _extract_make(text or ""),
        "model": _extract_model(text or ""),
        "model_year": _extract_year(text or ""),
    }
