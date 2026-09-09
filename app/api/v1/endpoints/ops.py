"""Phase 10 operator diagnostics.

Two tiers, deliberately separated:

* `GET /v1/ops/readiness` is **public** and reports only booleans and coarse
  status words — enough for a load balancer to decide whether to route traffic,
  and nothing an attacker can use to map the deployment.
* `GET /v1/ops/diagnostics` is **admin-only**, behind the same fail-closed
  `X-Admin-Token` guard as every other maintenance route, and adds the
  operational facts an operator needs when something is wrong: the Alembic
  revision, whether the answer provider is configured, whether admin protection
  itself is active, and recent audit counters.

Neither route returns a connection string, credential, hostname, or raw
exception text, and neither mutates anything.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.security import admin_protection_enabled, verify_admin_token
from app.services.ops.probes import readiness

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ops"])


class DependencyView(BaseModel):
    name: str
    reachable: bool
    required: bool
    detail: str | None = None
    latency_ms: int | None = None


class ReadinessResponse(BaseModel):
    status: str
    ready: bool
    dependencies: list[DependencyView] = Field(default_factory=list)


class DiagnosticsResponse(BaseModel):
    ready: bool
    dependencies: list[DependencyView] = Field(default_factory=list)
    alembic_revision: str | None = None
    admin_protection_enabled: bool = False
    synthesis_provider: str | None = None
    synthesis_model: str | None = None
    external_provider_allowed: bool = False
    provider_credential_configured: bool = False
    deterministic_fallback_available: bool = True
    audit_runs_last_24h: int = 0
    audit_failures_last_24h: int = 0
    phase: str = "phase_10"


def _alembic_revision() -> str | None:
    """Current schema revision, or None when it cannot be read.

    Read through the application engine; a failure here is reported as `None`
    rather than raised, because diagnostics must still answer when the database
    is the thing that is broken.
    """
    try:
        from sqlalchemy import text

        from app.db.session import get_sync_engine

        with get_sync_engine().connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception as exc:
        logger.warning("Diagnostics could not read the Alembic revision: %s", exc)
        return None


@router.get("/readiness", response_model=ReadinessResponse)
def ops_readiness() -> ReadinessResponse:
    """Public readiness. Safe to expose to a load balancer."""
    report = readiness()
    return ReadinessResponse(**report.to_dict())


@router.get(
    "/diagnostics",
    response_model=DiagnosticsResponse,
    dependencies=[Depends(verify_admin_token)],
)
def ops_diagnostics() -> DiagnosticsResponse:
    """Admin-only operational detail. Reports configuration *shape*, never values."""
    settings = get_settings()
    report = readiness()

    credential = getattr(settings, "PHASE7_PROVIDER_API_KEY", None)
    if hasattr(credential, "get_secret_value"):
        credential = credential.get_secret_value()

    runs = failures = 0
    try:
        from app.services.ops.audit_reader import build_audit_reader

        summary = build_audit_reader().summary(window_hours=24)
        runs, failures = summary.total_runs, summary.failed
    except Exception as exc:
        logger.warning("Diagnostics could not read audit counters: %s", exc)

    return DiagnosticsResponse(
        ready=report.ready,
        dependencies=[DependencyView(**d.to_dict()) for d in report.dependencies],
        alembic_revision=_alembic_revision(),
        admin_protection_enabled=admin_protection_enabled(settings),
        synthesis_provider=getattr(settings, "PHASE7_SYNTHESIS_PROVIDER", None),
        synthesis_model=getattr(settings, "PHASE7_SYNTHESIS_MODEL", None) or None,
        external_provider_allowed=bool(
            getattr(settings, "PHASE7_SYNTHESIS_ALLOW_EXTERNAL", False)
        ),
        # Presence only. The value is never read into a response.
        provider_credential_configured=bool(credential),
        deterministic_fallback_available=True,
        audit_runs_last_24h=runs,
        audit_failures_last_24h=failures,
    )
