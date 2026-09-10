"""
Guarded answer models — Phase 7D.

Final Phase 7 contract: application-validated claims, citations,
sufficiency, confidence, and the guarded answer result. The application
is the final authority — these models never carry raw provider prompts,
raw provider responses, API keys, database/graph clients, or tracebacks.

Deterministic serialization. Bounded text and list sizes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

MAX_CLAIMS = 8
MAX_CITATIONS = 20
MAX_WARNINGS = 20
MAX_TEXT_CHARS = 2000
MAX_ANSWER_CHARS = 8000
MAX_QUESTION_CHARS = 1000
MAX_TEXT_SPAN_CHARS = 500
MAX_MESSAGES = 20


def _bound(text: str | None, limit: int) -> str:
    if not text:
        return ""
    return text[:limit]


@dataclass
class GuardedClaim:
    """A single application-validated factual claim in the final answer."""
    claim_id: str
    text: str
    claim_type: str
    citation_ids: list[str] = field(default_factory=list)
    support_level: str = "supported"  # supported | partial | unsupported
    official_status: str = "none"  # none | existence | applicability | potential
    validation_status: str = "accepted"  # accepted | repaired | rejected
    validation_messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "text": _bound(self.text, MAX_TEXT_CHARS),
            "claim_type": self.claim_type,
            "citation_ids": self.citation_ids[:MAX_CITATIONS],
            "support_level": self.support_level,
            "official_status": self.official_status,
            "validation_status": self.validation_status,
            "validation_messages": self.validation_messages[:MAX_MESSAGES],
        }


@dataclass
class GuardedCitation:
    """A single application-owned citation available to back claims."""
    citation_id: str
    source_type: str
    source_record_key: str
    source_entity_id: str | None = None
    title: str | None = None
    source_url: str | None = None
    text_span: str = ""
    retrieval_score: float = 0.0
    relation_basis: str | None = None
    tool_name: str | None = None

    def to_dict(self) -> dict:
        return {
            "citation_id": self.citation_id,
            "source_type": self.source_type,
            "source_record_key": self.source_record_key,
            "source_entity_id": self.source_entity_id,
            "title": self.title,
            "source_url": self.source_url,
            "text_span": _bound(self.text_span, MAX_TEXT_SPAN_CHARS),
            "retrieval_score": round(self.retrieval_score, 4),
            "relation_basis": self.relation_basis,
            "tool_name": self.tool_name,
        }


@dataclass
class CitationValidationResult:
    """Outcome of application-owned claim/citation validation."""
    valid: bool
    factual_claim_count: int = 0
    cited_claim_count: int = 0
    valid_citation_count: int = 0
    invalid_citation_count: int = 0
    citation_coverage: float = 0.0
    unsupported_claim_ids: list[str] = field(default_factory=list)
    invalid_citation_ids: list[str] = field(default_factory=list)
    repaired: bool = False
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "factual_claim_count": self.factual_claim_count,
            "cited_claim_count": self.cited_claim_count,
            "valid_citation_count": self.valid_citation_count,
            "invalid_citation_count": self.invalid_citation_count,
            "citation_coverage": round(self.citation_coverage, 4),
            "unsupported_claim_ids": self.unsupported_claim_ids[:MAX_MESSAGES],
            "invalid_citation_ids": self.invalid_citation_ids[:MAX_MESSAGES],
            "repaired": self.repaired,
            "messages": self.messages[:MAX_MESSAGES],
        }


@dataclass
class EvidenceSufficiencyResult:
    """Deterministic evidence sufficiency classification."""
    status: str  # sufficient | partial | insufficient
    reasons: list[str] = field(default_factory=list)
    evidence_count: int = 0
    valid_citation_count: int = 0
    source_types: list[str] = field(default_factory=list)
    official_relation_count: int = 0
    graph_available: bool = True
    max_retrieval_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reasons": self.reasons[:MAX_MESSAGES],
            "evidence_count": self.evidence_count,
            "valid_citation_count": self.valid_citation_count,
            "source_types": sorted(self.source_types),
            "official_relation_count": self.official_relation_count,
            "graph_available": self.graph_available,
            "max_retrieval_score": round(self.max_retrieval_score, 4),
        }


@dataclass
class ConfidenceResult:
    """Deterministic, application-computed confidence in evidence support."""
    score: float
    level: str  # low | medium | high
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "level": self.level,
            "reasons": self.reasons[:MAX_MESSAGES],
        }


@dataclass
class RetrievalSummary:
    chunks_retrieved: int = 0
    citations_assembled: int = 0
    graph_paths_found: int = 0
    neo4j_available: bool = True
    tool_calls_made: int = 0

    def to_dict(self) -> dict:
        return {
            "chunks_retrieved": self.chunks_retrieved,
            "citations_assembled": self.citations_assembled,
            "graph_paths_found": self.graph_paths_found,
            "neo4j_available": self.neo4j_available,
            "tool_calls_made": self.tool_calls_made,
        }


@dataclass
class GuardedTrace:
    """Audit trace for Phase 7D decisions. No raw prompt, no API key, no traceback."""
    original_provider: str
    provider_available: bool
    fallback_used: bool
    validation_outcome: str
    repaired_claim_count: int = 0
    rejected_claim_count: int = 0
    abstention_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "original_provider": self.original_provider,
            "provider_available": self.provider_available,
            "fallback_used": self.fallback_used,
            "validation_outcome": self.validation_outcome,
            "repaired_claim_count": self.repaired_claim_count,
            "rejected_claim_count": self.rejected_claim_count,
            "abstention_reason": self.abstention_reason,
        }


class GuardedAnswerLike(Protocol):
    """What a caller needs from the guarded service.

    `GuardedAnswerService` and the Phase 9 `AuditedGuardedAnswerService` wrapper
    both satisfy this. The wrapper is a transparent decorator rather than a
    subclass, so declaring the concrete class at a call site was inaccurate.
    """

    def answer(self, question: str) -> GuardedAnswerResult: ...


@dataclass
class GuardedAnswerResult:
    """
    Final Phase 7 guarded answer contract.

    The application is the final authority: this result reflects only
    validated, citation-backed claims or a deterministic abstention.
    """
    query: str
    answer: str
    claims: list[GuardedClaim] = field(default_factory=list)
    citations: list[GuardedCitation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: ConfidenceResult = field(default_factory=lambda: ConfidenceResult(score=0.0, level="low"))
    abstained: bool = False
    abstention_reason: str | None = None
    synthesis_mode: str = "abstention"  # llm | deterministic | fallback | repaired | abstention
    provider: str = ""
    retrieval_summary: RetrievalSummary = field(default_factory=RetrievalSummary)
    validation: CitationValidationResult | None = None
    trace: GuardedTrace | None = None
    phase: str = "phase_7"

    def to_dict(self) -> dict:
        return {
            "query": _bound(self.query, MAX_QUESTION_CHARS),
            "answer": _bound(self.answer, MAX_ANSWER_CHARS),
            "claims": [c.to_dict() for c in self.claims[:MAX_CLAIMS]],
            "citations": [c.to_dict() for c in self.citations[:MAX_CITATIONS]],
            "warnings": self.warnings[:MAX_WARNINGS],
            "confidence": self.confidence.to_dict(),
            "abstained": self.abstained,
            "abstention_reason": self.abstention_reason,
            "synthesis_mode": self.synthesis_mode,
            "provider": self.provider,
            "retrieval_summary": self.retrieval_summary.to_dict(),
            "validation": self.validation.to_dict() if self.validation else None,
            "trace": self.trace.to_dict() if self.trace else None,
            "phase": self.phase,
        }
