from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter(tags=["ingestion"])

class NhtsaProbeRequest(BaseModel):
    source: str = "complaints"
    years: list[int] = [2024, 2025]
    makes: Optional[list[str]] = None

class NhtsaProbeResponse(BaseModel):
    status: str
    message: str
    phase: str = "phase_0_stub"

@router.post("/nhtsa/probe", response_model=NhtsaProbeResponse)
def probe_nhtsa(data: NhtsaProbeRequest):
    return NhtsaProbeResponse(
        status="deferred",
        message="NHTSA bulk ingestion deferred to Phase 1. See docs/02_data_sources_and_ingestion.md for strategy.",
        phase="phase_0_stub"
    )
