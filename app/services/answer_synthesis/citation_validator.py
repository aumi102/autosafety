"""
Citation and claim validator — Phase 7D.

The application is the final authority on claim safety. This module
validates every provider-produced (or deterministically composed) claim
against the actual evidence bundle, never trusting the provider's own
claim_type or citation_ids.

Responsibilities:
- Reject invented citation IDs
- Reject unknown/unmappable claim types
- Enforce claim-type-specific evidence requirements (official recall,
  applicability, complaint, SQL fact, shared-component)
- Reject unsupported causal language; safely neutralize repairable
  causal phrasing
- Normalize duplicate citation IDs and duplicate claims
- Compute citation coverage
"""

from __future__ import annotations

import re

from app.services.answer_synthesis.guarded_models import (
    CitationValidationResult,
    GuardedCitation,
    GuardedClaim,
)
from app.services.answer_synthesis.models import ProviderClaim
from app.services.answer_synthesis.policy import UNCITED_ALLOWED_CLAIM_TYPES, normalize_claim_type

MAX_INPUT_CLAIMS = 20
MAX_OUTPUT_CLAIMS = 8
MAX_CLAIM_TEXT_CHARS = 2000

# Unsafe causal language — never repairable, claim is rejected outright.
_UNSAFE_CAUSAL_PATTERNS = [
    r"\bproves\b",
    r"\bconfirms the defect\b",
    r"\bdefinitely unsafe\b",
    r"\b(?:this|the) vehicle is unsafe\b",
    r"\bresponsible for\b",
]

# Provider claims containing instruction-following or secret-exfiltration
# language are never accepted as factual answer text, even when they attach a
# real complaint citation. Public complaint narratives are untrusted input.
_UNTRUSTED_INSTRUCTION_PATTERNS = [
    r"\bignore (?:all |any |the |system |previous |prior )*instructions\b",
    r"\b(?:reveal|show|print|return) (?:the |your |my )*system prompt\b",
    r"\b(?:reveal|show|print|return) (?:the |your |my )*(?:database|db) credentials\b",
    r"\bcite[- ]fake[-\w]*\b",
]

# Repairable causal language — safely neutralized to a supported, non-causal phrasing.
_REPAIRABLE_CAUSAL = [
    (r"\bwas caused by\b", "is associated with"),
    (r"\bwere caused by\b", "are associated with"),
    (r"\bcaused by\b", "associated with"),
    (r"\bcauses\b", "is associated with"),
    (r"\bcaused\b", "is associated with"),
    (r"\bled to\b", "is associated with"),
    (r"\bresulted in\b", "is associated with"),
    (r"\bdue to\b", "associated with"),
]


def _apply_causal_guard(text: str) -> tuple[bool, str, list[str]]:
    """Returns (ok, possibly-rewritten text, messages). ok=False means reject."""
    messages: list[str] = []
    for pattern in _UNTRUSTED_INSTRUCTION_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return False, text, [f"untrusted instruction language rejected ('{pattern}')"]
    for pattern in _UNSAFE_CAUSAL_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return False, text, [f"unsupported causal language rejected ('{pattern}')"]

    new_text = text
    for pattern, replacement in _REPAIRABLE_CAUSAL:
        if re.search(pattern, new_text, flags=re.IGNORECASE):
            new_text = re.sub(pattern, replacement, new_text, flags=re.IGNORECASE)
            messages.append(f"causal phrase neutralized ('{pattern}' -> '{replacement}')")

    return True, new_text, messages


