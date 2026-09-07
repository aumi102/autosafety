"""GraphRAG API endpoints — Phase 6."""

from __future__ import annotations

import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.security import verify_admin_token
from app.services.graphrag import (
    index_graphrag_documents,
    retrieve_graphrag_evidence,
    get_graphrag_status,
)

router = APIRouter(tags=["graphrag"])


class GraphRAGIndexRequest(BaseModel):
    dry_run: bool = False
    source_type: Optional[str] = None  # "complaint" | "recall" | None
    limit: Optional[int] = None
    force_reembed: bool = False


class GraphRAGIndexResponse(BaseModel):
    success: bool
    stats: dict
    phase: str = "phase_6"


class GraphRAGRetrieveRequest(BaseModel):
    question: str
    top_k: int = 5
    include_graph: bool = True
    source_type: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    model_year: Optional[int] = None


class GraphRAGRetrieveResponse(BaseModel):
    query: str
    retrieval_mode: str
    retrieved_chunks: list[dict]
    citations: list[dict]
    graph_paths: list[dict]
    warnings: list[str]
    confidence: dict
    total_chunks_returned: int
    execution_ms: int
    neo4j_available: bool
    neo4j_error: Optional[str]
    phase: str = "phase_6"


class GraphRAGStatusResponse(BaseModel):
    vector_backend_available: bool
    vector_backend: str
    embedding_model: str
    embedding_dimension: int
    document_count: int
    chunk_count: int
    complaints_indexed: int
    recalls_indexed: int
    error: Optional[str]
    phase: str = "phase_6"


class GraphRAGHealthResponse(BaseModel):
    vector_backend_available: bool
    phase: str = "phase_6"


@router.get("/health", response_model=GraphRAGHealthResponse)
def graphrag_health():
    """
    Check GraphRAG vector backend availability.

    Returns whether the PostgreSQL vector store is reachable.
    """
    try:
        status = get_graphrag_status()
        return GraphRAGHealthResponse(
            vector_backend_available=status.vector_backend_available,
        )
    except Exception:
        return GraphRAGHealthResponse(vector_backend_available=False)


@router.get("/status", response_model=GraphRAGStatusResponse)
def graphrag_status():
    """
    Return current GraphRAG index status.

    Shows document/chunk counts and embedding configuration.
    """
    status = get_graphrag_status()
    return GraphRAGStatusResponse(
        vector_backend_available=status.vector_backend_available,
        vector_backend=status.vector_backend,
        embedding_model=status.embedding_model,
        embedding_dimension=status.embedding_dimension,
        document_count=status.document_count,
        chunk_count=status.chunk_count,
        complaints_indexed=status.complaints_indexed,
        recalls_indexed=status.recalls_indexed,
        error=status.error,
    )


@router.post(
    "/index",
    response_model=GraphRAGIndexResponse,
    dependencies=[Depends(verify_admin_token)],
)
def graphrag_index(data: GraphRAGIndexRequest):
    """
    Index complaints and recalls as canonical evidence documents with embeddings.

    - dry_run=true: count records without writing to PostgreSQL
    - source_type: "complaint" | "recall" | None (both)
    - limit: max records per type
    - force_reembed: re-embed even unchanged documents
    """
    if data.source_type not in (None, "complaint", "recall"):
        raise HTTPException(
            status_code=400,
            detail="source_type must be 'complaint', 'recall', or omitted"
        )
    if data.limit is not None and data.limit < 1:
        raise HTTPException(
            status_code=400,
            detail="limit must be a positive integer"
        )

    start = time.time()
    stats = index_graphrag_documents(
        dry_run=data.dry_run,
        source_type=data.source_type,
        limit=data.limit,
        force_reembed=data.force_reembed,
    )
    elapsed_ms = int((time.time() - start) * 1000)

    return GraphRAGIndexResponse(
        success=stats.errors_count == 0,
        stats={
            **stats.to_dict(),
            "elapsed_ms": elapsed_ms,
        },
    )


@router.post("/retrieve", response_model=GraphRAGRetrieveResponse)
def graphrag_retrieve(data: GraphRAGRetrieveRequest):
    """
    Perform semantic retrieval over indexed complaint and recall evidence.

    Combines vector similarity search with optional Neo4j graph expansion.
    Returns structured evidence with citations and graph paths.

    WARNING: Retrieved complaint records are public reports and may be noisy.
    Semantic similarity does not prove a safety defect.
    Shared component paths are potential associations, not causality.
    """
    if not data.question or not data.question.strip():
        raise HTTPException(
            status_code=400,
            detail="question cannot be empty"
        )
    if data.top_k < 1 or data.top_k > 50:
        raise HTTPException(
            status_code=400,
            detail="top_k must be between 1 and 50"
        )
    if data.source_type not in (None, "complaint", "recall"):
        raise HTTPException(
            status_code=400,
            detail="source_type must be 'complaint', 'recall', or omitted"
        )

    result = retrieve_graphrag_evidence(
        question=data.question,
        top_k=data.top_k,
        include_graph=data.include_graph,
        source_type=data.source_type,
        make=data.make,
        model=data.model,
        model_year=data.model_year,
    )

    return GraphRAGRetrieveResponse(
        query=result.query,
        retrieval_mode=result.retrieval_mode,
        retrieved_chunks=[c.to_dict() for c in result.retrieved_chunks],
        citations=[c.to_dict() for c in result.citations],
        graph_paths=[p.to_dict() for p in result.graph_paths],
        warnings=result.warnings,
        confidence={
            "label": result.confidence_label,
            "score": result.confidence_score,
            "reasons": result.confidence_reasons,
        },
        total_chunks_returned=result.total_chunks_returned,
        execution_ms=result.execution_ms,
        neo4j_available=result.neo4j_available,
        neo4j_error=result.neo4j_error,
    )
