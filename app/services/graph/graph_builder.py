"""
Graph builder — PostgreSQL → Neo4j projection.

Reads domain data from PostgreSQL via SQLAlchemy session.
Writes to Neo4j using MERGE for idempotent node/relationship creation.
Tracks statistics per entity type.
Supports dry-run and limit_vehicles.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.domain import Complaint, Component, Recall, RecallVehicleLink, Vehicle
from app.services.graph.graph_models import GraphBuildStats
from app.services.graph.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


def _date_str(d) -> str | None:
    """Convert a date/datetime to ISO string or None."""
    if d is None:
        return None
    if hasattr(d, "isoformat"):
        return d.isoformat()[:10]
    return str(d)


def build_graph(
    pg_session: Session,
    neo4j_client: Neo4jClient,
    *,
    dry_run: bool = False,
    limit_vehicles: int | None = None,
) -> GraphBuildStats:
    """
    Project PostgreSQL domain data into Neo4j.

    Reads: vehicles, components, complaints, recalls, recall_vehicle_links.
    Writes: VehicleMake, VehicleModel, ModelYear, Component, Complaint, Recall,
            and all relationships including MENTIONS_COMPONENT and RELATED_TO_COMPONENT.

    Uses MERGE so subsequent runs are idempotent.

    Args:
        pg_session: SQLAlchemy session for PostgreSQL
        neo4j_client: Neo4j client
        dry_run: if True, count PostgreSQL records without writing to Neo4j
        limit_vehicles: limit number of vehicle records to process

    Returns:
        GraphBuildStats with counts and timing
    """
    start_time = time.time()
    stats = GraphBuildStats(dry_run=dry_run)
    errors: list[str] = []

    try:
        # Build component map (used by complaints and recalls)
        component_map: dict[str, dict] = _build_component_map(pg_session, errors)
        stats.components_seen = len(component_map)

        # Build recall map
        recall_map: dict[str, dict] = _build_recall_map(pg_session, errors)
        stats.recalls_seen = len(recall_map)

        if dry_run:
            # Count component nodes
            for comp_data in component_map.values():
                stats.component_nodes_merged += 1
            for vehicle in _fetch_vehicles(pg_session, limit_vehicles):
                # Count vehicle model/year stats
                stats.vehicle_makes_seen += 1
                stats.vehicle_models_seen += 1
                stats.model_years_seen += 1
                complaint_count = pg_session.query(Complaint).filter(
                    Complaint.vehicle_id == vehicle.id
                ).count()
                stats.complaints_seen += complaint_count
                stats.complaint_component_links_seen += complaint_count
                stats.nodes_merged += 1  # ModelYear node
            stats.rows_skipped = stats.components_seen - stats.component_nodes_merged
            stats.duration_ms = int((time.time() - start_time) * 1000)
            stats.errors = errors
            return stats

        # Phase 5: MERGE all Component nodes first
        _upsert_all_components(neo4j_client, component_map, stats)

        # Iterate vehicles
        vehicles = _fetch_vehicles(pg_session, limit_vehicles)
        for vehicle in vehicles:
            try:
                _process_vehicle(
                    vehicle, pg_session, neo4j_client,
                    component_map, recall_map, stats,
                )
            except Exception as e:
                stats.errors_count += 1
                errors.append(f"Vehicle {vehicle.id}: {e}")
                logger.warning(f"Graph build error for vehicle {vehicle.id}: {e}")

        stats.duration_ms = int((time.time() - start_time) * 1000)
        stats.errors = errors

    except Exception as e:
        stats.errors_count += 1
        errors.append(f"Fatal: {e}")
        stats.errors = errors
        stats.duration_ms = int((time.time() - start_time) * 1000)
        logger.exception(f"Graph build fatal error: {e}")

    return stats


def _upsert_all_components(
    neo4j_client: Neo4jClient,
    component_map: dict[str, dict],
    stats: GraphBuildStats,
) -> None:
    """MERGE all Component nodes into Neo4j."""
    for comp_data in component_map.values():
        normalized = comp_data.get("normalized_name")
        if not normalized:
            stats.component_links_skipped += 1
            continue
        try:
            neo4j_client.execute(
                """
                MERGE (comp:Component {normalized_name: $name})
                ON CREATE SET
                    comp.name = $display_name,
                    comp.category = $category
                """,
                {
                    "name": normalized,
                    "display_name": comp_data.get("name", normalized),
                    "category": comp_data.get("category"),
                },
            )
            stats.component_nodes_merged += 1
            stats.nodes_merged += 1
        except Exception as e:
            stats.component_link_errors += 1
            stats.errors.append(f"Component {normalized}: {e}")


def _build_component_map(session: Session, errors: list[str]) -> dict[str, dict]:
    """Index components by normalized_name."""
    comp_map: dict[str, dict] = {}
    try:
        stmt = select(Component)
        for comp in session.scalars(stmt):
            comp_map[comp.normalized_name] = {
                "id": str(comp.id),
                "name": comp.name,
                "normalized_name": comp.normalized_name,
                "category": comp.category,
            }
    except Exception as e:
        errors.append(f"Component fetch error: {e}")
    return comp_map


def _build_recall_map(session: Session, errors: list[str]) -> dict[str, dict]:
    """Index recalls by campaign_number."""
    recall_map: dict[str, dict] = {}
    try:
        stmt = select(Recall)
        for recall in session.scalars(stmt):
            if recall.campaign_number:
                recall_map[recall.campaign_number] = {
                    "id": str(recall.id),
                    "campaign_number": recall.campaign_number,
                    "component_id": str(recall.component_id) if recall.component_id else None,
                    "component_normalized": (
                        None if not recall.component_id else None
                    ),
                    "original_component": recall.original_component,
                    "summary": recall.summary,
                    "remedy": recall.remedy,
                    "consequence": recall.consequence,
                    "units_affected": recall.units_affected,
                    "report_received_date": _date_str(recall.report_received_date),
                    "source_url": recall.source_url,
                }
    except Exception as e:
        errors.append(f"Recall fetch error: {e}")
    return recall_map


def _fetch_vehicles(session: Session, limit: int | None) -> Iterator[Vehicle]:
    """Fetch vehicles ordered by make/model/year."""
    stmt = (
        select(Vehicle)
        .order_by(Vehicle.normalized_make, Vehicle.normalized_model, Vehicle.model_year)
    )
    if limit:
        stmt = stmt.limit(limit)
    yield from session.scalars(stmt)


def _process_vehicle(
    vehicle: Vehicle,
    pg_session: Session,
    neo4j_client: Neo4jClient,
    component_map: dict[str, dict],
    recall_map: dict[str, dict],
    stats: GraphBuildStats,
) -> None:
    """Process one vehicle and its complaints/recalls into Neo4j."""
    make_key = vehicle.normalized_make
    model_key = f"{vehicle.normalized_make}:{vehicle.normalized_model}"
    year_key = f"{vehicle.normalized_model}:{vehicle.model_year}"

    # Track stats
    if make_key not in (None, ""):
        stats.vehicle_makes_seen += 1
    stats.vehicle_models_seen += 1
    stats.model_years_seen += 1

    # ── MERGE VehicleMake ────────────────────────────────────────────────────
    neo4j_client.execute(
        """
        MERGE (make:VehicleMake {normalized_name: $make})
        ON CREATE SET make.name = $make_name
        """,
        {"make": make_key, "make_name": vehicle.make},
    )
    stats.nodes_merged += 1

    # ── MERGE VehicleModel ───────────────────────────────────────────────────
    neo4j_client.execute(
        """
        MERGE (m:VehicleModel {key: $model_key})
        ON CREATE SET
            m.normalized_name = $model,
            m.name = $model_name
        """,
        {
            "model_key": model_key,
            "model": vehicle.normalized_model,
            "model_name": vehicle.model,
        },
    )
    stats.nodes_merged += 1

    # ── MERGE ModelYear ───────────────────────────────────────────────────────
    neo4j_client.execute(
        """
        MERGE (y:ModelYear {key: $year_key})
        ON CREATE SET
            y.year = $year,
            y.vehicle_id = $vehicle_id
        """,
        {
            "year_key": year_key,
            "year": vehicle.model_year,
            "vehicle_id": str(vehicle.id),
        },
    )
    stats.nodes_merged += 1

    # ── MERGE relationships: Make→Model, Model→Year ─────────────────────────
    neo4j_client.execute(
        """
        MATCH (make:VehicleMake {normalized_name: $make})
        MATCH (m:VehicleModel {key: $model_key})
        MERGE (make)-[:HAS_MODEL]->(m)
        """,
        {"make": make_key, "model_key": model_key},
    )
    stats.relationships_merged += 1

    neo4j_client.execute(
        """
        MATCH (m:VehicleModel {key: $model_key})
        MATCH (y:ModelYear {key: $year_key})
        MERGE (m)-[:HAS_YEAR]->(y)
        """,
        {"model_key": model_key, "year_key": year_key},
    )
    stats.relationships_merged += 1

    # ── MERGE Complaints ──────────────────────────────────────────────────────
    complaints = pg_session.query(Complaint).filter(
        Complaint.vehicle_id == vehicle.id
    ).all()

    for complaint in complaints:
        stats.complaints_seen += 1
        try:
            _upsert_complaint(
                complaint, neo4j_client, year_key,
                component_map, stats,
            )
        except Exception as e:
            stats.errors_count += 1
            stats.errors.append(f"Complaint {complaint.id}: {e}")

    # ── MERGE RecallVehicleLinks (recalls that AFFECT this vehicle) ──────────
    recall_links = pg_session.query(RecallVehicleLink).filter(
        RecallVehicleLink.vehicle_id == vehicle.id
    ).all()

    for link in recall_links:
        recall = recall_map.get(link.recall_id.hex if hasattr(link.recall_id, 'hex') else str(link.recall_id))
        if recall:
            _upsert_recall_and_affects(
                recall, year_key, component_map,
                neo4j_client, stats,
            )
        else:
            # Try fetching from DB
            recall_obj = pg_session.get(Recall, link.recall_id)
            if recall_obj and recall_obj.campaign_number:
                _upsert_recall_and_affects(
                    {
                        "id": str(recall_obj.id),
                        "campaign_number": recall_obj.campaign_number,
                        "component_id": str(recall_obj.component_id) if recall_obj.component_id else None,
                        "original_component": recall_obj.original_component,
                        "summary": recall_obj.summary,
                        "remedy": recall_obj.remedy,
                        "consequence": recall_obj.consequence,
                        "units_affected": recall_obj.units_affected,
                        "report_received_date": _date_str(recall_obj.report_received_date),
                        "source_url": recall_obj.source_url,
                    },
                    year_key, component_map, neo4j_client, stats,
                )


def _upsert_complaint(
    complaint,
    neo4j_client: Neo4jClient,
    year_key: str,
    component_map: dict[str, dict],
    stats: GraphBuildStats,
) -> None:
    """MERGE a Complaint node and link to ModelYear and Component."""
    odi = complaint.odi_number or f"local_{complaint.id}"
    neo4j_client.execute(
        """
        MERGE (c:Complaint {odi_number: $odi})
        ON CREATE SET
            c.received_date = $received_date,
            c.summary = $summary,
            c.crash_flag = $crash_flag,
            c.injury_flag = $injury_flag,
            c.death_flag = $death_flag,
            c.source_url = $source_url
        """,
        {
            "odi": odi,
            "received_date": _date_str(complaint.received_date),
            "summary": complaint.summary[:500] if complaint.summary else None,
            "crash_flag": complaint.crash_flag or False,
            "injury_flag": complaint.injury_flag or False,
            "death_flag": complaint.death_flag or False,
            "source_url": complaint.source_url,
        },
    )
    stats.nodes_merged += 1

    # Link to ModelYear
    neo4j_client.execute(
        """
        MATCH (y:ModelYear {key: $year_key})
        MATCH (c:Complaint {odi_number: $odi})
        MERGE (y)-[:HAS_COMPLAINT]->(c)
        """,
        {"year_key": year_key, "odi": odi},
    )
    stats.relationships_merged += 1

    # Link Complaint → Component (MENTIONS_COMPONENT)
    if complaint.original_component:
        from app.services.ingestion.normalization import normalize_component_name
        norm = normalize_component_name(complaint.original_component)
        comp_data = component_map.get(norm) if norm else None
        if comp_data and norm:
            stats.complaint_component_links_seen += 1
            try:
                neo4j_client.execute(
                    """
                    MATCH (c:Complaint {odi_number: $odi})
                    MATCH (comp:Component {normalized_name: $comp_name})
                    MERGE (c)-[:MENTIONS_COMPONENT]->(comp)
                    """,
                    {"odi": odi, "comp_name": norm},
                )
                stats.complaint_component_links_merged += 1
                stats.relationships_merged += 1
            except Exception as e:
                stats.component_link_errors += 1
                stats.errors.append(f"MENTIONS_COMPONENT {odi}→{norm}: {e}")
        else:
            stats.component_links_skipped += 1


def _upsert_recall_and_affects(
    recall_data: dict,
    year_key: str,
    component_map: dict[str, dict],
    neo4j_client: Neo4jClient,
    stats: GraphBuildStats,
) -> None:
    """MERGE a Recall node, link to ModelYear (AFFECTS), and Component."""
    campaign = recall_data.get("campaign_number")
    if not campaign:
        return

    neo4j_client.execute(
        """
        MERGE (r:Recall {campaign_number: $campaign})
        ON CREATE SET
            r.report_received_date = $report_date,
            r.summary = $summary,
            r.remedy = $remedy,
            r.units_affected = $units,
            r.source_url = $source_url
        """,
        {
            "campaign": campaign,
            "report_date": recall_data.get("report_received_date"),
            "summary": (recall_data.get("summary") or "")[:500],
            "remedy": (recall_data.get("remedy") or "")[:500],
            "units": recall_data.get("units_affected"),
            "source_url": recall_data.get("source_url"),
        },
    )
    stats.nodes_merged += 1

    # Link Recall → ModelYear (AFFECTS)
    neo4j_client.execute(
        """
        MATCH (r:Recall {campaign_number: $campaign})
        MATCH (y:ModelYear {key: $year_key})
        MERGE (r)-[:AFFECTS {relation_source: 'source_record'}]->(y)
        """,
        {"campaign": campaign, "year_key": year_key},
    )
    stats.relationships_merged += 1

    # Link Recall → Component (RELATED_TO_COMPONENT) if known
    if recall_data.get("original_component"):
        from app.services.ingestion.normalization import normalize_component_name
        norm = normalize_component_name(recall_data["original_component"])
        comp_data = component_map.get(norm) if norm else None
        if comp_data and norm:
            stats.recall_component_links_seen += 1
            try:
                neo4j_client.execute(
                    """
                    MATCH (r:Recall {campaign_number: $campaign})
                    MATCH (comp:Component {normalized_name: $comp_name})
                    MERGE (r)-[:RELATED_TO_COMPONENT {relation_source: 'normalized_join'}]->(comp)
                    """,
                    {"campaign": campaign, "comp_name": norm},
                )
                stats.recall_component_links_merged += 1
                stats.relationships_merged += 1
            except Exception as e:
                stats.component_link_errors += 1
                stats.errors.append(f"RELATED_TO_COMPONENT {campaign}→{norm}: {e}")
        else:
            stats.component_links_skipped += 1
