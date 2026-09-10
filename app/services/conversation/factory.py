"""Application-owned Phase 8 dependency wiring.

Reuses the existing Phase 7 service graph and the process-level SQLAlchemy
engine. User input never selects providers, tools, database connections,
tool budgets, or conversation bounds.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import get_sync_engine
from app.services.answer_synthesis.factory import (
    build_execution_audit_recorder,
    get_unaudited_guarded_answer_service,
)
from app.services.answer_synthesis.guarded_models import GuardedAnswerLike
from app.services.conversation.service import ConversationService
from app.services.observability.context import AuditRecorder


@dataclass(frozen=True)
class ConversationStatus:
    """Safe operational status. Contains no credentials or connection strings."""

    conversation_available: bool
    max_context_turns: int
    max_turns_per_conversation: int
    max_context_chars: int
    max_stored_citations_per_turn: int
    retention_days: int
    persistence_backend: str
    cache_backend: str
    guarded_service_required: bool
    cross_turn_provenance_enforced: bool
    prior_assistant_text_used_as_evidence: bool
    phase: str = "phase_8"

    def to_dict(self) -> dict:
        return {
            "conversation_available": self.conversation_available,
            "max_context_turns": self.max_context_turns,
            "max_turns_per_conversation": self.max_turns_per_conversation,
            "max_context_chars": self.max_context_chars,
            "max_stored_citations_per_turn": self.max_stored_citations_per_turn,
            "retention_days": self.retention_days,
            "persistence_backend": self.persistence_backend,
            "cache_backend": self.cache_backend,
            "guarded_service_required": self.guarded_service_required,
            "cross_turn_provenance_enforced": self.cross_turn_provenance_enforced,
            "prior_assistant_text_used_as_evidence": self.prior_assistant_text_used_as_evidence,
            "phase": self.phase,
        }


def build_conversation_service(
    settings: Settings | None = None,
    *,
    session_factory: Callable[[], Session] | None = None,
    guarded_service: GuardedAnswerLike | None = None,
    audit_recorder: AuditRecorder | None = None,
) -> ConversationService:
    """Build the application-owned Phase 8 service dependency.

    Optional arguments exist for deterministic tests and controlled runtime
    injection only. None is derived from an API request or CLI flag.
    """
    resolved_settings = settings or get_settings()
    if session_factory is None:
        session_factory = sessionmaker(bind=get_sync_engine(), expire_on_commit=False)
    if guarded_service is None:
        # The unaudited graph: ConversationService opens its own Phase 9 audit
        # run so it can link the persisted conversation and turn to it.
        guarded_service = get_unaudited_guarded_answer_service()
    if audit_recorder is None:
        audit_recorder = build_execution_audit_recorder(
            resolved_settings, session_factory=session_factory
        )
    return ConversationService(
        session_factory=session_factory,
        guarded_service=guarded_service,
        settings=resolved_settings,
        audit_recorder=audit_recorder,
    )


@lru_cache(maxsize=1)
def get_conversation_service() -> ConversationService:
    """Return one process-level service graph for FastAPI dependency injection."""
    return build_conversation_service()


def get_conversation_status(settings: Settings | None = None) -> ConversationStatus:
    """Return safe configuration status without any network or database call."""
    resolved = settings or get_settings()
    return ConversationStatus(
        conversation_available=True,
        max_context_turns=int(resolved.PHASE8_MAX_CONTEXT_TURNS),
        max_turns_per_conversation=int(resolved.PHASE8_MAX_TURNS_PER_CONVERSATION),
        max_context_chars=int(resolved.PHASE8_MAX_CONTEXT_CHARS),
        max_stored_citations_per_turn=int(resolved.PHASE8_MAX_STORED_CITATIONS_PER_TURN),
        retention_days=int(resolved.PHASE8_CONVERSATION_RETENTION_DAYS),
        persistence_backend="postgresql",
        cache_backend="none",
        guarded_service_required=True,
        cross_turn_provenance_enforced=True,
        prior_assistant_text_used_as_evidence=False,
    )
