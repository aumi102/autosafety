"""
Evidence sufficiency, claim-type allowlist, and mandatory warnings — Phase 7D.

The application, not the LLM, decides whether evidence is sufficient,
which claim types are allowed, and which warnings are mandatory.
Deterministic and side-effect free.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.answer_synthesis.guarded_models import EvidenceSufficiencyResult, GuardedCitation

# =============================================================================
# Claim type allowlist
# =============================================================================

CLAIM_TYPES: frozenset[str] = frozenset(
    {
        "complaint_observation",
        "complaint_component_observation",
        "official_recall",
        "official_recall_applicability",
        "potential_shared_component_association",
        "sql_fact",
        "data_limitation",
        "synthesis_summary",
    }
)

# Claim types a provider might plausibly emit that map onto the allowlist.
CLAIM_TYPE_ALIASES: dict[str, str] = {
    "shared_component_association": "potential_shared_component_association",
    "shared_component_observation": "potential_shared_component_association",
    "recall_observation": "official_recall",
    "recall_applicability": "official_recall_applicability",
    "limitation": "data_limitation",
    "summary": "synthesis_summary",
}

# Claim types that may be uncited (system-level limitation statements only).
UNCITED_ALLOWED_CLAIM_TYPES: frozenset[str] = frozenset({"data_limitation"})


def normalize_claim_type(raw: str) -> str | None:
    """Map a raw claim_type string onto the Phase 7D allowlist, or None if unmappable."""
    candidate = (raw or "").strip()
    if candidate in CLAIM_TYPES:
        return candidate
    return CLAIM_TYPE_ALIASES.get(candidate)


# =============================================================================
# Question intent classification
# =============================================================================


@dataclass
class QuestionIntent:
    is_causal: bool
    asks_official_recall: bool
    asks_applicability: bool
    asks_complaint: bool


_CAUSAL_KEYWORDS = (
    "why did",
    "why do",
    "why does",
    "why is",
    "why are",
    "what caused",
    "what causes",
    "caused by",
    "cause of",
    "reason for",
    "cause the",
    "causes the",
    "caused the",
    "cause this",
    "reason why",
)
_RECALL_KEYWORDS = ("recall", "campaign")
_APPLICABILITY_KEYWORDS = (
    "apply to",
    "applies to",
    "affected by",
    "does it affect",
    "is my vehicle",
    "does the recall apply",
    "affects my",
)
_COMPLAINT_KEYWORDS = ("complaint", "complaints")


def classify_question_intent(question: str) -> QuestionIntent:
    """
    Deterministic keyword-based question intent classification.

    Causal detection is intentionally broad (any mention of "cause"/
    "caused"/"causes") — over-triggering the causal-limitation warning
    is safe; missing a genuine causal question is not.
    """
    q = f" {(question or '').lower().strip()} "
    is_causal = "cause" in q or any(k in q for k in _CAUSAL_KEYWORDS) or q.strip().startswith("why")
    return QuestionIntent(
        is_causal=is_causal,
        asks_official_recall=any(k in q for k in _RECALL_KEYWORDS),
        asks_applicability=any(k in q for k in _APPLICABILITY_KEYWORDS),
        asks_complaint=any(k in q for k in _COMPLAINT_KEYWORDS),
    )


# =============================================================================
# Evidence sufficiency gate
# =============================================================================


def evaluate_sufficiency(
    *,
    question: str,
    citations: list[GuardedCitation],
    graph_available: bool,
    only_failed_tool_evidence: bool,
) -> EvidenceSufficiencyResult:
    """
    Classify evidence as sufficient / partial / insufficient.

    Required behavior (Phase 7D policy):
    - no evidence -> insufficient / no_evidence
    - only failed tool results -> insufficient / tool_evidence_unavailable
    - weak semantic evidence with a valid citation -> partial
    - complaint-only evidence is insufficient for an official recall
      applicability claim (partial, not full insufficiency — a limited
      complaint-only answer remains possible)
    - causal questions -> partial with an explicit limitation reason
    """
    intent = classify_question_intent(question)
    evidence_count = len(citations)
    source_types = sorted({c.source_type for c in citations})
    official_relation_count = sum(
        1 for c in citations if c.relation_basis == "official_recall_affects_vehicle"
    )
    max_score = max((c.retrieval_score for c in citations), default=0.0)

    if evidence_count == 0:
        return EvidenceSufficiencyResult(
            status="insufficient",
            reasons=["no_evidence"],
            evidence_count=0,
            valid_citation_count=0,
            source_types=[],
            official_relation_count=0,
            graph_available=graph_available,
            max_retrieval_score=0.0,
        )

    if only_failed_tool_evidence:
        return EvidenceSufficiencyResult(
            status="insufficient",
            reasons=["tool_evidence_unavailable"],
            evidence_count=evidence_count,
            valid_citation_count=evidence_count,
            source_types=source_types,
            official_relation_count=official_relation_count,
            graph_available=graph_available,
            max_retrieval_score=max_score,
        )

    reasons: list[str] = []
    weak = max_score < 0.3 and official_relation_count == 0 and evidence_count < 2
    if weak:
        reasons.append("weak_evidence")

    if intent.asks_applicability and official_relation_count == 0:
        reasons.append("official_applicability_unverified")

    if intent.asks_official_recall and "recall" not in source_types:
        reasons.append("no_recall_evidence_for_recall_question")

    if intent.is_causal:
        reasons.append("causal_conclusion_unsupported")

    if (
        weak
        or "official_applicability_unverified" in reasons
        or "no_recall_evidence_for_recall_question" in reasons
        or intent.is_causal
    ):
        status = "partial"
    else:
        status = "sufficient"

    if not reasons:
        reasons.append("evidence_sufficient")

    return EvidenceSufficiencyResult(
        status=status,
        reasons=reasons,
        evidence_count=evidence_count,
        valid_citation_count=evidence_count,
        source_types=source_types,
        official_relation_count=official_relation_count,
        graph_available=graph_available,
        max_retrieval_score=max_score,
    )


# =============================================================================
# Mandatory warnings
# =============================================================================

COMPLAINT_WARNING = (
    "Complaint records are public reports and may be incomplete or noisy. "
    "Complaint volume alone does not prove a safety defect."
)
SHARED_COMPONENT_WARNING = (
    "Shared-component evidence is a potential association, not proof of "
    "causality or official linkage."
)
GRAPH_UNAVAILABLE_WARNING = (
    "Graph-based applicability could not be verified because the graph database was unavailable."
)
NO_AFFECTS_WARNING = (
    "Official vehicle applicability was not verified because no official "
    "recall-to-vehicle relation was found in the evidence."
)
RELATED_TO_COMPONENT_WARNING = (
    "Recall component relations are unavailable because source recall "
    "component fields are missing in current data."
)
FALLBACK_WARNING = "LLM synthesis was unavailable or invalid; deterministic synthesis was used."
REPAIRED_WARNING = "Generated output required application-level correction before acceptance."
PARTIAL_EVIDENCE_WARNING = (
    "This answer is limited to the available evidence and may not be complete."
)
CAUSAL_LIMITATION_WARNING = "Available evidence cannot establish causality."


def build_mandatory_warnings(
    *,
    citations: list[GuardedCitation],
    sufficiency: EvidenceSufficiencyResult,
    intent: QuestionIntent,
    fallback_used: bool,
    repaired: bool,
    claim_types_used: set[str],
) -> list[str]:
    """Deterministic, deduplicated mandatory warnings for the final answer."""
    warnings: list[str] = []

    def add(w: str) -> None:
        if w not in warnings:
            warnings.append(w)

    if any(c.source_type == "complaint" for c in citations):
        add(COMPLAINT_WARNING)

    if (
        any(c.relation_basis == "potentially_related_by_shared_component" for c in citations)
        or "potential_shared_component_association" in claim_types_used
    ):
        add(SHARED_COMPONENT_WARNING)

    if not sufficiency.graph_available:
        add(GRAPH_UNAVAILABLE_WARNING)

    if intent.asks_applicability and sufficiency.official_relation_count == 0:
        add(NO_AFFECTS_WARNING)

    if (intent.asks_official_recall or intent.asks_complaint) and not any(
        c.relation_basis == "recall_related_to_component" for c in citations
    ):
        add(RELATED_TO_COMPONENT_WARNING)

    if fallback_used:
        add(FALLBACK_WARNING)

    if repaired:
        add(REPAIRED_WARNING)

    if sufficiency.status == "partial":
        add(PARTIAL_EVIDENCE_WARNING)

    if intent.is_causal:
        add(CAUSAL_LIMITATION_WARNING)

    return warnings
