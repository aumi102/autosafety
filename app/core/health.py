from fastapi import APIRouter

router = APIRouter()

@router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}

@router.get("/readyz")
def readyz() -> dict:
    return {"status": "ready"}
