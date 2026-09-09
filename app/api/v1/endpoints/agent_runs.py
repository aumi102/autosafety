"""Phase 10 admin-only execution audit API.

`docs/06_api_contract.md` recorded `GET /v1/agent-runs/*` as "not implemented",
because exposing the Phase 9 audit trail "requires authorization and redaction
rules not yet defined". Phase 9 defined both, so these routes implement exactly
that surface and nothing wider.

Every route reuses `verify_admin_token` — the same fail-closed guard that
protects ingestion and rebuilds. No second authorization mechanism is
introduced: with no `ADMIN_API_TOKEN` configured the audit trail is
unreachable over HTTP, which is the correct default for an audit log.

Responses are built by `ExecutionAuditReader`, which projects rows through an
explicit safe-field allowlist. Prompts, provider responses, tool arguments,
raw SQL, raw Cypher, and credentials have no path to these responses.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.security import verify_admin_token
from app.services.ops.audit_reader import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MAX_LOOKBACK_DAYS,
    AuditQuery,
    build_audit_reader,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent-runs"], dependencies=[Depends(verify_admin_token)])


class ToolCallView(BaseModel):
    call_id: str | None = None
    tool_name: str | None = None
    operation: str | None = None
    status: str | None = None
    success: bool = False
    error_code: str | None = None
    evidence_item_count: int = 0
    truncated: bool = False
    latency_ms: int | None = None
    started_at: str | None = None
    completed_at: str | None = None


class AgentRunView(BaseModel):
    run_id: str
    conversation_id: str | None = None
    turn_id: str | None = None
    phase: str | None = None
    surface: str | None = None
    status: str | None = None
    provider: str | None = None
    model: str | None = None
    synthesis_mode: str | None = None
    fallback_used: bool = False
    abstained: bool = False
    abstention_reason: str | None = None
    confidence_score: float = 0.0
    confidence_level: str | None = None
    validation_outcome: str | None = None
    tool_call_count: int = 0
    error_code: str | None = None
    latency_ms: int | None = None
    started_at: str | None = None
    completed_at: str | None = None


class AgentRunDetail(AgentRunView):
    tool_calls: list[ToolCallView] = Field(default_factory=list)


class AgentRunListResponse(BaseModel):
    agent_runs: list[AgentRunView]
    total: int
    limit: int
    offset: int


class AuditSummaryResponse(BaseModel):
    window_hours: int
    total_runs: int
    completed: int
    failed: int
    fallback_used: int
    abstained: int
    tool_calls: int
    tool_call_failures: int


def _not_found(run_id: str) -> HTTPException:
    """Uniform 404. Reveals nothing about which ids exist."""
    return HTTPException(
        status_code=404,
        detail={
            "error": {
                "code": "AGENT_RUN_NOT_FOUND",
                "message": "No execution audit run matches that identifier.",
                "details": {"run_id": run_id},
            }
        },
    )


@router.get("", response_model=AgentRunListResponse)
def list_agent_runs(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, max_length=50),
    provider: str | None = Query(None, max_length=64),
    surface: str | None = Query(None, max_length=32),
    conversation_id: uuid.UUID | None = Query(None),
    fallback_used: bool | None = Query(None),
    abstained: bool | None = Query(None),
    since_hours: int | None = Query(None, ge=1, le=MAX_LOOKBACK_DAYS * 24),
) -> AgentRunListResponse:
    """List recent guarded-execution audit runs, newest first."""
    reader = build_audit_reader()
    runs, total = reader.list_runs(
        AuditQuery(
            limit=limit,
            offset=offset,
            status=status,
            provider=provider,
            surface=surface,
            conversation_id=conversation_id,
            fallback_used=fallback_used,
            abstained=abstained,
            since_hours=since_hours,
        )
    )
    return AgentRunListResponse(
        agent_runs=[AgentRunView(**run) for run in runs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/summary", response_model=AuditSummaryResponse)
def agent_run_summary(
    window_hours: int = Query(24, ge=1, le=MAX_LOOKBACK_DAYS * 24),
) -> AuditSummaryResponse:
    """Aggregate execution counters over a bounded window."""
    summary = build_audit_reader().summary(window_hours=window_hours)
    return AuditSummaryResponse(**summary.to_dict())


@router.get("/{run_id}", response_model=AgentRunDetail)
def get_agent_run(run_id: uuid.UUID) -> AgentRunDetail:
    """Return one audit run with its safe tool-call metadata."""
    run = build_audit_reader().get_run(run_id)
    if run is None:
        raise _not_found(str(run_id))
    tool_calls = [ToolCallView(**call) for call in run.pop("tool_calls", [])]
    return AgentRunDetail(**run, tool_calls=tool_calls)
