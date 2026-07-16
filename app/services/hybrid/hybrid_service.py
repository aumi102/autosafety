"""
Hybrid service — orchestrate SQL analytics + graph retrieval for Phase 4.

Coordinates Phase 2 SQL analytics with Phase 3 graph retrieval.
Returns answer-contract-compatible response with SQL results + graph evidence.
No LLM calls.
"""

from __future__ import annotations

import time
import logging
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.services.sql_analytics.service import answer_sql_analytics_question
from app.services.graph import (
    get_vehicle_neighborhood as graph_vehicle_neighborhood,
    get_vehicle_recall_paths as graph_vehicle_recall_paths,
    get_vehicle_component_evidence as graph_vehicle_component_evidence,
    get_vehicle_shared_component_recalls as graph_vehicle_shared_component_recalls,
    get_graph_status,
)
from app.services.hybrid.hybrid_parser import (
    parse_hybrid_question,
    is_hybrid_question,
)
from app.services.hybrid.hybrid_models import (
    HybridAnswerResult,
    GraphEvidenceItem,
    HYBRID_INTENTS,
)
from app.services.hybrid.answer_composer import compose_hybrid_answer

logger = logging.getLogger(__name__)


def answer_hybrid_question(question: str) -> dict:
    """
    Answer a hybrid SQL + graph question.

    Flow:
    1. Parse question to detect hybrid intent
    2. Run SQL analytics (Phase 2)
    3. Run graph retrieval (Phase 3) if vehicle extracted
    4. Compose answer with SQL + graph evidence

    Returns dict conforming to answer_contract.md with intent="hybrid".
    """
    start_time = time.time()
    tool_calls = 0

    # Step 1: parse
    tool_calls += 1
    intent = parse_hybrid_question(question)

    # Step 2: run SQL analytics
    tool_calls += 1
    sql_result = _run_sql_analytics(question)

    # Step 3: run graph retrieval if applicable
    graph_evidence: list[GraphEvidenceItem] = []
    neo4j_available = False
    neo4j_error: Optional[str] = None

    if intent.hybrid_type in HYBRID_INTENTS and intent.vehicle_extracted:
        tool_calls += 1
        graph_evidence, neo4j_available, neo4j_error = _run_graph_retrieval(
            intent
        )

    # Step 4: compose answer
    tool_calls += 1
    hybrid_result = HybridAnswerResult(
        sql_response=sql_result,
        graph_evidence=graph_evidence,
        neo4j_available=neo4j_available,
        neo4j_error=neo4j_error,
    )

    response = compose_hybrid_answer(
        result=hybrid_result,
        question=question,
        tool_calls=tool_calls,
        start_time=start_time,
    )

    return response.to_dict()


def _run_sql_analytics(question: str) -> dict:
    """Run Phase 2 SQL analytics and return as dict."""
    try:
        session = _get_pg_session()
        try:
            response = answer_sql_analytics_question(session, question)
            return response.to_dict()
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"SQL analytics failed: {e}")
        # Return error response
        return {
            "run_id": "",
            "intent": "sql",
            "answer": {"summary": f"SQL analytics error: {e}", "sections": []},
            "sql": {
                "used": False,
                "query": None,
                "columns": None,
                "rows": None,
                "row_count": 0,
                "execution_ms": 0,
                "validated": False,
            },
            "evidence": {"citations": [], "graph_paths": []},
            "warnings": ["SQL analytics failed."],
            "confidence": {"label": "low", "score": 0.1, "reasons": [f"SQL error: {e}"]},
            "debug": {"tool_call_count": 1, "latency_ms": 0},
        }


