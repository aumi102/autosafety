"""
Graph expander - Neo4j neighborhood expansion from retrieved source entities.

Uses predefined parameterized Cypher only. Bounded depth. Tolerates Neo4j unavailable.
"""

from __future__ import annotations

import logging

from app.services.graph.neo4j_client import Neo4jClient
from app.services.graphrag.models import GraphRAGGraphPath

logger = logging.getLogger(__name__)

# Expand from a complaint ODI number
COMPLAINT_NEIGHBORHOOD_CYPHER = """
MATCH (c:Complaint {odi_number: $odi})
OPTIONAL MATCH (c)-[:MENTIONS_COMPONENT]->(comp:Component)
OPTIONAL MATCH (r:Recall)-[:RELATED_TO_COMPONENT]->(comp)
OPTIONAL MATCH (c)<-[:HAS_COMPLAINT]-(my:ModelYear)<-[:HAS_YEAR]-(vm:VehicleModel)<-[:HAS_MODEL]-(mk:VehicleMake)
OPTIONAL MATCH (r)-[:AFFECTS]->(my2:ModelYear)
WHERE my2.year = my.year
WITH c, comp, r, mk, vm, my
RETURN
    c.odi_number AS odi_number,
    comp.name AS component,
    collect(DISTINCT {
        recall_campaign: r.campaign_number,
        recall_consequence: left(r.summary, 200),
        remedy: left(r.remedy, 200)
    }) AS related_recalls,
    mk.name AS make,
    vm.name AS model,
    my.year AS year
LIMIT 1
"""

# Expand from a recall campaign number
RECALL_NEIGHBORHOOD_CYPHER = """
MATCH (r:Recall {campaign_number: $campaign})
OPTIONAL MATCH (r)-[:RELATED_TO_COMPONENT]->(comp:Component)
OPTIONAL MATCH (r)-[:AFFECTS]->(my:ModelYear)<-[:HAS_YEAR]-(vm:VehicleModel)<-[:HAS_MODEL]-(mk:VehicleMake)
OPTIONAL MATCH (comp)<-[:MENTIONS_COMPONENT]-(c:Complaint)
WITH r, comp, c, mk, vm, my
RETURN
    r.campaign_number AS campaign_number,
    comp.name AS component,
    collect(DISTINCT {
        complaint_odi: c.odi_number,
        complaint_summary: left(c.summary, 200)
    }) AS related_complaints,
    mk.name AS make,
    vm.name AS model,
    my.year AS year
LIMIT 1
"""


def expand_complaint_neighborhood(
    client: Neo4jClient,
    odi_number: str,
) -> list[GraphRAGGraphPath]:
    """
    Expand graph neighborhood from a complaint ODI number.

    Returns graph paths showing:
    - Complaint -> MENTIONS_COMPONENT -> Component
    - Recall -> RELATED_TO_COMPONENT -> same Component (potential association)
    """
    paths: list[GraphRAGGraphPath] = []
    try:
        result = client.execute_single(COMPLAINT_NEIGHBORHOOD_CYPHER, {"odi": odi_number})
        if not result:
            return paths

        comp = result.get("component") or "Unknown Component"
        recalls = result.get("related_recalls") or []
        make = result.get("make") or "Unknown"
        model = result.get("model") or "Unknown"
        year = result.get("year") or "?"

        # Path: complaint -> component
        paths.append(
            GraphRAGGraphPath(
                path_text=f"{make} {model} {year} complaint {odi_number} mentions component {comp}",
                relation_source="complaint_mentions_component",
                source_type="complaint",
                source_key=odi_number,
                confidence=0.9,
            )
        )

        # Paths: recall -> related component (potential association)
        for recall in recalls[:5]:
            if not recall or not recall.get("recall_campaign"):
                continue
            campaign = recall["recall_campaign"]
            paths.append(
                GraphRAGGraphPath(
                    path_text=f"{make} {model} {year} recall {campaign} related to component {comp} (potential association)",
                    relation_source="potentially_related_by_shared_component",
                    source_type="recall",
                    source_key=campaign,
                    confidence=0.7,
                )
            )

    except Exception as e:
        logger.warning(f"Complaint graph expansion failed for {odi_number}: {e}")

    return paths


def expand_recall_neighborhood(
    client: Neo4jClient,
    campaign_number: str,
) -> list[GraphRAGGraphPath]:
    """
    Expand graph neighborhood from a recall campaign number.

    Returns graph paths showing:
    - Recall -> AFFECTS -> ModelYear (official)
    - Recall -> RELATED_TO_COMPONENT -> Component
    - Recall -> related complaint via shared component (potential)
    """
    paths: list[GraphRAGGraphPath] = []
    try:
        result = client.execute_single(RECALL_NEIGHBORHOOD_CYPHER, {"campaign": campaign_number})
        if not result:
            return paths

        comp = result.get("component") or "Unknown Component"
        complaints = result.get("related_complaints") or []
        make = result.get("make") or "Unknown"
        model = result.get("model") or "Unknown"
        year = result.get("year") or "?"

        # Path: recall -> affects vehicle (official)
        paths.append(
            GraphRAGGraphPath(
                path_text=f"{make} {model} {year} recall {campaign_number} affects vehicle (official)",
                relation_source="official_recall_affects_vehicle",
                source_type="recall",
                source_key=campaign_number,
                confidence=0.95,
            )
        )

        # Path: recall -> related component
        if comp != "Unknown Component":
            paths.append(
                GraphRAGGraphPath(
                    path_text=f"{make} {model} {year} recall {campaign_number} related to component {comp}",
                    relation_source="recall_related_to_component",
                    source_type="recall",
                    source_key=campaign_number,
                    confidence=0.8,
                )
            )

        # Paths: potential complaint associations via shared component
        for complaint in complaints[:5]:
            if not complaint or not complaint.get("complaint_odi"):
                continue
            odi = complaint["complaint_odi"]
            paths.append(
                GraphRAGGraphPath(
                    path_text=f"{make} {model} {year} recall {campaign_number} potentially related to complaint {odi} via component {comp}",
                    relation_source="potentially_related_by_shared_component",
                    source_type="complaint",
                    source_key=odi,
                    confidence=0.6,
                )
            )

    except Exception as e:
        logger.warning(f"Recall graph expansion failed for {campaign_number}: {e}")

    return paths


def expand_sources(
    client: Neo4jClient,
    retrieved_chunks: list[dict],
    max_paths_per_source: int = 5,
) -> list[GraphRAGGraphPath]:
    """
    Expand graph paths for a list of retrieved source entities.

    Groups by source type and calls the appropriate expansion function.
    Bounded: max_paths_per_source per entity.
    """
    all_paths: list[GraphRAGGraphPath] = []
    seen_keys: set[str] = set()

    complaint_keys: list[str] = []
    recall_keys: list[str] = []

    for chunk in retrieved_chunks:
        key = "{st}:{sk}".format(st=chunk["source_type"], sk=chunk["source_record_key"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if chunk["source_type"] == "complaint":
            complaint_keys.append(chunk["source_record_key"])
        elif chunk["source_type"] == "recall":
            recall_keys.append(chunk["source_record_key"])

    for odi in set(complaint_keys):
        paths = expand_complaint_neighborhood(client, odi)
        all_paths.extend(paths[:max_paths_per_source])

    for campaign in set(recall_keys):
        paths = expand_recall_neighborhood(client, campaign)
        all_paths.extend(paths[:max_paths_per_source])

    return all_paths
