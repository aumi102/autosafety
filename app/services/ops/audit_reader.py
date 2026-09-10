"""Phase 10 read-only access to the Phase 9 execution audit trail.

`docs/06_api_contract.md` deferred `GET /v1/agent-runs/*` because "exposing one
requires authorization and redaction rules not yet defined". Phase 9 then
defined both — the fail-closed `X-Admin-Token` guard in
`docs/08_security_safety_guardrails.md` §"Maintenance/admin protection", and
the audit privacy boundary in §"Execution audit privacy" — so this module
implements the surface those rules now permit.

Two properties hold by construction:

* **Allowlist, not exclusion.** Rows are projected through the explicit field
  lists below. A column added to `AgentRun` or `ToolCall` later does not appear
  in an API response until someone adds it here on purpose. `input_json`,
  `output_json`, `intent`, and `warnings` are deliberately absent: the first two
  exist only as always-empty Phase 9 legacy columns, and the latter two copy
  strings out of the guarded result, which this module will not assume are free
  of user text.
* **Read-only.** Every session here is opened, queried, and closed. Nothing in
  this module writes, and the audit trail is never fed back into an answer.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from app.db.models.app import AgentRun, ToolCall
from app.db.session import get_sync_engine

logger = logging.getLogger(__name__)

# Bounds. A caller cannot ask for an unbounded scan.
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_LOOKBACK_DAYS = 90
MAX_TOOL_CALLS_PER_RUN = 50

# The only AgentRun columns that may ever leave the process.
AGENT_RUN_SAFE_FIELDS = (
    "run_id",
    "conversation_id",
    "turn_id",
    "phase",
    "surface",
    "status",
    "provider",
    "model",
    "synthesis_mode",
    "fallback_used",
    "abstained",
    "abstention_reason",
    "confidence_score",
    "confidence_level",
    "validation_outcome",
    "tool_call_count",
    "error_code",
    "latency_ms",
    "started_at",
    "completed_at",
)

# The only ToolCall columns that may ever leave the process.
TOOL_CALL_SAFE_FIELDS = (
    "call_id",
    "tool_name",
    "operation",
    "status",
    "success",
    "error_code",
    "evidence_item_count",
    "truncated",
    "latency_ms",
    "started_at",
    "completed_at",
)


@dataclass(frozen=True)
class AuditQuery:
    """Validated, bounded filter set. Built by the API layer, never by a client."""

    limit: int = DEFAULT_LIMIT
    offset: int = 0
    status: str | None = None
    provider: str | None = None
    surface: str | None = None
    conversation_id: uuid.UUID | None = None
    fallback_used: bool | None = None
    abstained: bool | None = None
    since_hours: int | None = None

    def bounded(self) -> AuditQuery:
        """Clamp every caller-supplied number into its permitted range."""
        max_hours = MAX_LOOKBACK_DAYS * 24
        return AuditQuery(
            limit=max(1, min(int(self.limit), MAX_LIMIT)),
            offset=max(0, int(self.offset)),
            status=self.status,
            provider=self.provider,
            surface=self.surface,
            conversation_id=self.conversation_id,
            fallback_used=self.fallback_used,
            abstained=self.abstained,
            since_hours=(
                None if self.since_hours is None else max(1, min(int(self.since_hours), max_hours))
            ),
        )


@dataclass(frozen=True)
class AgentRunView:
    """Safe projection of one audit run."""

    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dict(self.data)


@dataclass(frozen=True)
class AuditSummary:
    """Aggregate counters over a bounded window. Contains no per-run detail."""

    window_hours: int
    total_runs: int
    completed: int
    failed: int
    fallback_used: int
    abstained: int
    tool_calls: int
    tool_call_failures: int

    def to_dict(self) -> dict:
        return {
            "window_hours": self.window_hours,
            "total_runs": self.total_runs,
            "completed": self.completed,
            "failed": self.failed,
            "fallback_used": self.fallback_used,
            "abstained": self.abstained,
            "tool_calls": self.tool_calls,
            "tool_call_failures": self.tool_call_failures,
        }


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _project_run(run: AgentRun) -> dict:
    """Project an ORM row onto AGENT_RUN_SAFE_FIELDS. Nothing else is read."""
    return {
        "run_id": str(run.id),
        "conversation_id": str(run.conversation_id) if run.conversation_id else None,
        "turn_id": str(run.turn_id) if run.turn_id else None,
        "phase": run.phase,
        "surface": run.surface,
        "status": run.status,
        "provider": run.provider,
        "model": run.model,
        "synthesis_mode": run.synthesis_mode,
        "fallback_used": bool(run.fallback_used),
        "abstained": bool(run.abstained),
        "abstention_reason": run.abstention_reason,
        # Stored as score * 10000; presented as the original 0..1 value.
        "confidence_score": round((run.confidence_score or 0) / 10000, 4),
        "confidence_level": run.confidence_level,
        "validation_outcome": run.validation_outcome,
        "tool_call_count": int(run.tool_call_count or 0),
        "error_code": run.error_code,
        "latency_ms": run.latency_ms,
        "started_at": _isoformat(run.started_at),
        "completed_at": _isoformat(run.finished_at),
    }


def _project_tool_call(call: ToolCall) -> dict:
    """Project an ORM row onto TOOL_CALL_SAFE_FIELDS. input/output_json are never read."""
    return {
        "call_id": call.call_id,
        "tool_name": call.tool_name,
        "operation": call.operation,
        "status": call.status,
        "success": bool(call.success),
        "error_code": call.error_code,
        "evidence_item_count": int(call.evidence_item_count or 0),
        "truncated": bool(call.truncated),
        "latency_ms": call.latency_ms,
        "started_at": _isoformat(call.started_at),
        "completed_at": _isoformat(call.completed_at),
    }


class ExecutionAuditReader:
    """Application-owned, read-only view over `agent_runs` and `tool_calls`."""

    def __init__(self, session_factory: Callable[[], Session] | None = None):
        self._session_factory = session_factory or sessionmaker(
            bind=get_sync_engine(), expire_on_commit=False
        )

    def _filtered(self, query: AuditQuery) -> Select[tuple[AgentRun]]:
        statement = select(AgentRun)
        if query.status:
            statement = statement.where(AgentRun.status == query.status)
        if query.provider:
            statement = statement.where(AgentRun.provider == query.provider)
        if query.surface:
            statement = statement.where(AgentRun.surface == query.surface)
        if query.conversation_id:
            statement = statement.where(AgentRun.conversation_id == query.conversation_id)
        if query.fallback_used is not None:
            statement = statement.where(AgentRun.fallback_used == query.fallback_used)
        if query.abstained is not None:
            statement = statement.where(AgentRun.abstained == query.abstained)
        if query.since_hours is not None:
            cutoff = datetime.now(UTC) - timedelta(hours=query.since_hours)
            statement = statement.where(AgentRun.started_at >= cutoff)
        return statement

    def list_runs(self, query: AuditQuery | None = None) -> tuple[list[dict], int]:
        """Return one bounded page of safe run projections, plus the total match count."""
        bounded = (query or AuditQuery()).bounded()
        with self._session_factory() as session:
            statement = self._filtered(bounded)
            total = session.scalar(select(func.count()).select_from(statement.subquery()))
            rows = session.scalars(
                statement.order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
                .offset(bounded.offset)
                .limit(bounded.limit)
            ).all()
            return [_project_run(row) for row in rows], int(total or 0)

    def get_run(self, run_id: uuid.UUID) -> dict | None:
        """Return one run with its tool calls, or None when it does not exist."""
        with self._session_factory() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                return None
            calls = session.scalars(
                select(ToolCall)
                .where(ToolCall.agent_run_id == run_id)
                .order_by(ToolCall.started_at.asc(), ToolCall.id.asc())
                .limit(MAX_TOOL_CALLS_PER_RUN)
            ).all()
            projected = _project_run(run)
            projected["tool_calls"] = [_project_tool_call(call) for call in calls]
            return projected

    def summary(self, window_hours: int = 24) -> AuditSummary:
        """Aggregate counters over a bounded window."""
        hours = max(1, min(int(window_hours), MAX_LOOKBACK_DAYS * 24))
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        with self._session_factory() as session:
            recent = select(AgentRun).where(AgentRun.started_at >= cutoff).subquery()

            def _count(condition: ColumnElement[bool] | None = None) -> int:
                statement = select(func.count()).select_from(recent)
                if condition is not None:
                    statement = statement.where(condition)
                return int(session.scalar(statement) or 0)

            tool_calls = int(
                session.scalar(
                    select(func.count())
                    .select_from(ToolCall)
                    .join(recent, recent.c.id == ToolCall.agent_run_id)
                )
                or 0
            )
            tool_failures = int(
                session.scalar(
                    select(func.count())
                    .select_from(ToolCall)
                    .join(recent, recent.c.id == ToolCall.agent_run_id)
                    .where(ToolCall.success.is_(False))
                )
                or 0
            )
            return AuditSummary(
                window_hours=hours,
                total_runs=_count(),
                completed=_count(recent.c.status == "completed"),
                failed=_count(recent.c.status == "failed"),
                fallback_used=_count(recent.c.fallback_used.is_(True)),
                abstained=_count(recent.c.abstained.is_(True)),
                tool_calls=tool_calls,
                tool_call_failures=tool_failures,
            )


def build_audit_reader(
    session_factory: Callable[[], Session] | None = None,
) -> ExecutionAuditReader:
    """Application-owned construction. No request input selects the session."""
    return ExecutionAuditReader(session_factory=session_factory)
