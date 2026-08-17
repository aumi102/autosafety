from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.logging import setup_logging

settings = get_settings()
setup_logging()

app = FastAPI(
    title="AutoSafety GraphQL Copilot",
    description="Hybrid GraphRAG and Text-to-SQL analyst for vehicle safety data",
    version="0.1.0",
)


@app.exception_handler(RequestValidationError)
async def safe_request_validation_error(_request: Request, exc: RequestValidationError):
    """Return normal 422 details without reflecting rejected request values."""
    safe_errors = [
        {key: value for key, value in error.items() if key not in {"input", "ctx", "url"}}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": safe_errors})

@app.get("/")
def root():
    return {"message": "AutoSafety GraphQL Copilot", "version": "0.1.0", "phase": "phase_0"}

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/readyz")
def readyz():
    return {"status": "ready"}


app.include_router(v1_router, prefix="/v1")