def _check_claim_type_support(
    claim_type: str,
    citations: list[GuardedCitation],
    text: str,
) -> tuple[bool, list[str], str | None, str]:
    """
    Returns (ok, messages, remapped_claim_type_or_None, official_status).

    ok=False means the claim cannot be supported and must be rejected.
    remapped_claim_type is set only for safe downgrades (e.g. applicability
    -> existence when AFFECTS evidence is absent).
    """
    types_present = {c.source_type for c in citations}

    if claim_type == "complaint_observation":
        if "complaint" not in types_present:
            return False, ["complaint_observation requires a complaint citation"], None, "none"
        return True, [], None, "none"

    if claim_type == "complaint_component_observation":
        complaint_citations = [c for c in citations if c.source_type == "complaint"]
        if not complaint_citations:
            return False, ["complaint_component_observation requires a complaint citation"], None, "none"
        if not any(c.text_span for c in complaint_citations):
            return False, ["complaint_component_observation requires component evidence"], None, "none"
        return True, [], None, "none"

    if claim_type == "official_recall":
        if "recall" not in types_present:
            return False, ["official_recall requires a recall citation"], None, "none"
        return True, [], None, "existence"

    if claim_type == "official_recall_applicability":
        recall_citations = [c for c in citations if c.source_type == "recall"]
        if not recall_citations:
            return False, ["official_recall_applicability requires a recall citation"], None, "none"
        affects_keys = {
            c.source_record_key for c in citations
            if c.relation_basis == "official_recall_affects_vehicle"
        }
        matched = any(c.source_record_key in affects_keys for c in recall_citations)
        if not matched:
            return (
                True,
                ["downgraded official_recall_applicability to official_recall — no matching "
                 "official_recall_affects_vehicle relation in cited evidence"],
                "official_recall",
                "existence",
            )
        return True, [], None, "applicability"

    if claim_type == "potential_shared_component_association":
        if "complaint" not in types_present or "recall" not in types_present:
            return (
                False,
                ["potential_shared_component_association requires both complaint and recall citations"],
                None,
                "none",
            )
        return True, [], None, "potential"

    if claim_type == "sql_fact":
        sql_citations = [c for c in citations if c.source_type == "sql_result"]
        if not sql_citations:
            return False, ["sql_fact requires SQL evidence"], None, "none"
        combined_text = " ".join(c.text_span for c in sql_citations)
        numbers_in_claim = set(re.findall(r"\d+", text))
        numbers_in_evidence = set(re.findall(r"\d+", combined_text))
        unmatched = numbers_in_claim - numbers_in_evidence
        if unmatched:
            return False, [f"sql_fact contains figures not present in cited SQL evidence: {sorted(unmatched)}"], None, "none"
        return True, [], None, "none"

    if claim_type == "data_limitation":
        return True, [], None, "none"

    if claim_type == "synthesis_summary":
        if not citations:
            return False, ["synthesis_summary must cite underlying factual sources"], None, "none"
        return True, [], None, "none"

    return False, [f"unhandled claim type '{claim_type}'"], None, "none"


