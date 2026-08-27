"""
Deterministic confidence scoring — Phase 7D.

Confidence represents evidence support for the final answer — NOT the
probability of a safety defect, causality, or that a vehicle is unsafe.
Always computed by application code from validated evidence and claim
outcomes; never trusts provider self-reported confidence.
"""

from __future__ import annotations

from app.services.answer_synthesis.guarded_models import (
    ConfidenceResult,
    CitationValidationResult,
    EvidenceSufficiencyResult,
    GuardedCitation,
)

W_RETRIEVAL = 0.20
W_CITATION_COUNT = 0.15
W_COVERAGE = 0.20
W_CLAIM_QUALITY = 0.15
W_OFFICIAL_RELATION = 0.15
W_SOURCE_DIVERSITY = 0.05
W_METADATA = 0.05
W_TOOL_SUCCESS = 0.05

FALLBACK_PENALTY = 0.05
REPAIRED_PENALTY = 0.05
GRAPH_UNAVAILABLE_PENALTY = 0.05
PARTIAL_EVIDENCE_PENALTY = 0.05

HIGH_THRESHOLD = 0.75
MEDIUM_THRESHOLD = 0.45


def compute_confidence(
    *,
    citations: list[GuardedCitation],
    sufficiency: EvidenceSufficiencyResult,
    validation: CitationValidationResult,
    accepted_claim_count: int,
    considered_claim_count: int,
    fallback_used: bool,
    repaired: bool,
    tool_calls_total: int,
    tool_calls_failed: int,
) -> ConfidenceResult:
    if sufficiency.evidence_count == 0:
        return ConfidenceResult(score=0.0, level="low", reasons=["no evidence available"])

    reasons: list[str] = []
    score = 0.0

    n = sufficiency.evidence_count
    retrieval_component = W_RETRIEVAL * min(1.0, n / 3.0) * max(0.3, min(1.0, sufficiency.max_retrieval_score))
    score += retrieval_component
    if retrieval_component > 0:
        reasons.append(f"retrieval strength from {n} evidence item(s)")

    if sufficiency.valid_citation_count == 0:
        citation_component = 0.0
    elif sufficiency.valid_citation_count <= 2:
        citation_component = W_CITATION_COUNT * 0.5
    else:
        citation_component = W_CITATION_COUNT
    score += citation_component
    if citation_component:
        reasons.append(f"{sufficiency.valid_citation_count} valid citation(s) in evidence")

    coverage_component = W_COVERAGE * validation.citation_coverage
    score += coverage_component
    if coverage_component:
        reasons.append(f"citation coverage {validation.citation_coverage:.2f}")

    claim_quality = (accepted_claim_count / considered_claim_count) if considered_claim_count else 0.0
    claim_component = W_CLAIM_QUALITY * claim_quality
    score += claim_component
    if claim_component:
        reasons.append(f"claim validation quality {claim_quality:.2f}")

    if sufficiency.official_relation_count > 0:
        score += W_OFFICIAL_RELATION
        reasons.append("official recall-to-vehicle relation present")

    if len(set(sufficiency.source_types)) >= 2:
        score += W_SOURCE_DIVERSITY
        reasons.append("multiple evidence source types present")
    else:
        reasons.append("single evidence source type only")

    if citations:
        complete = sum(1 for c in citations if c.text_span and c.source_record_key)
        metadata_ratio = complete / len(citations)
    else:
        metadata_ratio = 0.0
    score += W_METADATA * metadata_ratio

    if tool_calls_total > 0:
        tool_success_ratio = (tool_calls_total - tool_calls_failed) / tool_calls_total
    else:
        tool_success_ratio = 1.0
    score += W_TOOL_SUCCESS * tool_success_ratio
    if tool_calls_failed > 0:
        reasons.append(f"{tool_calls_failed} of {tool_calls_total} tool call(s) failed")

    if fallback_used:
        score -= FALLBACK_PENALTY
        reasons.append("deterministic fallback used (penalty applied)")
    if repaired:
        score -= REPAIRED_PENALTY
        reasons.append("provider output required repair (penalty applied)")
    if not sufficiency.graph_available:
        score -= GRAPH_UNAVAILABLE_PENALTY
        reasons.append("graph unavailable (penalty applied)")
    if sufficiency.status == "partial":
        score -= PARTIAL_EVIDENCE_PENALTY
        reasons.append("partial evidence only (penalty applied)")

    score = max(0.0, min(1.0, score))

    # Phase 7 design requires weak evidence to remain low-confidence even when
    # citation/claim-quality components are otherwise internally consistent.
    if "weak_evidence" in sufficiency.reasons and score >= MEDIUM_THRESHOLD:
        score = MEDIUM_THRESHOLD - 0.01
        reasons.append("weak evidence caps confidence at low")

    if score >= HIGH_THRESHOLD:
        level = "high"
    elif score >= MEDIUM_THRESHOLD:
        level = "medium"
    else:
        level = "low"

    return ConfidenceResult(score=score, level=level, reasons=reasons)
