"""
Pydantic/dataclass models for graph nodes, relationships, and build stats.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GraphNodeStats:
    label: str
    count: int


@dataclass
class GraphRelStats:
    type: str
    count: int


@dataclass
class GraphBuildStats:
    """Statistics from a graph build run."""
    vehicle_makes_seen: int = 0
    vehicle_models_seen: int = 0
    model_years_seen: int = 0
    components_seen: int = 0
    complaints_seen: int = 0
    recalls_seen: int = 0
    nodes_merged: int = 0
    relationships_merged: int = 0
    rows_skipped: int = 0
    errors_count: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0
    dry_run: bool = False
    # Phase 5: component relationship stats
    component_nodes_merged: int = 0
    complaint_component_links_seen: int = 0
    complaint_component_links_merged: int = 0
    recall_component_links_seen: int = 0
    recall_component_links_merged: int = 0
    component_links_skipped: int = 0
    component_link_errors: int = 0

    def to_dict(self) -> dict:
        return {
            "vehicle_makes_seen": self.vehicle_makes_seen,
            "vehicle_models_seen": self.vehicle_models_seen,
            "model_years_seen": self.model_years_seen,
            "components_seen": self.components_seen,
            "complaints_seen": self.complaints_seen,
            "recalls_seen": self.recalls_seen,
            "nodes_merged": self.nodes_merged,
            "relationships_merged": self.relationships_merged,
            "rows_skipped": self.rows_skipped,
            "errors_count": self.errors_count,
            "errors": self.errors,
            "duration_ms": self.duration_ms,
            "dry_run": self.dry_run,
            "component_nodes_merged": self.component_nodes_merged,
            "complaint_component_links_seen": self.complaint_component_links_seen,
            "complaint_component_links_merged": self.complaint_component_links_merged,
            "recall_component_links_seen": self.recall_component_links_seen,
            "recall_component_links_merged": self.recall_component_links_merged,
            "component_links_skipped": self.component_links_skipped,
            "component_link_errors": self.component_link_errors,
        }


@dataclass
class GraphStatus:
    """Current status of the graph database."""
    neo4j_connected: bool
    node_count: int
    relationship_count: int
    node_labels: list[GraphNodeStats]
    relationship_types: list[GraphRelStats]
    postgres_vehicle_count: int
    postgres_complaint_count: int
    postgres_recall_count: int
    postgres_component_count: int
    last_build: dict | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "neo4j_connected": self.neo4j_connected,
            "node_count": self.node_count,
            "relationship_count": self.relationship_count,
            "node_labels": [{"label": n.label, "count": n.count} for n in self.node_labels],
            "relationship_types": [{"type": r.type, "count": r.count} for r in self.relationship_types],
            "postgres_vehicle_count": self.postgres_vehicle_count,
            "postgres_complaint_count": self.postgres_complaint_count,
            "postgres_recall_count": self.postgres_recall_count,
            "postgres_component_count": self.postgres_component_count,
            "last_build": self.last_build,
            "error": self.error,
        }


@dataclass
class VehicleMakeNode:
    normalized_name: str
    name: str


@dataclass
class VehicleModelNode:
    normalized_name: str
    name: str


@dataclass
class ModelYearNode:
    year: int
    vehicle_id: str


@dataclass
class ComponentNode:
    normalized_name: str
    name: str
    category: str | None = None


@dataclass
class ComplaintNode:
    odi_number: str | None
    received_date: str | None
    summary: str | None = None
    crash_flag: bool = False
    injury_flag: bool = False
    death_flag: bool = False


@dataclass
class RecallNode:
    campaign_number: str
    report_received_date: str | None = None
    summary: str | None = None
    component: str | None = None
    remedy: str | None = None
    units_affected: int | None = None

    def to_dict(self) -> dict:
        """Serialize one recall node.

        `GET /v1/graph/vehicles/{id}/recall-paths` calls this per recall, as its
        sibling node types allow. Without it that route raised AttributeError,
        and `RecallPathResult.to_dict` carried a duplicated inline copy of this
        mapping to work around the gap.
        """
        return {
            "campaign_number": self.campaign_number,
            "report_received_date": self.report_received_date,
            "summary": self.summary,
            "component": self.component,
            "remedy": self.remedy,
            "units_affected": self.units_affected,
        }


@dataclass
class ComponentEvidence:
    """Component-level evidence from graph retrieval."""
    make: str
    model: str
    year: int
    vehicle_id: str
    complaint_components: list[dict]  # complaints linked to components via MENTIONS_COMPONENT
    components: list[str]  # component names
    complaint_count: int
    shared_recalls: list[dict]  # recalls linked via RELATED_TO_COMPONENT
    recall_count: int
    path_type: str  # "complaint_mentions_component" | "recall_related_to_component"

    def to_dict(self) -> dict:
        return {
            "make": self.make,
            "model": self.model,
            "year": self.year,
            "vehicle_id": self.vehicle_id,
            "complaint_components": self.complaint_components,
            "components": self.components,
            "complaint_count": self.complaint_count,
            "shared_recalls": self.shared_recalls,
            "recall_count": self.recall_count,
            "path_type": self.path_type,
        }


@dataclass
class NeighborhoodNode:
    id: str
    label: str
    properties: dict

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "properties": self.properties}


@dataclass
class NeighborhoodEdge:
    type: str
    source_id: str
    target_id: str
    properties: dict = field(default_factory=dict)


@dataclass
class VehicleNeighborhood:
    """Vehicle neighborhood from graph retrieval."""
    make: str
    model: str
    year: int
    vehicle_id: str
    nodes: list[NeighborhoodNode]
    edges: list[NeighborhoodEdge]
    complaint_count: int
    recall_count: int
    components: list[str]

    def to_dict(self) -> dict:
        return {
            "make": self.make,
            "model": self.model,
            "year": self.year,
            "vehicle_id": self.vehicle_id,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [{"type": e.type, "source_id": e.source_id, "target_id": e.target_id, "properties": e.properties} for e in self.edges],
            "complaint_count": self.complaint_count,
            "recall_count": self.recall_count,
            "components": self.components,
        }


@dataclass
class RecallPathResult:
    """Recall paths for a vehicle from graph retrieval."""
    make: str
    model: str
    year: int
    vehicle_id: str
    recalls: list[RecallNode]
    path_type: str  # "potentially related by shared vehicle/component"

    def to_dict(self) -> dict:
        return {
            "make": self.make,
            "model": self.model,
            "year": self.year,
            "vehicle_id": self.vehicle_id,
            "recalls": [r.to_dict() for r in self.recalls],
            "path_type": self.path_type,
        }


@dataclass
class GraphSchemaSetupResult:
    success: bool
    constraints_created: int
    indexes_created: int
    errors: list[str]

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "constraints_created": self.constraints_created,
            "indexes_created": self.indexes_created,
            "errors": self.errors,
        }
