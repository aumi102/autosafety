"""
Phase 9 audited guarded-answer wrapper.

Wraps `GuardedAnswerService` so every guarded execution opens an audit run,
records the tool calls made inside it, and closes the run from the returned
contract — without changing the guarded contract or altering a single answer.

The wrapper is transparent by construction: it forwards the question unchanged
and returns the `GuardedAnswerResult` unchanged. It cannot influence retrieval,
tool selection, synthesis, validation, confidence, warnings, or abstention.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.services.observability.context import audit_run
from app.services.observability.recorder import ExecutionAuditRecorder

logger = logging.getLogger(__name__)


class AuditedGuardedAnswerService:
    """Transparent audit decorator over a guarded answer service."""

    def __init__(self, inner: Any, recorder: ExecutionAuditRecorder, *, surface: str = "service"):
        self._inner = inner
        self._recorder = recorder
        self._surface = surface

    @property
    def inner(self) -> Any:
        """The wrapped guarded service. Kept accessible for tests and diagnostics."""
        return self._inner

    def answer(self, question: str):
        """Run the guarded path, recording an audit run around it."""
        context = None
        try:
            context = self._recorder.start_run(surface=self._surface)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Execution audit: run not opened (%s)", type(exc).__name__)

        started = time.time()
        with audit_run(context):
            try:
                result = self._inner.answer(question)
            except Exception:
                if context is not None:
                    self._recorder.fail_run(
                        context.run_id,
                        "guarded_answer_failed",
                        latency_ms=int((time.time() - started) * 1000),
                    )
                raise

        if context is not None:
            self._recorder.finish_run(
                context.run_id,
                result,
                latency_ms=int((time.time() - started) * 1000),
            )
        return result

    def __getattr__(self, name: str) -> Any:
        """Forward any other attribute to the wrapped service."""
        return getattr(self._inner, name)
