"""Guarded multi-turn conversation API — Phase 8.

Thin transport layer over `ConversationService`. No conversation logic,
context selection, synthesis, retrieval, provider selection, SQL, or graph
business logic belongs here.

Responses expose safe data only: the user question, the resolved question,
allowlisted entity slots, the Phase 7 guarded answer contract, and citation
lineage. Raw prompts, raw provider requests/responses, connection strings,
internal tool objects, raw SQL, raw Cypher, and settings are never exposed.

This is the guarded multi-turn surface. The legacy `/v1/chat/*` routes
remain the Phase 2/Phase 4 single-turn surface and are unchanged.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.api.v1.endpoints.answer_synthesis import GuardedAnswerResponse
from app.services.conversation.factory import (
    get_conversation_service,
    get_conversation_status,
)
from app.services.conversation.models import (
    MAX_TURNS_RETURNED,
    ConversationLimitError,
    ConversationNotFoundError,
)
from app.services.conversation.service import ConversationService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["conversations"])

QuestionText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
]
TitleText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: TitleText | None = None


class ConversationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    title: str | None = None
    created_at: str
    last_activity_at: str
    turn_count: int
    phase: Literal["phase_8"]


class ConversationEntitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    make: str | None = None
    model: str | None = None
    model_year: int | None = None
    component: str | None = None


class ResolvedContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved_question: str
    entities: ConversationEntitiesResponse
    context_applied: bool
    inherited_slots: list[str]
    context_turn_index: int | None = None
    context_chars: int
    turns_considered: int


class TurnProvenanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_id: str
    source_type: str
    source_record_key: str
    first_seen_turn_index: int
    reused_from_prior_turn: bool
    cited_by_claim: bool


class ConversationMessageRequest(BaseModel):
    """Public request contract. Providers, tools, and bounds are not selectable."""

    model_config = ConfigDict(extra="forbid")

    question: QuestionText
    include_trace: bool = False


class ConversationTurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    turn_id: str
    turn_index: int
    question: str
    context: ResolvedContextResponse
    guarded_answer: GuardedAnswerResponse
    provenance: list[TurnProvenanceResponse]
    conversation_warnings: list[str]
    phase: Literal["phase_8"]


class ConversationTurnSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: str
    turn_index: int
    question: str
    resolved_question: str
    context_applied: bool
    entities: ConversationEntitiesResponse
    answer: str
    synthesis_mode: str
    provider: str
    abstained: bool
    abstention_reason: str | None = None
    confidence_score: float
    confidence_level: str
    claim_count: int
    citation_count: int
    warnings: list[str]
    created_at: str


class ConversationTurnListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    turns: list[ConversationTurnSummaryResponse]
    turn_count: int
    phase: Literal["phase_8"]


class ConversationDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    deleted: bool
    phase: Literal["phase_8"]


class ConversationStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    phase: Literal["phase_8"]


def get_conversation_service_dependency() -> ConversationService:
    """Resolve cached application-owned service; map construction failure safely."""
    try:
        return get_conversation_service()
    except Exception as exc:
        logger.error("Conversation dependency unavailable: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "CONVERSATION_UNAVAILABLE",
                    "message": "Conversation service is unavailable.",
                    "details": {},
                }
            },
        ) from None


def _not_found(conversation_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "error": {
                "code": "CONVERSATION_NOT_FOUND",
                "message": "Conversation not found.",
                "details": {"conversation_id": conversation_id[:64]},
            }
        },
    )


def _failed(code: str, message: str, status_code: int = 500) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "details": {}}},
    )


@router.post("", response_model=ConversationResponse, status_code=201)
def create_conversation(
    data: ConversationCreateRequest,
    service: ConversationService = Depends(get_conversation_service_dependency),
):
    """Create an empty conversation."""
    try:
        summary = service.start_conversation(data.title)
    except Exception as exc:
        logger.error("Conversation creation failed: %s", type(exc).__name__)
        raise _failed("CONVERSATION_CREATE_FAILED", "Could not create conversation.") from None
    return ConversationResponse.model_validate(summary.to_dict())


@router.get("/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: str,
    service: ConversationService = Depends(get_conversation_service_dependency),
):
    """Return safe conversation metadata."""
    try:
        summary = service.get_conversation(conversation_id)
    except ConversationNotFoundError:
        raise _not_found(conversation_id) from None
    except Exception as exc:
        logger.error("Conversation lookup failed: %s", type(exc).__name__)
        raise _failed("CONVERSATION_LOOKUP_FAILED", "Could not load conversation.") from None
    return ConversationResponse.model_validate(summary.to_dict())


@router.get("/{conversation_id}/turns", response_model=ConversationTurnListResponse)
def list_conversation_turns(
    conversation_id: str,
    limit: int = Query(default=MAX_TURNS_RETURNED, ge=1, le=MAX_TURNS_RETURNED),
    service: ConversationService = Depends(get_conversation_service_dependency),
):
    """Return this conversation's stored turns. Scoped to one conversation only."""
    try:
        turns = service.list_turns(conversation_id, limit)
    except ConversationNotFoundError:
        raise _not_found(conversation_id) from None
    except Exception as exc:
        logger.error("Conversation turn listing failed: %s", type(exc).__name__)
        raise _failed("CONVERSATION_TURNS_FAILED", "Could not load conversation turns.") from None
    return ConversationTurnListResponse.model_validate(
        {
            "conversation_id": conversation_id,
            "turns": [turn.to_dict() for turn in turns],
            "turn_count": len(turns),
            "phase": "phase_8",
        }
    )


@router.post("/{conversation_id}/messages", response_model=ConversationTurnResponse)
def send_conversation_message(
    conversation_id: str,
    data: ConversationMessageRequest,
    service: ConversationService = Depends(get_conversation_service_dependency),
):
    """Answer one turn through the Phase 7 guarded path and persist the outcome."""
    try:
        result = service.answer(conversation_id, data.question)
    except ConversationNotFoundError:
        raise _not_found(conversation_id) from None
    except ConversationLimitError:
        raise _failed(
            "CONVERSATION_TURN_LIMIT_REACHED",
            "This conversation reached its bounded turn limit.",
            status_code=409,
        ) from None
    except Exception as exc:
        logger.error("Conversation turn failed: %s", type(exc).__name__)
        raise _failed("CONVERSATION_TURN_FAILED", "Conversation turn failed.") from None

    payload = result.to_dict()
    if not data.include_trace:
        # Keep stable schema while suppressing optional audit detail.
        payload["guarded_answer"]["trace"] = None
    return ConversationTurnResponse.model_validate(payload)


@router.delete("/{conversation_id}", response_model=ConversationDeleteResponse)
def delete_conversation(
    conversation_id: str,
    service: ConversationService = Depends(get_conversation_service_dependency),
):
    """Hard-delete a conversation and every turn, message, and citation it owns."""
    try:
        deleted = service.delete_conversation(conversation_id)
    except Exception as exc:
        logger.error("Conversation deletion failed: %s", type(exc).__name__)
        raise _failed("CONVERSATION_DELETE_FAILED", "Could not delete conversation.") from None
    if not deleted:
        raise _not_found(conversation_id)
    return ConversationDeleteResponse.model_validate(
        {"conversation_id": conversation_id, "deleted": True, "phase": "phase_8"}
    )


@router.get("/status/config", response_model=ConversationStatusResponse)
def conversation_status():
    """Return safe conversation bounds and policy posture. No credentials."""
    return ConversationStatusResponse.model_validate(get_conversation_status().to_dict())
