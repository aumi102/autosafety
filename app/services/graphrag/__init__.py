"""
GraphRAG service package — Phase 6 semantic retrieval foundation.

Components:
- document_builder: canonical evidence documents from PostgreSQL entities
- chunker: deterministic text chunking with stable IDs
- embedding_provider: abstraction over embedding backends
- vector_store: pgvector-backed chunk storage and similarity search
  (uses pgvector when column exists; falls back to JSONB brute-force)
- retriever: semantic retrieval with citation metadata
- graph_expander: Neo4j neighborhood expansion from retrieved sources
- service: high-level orchestration
"""

from app.services.graphrag.service import (
    index_graphrag_documents,
    retrieve_graphrag_evidence,
    get_graphrag_status,
)

__all__ = [
    "index_graphrag_documents",
    "retrieve_graphrag_evidence",
    "get_graphrag_status",
]
