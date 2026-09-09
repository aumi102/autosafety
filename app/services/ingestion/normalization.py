"""
Normalization utilities for NHTSA data.

Keeps original strings for traceability, creates normalized versions for analytics.
"""

import re


def normalize_component_name(name: str | None) -> str | None:
    """
    Normalize NHTSA component names to canonical form.

    Phase 1 uses simple text normalization.
    Phase 2 may use LLM-based classification.
    """
    if not name:
        return None

    normalized = name.strip().upper()
    normalized = re.sub(r"\s+", " ", normalized)

    # Map common NHTSA component patterns to canonical names
    component_map = {
        "SERVICE BRAKES": "SERVICE BRAKES",
        "ELECTRICAL SYSTEM": "ELECTRICAL SYSTEM",
        "STEERING": "STEERING",
        "AIR BAGS": "AIR BAGS",
        "ENGINE": "ENGINE",
        "POWER TRAIN": "POWER TRAIN",
        "STRUCTURE": "STRUCTURE",
        "FORWARD COLLISION AVOIDANCE": "FORWARD COLLISION AVOIDANCE",
        "VISIBILITY": "VISIBILITY",
        "TIRES": "TIRES",
        "FUEL SYSTEM": "FUEL SYSTEM",
        "SUSPENSION": "SUSPENSION",
        "EXTERIOR LIGHTING": "EXTERIOR LIGHTING",
        "HYBRID PROPULSION SYSTEM": "HYBRID PROPULSION SYSTEM",
        "FUEL/PROPULSION SYSTEM": "FUEL/PROPULSION SYSTEM",
        "LANE DEPARTURE": "LANE DEPARTURE",
    }

    for key, canonical in component_map.items():
        if key in normalized:
            return canonical

    return normalized if normalized else None


def normalize_make(make: str | None) -> str:
    """Normalize vehicle make name."""
    if not make:
        return ""
    return re.sub(r"\s+", " ", make.strip().upper())


def normalize_model(model: str | None) -> str:
    """Normalize vehicle model name."""
    if not model:
        return ""
    return re.sub(r"\s+", " ", model.strip().upper())


def models_equivalent(a: str, b: str) -> bool:
    """
    Check if two model names refer to the same vehicle model.

    Handles F-150 / F150 / F 150 equivalence by stripping dashes and spaces.
    Case-insensitive comparison.
    """
    norm_a = normalize_model(a).replace("-", "").replace(" ", "")
    norm_b = normalize_model(b).replace("-", "").replace(" ", "")
    return norm_a == norm_b
