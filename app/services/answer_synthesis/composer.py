"""
Deterministic answer composer — Phase 7D.

Produces a safe, citation-backed answer directly from validated evidence.
No network, no database access, no tool execution, no dependence on raw
provider output. Deterministic: identical evidence + question always
produce identical output. Used when:

- DeterministicProvider is selected
- the external provider is unavailable or fails
- provider output cannot be safely validated
- only partial evidence supports a limited answer
"""

from __future__ import annotations

import re

from app.services.answer_synthesis.guarded_models import EvidenceSufficiencyResult, GuardedCitation
from app.services.answer_synthesis.models import ProviderClaim
from app.services.answer_synthesis.policy import QuestionIntent

MAX_COMPOSED_CLAIMS = 8
MAX_SNIPPET_CHARS = 220

_UNTRUSTED_EVIDENCE_PATTERNS = (
    r"\bignore (?:all |any |the |system |previous |prior )*instructions\b",
    r"\b(?:reveal|show|print|return) (?:the |your |my )*system prompt\b",
    r"\b(?:reveal|show|print|return) (?:the |your |my )*(?:database|db) credentials\b",
    r"\bcite[- ]fake[-\w]*\b",
)


def _contains_untrusted_instruction(text: str) -> bool:
    return any(
        re.search(pattern, text or "", flags=re.IGNORECASE)
        for pattern in _UNTRUSTED_EVIDENCE_PATTERNS
    )


def compose_answer(
    question: str,
    citations: list[GuardedCitation],
    sufficiency: EvidenceSufficiencyResult,
    intent: QuestionIntent,
) -> tuple[str, list[ProviderClaim]]:
    """Compose a deterministic answer + claims from validated evidence only."""
    ordered = sorted(citations, key=lambda c: c.citation_id)

    if not ordered:
        text = "No supporting evidence was available to answer this question."
        return text, [ProviderClaim(text=text, claim_type="data_limitation", citation_ids=[])]

    complaint_citations = [c for c in ordered if c.source_type == "complaint"]
    recall_citations = [c for c in ordered if c.source_type == "recall"]
    sql_citations = [c for c in ordered if c.source_type == "sql_result"]
    official_relations = {
        c.source_record_key: c
        for c in ordered
        if c.relation_basis == "official_recall_affects_vehicle"
    }
    official_recall_keys = set(official_relations)
    shared_citations = [c for c in recall_citations if c.relation_basis == "potentially_related_by_shared_component"]

    claims: list[ProviderClaim] = []
    lines: list[str] = []

    def add(claim: ProviderClaim, line: str) -> bool:
        if len(claims) >= MAX_COMPOSED_CLAIMS:
            return False
        claims.append(claim)
        lines.append(line)
        return True

    for c in recall_citations:
        is_applicable = c.source_record_key in official_recall_keys and c.relation_basis != "potentially_related_by_shared_component"
        if is_applicable:
            text = f"Official recall {c.source_record_key} applies to this vehicle per NHTSA records: {c.text_span[:MAX_SNIPPET_CHARS]}".strip()
            relation_citation = official_relations[c.source_record_key]
            citation_ids = list(dict.fromkeys([c.citation_id, relation_citation.citation_id]))
            add(ProviderClaim(text=text, claim_type="official_recall_applicability", citation_ids=citation_ids), text)
        elif c.relation_basis != "potentially_related_by_shared_component":
            text = f"Recall record {c.source_record_key} exists in public NHTSA data: {c.text_span[:MAX_SNIPPET_CHARS]}".strip()
            add(ProviderClaim(text=text, claim_type="official_recall", citation_ids=[c.citation_id]), text)

    for c in complaint_citations:
        if _contains_untrusted_instruction(c.text_span):
            text = (
                f"A complaint record ({c.source_record_key}) was retrieved, but instruction-like "
                "text inside the record was treated as untrusted evidence."
            )
        else:
            text = f"A complaint record ({c.source_record_key}) reports: {c.text_span[:MAX_SNIPPET_CHARS]}".strip()
        add(ProviderClaim(text=text, claim_type="complaint_observation", citation_ids=[c.citation_id]), text)

    for c in sql_citations:
        text = f"SQL analytics result ({c.source_record_key}): {c.text_span[:MAX_SNIPPET_CHARS + 100]}".strip()
        add(ProviderClaim(text=text, claim_type="sql_fact", citation_ids=[c.citation_id]), text)

    if shared_citations and complaint_citations and len(claims) < MAX_COMPOSED_CLAIMS:
        shared = shared_citations[0]
        complaint = complaint_citations[0]
        text = (
            f"Complaint record {complaint.source_record_key} and recall {shared.source_record_key} "
            f"share a component; this is a potential association only, not causal or official."
        )
        add(
            ProviderClaim(
                text=text,
                claim_type="potential_shared_component_association",
                citation_ids=[complaint.citation_id, shared.citation_id],
            ),
            text,
        )

    if intent.asks_official_recall and not recall_citations and complaint_citations:
        text = (
            "No official recall record was found in the available evidence for this vehicle; "
            "only complaint records are available."
        )
        add(ProviderClaim(text=text, claim_type="data_limitation", citation_ids=[]), text)

    if intent.is_causal:
        text = "Causality between complaints and any recall cannot be established from the available evidence."
        add(ProviderClaim(text=text, claim_type="data_limitation", citation_ids=[]), text)

    if not lines:
        text = "No supporting evidence was available to answer this question."
        return text, [ProviderClaim(text=text, claim_type="data_limitation", citation_ids=[])]

    return "\n".join(lines), claims[:MAX_COMPOSED_CLAIMS]
