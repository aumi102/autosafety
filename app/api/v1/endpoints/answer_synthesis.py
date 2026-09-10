"""Guarded answer API endpoints — Phase 7E.

Thin transport layer over GuardedAnswerService. No synthesis, retrieval,
provider-selection, SQL, or graph business logic belongs here.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.services.answer_synthesis.factory import (
    get_answer_synthesis_status,
    get_guarded_answer_service,
)
from app.services.answer_synthesis.guarded_models import GuardedAnswerLike

logger = logging.getLogger(__name__)
router = APIRouter(tags=["graphrag-answer"])

QuestionText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
]


class GuardedAnswerRequest(BaseModel):
    """Public request contract. Internal tools/providers are not request-selectable."""

    model_config = ConfigDict(extra="forbid")

    question: QuestionText
    include_trace: bool = False


class GuardedClaimResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    text: str
    claim_type: str
    citation_ids: list[str]
    support_level: str
    official_status: str
    validation_status: str
    validation_messages: list[str]


class GuardedCitationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_id: str
    source_type: str
    source_record_key: str
    source_entity_id: str | None = None
    title: str | None = None
    source_url: str | None = None
    text_span: str
    retrieval_score: float
    relation_basis: str | None = None
    tool_name: str | None = None


class ConfidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float
    level: Literal["low", "medium", "high"]
    reasons: list[str]


class RetrievalSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunks_retrieved: int
    citations_assembled: int
    graph_paths_found: int
    neo4j_available: bool
    tool_calls_made: int


class CitationValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    factual_claim_count: int
    cited_claim_count: int
    valid_citation_count: int
    invalid_citation_count: int
    citation_coverage: float
    unsupported_claim_ids: list[str]
    invalid_citation_ids: list[str]
    repaired: bool
    messages: list[str]


class GuardedTraceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_provider: str
    provider_available: bool
    fallback_used: bool
    validation_outcome: str
    repaired_claim_count: int
    rejected_claim_count: int
    abstention_reason: str | None = None


class GuardedAnswerResponse(BaseModel):
    """Public schema matching GuardedAnswerResult.to_dict()."""

    model_config = ConfigDict(extra="forbid")

    query: str
    answer: str
    claims: list[GuardedClaimResponse]
    citations: list[GuardedCitationResponse]
    warnings: list[str]
    confidence: ConfidenceResponse
    abstained: bool
    abstention_reason: str | None = None
    synthesis_mode: Literal["llm", "deterministic", "fallback", "repaired", "abstention"]
    provider: str
    retrieval_summary: RetrievalSummaryResponse
    validation: CitationValidationResponse | None = None
    trace: GuardedTraceResponse | None = None
    phase: Literal["phase_7"]


class AnswerSynthesisStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    synthesis_available: bool
    configured_provider: str
    active_provider: str
    provider_available: bool
    real_llm_enabled: bool
    real_llm_configured: bool
    deterministic_fallback_available: bool
    tool_calling_enabled: bool
    max_tool_rounds: int
    max_tool_calls: int
    graphrag_base_required: bool
    guarded_validation_enabled: bool
    phase: Literal["phase_7"]


def get_guarded_answer_service_dependency() -> GuardedAnswerLike:
    """Resolve cached application-owned service; map construction failure safely."""
    try:
        service: GuardedAnswerLike = get_guarded_answer_service()
        return service
    except Exception as exc:
        logger.error("Guarded answer dependency unavailable: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "ANSWER_SYNTHESIS_UNAVAILABLE",
                    "message": "Guarded answer service is unavailable.",
                    "details": {},
                }
            },
        ) from None


@router.post("/answer", response_model=GuardedAnswerResponse)
def guarded_answer(
    data: GuardedAnswerRequest,
    service: GuardedAnswerLike = Depends(get_guarded_answer_service_dependency),
) -> GuardedAnswerResponse:
    """Return the final Phase 7 application-validated answer contract."""
    try:
        result = service.answer(data.question)
        payload = result.to_dict()
        if not data.include_trace:
            # Keep stable schema while suppressing optional audit detail.
            payload["trace"] = None
        return GuardedAnswerResponse.model_validate(payload)
    except Exception as exc:
        logger.error("Guarded answer request failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": "ANSWER_SYNTHESIS_FAILED",
                    "message": "Guarded answer synthesis failed.",
                    "details": {},
                }
            },
        ) from None


@router.get("/answer/status", response_model=AnswerSynthesisStatusResponse)
def guarded_answer_status() -> AnswerSynthesisStatusResponse:
    """Return safe configuration status without probing an external provider."""
    status = get_answer_synthesis_status()
    return AnswerSynthesisStatusResponse.model_validate(status.to_dict())
