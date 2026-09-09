"""
Phase 9 — execution audit and observability.

Application-owned recording of guarded-execution metadata into `agent_runs`
and `tool_calls`. Audit data is never agent memory: nothing recorded here is
read back into a later answer, and the LLM never writes an audit row.
"""

from app.services.observability.audited import AuditedGuardedAnswerService
from app.services.observability.context import (
    AuditRunContext,
    audit_run,
    current_run,
    current_run_id,
    notify_tool_call,
)
from app.services.observability.recorder import ExecutionAuditRecorder

__all__ = [
    "AuditRunContext",
    "AuditedGuardedAnswerService",
    "ExecutionAuditRecorder",
    "audit_run",
    "current_run",
    "current_run_id",
    "notify_tool_call",
]
