"""
Safe predefined graph retrieval queries for Phase 3.

All queries use fixed Cypher templates. NO arbitrary user Cypher.
Each function is parameterized and validated before execution.
"""

from __future__ import annotations

import re
from typing import Optional
from dataclasses import dataclass

from app.services.graph.neo4j_client import Neo4jClient
from app.services.graph.graph_models import (
    VehicleNeighborhood,
    RecallPathResult,
    RecallNode,
    NeighborhoodNode,
    NeighborhoodEdge,
)


# Maximum result limits enforced server-side
MAX_NEIGHBORHOOD_NODES = 500
MAX_RECALL_PATHS = 100


def _validate_identifier(value: str, field_name: str) -> str:
    """Validate and sanitize an identifier value."""
    if not value or not value.strip():
        raise ValueError(f"{field_name} cannot be empty")
    # Only allow alphanumeric, dash, underscore, space, colon
    if not re.match(r'^[\w\-\s:]+$', value):
        raise ValueError(f"Invalid characters in {field_name}: {value!r}")
    return value.strip()


# =============================================================================
# Vehicle neighborhood query
# =============================================================================

VEHICLE_NEIGHBORHOOD_CYPHER = """
MATCH (make:VehicleMake {normalized_name: $make})-[:HAS_MODEL]->(model:VehicleModel {key: $model_key})-[:HAS_YEAR]->(year:ModelYear {key: $year_key})
OPTIONAL MATCH (year)-[:HAS_COMPLAINT]->(c:Complaint)
OPTIONAL MATCH (r:Recall)-[:AFFECTS]->(year)
OPTIONAL MATCH (comp:Component)
OPTIONAL MATCH (c)-[:MENTIONS_COMPONENT]->(comp)
RETURN
    make.normalized_name AS make_name,
    make.name AS make_display,
    model.normalized_name AS model_name,
    year.year AS model_year,
    year.vehicle_id AS vehicle_id,
    collect(DISTINCT {
        id: coalesce(c.odi_number, id(c)),
        label: 'Complaint',
        properties: coalesce({
            odi_number: c.odi_number,
            received_date: c.received_date,
            summary: left(c.summary, 200),
            crash_flag: c.crash_flag,
            injury_flag: c.injury_flag,
            death_flag: c.death_flag
        }, {})
    }) AS complaints,
    collect(DISTINCT {
        id: r.campaign_number,
        label: 'Recall',
        properties: coalesce({
            campaign_number: r.campaign_number,
            report_received_date: r.report_received_date,
            summary: left(r.summary, 200),
            remedy: left(r.remedy, 200),
            units_affected: r.units_affected
        }, {})
    }) AS recalls,
    collect(DISTINCT {
        id: comp.normalized_name,
        label: 'Component',
        properties: coalesce({
            name: comp.name,
            normalized_name: comp.normalized_name,
            category: comp.category
        }, {})
    }) AS components,
    count(DISTINCT c) AS complaint_count,
    count(DISTINCT r) AS recall_count
LIMIT 1
"""


def get_vehicle_neighborhood(
    client: Neo4jClient,
    make: str,
    model: str,
    year: int,
    vehicle_id: Optional[str] = None,
) -> Optional[VehicleNeighborhood]:
    """
    Retrieve vehicle neighborhood from graph.

    Returns complaints, recalls, and components connected to the vehicle.
    """
    make = _validate_identifier(make, "make")
    model = _validate_identifier(model, "model")
    year_key = f"{model.upper()}:{year}"
    model_key = f"{make.upper()}:{model.upper()}"

    params = {
        "make": make.upper(),
        "model_key": model_key,
        "year_key": year_key,
    }

    result = client.execute_single(VEHICLE_NEIGHBORHOOD_CYPHER, params)

    if not result:
        return None

    # Build node list
    nodes: list[NeighborhoodNode] = []
    edges: list[NeighborhoodEdge] = []
    components: list[str] = []

    # Add VehicleMake node
    nodes.append(NeighborhoodNode(
        id=result["make_name"],
        label="VehicleMake",
        properties={"normalized_name": result["make_name"], "name": result.get("make_display", result["make_name"])},
    ))

    # Add VehicleModel node
    nodes.append(NeighborhoodNode(
        id=result["model_name"],
        label="VehicleModel",
        properties={"normalized_name": result["model_name"]},
    ))

    # Add ModelYear node
    year_id = result["vehicle_id"] or year_key
    nodes.append(NeighborhoodNode(
        id=year_id,
        label="ModelYear",
        properties={"year": result["model_year"], "vehicle_id": result["vehicle_id"]},
    ))

    complaint_count = result.get("complaint_count", 0) or 0
    recall_count = result.get("recall_count", 0) or 0

    # Add complaint nodes and edges
    for c_data in (result.get("complaints") or []):
        if c_data and c_data.get("id"):
            props = c_data.get("properties") or {}
            nodes.append(NeighborhoodNode(
                id=str(c_data["id"]),
                label="Complaint",
                properties={k: v for k, v in props.items() if v is not None},
            ))
            edges.append(NeighborhoodEdge(
                type="HAS_COMPLAINT",
                source_id=year_id,
                target_id=str(c_data["id"]),
            ))

            comp_props = props.get("component_normalized_name")
            if comp_props:
                edges.append(NeighborhoodEdge(
                    type="MENTIONS_COMPONENT",
                    source_id=str(c_data["id"]),
                    target_id=comp_props,
                ))

    # Add recall nodes and edges
    for r_data in (result.get("recalls") or []):
        if r_data and r_data.get("id"):
            props = r_data.get("properties") or {}
            nodes.append(NeighborhoodNode(
                id=str(r_data["id"]),
                label="Recall",
                properties={k: v for k, v in props.items() if v is not None},
            ))
            edges.append(NeighborhoodEdge(
                type="AFFECTS",
                source_id=str(r_data["id"]),
                target_id=year_id,
            ))

    # Add component nodes
    for comp_data in (result.get("components") or []):
        if comp_data and comp_data.get("id"):
            props = comp_data.get("properties") or {}
            if props.get("normalized_name"):
                components.append(props["normalized_name"])
                nodes.append(NeighborhoodNode(
                    id=props["normalized_name"],
                    label="Component",
                    properties={k: v for k, v in props.items() if v is not None},
                ))

    return VehicleNeighborhood(
        make=result["make_name"],
        model=result["model_name"],
        year=result["model_year"],
        vehicle_id=result["vehicle_id"] or year_id,
        nodes=nodes,
        edges=edges,
        complaint_count=complaint_count,
        recall_count=recall_count,
        components=components,
    )


