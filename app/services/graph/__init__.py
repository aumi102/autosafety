"""
Graph service package — Neo4j graph foundation for Phase 3.

Components:
- neo4j_client: driver creation, connectivity, dependency injection
- graph_schema: constraints and indexes for MERGE idempotency
- graph_models: Pydantic/dataclass models for nodes, paths, stats
- graph_builder: PostgreSQL → Neo4j projection via MERGE
- graph_queries: safe predefined graph retrieval queries
- graph_service: high-level orchestration

Phase 3 is NOT full GraphRAG. No embeddings, vector search, or LLM reasoning.
"""

from app.services.graph.graph_service import (
    build_graph_from_postgres,
    get_graph_status,
    get_vehicle_component_evidence,
    get_vehicle_neighborhood,
    get_vehicle_recall_paths,
    get_vehicle_shared_component_recalls,
    setup_graph_schema,
)

__all__ = [
    "setup_graph_schema",
    "build_graph_from_postgres",
    "get_graph_status",
    "get_vehicle_neighborhood",
    "get_vehicle_recall_paths",
    "get_vehicle_component_evidence",
    "get_vehicle_shared_component_recalls",
]
