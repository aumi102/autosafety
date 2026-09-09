"""
Phase 9 execution audit recorder.

Application-owned persistence of guarded-execution metadata into the
`agent_runs` and `tool_calls` tables that have existed since migration
`2025_01_01_0001` but were never populated.

This is **audit and observability data, not agent memory.** Nothing written
here is ever read back into a later answer, and the LLM never writes these rows
— only the application does, after the fact.

Persistence policy (fail-open, deliberately):

    An audit write failure logs and continues. The guarded answer is the
    product; losing an audit row must not lose an answer. Nothing in
    `docs/08_security_safety_guardrails.md` requires fail-closed audit, and its
    "Observability requirements" section is about what to store, not about
    refusing to answer. This choice is documented in `docs/phase9_design.md`.

Never persisted: prompts, provider raw requests/responses, API keys, provider
credentials, connection strings, raw SQL, raw Cypher, unrestricted tool
arguments, evidence text, or tracebacks.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.db.models.app import AgentRun, ToolCall
from app.services.observability.context import AuditRunContext

logger = logging.getLogger(__name__)

_SCORE_SCALE = 10000

# Tool argument keys safe to retain as audit metadata. Everything else is
# dropped: a tool argument is untrusted input and is never persisted wholesale.
_SAFE_OPERATION_KEY = "operation"
MAX_OPERATION_CHARS = 64
MAX_ERROR_CODE_CHARS = 64
MAX_PROVIDER_CHARS = 64
MAX_MODEL_CHARS = 128


def _score_to_int(score: float) -> int:
    try:
        bounded = max(0.0, min(1.0, float(score or 0.0)))
    except (TypeError, ValueError):
        return 0
    return int(round(bounded * _SCORE_SCALE))


def _bounded(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _evidence_item_count(result: Any) -> int:
    """Count evidence items in a tool result without retaining their content."""
    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        return 0
    total = 0
    for value in data.values():
        if isinstance(value, list):
            total += len(value)
    return total


class ExecutionAuditRecorder:
    """Writes bounded, non-sensitive execution audit rows."""

    def __init__(self, session_factory: Callable[[], Session], *, enabled: bool = True):
        self._session_factory = session_factory
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled and self._session_factory is not None

    # ------------------------------------------------------------------ runs

    def start_run(
        self,
        *,
        surface: str,
        provider: str | None = None,
        model: str | None = None,
        conversation_id: uuid.UUID | None = None,
    ) -> AuditRunContext | None:
        """Open an audit run. Returns None when auditing is off or unavailable."""
        if not self.enabled:
            return None
        run_id = uuid.uuid4()
        now = datetime.now(UTC)
        try:
            with self._unit_of_work() as session:
                session.add(
                    AgentRun(
                        id=run_id,
                        message_id=None,
                        intent=None,
                        status="running",
                        warnings=[],
                        created_at=now,
                        started_at=now,
                        phase="phase_9",
                        surface=_bounded(surface, 32),
                        provider=_bounded(provider, MAX_PROVIDER_CHARS),
                        model=_bounded(model, MAX_MODEL_CHARS),
                        conversation_id=conversation_id,
                    )
                )
        except Exception as exc:
            logger.warning("Execution audit: run not started (%s)", type(exc).__name__)
            return None
        return AuditRunContext(run_id=run_id, recorder=self, surface=surface)

    def finish_run(
        self,
        run_id: uuid.UUID,
        guarded: Any,
        *,
        latency_ms: int,
        intent: str | None = None,
    ) -> None:
        """Finalize an audit run from the guarded answer contract."""
        if not self.enabled:
            return
        try:
            confidence = getattr(guarded, "confidence", None)
            validation = getattr(guarded, "validation", None)
            trace = getattr(guarded, "trace", None)
            retrieval = getattr(guarded, "retrieval_summary", None)

            with self._unit_of_work() as session:
                run = session.get(AgentRun, run_id)
                if run is None:
                    return
                run.status = "completed"
                run.finished_at = datetime.now(UTC)
                run.latency_ms = max(0, int(latency_ms))
                run.intent = _bounded(intent, 64)
                run.synthesis_mode = _bounded(getattr(guarded, "synthesis_mode", None), 20)
                run.provider = (
                    _bounded(getattr(guarded, "provider", None), MAX_PROVIDER_CHARS)
                    or run.provider
                )
                run.abstained = bool(getattr(guarded, "abstained", False))
                run.abstention_reason = _bounded(
                    getattr(guarded, "abstention_reason", None), 128
                )
                run.warnings = list(getattr(guarded, "warnings", []) or [])[:20]
                if confidence is not None:
                    run.confidence_score = _score_to_int(getattr(confidence, "score", 0.0))
                    run.confidence_level = _bounded(getattr(confidence, "level", None), 10)
                if trace is not None:
                    run.fallback_used = bool(getattr(trace, "fallback_used", False))
                    run.validation_outcome = _bounded(
                        getattr(trace, "validation_outcome", None), 32
                    )
                elif validation is not None:
                    run.validation_outcome = (
                        "valid" if getattr(validation, "valid", False) else "invalid"
                    )
                if retrieval is not None:
                    run.tool_call_count = int(getattr(retrieval, "tool_calls_made", 0) or 0)
        except Exception as exc:
            logger.warning("Execution audit: run not finalized (%s)", type(exc).__name__)

    def fail_run(self, run_id: uuid.UUID, error_code: str, *, latency_ms: int = 0) -> None:
        """Mark an audit run failed. `error_code` is a stable code, never a traceback."""
        if not self.enabled:
            return
        try:
            with self._unit_of_work() as session:
                run = session.get(AgentRun, run_id)
                if run is None:
                    return
                run.status = "failed"
                run.finished_at = datetime.now(UTC)
                run.latency_ms = max(0, int(latency_ms))
                run.error_code = _bounded(error_code, MAX_ERROR_CODE_CHARS)
        except Exception as exc:
            logger.warning("Execution audit: run failure not recorded (%s)", type(exc).__name__)

    def link_conversation_turn(
        self,
        run_id: uuid.UUID,
        *,
        conversation_id: uuid.UUID | None = None,
        turn_id: uuid.UUID | None = None,
    ) -> None:
        """Attach a persisted conversation/turn to an already-recorded run."""
        if not self.enabled:
            return
        try:
            with self._unit_of_work() as session:
                run = session.get(AgentRun, run_id)
                if run is None:
                    return
                if conversation_id is not None:
                    run.conversation_id = conversation_id
                if turn_id is not None:
                    run.turn_id = turn_id
        except Exception as exc:
            logger.warning("Execution audit: run not linked (%s)", type(exc).__name__)

    # ------------------------------------------------------------- tool calls

    def record_tool_call(self, run_id: uuid.UUID, request: Any, result: Any) -> None:
        """Record one tool execution as bounded, non-sensitive metadata.

        Tool arguments are **not** persisted. Only the allowlisted `operation`
        name is retained, so no question text, filter value, or model-supplied
        argument reaches the audit trail.
        """
        if not self.enabled:
            return
        arguments = getattr(request, "arguments", None)
        operation = None
        if isinstance(arguments, dict):
            operation = _bounded(arguments.get(_SAFE_OPERATION_KEY), MAX_OPERATION_CHARS)

        success = bool(getattr(result, "success", False))
        now = datetime.now(UTC)
        duration = int(getattr(result, "duration_ms", 0) or 0)
        try:
            with self._unit_of_work() as session:
                session.add(
                    ToolCall(
                        id=uuid.uuid4(),
                        agent_run_id=run_id,
                        tool_name=_bounded(getattr(result, "tool_name", None), 64)
                        or _bounded(getattr(request, "tool_name", None), 64)
                        or "unknown",
                        # Raw arguments and raw output are never persisted.
                        input_json={},
                        output_json={},
                        latency_ms=duration,
                        status="success" if success else "error",
                        created_at=now,
                        call_id=_bounded(getattr(request, "call_id", None), 64),
                        operation=operation,
                        started_at=now,
                        completed_at=now,
                        success=success,
                        error_code=_bounded(
                            getattr(result, "error_code", None), MAX_ERROR_CODE_CHARS
                        ),
                        evidence_item_count=_evidence_item_count(result),
                        truncated=bool(getattr(result, "truncated", False)),
                    )
                )
        except Exception as exc:
            logger.warning("Execution audit: tool call not recorded (%s)", type(exc).__name__)

    # ------------------------------------------------------------------ units

    class _UnitOfWork:
        def __init__(self, session: Session):
            self._session = session

        def __enter__(self) -> Session:
            return self._session

        def __exit__(self, exc_type, exc, tb) -> Literal[False]:
            try:
                if exc_type is None:
                    self._session.commit()
                else:
                    self._session.rollback()
            finally:
                self._session.close()
            return False

    def _unit_of_work(self) -> ExecutionAuditRecorder._UnitOfWork:
        return self._UnitOfWork(self._session_factory())