def _run_graph_retrieval(
    intent,
) -> tuple[list[GraphEvidenceItem], bool, Optional[str]]:
    """
    Retrieve graph evidence using make/model/year from parsed intent.

    Returns (graph_evidence, neo4j_available, error).
    """
    try:
        # Check Neo4j connectivity first
        status = get_graph_status()
        if not status.neo4j_connected:
            return [], False, "Neo4j not connected"

        # Get vehicle info from parsed intent
        vehicle = intent.vehicle
        if not vehicle:
            return [], True, None

        make = vehicle.normalized_make
        model = vehicle.normalized_model
        year = vehicle.model_year

        if not year:
            return [], True, None

        evidence: list[GraphEvidenceItem] = []

        # Look up vehicle_id from PostgreSQL
        vehicle_id = _find_vehicle_id(make, model, year)
        if not vehicle_id:
            return [], True, None

        # 1. Get recall paths (Phase 4: recalls via AFFECTS)
        recall_result = graph_vehicle_recall_paths(vehicle_id)
        if recall_result:
            item = GraphEvidenceItem(
                path_type=recall_result.path_type,
                nodes=[],
                relationships=[],
                recall_campaigns=[r.campaign_number for r in recall_result.recalls],
                relation_basis="official_recall_affects_vehicle",
                summary=_summarize_recall_paths(recall_result),
            )
            evidence.append(item)

        # 2. Get component evidence (Phase 5: complaints via MENTIONS_COMPONENT)
        comp_evidence = graph_vehicle_component_evidence(vehicle_id)
        if comp_evidence and comp_evidence.complaint_components:
            item = GraphEvidenceItem(
                path_type=comp_evidence.path_type,
                nodes=[],
                relationships=[],
                recall_campaigns=[],
                relation_basis="complaint_mentions_component",
                summary=_summarize_component_evidence(comp_evidence),
            )
            evidence.append(item)

        # 3. Get shared component recall paths (Phase 5: recalls via RELATED_TO_COMPONENT)
        shared_result = graph_vehicle_shared_component_recalls(vehicle_id)
        if shared_result and shared_result.shared_recalls:
            item = GraphEvidenceItem(
                path_type=shared_result.path_type,
                nodes=[],
                relationships=[],
                recall_campaigns=[r.get("campaign_number", "") for r in shared_result.shared_recalls if r.get("campaign_number")],
                relation_basis="potentially_related_by_shared_component",
                summary=_summarize_shared_component_recalls(shared_result),
            )
            evidence.append(item)

        return evidence, True, None

    except Exception as e:
        logger.warning(f"Graph retrieval failed: {e}")
        return [], False, str(e)


def _find_vehicle_id(make: str, model: str, year: int) -> Optional[str]:
    """Look up a vehicle's PostgreSQL UUID from make/model/year."""
    try:
        session = _get_pg_session()
        try:
            from app.db.models.domain import Vehicle
            row = session.query(Vehicle).filter(
                Vehicle.normalized_make == make,
                Vehicle.normalized_model == model,
                Vehicle.model_year == year,
            ).first()
            if row:
                return str(row.id)
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"Vehicle lookup failed: {e}")
    return None


def _summarize_recall_paths(recall_result) -> str:
    """Build summary text for recall paths."""
    if not recall_result.recalls:
        return "No recalls found for this vehicle in the graph."
    count = len(recall_result.recalls)
    campaigns = [r.campaign_number for r in recall_result.recalls[:5]]
    parts = [f"{count} recall(s) potentially related by vehicle/component."]
    if campaigns:
        parts.append(f"Campaigns: {', '.join(campaigns)}")
    return " ".join(parts)


def _summarize_component_evidence(comp_evidence) -> str:
    """Build summary text for component evidence."""
    if not comp_evidence.complaint_components:
        return "No complaint-component links found in the graph."
    count = comp_evidence.complaint_count
    components = comp_evidence.components[:5]
    parts = [f"{count} complaint(s) linked to components via MENTIONS_COMPONENT."]
    if components:
        parts.append(f"Components: {', '.join(components)}")
    return " ".join(parts)


def _summarize_shared_component_recalls(shared_result) -> str:
    """Build summary text for shared component recalls."""
    if not shared_result.shared_recalls:
        return "No recalls linked via shared components in the graph."
    count = shared_result.recall_count
    recalls = [r.get("campaign_number", "") for r in shared_result.shared_recalls[:5] if r.get("campaign_number")]
    parts = [f"{count} recall(s) potentially related by shared component."]
    if recalls:
        parts.append(f"Campaigns: {', '.join(recalls)}")
    return " ".join(parts)


def _get_pg_session():
    """Create synchronous PostgreSQL session."""
    settings = get_settings()
    db_url = settings.DATABASE_URL_SYNC
    if "postgresql+asyncpg" in db_url:
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(db_url, echo=False)
    Session = sessionmaker(bind=engine)
    return Session()


def is_hybrid_question_wrapper(question: str) -> bool:
    """Quick wrapper for is_hybrid_question."""
    return is_hybrid_question(question)
