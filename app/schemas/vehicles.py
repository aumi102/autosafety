from pydantic import BaseModel
from typing import Optional

class VehicleBase(BaseModel):
    make: str
    model: str
    model_year: int

class VehicleResponse(VehicleBase):
    id: str

    class Config:
        from_attributes = True

class VehicleSearchResponse(BaseModel):
    vehicles: list[VehicleResponse]
    total: int
    phase: str = "phase_0_stub"
