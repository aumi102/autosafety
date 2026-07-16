"""
Graph service — high-level orchestration for Phase 3.

Exposes: schema setup, graph build, status, and retrieval.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.services.graph.neo4j_client import Neo4jClient, verify_connectivity
from app.services.graph.graph_schema import setup_schema
from app.services.graph.graph_builder import build_graph
from app.services.graph.graph_queries import (
    get_vehicle_neighborhood,
    get_recall_paths_for_vehicle,
    get_component_evidence_for_vehicle,
    get_shared_component_recall_paths,
)
from app.services.graph.graph_models import (
    GraphBuildStats,
    GraphStatus,
    GraphSchemaSetupResult,
    GraphNodeStats,
    GraphRelStats,
    VehicleNeighborhood,
    RecallPathResult,
    ComponentEvidence,
)
from app.db.models.domain import Vehicle, Complaint, Recall, Component

logger = logging.getLogger(__name__)


def _pg_engine():
    settings = get_settings()
    db_url = settings.DATABASE_URL_SYNC
    if "postgresql+asyncpg" in db_url:
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    return create_engine(db_url, echo=False)


def _pg_counts() -> dict[str, int]:
    """Get PostgreSQL entity counts."""
    try:
        engine = _pg_engine()
        Session = sessionmaker(bind=engine)
        session = Session()
        with session:
            return {
                "vehicles": session.query(func.count(Vehicle.id)).scalar() or 0,
                "complaints": session.query(func.count(Complaint.id)).scalar() or 0,
                "recalls": session.query(func.count(Recall.id)).scalar() or 0,
                "components": session.query(func.count(Component.id)).scalar() or 0,
            }
    except Exception as e:
        logger.warning(f"Could not count PostgreSQL rows: {e}")
        return {"vehicles": 0, "complaints": 0, "recalls": 0, "components": 0}


def setup_graph_schema() -> GraphSchemaSetupResult:
    """
    Create all Phase 3 constraints and indexes in Neo4j.
    Idempotent — safe to run multiple times.
    """
    try:
        client = Neo4jClient()
        try:
            constraints_created, indexes_created, errors = setup_schema(client.driver)
            return GraphSchemaSetupResult(
                success=len(errors) == 0,
                constraints_created=constraints_created,
                indexes_created=indexes_created,
                errors=errors,
            )
        finally:
            client.close()
    except Exception as e:
        logger.exception(f"Schema setup failed: {e}")
        return GraphSchemaSetupResult(
            success=False,
            constraints_created=0,
            indexes_created=0,
            errors=[f"Connection/setup error: {e}"],
        )


def build_graph_from_postgres(
    *,
    dry_run: bool = False,
    limit_vehicles: Optional[int] = None,
) -> GraphBuildStats:
    """
    Project PostgreSQL data into Neo4j.

    Args:
        dry_run: count records without writing to Neo4j
        limit_vehicles: process only N vehicles (ordered by make/model/year)

    Returns:
        GraphBuildStats with build statistics
    """
    try:
        engine = _pg_engine()
        Session = sessionmaker(bind=engine)
        pg_session = Session()
        neo4j_client = Neo4jClient()
        try:
            return build_graph(
                pg_session,
                neo4j_client,
                dry_run=dry_run,
                limit_vehicles=limit_vehicles,
            )
        finally:
            pg_session.close()
            neo4j_client.close()
    except Exception as e:
        logger.exception(f"Graph build failed: {e}")
        return GraphBuildStats(
            errors_count=1,
            errors=[f"Build fatal error: {e}"],
            dry_run=dry_run,
        )


def get_graph_status() -> GraphStatus:
    """
    Return current graph database status.

    Checks Neo4j connectivity, node/relationship counts,
    and PostgreSQL baseline counts for comparison.
    """
    neo4j_ok = False
    node_count = 0
    relationship_count = 0
    node_label_rows: list[dict] = []
    rel_type_rows: list[dict] = []
    error: Optional[str] = None

    try:
        client = Neo4jClient()
        try:
            neo4j_ok = True

            # Count nodes
            result = client.execute_single("MATCH (n) RETURN count(n) AS count")
            node_count = result["count"] if result else 0

            # Count relationships
            result = client.execute_single("MATCH ()-[r]->() RETURN count(r) AS count")
            relationship_count = result["count"] if result else 0

            # Node label distribution
            node_label_rows = client.execute(
                "MATCH (n) UNWIND labels(n) AS lbl "
                "RETURN lbl AS label, count(*) AS count ORDER BY count DESC"
            )

            # Relationship type distribution
            rel_type_rows = client.execute(
                "MATCH ()-[r]->() UNWIND type(r) AS rel_type "
                "RETURN rel_type, count(*) AS count ORDER BY count DESC"
            )
        finally:
            client.close()
    except Exception as e:
        error = str(e)
        logger.warning(f"Graph status check failed: {e}")

    pg_counts = _pg_counts()

    node_labels = [
        GraphNodeStats(label=row["label"], count=row["count"])
        for row in node_label_rows
    ]
    relationship_types = [
        GraphRelStats(
            type=row.get("rel_type") or row.get("type") or "",
            count=row["count"],
        )
        for row in rel_type_rows
    ]

    return GraphStatus(
        neo4j_connected=neo4j_ok,
        node_count=node_count,
        relationship_count=relationship_count,
        node_labels=node_labels,
        relationship_types=relationship_types,
        postgres_vehicle_count=pg_counts.get("vehicles", 0),
        postgres_complaint_count=pg_counts.get("complaints", 0),
        postgres_recall_count=pg_counts.get("recalls", 0),
        postgres_component_count=pg_counts.get("components", 0),
        error=error,
    )


def get_vehicle_neighborhood(vehicle_id: str) -> Optional[VehicleNeighborhood]:
    """
    Retrieve graph neighborhood for a vehicle by its UUID.

    First looks up the vehicle in PostgreSQL to get make/model/year,
    then queries the graph.
    """
    try:
        engine = _pg_engine()
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            vehicle = session.get(Vehicle, vehicle_id)
            if not vehicle:
                return None

            client = Neo4jClient()
            try:
                return get_vehicle_neighborhood(
                    client,
                    make=vehicle.normalized_make,
                    model=vehicle.normalized_model,
                    year=vehicle.model_year,
                    vehicle_id=str(vehicle.id),
                )
            finally:
                client.close()
        finally:
            session.close()
    except Exception as e:
        logger.exception(f"Vehicle neighborhood retrieval failed: {e}")
        return None


def get_vehicle_recall_paths(vehicle_id: str) -> Optional[RecallPathResult]:
    """
    Retrieve recall paths for a vehicle from the graph.

    Recalls are linked through AFFECTS → ModelYear and RELATED_TO_COMPONENT.
    This is potentially related — NOT official causality.
    """
    try:
        engine = _pg_engine()
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            vehicle = session.get(Vehicle, vehicle_id)
            if not vehicle:
                return None

            client = Neo4jClient()
            try:
                return get_recall_paths_for_vehicle(
                    client,
                    make=vehicle.normalized_make,
                    model=vehicle.normalized_model,
                    year=vehicle.model_year,
                    vehicle_id=str(vehicle.id),
                )
            finally:
                client.close()
        finally:
            session.close()
    except Exception as e:
        logger.exception(f"Recall path retrieval failed: {e}")
        return None


def get_vehicle_component_evidence(vehicle_id: str) -> Optional[ComponentEvidence]:
    """
    Retrieve component-level evidence for a vehicle from the graph.

    Returns complaints linked via MENTIONS_COMPONENT and shared recalls
    linked via RELATED_TO_COMPONENT.

    This is NOT causal — shared component links are potential associations only.
    """
    try:
        engine = _pg_engine()
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            vehicle = session.get(Vehicle, vehicle_id)
            if not vehicle:
                return None

            client = Neo4jClient()
            try:
                result = get_component_evidence_for_vehicle(
                    client,
                    make=vehicle.normalized_make,
                    model=vehicle.normalized_model,
                    year=vehicle.model_year,
                )
                if not result:
                    return None
                return ComponentEvidence(
                    make=result.get("make_name", ""),
                    model=result.get("model_name", ""),
                    year=result.get("model_year", 0),
                    vehicle_id=str(vehicle.id),
                    complaint_components=result.get("complaints", []),
                    components=[c.get("name", "") for c in result.get("components", [])],
                    complaint_count=result.get("complaint_count", 0),
                    shared_recalls=result.get("recalls", []),
                    recall_count=result.get("recall_count", 0),
                    path_type="complaint_mentions_component",
                )
            finally:
                client.close()
        finally:
            session.close()
    except Exception as e:
        logger.exception(f"Component evidence retrieval failed: {e}")
        return None


def get_vehicle_shared_component_recalls(vehicle_id: str) -> Optional[ComponentEvidence]:
    """
    Retrieve recalls shared through components for a vehicle.

    Finds recalls that AFFECT the vehicle and are RELATED_TO_COMPONENT
    components that complaints on this vehicle MENTIONS_COMPONENT.

    This is potentially_related — NOT official causality.
    """
    try:
        engine = _pg_engine()
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            vehicle = session.get(Vehicle, vehicle_id)
            if not vehicle:
                return None

            client = Neo4jClient()
            try:
                result = get_shared_component_recall_paths(
                    client,
                    make=vehicle.normalized_make,
                    model=vehicle.normalized_model,
                    year=vehicle.model_year,
                )
                if not result:
                    return None
                shared_recalls = result.get("recalls", []) or []
                shared_components = result.get("shared_components", []) or []
                return ComponentEvidence(
                    make=result.get("make_name", ""),
                    model=result.get("model_name", ""),
                    year=result.get("model_year", 0),
                    vehicle_id=str(vehicle.id),
                    complaint_components=[],
                    components=[c.get("name", "") for c in shared_components],
                    complaint_count=0,
                    shared_recalls=shared_recalls,
                    recall_count=len(shared_recalls),
                    path_type="potentially_related_by_shared_component",
                )
            finally:
                client.close()
        finally:
            session.close()
    except Exception as e:
        logger.exception(f"Shared component recalls retrieval failed: {e}")
        return None