# =============================================================================
# Recall paths query
# =============================================================================

RECALL_PATHS_CYPHER = """
MATCH (make:VehicleMake {normalized_name: $make})-[:HAS_MODEL]->(model:VehicleModel {key: $model_key})-[:HAS_YEAR]->(year:ModelYear {key: $year_key})
OPTIONAL MATCH (r:Recall)-[:AFFECTS]->(year)
OPTIONAL MATCH (comp:Component)
OPTIONAL MATCH (r)-[:RELATED_TO_COMPONENT]->(comp)
OPTIONAL MATCH (year)-[:HAS_COMPLAINT]->(c:Complaint)-[:MENTIONS_COMPONENT]->(comp)
WITH make, model, year, r, comp, count(DISTINCT c) AS shared_complaint_count
WHERE r IS NOT NULL
RETURN
    make.normalized_name AS make_name,
    model.normalized_name AS model_name,
    year.year AS model_year,
    year.vehicle_id AS vehicle_id,
    collect(DISTINCT {
        id: r.campaign_number,
        label: 'Recall',
        properties: coalesce({
            campaign_number: r.campaign_number,
            report_received_date: r.report_received_date,
            summary: left(r.summary, 200),
            component: comp.name,
            remedy: left(r.remedy, 200),
            units_affected: r.units_affected
        }, {}),
        shared_complaint_count: shared_complaint_count
    }) AS recalls
LIMIT 1
"""


def get_recall_paths_for_vehicle(
    client: Neo4jClient,
    make: str,
    model: str,
    year: int,
    vehicle_id: Optional[str] = None,
) -> Optional[RecallPathResult]:
    """
    Retrieve recall paths for a vehicle from the graph.

    Recalls are linked through AFFECTS → ModelYear and RELATED_TO_COMPONENT → Component.
    This is NOT official causality — it is potentially_related.
    """
    make = _validate_identifier(make, "make")
    model = _validate_identifier(model, "model")
    year_key = f"{model.upper()}:{year}"
    model_key = f"{make.upper()}:{model.upper()}"

    params = {
        "make": make.upper(),
        "model_key": model_key,
        "year_key": year_key,
    }

    result = client.execute_single(RECALL_PATHS_CYPHER, params)

    if not result:
        return None

    recalls: list[RecallNode] = []
    for r_data in (result.get("recalls") or []):
        if r_data and r_data.get("id"):
            props = r_data.get("properties") or {}
            recalls.append(RecallNode(
                campaign_number=str(r_data["id"]),
                report_received_date=props.get("report_received_date"),
                summary=props.get("summary"),
                component=props.get("component"),
                remedy=props.get("remedy"),
                units_affected=props.get("units_affected"),
            ))

    return RecallPathResult(
        make=result["make_name"],
        model=result["model_name"],
        year=result["model_year"],
        vehicle_id=result["vehicle_id"] or year_key,
        recalls=recalls,
        path_type="potentially related by shared vehicle/component",
    )


# =============================================================================
# Raw query guard — prevents any path from accepting arbitrary Cypher
# =============================================================================

ARBITRARY_CYPHER_BLOCKED = """
Arbitrary Cypher execution is not exposed in Phase 3.
All graph queries use predefined, parameterized templates.
To request a new query pattern, file a feature request.
"""


def execute_arbitrary_cypher(client: Neo4jClient, cypher: str) -> dict:
    """
    BLOCKED: Returns an error. Phase 3 does not expose arbitrary Cypher.
    """
    return {
        "error": "arbitrary_cypher_not_allowed",
        "message": ARBITRARY_CYPHER_BLOCKED.strip(),
    }
