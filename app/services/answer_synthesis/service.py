"""
Guarded answer synthesis service — Phase 7D.

Wires the Phase 7C SynthesisOrchestrator with Phase 7D evidence
adaptation, sufficiency policy, citation/claim validation, deterministic
composition, and confidence scoring. The application — not the LLM — is
the final authority on whether an answer is safe to return.

No public API endpoint. No CLI. No database or Neo4j client held here.
Provider prose is never returned unchanged when any claim was rejected
or no claims survived validation — a deterministic, citation-backed
answer replaces it in that case.
"""

from __future__ import annotations

import logging

from app.services.answer_synthesis.citation_validator import (
    MAX_INPUT_CLAIMS,
    validate_and_build_claims,
)
from app.services.answer_synthesis.composer import compose_answer
from app.services.answer_synthesis.confidence import compute_confidence
from app.services.answer_synthesis.evidence_adapter import (
    adapt_evidence,
    graph_availability,
    has_only_failed_tool_evidence,
    tool_call_stats,
)
from app.services.answer_synthesis.guarded_models import (
    ConfidenceResult,
    GuardedAnswerResult,
    GuardedTrace,
    RetrievalSummary,
)
from app.services.answer_synthesis.models import OrchestrationResult
from app.services.answer_synthesis.orchestrator import SynthesisOrchestrator
from app.services.answer_synthesis.policy import (
    build_mandatory_warnings,
    classify_question_intent,
    evaluate_sufficiency,
)

logger = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 1000
ABSTENTION_ANSWER = (
    "I cannot provide a reliable answer to this question based on available evidence."
)


