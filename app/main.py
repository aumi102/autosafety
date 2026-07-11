from fastapi import FastAPI
from app.core.config import get_settings
from app.core.logging import setup_logging

settings = get_settings()
setup_logging()

app = FastAPI(
    title="AutoSafety GraphQL Copilot",
    description="Hybrid GraphRAG and Text-to-SQL analyst for vehicle safety data",
    version="0.1.0",
)

@app.get("/")
def root():
    return {"message": "AutoSafety GraphQL Copilot", "version": "0.1.0", "phase": "phase_0"}

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/readyz")
def readyz():
    return {"status": "ready"}

from app.api.v1.router import router as v1_router
app.include_router(v1_router, prefix="/v1")
