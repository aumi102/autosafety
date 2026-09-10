from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str = "0.1.0"


@router.get("/", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")
