"""
Phase 8 — bounded, session-scoped multi-turn guarded conversation.

Layered strictly on top of the final Phase 7 guarded answer contract. The
`GuardedAnswerService` remains the sole authority on claims, citations,
warnings, confidence, and abstention.
"""

from app.services.conversation.context import (
    extract_component,
    extract_entities,
    resolve_context,
)
from app.services.conversation.factory import (
    ConversationStatus,
    build_conversation_service,
    get_conversation_service,
    get_conversation_status,
)
from app.services.conversation.models import (
    ConversationEntities,
    ConversationLimitError,
    ConversationNotFoundError,
    ConversationSummaryView,
    ConversationTurnResult,
    ConversationTurnView,
    ResolvedContext,
    TurnCitationProvenance,
)
from app.services.conversation.repository import ConversationRepository
from app.services.conversation.service import ConversationService

__all__ = [
    "ConversationEntities",
    "ConversationLimitError",
    "ConversationNotFoundError",
    "ConversationRepository",
    "ConversationService",
    "ConversationStatus",
    "ConversationSummaryView",
    "ConversationTurnResult",
    "ConversationTurnView",
    "ResolvedContext",
    "TurnCitationProvenance",
    "build_conversation_service",
    "extract_component",
    "extract_entities",
    "get_conversation_service",
    "get_conversation_status",
    "resolve_context",
]
