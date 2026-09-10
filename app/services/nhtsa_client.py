"""
NHTSA API client for complaints and recalls.

Phase 1: lightweight API-based ingestion (not bulk flat files).
Bulk flat files deferred to Phase 1.5/2.

API docs: https://api.nhtsa.gov/
"""

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# NHTSA EIEARS API base
NHTSA_API_BASE = "https://api.nhtsa.gov"
NHTSA_COMPLAINTS_API = f"{NHTSA_API_BASE}/complaints/complaintsByVehicle"
NHTSA_RECALLS_API = f"{NHTSA_API_BASE}/recalls/recallsByVehicle"

# Timeout for HTTP requests (seconds)
REQUEST_TIMEOUT = 30.0


class NhtsaApiError(Exception):
    """Raised when NHTSA API returns an error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class NhtsaVehicle:
    """Vehicle descriptor used for API queries."""

    make: str
    model: str
    model_year: int


@dataclass
class NhtsaComplaintRecord:
    """Normalized complaint record from NHTSA API."""

    odi_number: str | None = None
    make: str | None = None
    model: str | None = None
    model_year: int | None = None
    component: str | None = None
    summary: str | None = None
    crash: str = "N"
    fire: str = "N"
    injury: str = "N"
    death: str = "N"
    received_date: str | None = None
    incident_date: str | None = None
    source_url: str | None = None
    raw_json: dict = field(default_factory=dict)


@dataclass
class NhtsaRecallRecord:
    """Normalized recall record from NHTSA API."""

    campaign_number: str | None = None
    make: str | None = None
    model: str | None = None
    model_year: int | None = None
    component: str | None = None
    summary: str | None = None
    consequence: str | None = None
    remedy: str | None = None
    notes: str | None = None
    units_affected: int | None = None
    report_received_date: str | None = None
    source_url: str | None = None
    raw_json: dict = field(default_factory=dict)


def _build_complaint_from_raw(raw: dict) -> NhtsaComplaintRecord:
    """Parse a raw NHTSA complaint API record into NhtsaComplaintRecord."""
    return NhtsaComplaintRecord(
        odi_number=raw.get("odiNumber"),
        make=raw.get("make"),
        model=raw.get("model"),
        model_year=_safe_int(raw.get("modelYear")),
        component=raw.get("component"),
        summary=raw.get("summary"),
        crash=raw.get("crash", "N"),
        fire=raw.get("fire", "N"),
        injury=raw.get("injury", "N"),
        death=raw.get("death", "N"),
        received_date=raw.get("dateComplaintFiled"),
        incident_date=raw.get("dateIncident"),
        source_url=raw.get("ODIURL"),
        raw_json=raw,
    )


def _build_recall_from_raw(raw: dict) -> NhtsaRecallRecord:
    """Parse a raw NHTSA recall API record into NhtsaRecallRecord."""
    return NhtsaRecallRecord(
        campaign_number=raw.get("NHTSACampaignNumber"),
        make=raw.get("make"),
        model=raw.get("model"),
        model_year=_safe_int(raw.get("modelYear")),
        component=raw.get("component"),
        summary=raw.get("summary"),
        consequence=raw.get("consequence"),
        remedy=raw.get("remedy"),
        notes=raw.get("notes"),
        units_affected=_safe_int(raw.get("numberVehiclesAffected")),
        report_received_date=raw.get("reportReceivedDate"),
        source_url=raw.get("remedyUrl"),
        raw_json=raw,
    )


def _safe_int(value: str | int | float | None) -> int | None:
    """Safely convert value to int, return None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def fetch_complaints_by_vehicle(vehicle: NhtsaVehicle) -> list[NhtsaComplaintRecord]:
    """
    Fetch complaints from NHTSA EIEARS API for a specific vehicle.

    Args:
        vehicle: NhtsaVehicle with make, model, model_year

    Returns:
        List of NhtsaComplaintRecord

    Raises:
        NhtsaApiError: on hard network/HTTP errors (not soft 400s with empty results)
    """
    # NHTSA API may reject special chars like dashes; strip them from model for lookup
    model_for_api = vehicle.model.replace("-", "").replace(" ", "")
    params = {
        "make": vehicle.make,
        "model": model_for_api,
        "modelYear": str(vehicle.model_year),
    }

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.get(NHTSA_COMPLAINTS_API, params=params)
    except httpx.TimeoutException:
        raise NhtsaApiError(
            f"Timeout fetching complaints for {vehicle.make} {vehicle.model} {vehicle.model_year}"
        )
    except httpx.RequestError as e:
        raise NhtsaApiError(f"Network error fetching complaints: {e}")

    # NHTSA sometimes returns 400 with a body saying "Results returned successfully" — treat as empty
    if response.status_code == 400:
        try:
            body = response.json()
            if body.get("message", "").startswith("Results returned successfully"):
                logger.info(
                    f"No complaints found for {vehicle.make} {vehicle.model} {vehicle.model_year} (API returned 400 with empty results)"
                )
                return []
        except Exception:
            pass
        raise NhtsaApiError(
            f"NHTSA complaints API returned status {response.status_code}: {response.text[:200]}",
            status_code=response.status_code,
        )

    if response.status_code != 200:
        raise NhtsaApiError(
            f"NHTSA API returned status {response.status_code}: {response.text[:200]}",
            status_code=response.status_code,
        )

    data = response.json()
    results = data.get("results", [])

    if results is None:
        logger.warning(
            f"NHTSA API returned null results for {vehicle.make} {vehicle.model} {vehicle.model_year}"
        )
        return []

    records = []
    for raw in results:
        try:
            records.append(_build_complaint_from_raw(raw))
        except Exception as e:
            logger.warning(f"Failed to parse complaint record: {e}")
            continue

    return records


def fetch_recalls_by_vehicle(vehicle: NhtsaVehicle) -> list[NhtsaRecallRecord]:
    """
    Fetch recalls from NHTSA API for a specific vehicle.

    Args:
        vehicle: NhtsaVehicle with make, model, model_year

    Returns:
        List of NhtsaRecallRecord

    Raises:
        NhtsaApiError: on HTTP errors or API-level errors
    """
    params = {
        "make": vehicle.make,
        "model": vehicle.model,
        "modelYear": str(vehicle.model_year),
    }

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.get(NHTSA_RECALLS_API, params=params)
    except httpx.TimeoutException:
        raise NhtsaApiError(
            f"Timeout fetching recalls for {vehicle.make} {vehicle.model} {vehicle.model_year}"
        )
    except httpx.RequestError as e:
        raise NhtsaApiError(f"Network error fetching recalls: {e}")

    if response.status_code != 200:
        raise NhtsaApiError(
            f"NHTSA API returned status {response.status_code}: {response.text[:200]}",
            status_code=response.status_code,
        )

    data = response.json()
    results = data.get("results", [])

    if results is None:
        logger.warning(
            f"NHTSA API returned null results for {vehicle.make} {vehicle.model} {vehicle.model_year}"
        )
        return []

    records = []
    for raw in results:
        try:
            records.append(_build_recall_from_raw(raw))
        except Exception as e:
            logger.warning(f"Failed to parse recall record: {e}")
            continue

    return records