def validate_and_build_claims(
    provider_claims: list[ProviderClaim],
    citations: list[GuardedCitation],
) -> tuple[list[GuardedClaim], CitationValidationResult]:
    """
    Validate provider (or deterministically composed) claims against
    actual evidence. The application, not the provider, decides claim
    type, citation validity, and causal-language safety.
    """
    citation_by_id = {c.citation_id: c for c in citations}
    valid_ids = set(citation_by_id)

    guarded_claims: list[GuardedClaim] = []
    messages: list[str] = []
    unsupported_claim_ids: list[str] = []
    invalid_citation_ids_seen: list[str] = []
    repaired_any = False

    factual_claim_count = 0
    cited_claim_count = 0

    seen_signatures: set[tuple[str, str, tuple[str, ...]]] = set()

    for idx, pc in enumerate(provider_claims[:MAX_INPUT_CLAIMS]):
        claim_id = f"claim-{idx + 1}"
        raw_type = (pc.claim_type or "").strip()
        text = (pc.text or "").strip()[:MAX_CLAIM_TEXT_CHARS]

        signature = (raw_type, text.lower(), tuple(sorted(pc.citation_ids or [])))
        if signature in seen_signatures:
            messages.append(f"{claim_id}: duplicate claim skipped")
            continue
        seen_signatures.add(signature)

        claim_messages: list[str] = []

        claim_type = normalize_claim_type(raw_type)
        if claim_type is None:
            messages.append(f"{claim_id}: unknown claim type '{raw_type}' rejected")
            unsupported_claim_ids.append(claim_id)
            continue
        if claim_type != raw_type:
            claim_messages.append(f"claim type '{raw_type}' mapped to '{claim_type}'")
            repaired_any = True

        seen_cids: list[str] = []
        unknown_found = False
        duplicate_found = False
        for cid in pc.citation_ids or []:
            if cid in seen_cids:
                duplicate_found = True
                continue
            if cid not in valid_ids:
                unknown_found = True
                invalid_citation_ids_seen.append(cid)
                continue
            seen_cids.append(cid)
        if unknown_found:
            claim_messages.append("removed unknown citation id(s) not present in evidence")
            repaired_any = True
        if duplicate_found:
            claim_messages.append("removed duplicate citation id(s)")
            repaired_any = True

        cited_citations = [citation_by_id[c] for c in seen_cids]
        is_factual = claim_type not in UNCITED_ALLOWED_CLAIM_TYPES

        if is_factual:
            factual_claim_count += 1
            if seen_cids:
                cited_claim_count += 1
            if not seen_cids:
                messages.append(f"{claim_id}: rejected — factual claim has no valid citation")
                unsupported_claim_ids.append(claim_id)
                continue

        ok, type_messages, remapped_type, official_status = _check_claim_type_support(
            claim_type, cited_citations, text
        )
        claim_messages.extend(type_messages)
        if not ok:
            messages.append(f"{claim_id}: rejected — {'; '.join(type_messages) if type_messages else 'insufficient supporting evidence'}")
            unsupported_claim_ids.append(claim_id)
            continue
        if remapped_type and remapped_type != claim_type:
            previous_claim_type = claim_type
            claim_type = remapped_type
            if previous_claim_type == "official_recall_applicability" and claim_type == "official_recall":
                recall_key = next(
                    (c.source_record_key for c in cited_citations if c.source_type == "recall"),
                    "unknown",
                )
                text = (
                    f"Recall record {recall_key} exists in the supplied public evidence; "
                    "applicability to this vehicle was not verified."
                )
            repaired_any = True

        causal_ok, new_text, causal_messages = _apply_causal_guard(text)
        if not causal_ok:
            messages.append(f"{claim_id}: rejected — {causal_messages[0]}")
            unsupported_claim_ids.append(claim_id)
            continue
        if causal_messages:
            claim_messages.extend(causal_messages)
            text = new_text
            repaired_any = True

        validation_status = "repaired" if claim_messages else "accepted"
        support_level = "supported" if (seen_cids or not is_factual) else "partial"

        guarded_claims.append(GuardedClaim(
            claim_id=claim_id,
            text=text,
            claim_type=claim_type,
            citation_ids=seen_cids,
            support_level=support_level,
            official_status=official_status,
            validation_status=validation_status,
            validation_messages=claim_messages,
        ))

    guarded_claims = guarded_claims[:MAX_OUTPUT_CLAIMS]

    valid_citation_count = len({cid for cl in guarded_claims for cid in cl.citation_ids})
    invalid_citation_count = len(set(invalid_citation_ids_seen))
    citation_coverage = (cited_claim_count / factual_claim_count) if factual_claim_count else (
        1.0 if guarded_claims else 0.0
    )

    result = CitationValidationResult(
        valid=len(guarded_claims) > 0,
        factual_claim_count=factual_claim_count,
        cited_claim_count=cited_claim_count,
        valid_citation_count=valid_citation_count,
        invalid_citation_count=invalid_citation_count,
        citation_coverage=citation_coverage,
        unsupported_claim_ids=unsupported_claim_ids,
        invalid_citation_ids=sorted(set(invalid_citation_ids_seen)),
        repaired=repaired_any,
        messages=messages,
    )
    return guarded_claims, result
