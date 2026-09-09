"""
Hybrid question parser for Phase 4.

Reuses Phase 2 parser for base intent classification,
then extends for hybrid SQL+graph question patterns.
"""

from __future__ import annotations

from app.services.hybrid.hybrid_models import HYBRID_INTENTS, HybridIntent
from app.services.sql_analytics.question_parser import ParsedQuestion, parse_question

# Hybrid keyword patterns — presence of these activates graph retrieval
GRAPH_KEYWORDS = [
    "related recall", "related recalls", "recall evidence",
    "recall paths", "recall link", "recalls linked",
    "associated recall", "associated recalls",
    "connected recall", "graph evidence",
    "recall connection", "complaint and recall",
    "complaints and recalls", "recall pattern",
]

SQL_ONLY_KEYWORDS = [
    "only", "just sql", "no graph", "skip graph",
    "without recalls", "without graph",
]


def parse_hybrid_question(question: str) -> HybridIntent:
    """
    Parse a question into hybrid intent.

    Steps:
    1. Run Phase 2 parser to get base SQL intent + vehicle entities
    2. Check for hybrid keywords indicating graph retrieval request
    3. Return HybridIntent with sql_intent, hybrid_type, wants_graph
    """
    q = question.lower().strip()

    # Step 1: base parse
    parsed: ParsedQuestion = parse_question(q)
    sql_intent = parsed.intent
    vehicle_extracted = parsed.vehicle is not None

    # Step 2: detect graph request
    wants_graph = _detect_graph_request(q)

    # Step 3: classify hybrid type
    hybrid_type = _classify_hybrid_type(q, sql_intent, wants_graph, vehicle_extracted)

    # Confidence: higher when vehicle extracted + graph keywords present
    confidence = 0.3
    if sql_intent not in ("unknown", "clarification_needed"):
        if vehicle_extracted and wants_graph:
            confidence = 0.8
        elif vehicle_extracted or wants_graph:
            confidence = 0.6
        else:
            confidence = 0.4

    return HybridIntent(
        sql_intent=sql_intent,
        hybrid_type=hybrid_type,
        wants_graph=wants_graph,
        wants_sql=True,
        vehicle_extracted=vehicle_extracted,
        confidence=confidence,
        vehicle=parsed.vehicle,
    )


def _detect_graph_request(text: str) -> bool:
    """Check if question requests graph/recall evidence."""
    # Explicit opt-out
    for kw in SQL_ONLY_KEYWORDS:
        if kw in text:
            return False

    # Graph/recall evidence keywords
    for kw in GRAPH_KEYWORDS:
        if kw in text:
            return True

    return False


def _classify_hybrid_type(
    text: str,
    sql_intent: str,
    wants_graph: bool,
    vehicle_extracted: bool,
) -> str:
    """
    Classify the specific hybrid question type.

    Returns a string identifier for the hybrid query pattern.
    """
    if not wants_graph:
        return "sql_only"

    # "top component with most complaints and related recalls"
    if any(kw in text for kw in [
        "top complaint component", "most complaints",
        "top component", "component with most",
    ]):
        if any(kw in text for kw in ["recall", "related", "linked", "connection"]):
            return "top_complaint_component_with_related_recalls"

    # "complaints and recall evidence"
    if any(kw in text for kw in [
        "complaint and recall", "complaints and recall",
        "recall evidence", "complaints and recalls",
        "complaint and recalls",
    ]):
        return "complaint_count_with_related_recalls"

    # "does X have complaints and recalls"
    if any(kw in text for kw in ["does", "have"]) and \
       "complaint" in text and "recall" in text:
        return "vehicle_recall_evidence"

    # "complaints by component with graph evidence"
    if any(kw in text for kw in [
        "by component", "component breakdown", "component complaints",
    ]):
        return "component_complaints_with_graph_evidence"

    # "vehicles with most complaints and recall evidence"
    if sql_intent == "vehicles_by_complaint_count":
        if any(kw in text for kw in ["recall", "graph", "evidence"]):
            return "vehicles_complaint_count_with_recall_evidence"
        return "sql_only"

    # Generic hybrid fallback
    if wants_graph and vehicle_extracted:
        return "vehicle_recall_evidence"

    return "unsupported"


def is_hybrid_question(question: str) -> bool:
    """
    Quick check: does this question look like a hybrid question?

    Used by chat endpoint to decide routing.
    """
    intent = parse_hybrid_question(question)
    return (
        intent.hybrid_type not in ("sql_only", "unsupported")
        and intent.hybrid_type in HYBRID_INTENTS
    )
