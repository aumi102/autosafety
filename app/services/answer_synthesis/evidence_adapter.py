"""
Evidence adapter — Phase 7D.

Converts a Phase 7B/7C EvidenceBundle into final, citation-capable
GuardedCitation objects for Phase 7D validation and synthesis.

Deterministic ordering and deduplication. Preserves source_record_key,
source_entity_id, tool_name, retrieval score, and relation_basis.
Strips everything else (no metadata dict, no raw vectors, no SQL/Cypher,
no credentials) — only the fields on GuardedCitation ever leave this
module.

Some Phase 7B tool adapters do not assign a citation_id to every
EvidenceItem (only GraphRAG chunks and official graph_evidence_tool
recalls do). SQL results, shared-component recalls, graph paths, and
vehicle resolution items reach this adapter with citation_id=None. To
keep every evidence item citation-capable without modifying Phase 7B/7C
files, this module derives a stable citation_id from the item's own
`evidence_type` and `source_record_key` (`cite-{evidence_type}-{key}`)
when one is missing.

Note: this is derived from `evidence_type` (the EvidenceItem field), not
from the pre-existing `evidence_id` string — graph-path evidence items
carry `evidence_type="graph_path"` but their `evidence_id` is built from
a different internal source-type label, which would otherwise collide
with an unrelated recall-existence citation for the same record and
silently drop the AFFECTS relation_basis.
"""

from __future__ import annotations

import re

from app.services.answer_synthesis.tools.base import EvidenceBundle, EvidenceItem
from app.services.answer_synthesis.guarded_models import GuardedCitation

MAX_TEXT_SPAN_CHARS = 500

_UNTRUSTED_INSTRUCTION_PATTERNS = (
    r"\bignore (?:all |any |the |system |previous |prior )*instructions\b",
    r"\b(?:reveal|show|print|return) (?:the |your |my )*system prompt\b",
    r"\b(?:reveal|show|print|return) (?:the |your |my )*(?:database|db) credentials\b",
    r"\bcite[- ]fake[-\w]*\b",
)


def _public_text_span(text: str) -> str:
    """Redact instruction-like content from final public citations."""
    bounded = (text or "")[:MAX_TEXT_SPAN_CHARS]
    if any(
        re.search(pattern, bounded, flags=re.IGNORECASE)
        for pattern in _UNTRUSTED_INSTRUCTION_PATTERNS
    ):
        return "[Instruction-like content redacted from untrusted evidence.]"
    return bounded


def _derive_citation_id(item: EvidenceItem) -> str:
    if item.citation_id:
        return item.citation_id
    if item.relation_basis:
        return f"cite-{item.evidence_type}-{item.relation_basis}-{item.source_record_key}"
    return f"cite-{item.evidence_type}-{item.source_record_key}"


def adapt_evidence(bundle: EvidenceBundle) -> list[GuardedCitation]:
    """
    Adapt bundle evidence items into GuardedCitation objects.

    Ordering is deterministic (input item order). Deduplicated by the
    final citation_id — first occurrence wins, matching the dedup
    semantics already applied upstream by EvidenceBundleBuilder.
    """
    seen: dict[str, GuardedCitation] = {}
    for item in bundle.items:
        citation_id = _derive_citation_id(item)
        if citation_id in seen:
            continue
        seen[citation_id] = GuardedCitation(
            citation_id=citation_id,
            source_type=item.evidence_type,
            source_record_key=item.source_record_key,
            source_entity_id=item.source_entity_id,
            title=item.citation_label or f"{item.evidence_type} {item.source_record_key}".strip(),
            source_url=None,
            text_span=_public_text_span(item.text),
            retrieval_score=item.score,
            relation_basis=item.relation_basis,
            tool_name=item.tool_name,
        )
    return list(seen.values())


def graph_availability(bundle: EvidenceBundle) -> bool:
    """
    True unless the mandatory GraphRAG base retrieval reported
    neo4j_available=False, or the base retrieval call itself failed.
    """
    for call in bundle.tool_calls:
        if call.tool_name != "graphrag_retrieval_tool":
            continue
        if not call.success or call.data is None:
            return False
        return bool(call.data.get("neo4j_available", True))
    return True


def tool_call_stats(bundle: EvidenceBundle) -> tuple[int, int]:
    """Return (total_tool_calls, failed_tool_calls)."""
    total = len(bundle.tool_calls)
    failed = sum(1 for c in bundle.tool_calls if not c.success)
    return total, failed


def has_only_failed_tool_evidence(bundle: EvidenceBundle) -> bool:
    """True when tool calls were attempted, produced no evidence, and all failed."""
    total, failed = tool_call_stats(bundle)
    return total > 0 and failed == total and len(bundle.items) == 0


def has_recall_component_relation(citations: list[GuardedCitation]) -> bool:
    """
    True if any citation carries an explicit RELATED_TO_COMPONENT-derived
    relation. In the current corpus this is always False (Phase 5
    limitation: RELATED_TO_COMPONENT = 0) — surfaced as a warning trigger,
    not treated as an error.
    """
    return any(
        c.relation_basis in ("recall_related_to_component",)
        for c in citations
    )
