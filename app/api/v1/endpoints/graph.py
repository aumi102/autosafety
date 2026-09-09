"""Graph API endpoints — Phase 3."""


from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.security import verify_admin_token
from app.services.graph import (
    build_graph_from_postgres,
    get_graph_status,
    get_vehicle_component_evidence,
    get_vehicle_neighborhood,
    get_vehicle_recall_paths,
    setup_graph_schema,
)

router = APIRouter(tags=["graph"])


class GraphBuildRequest(BaseModel):
    dry_run: bool = False
    limit_vehicles: int | None = None


class GraphBuildResponse(BaseModel):
    success: bool
    stats: dict
    phase: str = "phase_3"


class GraphSchemaResponse(BaseModel):
    success: bool
    constraints_created: int
    indexes_created: int
    errors: list[str]
    phase: str = "phase_3"


class GraphStatusResponse(BaseModel):
    neo4j_connected: bool
    node_count: int
    relationship_count: int
    node_labels: list[dict]
    relationship_types: list[dict]
    postgres_vehicle_count: int
    postgres_complaint_count: int
    postgres_recall_count: int
    postgres_component_count: int
    last_build: dict | None = None
    error: str | None = None
    phase: str = "phase_3"


class GraphNeighborhoodResponse(BaseModel):
    make: str
    model: str
    year: int
    vehicle_id: str
    nodes: list[dict]
    edges: list[dict]
    complaint_count: int
    recall_count: int
    components: list[str]
    phase: str = "phase_3"


class GraphRecallPathsResponse(BaseModel):
    make: str
    model: str
    year: int
    vehicle_id: str
    recalls: list[dict]
    path_type: str
    phase: str = "phase_3"


class ComponentEvidenceResponse(BaseModel):
    make: str
    model: str
    year: int
    vehicle_id: str
    complaint_components: list[dict]
    components: list[str]
    complaint_count: int
    shared_recalls: list[dict]
    recall_count: int
    path_type: str
    phase: str = "phase_5"


class GraphHealthResponse(BaseModel):
    neo4j_connected: bool
    phase: str = "phase_3"


@router.get("/health", response_model=GraphHealthResponse)
def graph_health():
    """
    Check Neo4j connectivity.

    Returns whether the graph database is reachable.
    """
    from app.services.graph.neo4j_client import verify_connectivity
    connected = verify_connectivity()
    return GraphHealthResponse(neo4j_connected=connected)


@router.post(
    "/schema/setup",
    response_model=GraphSchemaResponse,
    dependencies=[Depends(verify_admin_token)],
)
def graph_schema_setup():
    """
    Create Neo4j schema constraints and indexes.

    Idempotent — safe to run multiple times.
    Creates uniqueness constraints on key properties (VehicleMake.normalized_name,
    VehicleModel.key, ModelYear.key, Component.normalized_name,
    Complaint.odi_number, Recall.campaign_number) plus performance indexes.
    """
    result = setup_graph_schema()
    return GraphSchemaResponse(
        success=result.success,
        constraints_created=result.constraints_created,
        indexes_created=result.indexes_created,
        errors=result.errors,
    )


@router.post(
    "/build",
    response_model=GraphBuildResponse,
    dependencies=[Depends(verify_admin_token)],
)
def graph_build(data: GraphBuildRequest):
    """
    Build graph projection from PostgreSQL into Neo4j.

    Reads vehicles, complaints, recalls, and components from PostgreSQL.
    Projects them into Neo4j as VehicleMake → VehicleModel → ModelYear →
    Complaint → Component and Recall → AFFECTS → ModelYear nodes.

    - dry_run=true: count records without writing to Neo4j
    - limit_vehicles=N: process only N vehicles
    """
    stats = build_graph_from_postgres(
        dry_run=data.dry_run,
        limit_vehicles=data.limit_vehicles,
    )
    return GraphBuildResponse(
        success=stats.errors_count == 0,
        stats=stats.to_dict(),
    )


