from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.services.ops.probes import readiness

settings = get_settings()
setup_logging()

app = FastAPI(
    title="AutoSafety GraphQL Copilot",
    description="Hybrid GraphRAG and Text-to-SQL analyst for vehicle safety data",
    version="0.1.0",
)


@app.exception_handler(RequestValidationError)
async def safe_request_validation_error(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return normal 422 details without reflecting rejected request values."""
    safe_errors = [
        {key: value for key, value in error.items() if key not in {"input", "ctx", "url"}}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": safe_errors})


@app.get("/")
def root() -> dict:
    return {"message": "AutoSafety GraphQL Copilot", "version": "0.1.0", "phase": "phase_10"}


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


# response_model=None: the handler returns either a plain dict or a
# JSONResponse (503), and FastAPI cannot build a response model from that
# union. The shape is documented in docs/06_api_contract.md.
@app.get("/readyz", response_model=None)
def readyz() -> dict | JSONResponse:
    """Real readiness: probes required dependencies and 503s when one is down.

    This used to return a hardcoded {"status": "ready"}, so an orchestrator kept
    routing traffic to an instance whose database was unreachable. The response
    stays deliberately thin — booleans and coarse status words only, no
    connection detail — because this route is unauthenticated. Operators who
    need more use the admin-only GET /v1/ops/diagnostics.
    """
    report = readiness()
    payload = report.to_dict()
    if not report.ready:
        return JSONResponse(status_code=503, content=payload)
    return payload


app.include_router(v1_router, prefix="/v1")
