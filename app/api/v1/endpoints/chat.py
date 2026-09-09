"""Legacy chat API endpoints — deprecated, guarded bridge (Phase 9).

`docs/06_api_contract.md` documents `/v1/chat/sessions*`, and
`docs/contracts/answer_contract.md` documents the response shape. Those
contracts are preserved.

What changed in Phase 9 is what sits behind them. Previously these routes
called `SqlAnalyticsService` (Phase 2) and `answer_hybrid_question` (Phase 4)
directly, which **bypassed `GuardedAnswerService` entirely** — no citation
validation, no causality guard, no deterministic confidence, no abstention —
and `POST /sessions` returned a session id that was never persisted.

They are now a thin, deprecated bridge over the Phase 8 `ConversationService`,
so every legacy request runs the full guarded path:

```text
/v1/chat/sessions/{id}/messages
→ ConversationService.answer()
→ GuardedAnswerService (mandatory GraphRAG, tools, validation, abstention)
→ Phase 8 cross-turn provenance gate
→ mapped into the documented answer_contract shape
```

The bridge only *narrows* what is exposed: raw SQL is never returned, because
the guarded path does not surface it. `sql.used` reports whether the allowlisted
SQL analytics tool contributed evidence.

Migration path for clients: `/v1/chat/*` → `/v1/conversations/*`, which returns
the richer `phase_8` contract (validated claims, citation provenance, validation
outcome). See `docs/phase9_design.md`.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, StringConstraints

from app.services.conversation.factory import get_conversation_service
from app.services.conversation.models import (
    ConversationLimitError,
    ConversationNotFoundError,
    ConversationTurnResult,
)
from app.services.conversation.service import ConversationService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])

DEPRECATION_WARNING = (
    "The /v1/chat endpoints are deprecated. Use /v1/conversations for the guarded "
    "multi-turn contract."
)
SUCCESSOR_LINK = '</v1/conversations>; rel="successor-version"'
LEGACY_PHASE = "phase_9_legacy_bridge"

# Raising an HTTPException discards the injected Response, so error paths must
# carry the deprecation signal explicitly or clients would only see it on 2xx.
DEPRECATION_HEADERS = {
    "Deprecation": "true",
    "Link": SUCCESSOR_LINK,
    "Warning": f'299 - "{DEPRECATION_WARNING}"',
}

MessageText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]


class ChatSessionCreate(BaseModel):
    title: str | None = None


class ChatSession(BaseModel):
    id: str
    title: str | None
    created_at: str


class ChatMessageCreate(BaseModel):
    content: MessageText
    options: dict | None = {}


class ChatMessageResponse(BaseModel):
    message_id: str
    run_id: str
    intent: str
    answer: dict
    sql: dict
    evidence: dict
    warnings: list[str]
    confidence: dict
    debug: dict
    phase: str


def get_conversation_service_dependency() -> ConversationService:
    """Resolve the application-owned conversation service for the legacy bridge."""
    try:
        return get_conversation_service()
    except Exception as exc:
        logger.error("Legacy chat dependency unavailable: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "CHAT_UNAVAILABLE",
                    "message": "Chat service is unavailable.",
                    "details": {},
                }
            },
            headers=DEPRECATION_HEADERS,
        ) from None


def _mark_deprecated(response: Response) -> None:
    response.headers.update(DEPRECATION_HEADERS)


def _legacy_intent(result: ConversationTurnResult) -> str:
    """Map the guarded outcome onto the documented answer_contract intent enum."""
    guarded = result.guarded
    if guarded.abstained:
        return "safety"
    if any(c.tool_name == "sql_analytics_tool" for c in guarded.citations):
        return "hybrid"
    return "graph_rag"


def _legacy_answer(result: ConversationTurnResult) -> dict:
    guarded = result.guarded
    sections: list[dict[str, Any]] = []
    if guarded.claims:
        sections.append(
            {
                "title": "Evidence summary",
                "content": "\n".join(claim.text for claim in guarded.claims),
                "type": "evidence_summary",
            }
        )
    for warning in guarded.warnings:
        sections.append({"title": "Caveat", "content": warning, "type": "caveat"})
    sections.append({"title": "Deprecation", "content": DEPRECATION_WARNING, "type": "text"})
    return {"summary": guarded.answer, "sections": sections}


def _legacy_evidence(result: ConversationTurnResult) -> dict:
    guarded = result.guarded
    citations = [
        {
            "source_type": c.source_type,
            "source_id": c.source_entity_id or c.citation_id,
            "source_key": c.source_record_key,
            "field_name": None,
            "text_span": c.text_span,
            "confidence": round(c.retrieval_score, 4),
        }
        for c in guarded.citations
        if c.source_type != "graph_path"
    ]
    graph_paths = [
        {
            "path_text": c.text_span or c.title or "",
            "relation_source": c.relation_basis or "normalized_join",
            "confidence": round(c.retrieval_score, 4),
        }
        for c in guarded.citations
        if c.source_type == "graph_path"
    ]
    return {"citations": citations, "graph_paths": graph_paths}


def _to_legacy_contract(result: ConversationTurnResult) -> ChatMessageResponse:
    """Map the Phase 8 guarded result onto the documented answer contract."""
    guarded = result.guarded
    return ChatMessageResponse(
        message_id=result.turn_id,
        run_id=result.turn_id,
        intent=_legacy_intent(result),
        answer=_legacy_answer(result),
        sql={
            # The guarded path never exposes generated SQL text. `used` reports
            # only whether the allowlisted SQL analytics tool supplied evidence.
            "used": any(c.tool_name == "sql_analytics_tool" for c in guarded.citations),
            "query": None,
            "columns": None,
            "rows": None,
            "row_count": None,
            "execution_ms": None,
            "validated": True,
        },
        evidence=_legacy_evidence(result),
        warnings=[*guarded.warnings, *result.conversation_warnings, DEPRECATION_WARNING],
        confidence={
            "label": guarded.confidence.level,
            "score": round(guarded.confidence.score, 4),
            "reasons": guarded.confidence.reasons,
        },
        debug={
            "tool_call_count": guarded.retrieval_summary.tool_calls_made,
            "latency_ms": 0,
            "deprecated": True,
            "successor": "/v1/conversations",
        },
        phase=LEGACY_PHASE,
    )


def _not_found(session_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "error": {
                "code": "CHAT_SESSION_NOT_FOUND",
                "message": "Chat session not found. Create one with POST /v1/chat/sessions.",
                "details": {"session_id": session_id[:64]},
            }
        },
        headers=DEPRECATION_HEADERS,
    )


@router.post(
    "/sessions",
    response_model=ChatSession,
    deprecated=True,
    summary="Deprecated — use POST /v1/conversations",
)
def create_session(
    data: ChatSessionCreate,
    response: Response,
    service: ConversationService = Depends(get_conversation_service_dependency),
):
    """Create a real, persisted conversation and return it in the legacy shape."""
    _mark_deprecated(response)
    try:
        summary = service.start_conversation(data.title)
    except Exception as exc:
        logger.error("Legacy chat session creation failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": "CHAT_SESSION_CREATE_FAILED",
                    "message": "Could not create chat session.",
                    "details": {},
                }
            },
            headers=DEPRECATION_HEADERS,
        ) from None
    return ChatSession(
        id=summary.conversation_id,
        title=summary.title,
        created_at=summary.created_at,
    )


@router.post(
    "/sessions/{session_id}/messages",
    response_model=ChatMessageResponse,
    deprecated=True,
    summary="Deprecated — use POST /v1/conversations/{conversation_id}/messages",
)
def send_message(
    session_id: str,
    data: ChatMessageCreate,
    response: Response,
    service: ConversationService = Depends(get_conversation_service_dependency),
):
    """Answer through the full guarded path, mapped to the legacy answer contract."""
    _mark_deprecated(response)
    try:
        result = service.answer(session_id, data.content)
    except ConversationNotFoundError:
        raise _not_found(session_id) from None
    except ConversationLimitError:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "CHAT_SESSION_TURN_LIMIT_REACHED",
                    "message": "This chat session reached its bounded turn limit.",
                    "details": {},
                }
            },
            headers=DEPRECATION_HEADERS,
        ) from None
    except Exception as exc:
        logger.error("Legacy chat message failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": "CHAT_MESSAGE_FAILED",
                    "message": "Chat message failed.",
                    "details": {},
                }
            },
            headers=DEPRECATION_HEADERS,
        ) from None
    return _to_legacy_contract(result)
