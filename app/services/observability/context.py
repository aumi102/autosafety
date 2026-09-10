"""
Phase 9 execution audit context.

Carries the active audit run through one guarded execution without threading a
run id through the Phase 7 orchestrator, tool registry, and adapters. That
matters: Phase 9 is an observability phase, and rewiring stable Phase 7
internals to pass an audit id would be a far larger and riskier change than
the audit itself.

A `ContextVar` is request-scoped and async-safe, so concurrent requests never
share an audit run, and a run never leaks between them.

Audit is strictly a side channel. Nothing here influences retrieval, tool
selection, synthesis, validation, confidence, or abstention, and no audit value
is ever read back into a later answer.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import UUID

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.observability.recorder import ExecutionAuditRecorder

logger = logging.getLogger(__name__)


@dataclass
class AuditRunContext:
    """The audit run currently in scope, if any."""

    run_id: UUID
    recorder: ExecutionAuditRecorder
    surface: str
    tool_calls_recorded: int = 0
    # Application-owned call ids already recorded for this run. Guarantees a
    # replayed or retried execution cannot create duplicate audit rows.
    seen_call_ids: set[str] = field(default_factory=set)


@runtime_checkable
class AuditRecorder(Protocol):
    """The audit surface a caller may use.

    Declared as a Protocol so callers depend on the shape rather than on
    `ExecutionAuditRecorder` itself, which keeps the audit side channel
    substitutable in tests without weakening the call sites to `object`.
    """

    @property
    def enabled(self) -> bool: ...

    def start_run(
        self,
        *,
        surface: str,
        provider: str | None = None,
        model: str | None = None,
        conversation_id: UUID | None = None,
    ) -> AuditRunContext | None: ...

    def finish_run(
        self,
        run_id: UUID,
        guarded: Any,
        *,
        latency_ms: int,
        intent: str | None = None,
    ) -> None: ...

    def fail_run(self, run_id: UUID, error_code: str, *, latency_ms: int = 0) -> None: ...

    def link_conversation_turn(
        self,
        run_id: UUID,
        *,
        conversation_id: UUID | None = None,
        turn_id: UUID | None = None,
    ) -> None: ...


_current_run: ContextVar[AuditRunContext | None] = ContextVar("autosafety_audit_run", default=None)


def current_run() -> AuditRunContext | None:
    """Return the audit run in scope, or None when auditing is not active."""
    return _current_run.get()


def current_run_id() -> UUID | None:
    """Return the id of the audit run in scope, or None."""
    context = _current_run.get()
    return context.run_id if context else None


@contextmanager
def audit_run(context: AuditRunContext | None) -> Iterator[AuditRunContext | None]:
    """Bind an audit run for the duration of one guarded execution."""
    if context is None:
        yield None
        return
    token = _current_run.set(context)
    try:
        yield context
    finally:
        _current_run.reset(token)


def notify_tool_call(request: Any, result: Any) -> None:
    """Record one tool execution against the active audit run.

    Called from `ToolRegistry.execute`. A no-op when no run is in scope, so
    offline tests and unaudited callers are entirely unaffected.

    Audit failure never fails the user's request: the guarded answer is the
    product, and losing an audit row must not lose an answer. Failures are
    logged and execution continues.
    """
    context = _current_run.get()
    if context is None:
        return
    try:
        call_id = getattr(request, "call_id", "") or ""
        if call_id and call_id in context.seen_call_ids:
            return
        context.recorder.record_tool_call(context.run_id, request, result)
        if call_id:
            context.seen_call_ids.add(call_id)
        context.tool_calls_recorded += 1
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Execution audit: tool call not recorded (%s)", type(exc).__name__)