@router.get("/status", response_model=GraphStatusResponse)
def graph_status():
    """
    Return current graph database status.

    Shows Neo4j node/relationship counts, label distribution,
    and PostgreSQL baseline counts for comparison.
    """
    status = get_graph_status()
    return GraphStatusResponse(
        neo4j_connected=status.neo4j_connected,
        node_count=status.node_count,
        relationship_count=status.relationship_count,
        node_labels=[{"label": n.label, "count": n.count} for n in status.node_labels],
        relationship_types=[{"type": r.type, "count": r.count} for r in status.relationship_types],
        postgres_vehicle_count=status.postgres_vehicle_count,
        postgres_complaint_count=status.postgres_complaint_count,
        postgres_recall_count=status.postgres_recall_count,
        postgres_component_count=status.postgres_component_count,
        last_build=status.last_build,
        error=status.error,
    )


@router.get("/vehicles/{vehicle_id}/neighborhood", response_model=GraphNeighborhoodResponse)
def graph_vehicle_neighborhood(vehicle_id: str):
    """
    Retrieve graph neighborhood for a vehicle by its PostgreSQL UUID.

    Returns connected complaints, recalls, and components from the graph.
    """
    result = get_vehicle_neighborhood(vehicle_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Vehicle {vehicle_id} not found in graph or PostgreSQL"
        )
    return GraphNeighborhoodResponse(
        make=result.make,
        model=result.model,
        year=result.year,
        vehicle_id=result.vehicle_id,
        nodes=[n.to_dict() for n in result.nodes],
        edges=[{"type": e.type, "source_id": e.source_id, "target_id": e.target_id, "properties": e.properties} for e in result.edges],
        complaint_count=result.complaint_count,
        recall_count=result.recall_count,
        components=result.components,
    )


@router.get("/vehicles/{vehicle_id}/recall-paths", response_model=GraphRecallPathsResponse)
def graph_recall_paths(vehicle_id: str):
    """
    Retrieve recall paths for a vehicle from the graph.

    Returns recalls linked through AFFECTS → ModelYear and RELATED_TO_COMPONENT.

    WARNING: These recall links are POTENTIALLY RELATED BY SHARED VEHICLE/COMPONENT.
    They are NOT official causality. Complaint volume alone does not prove a defect.
    """
    result = get_vehicle_recall_paths(vehicle_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Vehicle {vehicle_id} not found in graph or PostgreSQL"
        )
    return GraphRecallPathsResponse(
        make=result.make,
        model=result.model,
        year=result.year,
        vehicle_id=result.vehicle_id,
        recalls=[r.to_dict() for r in result.recalls],
        path_type=result.path_type,
    )


@router.get("/vehicles/{vehicle_id}/component-evidence", response_model=ComponentEvidenceResponse)
def graph_component_evidence(vehicle_id: str):
    """
    Retrieve component-level evidence for a vehicle.

    Returns complaints linked via MENTIONS_COMPONENT and recalls
    linked via RELATED_TO_COMPONENT to shared components.

    WARNING: Shared component links are POTENTIAL ASSOCIATIONS only.
    Complaint volume alone does not prove a safety defect.
    Only Recall → AFFECTS → ModelYear is official when campaign applies.
    """
    # Get component evidence (complaint → component links)
    evidence = get_vehicle_component_evidence(vehicle_id)
    if evidence is None:
        raise HTTPException(
            status_code=404,
            detail=f"Vehicle {vehicle_id} not found in graph or PostgreSQL"
        )
    return ComponentEvidenceResponse(
        make=evidence.make,
        model=evidence.model,
        year=evidence.year,
        vehicle_id=evidence.vehicle_id,
        complaint_components=evidence.complaint_components,
        components=evidence.components,
        complaint_count=evidence.complaint_count,
        shared_recalls=evidence.shared_recalls,
        recall_count=evidence.recall_count,
        path_type=evidence.path_type,
    )
