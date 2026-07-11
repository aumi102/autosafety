"""
NHTSA API client placeholder.

Phase 0: no real ingestion. Stubs only.
Phase 1: implement bulk download from NHTSA flat files.
        See docs/02_data_sources_and_ingestion.md for strategy.
"""

from typing import Optional

# NHTSA EIEARS / complaints API base URL (for probe/test use only)
NHTSA_API_BASE = "https://api.nhtsa.gov"
NHTSA_COMPLAINTS_API = f"{NHTSA_API_BASE}/complaints/complaintsByVehicle"

# NHTSA flat file download URLs (bulk data)
NHTSA_FLATS_BASE = "https://static.nhtsa.gov/odi/ffds"

COMPLAINT_FLATS = {
    "complaints_2024": f"{NHTSA_FLATS_BASE}/complaints/FLAT_COMPLAINTS_2024.zip",
    "complaints_2025": f"{NHTSA_FLATS_BASE}/complaints/FLAT_COMPLAINTS_2025.zip",
}

RECALL_FLATS = {
    "recalls_2024": f"{NHTSA_FLATS_BASE}/recalls/FLAT_RCL_2024.zip",
}

INVESTIGATION_FLATS = {
    "investigations_2024": f"{NHTSA_FLATS_BASE}/investigations/FLAT_INVESTIGATIONS_2024.zip",
}

MANUFACTURER_COMM_FLATS = {
    "mfgr_comm_2024": f"{NHTSA_FLATS_BASE}/manufacturer_communications/FLAT_MFR_COMM_2024.zip",
}

async def lookup_complaints(
    make: Optional[str] = None,
    model: Optional[str] = None,
    model_year: Optional[int] = None,
    component: Optional[str] = None,
) -> list[dict]:
    """
    Lookup complaints from NHTSA API.

    TODO Phase 1:
    - Implement actual API calls using httpx
    - Parse NHTSA complaint response format
    - Apply vehicle/component filtering
    - Return structured complaint records
    """
    raise NotImplementedError("Phase 1: implement NHTSA complaint lookup")

async def lookup_recalls(
    make: Optional[str] = None,
    model: Optional[str] = None,
    campaign_number: Optional[str] = None,
) -> list[dict]:
    """
    Lookup recalls from NHTSA.

    TODO Phase 1:
    - Use NHTSA recall API or flat files
    - Match recalls to vehicles
    """
    raise NotImplementedError("Phase 1: implement NHTSA recall lookup")

async def lookup_investigations(
    investigation_number: Optional[str] = None,
    component: Optional[str] = None,
) -> list[dict]:
    """TODO Phase 1: implement NHTSA investigation lookup."""
    raise NotImplementedError("Phase 1: implement NHTSA investigation lookup")

async def lookup_manufacturer_communications(
    communication_number: Optional[str] = None,
    component: Optional[str] = None,
) -> list[dict]:
    """TODO Phase 1: implement NHTSA manufacturer communication lookup."""
    raise NotImplementedError("Phase 1: implement NHTSA manufacturer communication lookup")
