from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional
import uuid

router = APIRouter(tags=["vehicles"])

class Vehicle(BaseModel):
    id: str
    make: str
    model: str
    model_year: int

class VehicleSearchResponse(BaseModel):
    vehicles: list[Vehicle]
    total: int
    phase: str = "phase_0_stub"

@router.get("/search", response_model=VehicleSearchResponse)
def search(
    make: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    model_year: Optional[int] = Query(None),
):
    return VehicleSearchResponse(
        vehicles=[],
        total=0,
        phase="phase_0_stub - no real data yet"
    )
