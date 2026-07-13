"""
Hybrid query models for Phase 4.

Defines hybrid intent classification, query plan, and result structures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# Hybrid intents that require both SQL + graph
HYBRID_INTENTS = {
    "top_complaint_component_with_related_recalls",
    "complaint_count_with_related_recalls",
    "vehicle_recall_evidence",
    "component_complaints_with_graph_evidence",
    "vehicles_complaint_count_with_recall_evidence",
}


@dataclass
class HybridIntent:
    """Result of parsing a hybrid question."""
    # Base SQL intent from Phase 2 parser
    sql_intent: str  # e.g. "top_complaint_components_by_vehicle"
    # Hybrid subtype
    hybrid_type: str  # e.g. "top_complaint_component_with_related_recalls"
    # Whether graph retrieval is requested
    wants_graph: bool
    wants_sql: bool = True
    vehicle_extracted: bool = False
    confidence: float = 0.5
    # Vehicle entity from Phase 2 parser (for graph lookup)
    vehicle: Optional[object] = None


@dataclass
class GraphEvidenceItem:
    """A single evidence item from graph retrieval."""
    path_type: str  # e.g. "potentially related by shared vehicle/component"
    nodes: list[dict] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)
    recall_campaigns: list[str] = field(default_factory=list)
    relation_basis: str = "unknown"  # official_recall_affects_vehicle | potentially_related_by_shared_component
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "path_type": self.path_type,
            "nodes": self.nodes,
            "relationships": self.relationships,
            "recall_campaigns": self.recall_campaigns,
            "relation_basis": self.relation_basis,
            "summary": self.summary,
        }


@dataclass
class HybridAnswerResult:
    """Result of answering a hybrid question."""
    sql_response: dict  # from Phase 2 answer_sql_analytics_question
    graph_evidence: list[GraphEvidenceItem] = field(default_factory=list)
    neo4j_available: bool = True
    neo4j_error: Optional[str] = None
    sql_error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sql_response": self.sql_response,
            "graph_evidence": [e.to_dict() for e in self.graph_evidence],
            "neo4j_available": self.neo4j_available,
            "neo4j_error": self.neo4j_error,
            "sql_error": self.sql_error,
            "warnings": self.warnings,
        }