class GuardedAnswerService:
    """
    Full Phase 7D guarded flow:

    question -> SynthesisOrchestrator -> evidence adaptation ->
    sufficiency policy -> claim/citation validation -> safe repair ->
    deterministic rescue where unsafe/unsupported -> confidence ->
    mandatory warnings -> GuardedAnswerResult.
    """

    def __init__(self, orchestrator: SynthesisOrchestrator):
        self._orchestrator = orchestrator

    def answer(self, question: str) -> GuardedAnswerResult:
        question = (question or "").strip()[:MAX_QUESTION_CHARS]
        if not question:
            return self._abstain(question, "empty_question")

        orchestration: OrchestrationResult = self._orchestrator.orchestrate(question)
        bundle = orchestration.evidence_bundle

        original_provider = self._orchestrator.primary_provider_name
        provider_available = self._orchestrator.primary_provider_available
        fallback_used = orchestration.trace.fallback_used

        if bundle is None:
            return self._abstain(
                question, "no_evidence",
                provider=original_provider, provider_available=provider_available,
                fallback_used=fallback_used,
            )

        citations = adapt_evidence(bundle)
        graph_available = graph_availability(bundle)
        only_failed = has_only_failed_tool_evidence(bundle)
        tool_calls_total, tool_calls_failed = tool_call_stats(bundle)

        sufficiency = evaluate_sufficiency(
            question=question,
            citations=citations,
            graph_available=graph_available,
            only_failed_tool_evidence=only_failed,
        )
        intent = classify_question_intent(question)

        if sufficiency.status == "insufficient":
            return self._abstain(
                question, sufficiency.reasons[0] if sufficiency.reasons else "insufficient_evidence",
                provider=original_provider, provider_available=provider_available,
                fallback_used=fallback_used,
            )

        provider_result = orchestration.provider_result

        if provider_result.error_code:
            return self._abstain(
                question, provider_result.error_code,
                provider=original_provider, provider_available=provider_available,
                fallback_used=True,
            )

        if provider_result.abstain:
            return self._abstain(
                question, provider_result.abstention_reason or "provider_abstained",
                provider=original_provider, provider_available=provider_available,
                fallback_used=fallback_used,
            )

        base_mode = "fallback" if fallback_used else (
            "deterministic" if original_provider == "deterministic" else "llm"
        )

        candidate_claims = provider_result.claims or []
        guarded_claims, validation = validate_and_build_claims(candidate_claims, citations)
        rejected_count = len(validation.unsupported_claim_ids)
        needs_rescue = rejected_count > 0 or not guarded_claims
        considered_claims = candidate_claims

        if needs_rescue:
            composed_answer, composed_claims = compose_answer(question, citations, sufficiency, intent)
            guarded_claims, validation = validate_and_build_claims(composed_claims, citations)
            considered_claims = composed_claims
            if not guarded_claims:
                return self._abstain(
                    question, "invalid_output_unrepairable",
                    provider=original_provider, provider_available=provider_available,
                    fallback_used=True,
                )
            answer_text = composed_answer
            final_mode = "fallback" if base_mode == "llm" else base_mode
            fallback_used = fallback_used or base_mode == "llm"
        else:
            # Public answer text is assembled only from application-validated
            # claims. Raw provider prose may contain unsupported statements not
            # represented in its structured claims and is never authoritative.
            answer_text = "\n".join(claim.text for claim in guarded_claims)
            final_mode = "repaired" if (validation.repaired and base_mode == "llm") else base_mode

        claim_types_used = {c.claim_type for c in guarded_claims}
        warnings = build_mandatory_warnings(
            citations=citations,
            sufficiency=sufficiency,
            intent=intent,
            fallback_used=fallback_used,
            repaired=validation.repaired,
            claim_types_used=claim_types_used,
        )

        considered_claim_count = min(len(considered_claims), MAX_INPUT_CLAIMS) if considered_claims else len(guarded_claims)
        confidence = compute_confidence(
            citations=citations,
            sufficiency=sufficiency,
            validation=validation,
            accepted_claim_count=len(guarded_claims),
            considered_claim_count=considered_claim_count,
            fallback_used=fallback_used,
            repaired=validation.repaired,
            tool_calls_total=tool_calls_total,
            tool_calls_failed=tool_calls_failed,
        )

        retrieval_summary = RetrievalSummary(
            chunks_retrieved=sum(
                1 for c in citations
                if c.tool_name == "graphrag_retrieval_tool" and c.source_type in ("complaint", "recall")
            ),
            citations_assembled=len(citations),
            graph_paths_found=sum(1 for c in citations if c.source_type == "graph_path"),
            neo4j_available=graph_available,
            tool_calls_made=tool_calls_total,
        )

        validation_outcome = (
            "rejected" if rejected_count and needs_rescue else
            "repaired" if validation.repaired else
            "accepted"
        )
        trace = GuardedTrace(
            original_provider=original_provider,
            provider_available=provider_available,
            fallback_used=fallback_used,
            validation_outcome=validation_outcome,
            repaired_claim_count=sum(1 for c in guarded_claims if c.validation_status == "repaired"),
            rejected_claim_count=rejected_count,
            abstention_reason=None,
        )

        return GuardedAnswerResult(
            query=question,
            answer=answer_text,
            claims=guarded_claims,
            citations=citations,
            warnings=warnings,
            confidence=confidence,
            abstained=False,
            abstention_reason=None,
            synthesis_mode=final_mode,
            provider="deterministic" if final_mode in ("deterministic", "fallback") else original_provider,
            retrieval_summary=retrieval_summary,
            validation=validation,
            trace=trace,
        )

    def _abstain(
        self,
        question: str,
        reason: str,
        *,
        provider: str = "",
        provider_available: bool = False,
        fallback_used: bool = False,
    ) -> GuardedAnswerResult:
        return GuardedAnswerResult(
            query=question,
            answer=ABSTENTION_ANSWER,
            claims=[],
            citations=[],
            warnings=[],
            confidence=ConfidenceResult(score=0.0, level="low", reasons=["insufficient or unsafe evidence"]),
            abstained=True,
            abstention_reason=reason,
            synthesis_mode="abstention",
            provider=provider,
            retrieval_summary=RetrievalSummary(),
            validation=None,
            trace=GuardedTrace(
                original_provider=provider,
                provider_available=provider_available,
                fallback_used=fallback_used,
                validation_outcome="abstention_required",
                repaired_claim_count=0,
                rejected_claim_count=0,
                abstention_reason=reason,
            ),
        )
