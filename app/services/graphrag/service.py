"""
GraphRAG service — high-level orchestration for indexing and retrieval.

Coordinates: document builder → chunker → embedding provider → vector store → graph expander.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.models.domain import Complaint, Recall, RecallVehicleLink, Vehicle
from app.services.graph.neo4j_client import Neo4jClient, verify_connectivity
from app.services.graphrag.chunker import chunk_document
from app.services.graphrag.document_builder import (
    build_complaint_document,
    build_recall_document,
)
from app.services.graphrag.embedding_provider import (
    EmbeddingProvider,
    get_embedding_provider,
)
from app.services.graphrag.graph_expander import expand_sources
from app.services.graphrag.models import (
    GraphRAGGraphPath,
    GraphRAGIndexStats,
    GraphRAGRetrievalResult,
    GraphRAGStatus,
    RetrievedChunk,
)
from app.services.graphrag.retriever import GraphRAGRetriever
from app.services.graphrag.vector_store import VectorStore


def _pg_engine() -> Engine:
    settings = get_settings()
    db_url = settings.DATABASE_URL_SYNC
    if "postgresql+asyncpg" in db_url:
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    return create_engine(db_url, echo=False)


def _pg_session() -> Session:
    engine = _pg_engine()
    Session = sessionmaker(bind=engine)
    return Session()


logger = logging.getLogger(__name__)

# Required safety caveats for GraphRAG responses
CAVEAT_RETRIEVAL = "Retrieved complaint records are public reports and may be noisy."
CAVEAT_SIMILARITY = "Semantic similarity does not prove a safety defect or official causality."
CAVEAT_SHARED_COMPONENT = "Shared component paths are potential associations, not causality."
CAVEAT_OFFICIAL_RECALL = (
    "Official recall applicability is represented by Recall -> AFFECTS -> ModelYear."
)


def index_graphrag_documents(
    *,
    dry_run: bool = False,
    source_type: str | None = None,
    limit: int | None = None,
    force_reembed: bool = False,
) -> GraphRAGIndexStats:
    """
    Index complaints and recalls as canonical evidence documents with embeddings.

    Flow:
    1. Read complaints/recalls from PostgreSQL
    2. Build canonical EvidenceDocument for each
    3. Chunk each document
    4. Generate embeddings
    5. Upsert documents + chunks to vector store

    Args:
        dry_run: count records without writing
        source_type: "complaint" | "recall" | None (both)
        limit: max records per type
        force_reembed: re-embed even unchanged documents

    Returns:
        GraphRAGIndexStats with counts
    """
    start_time = time.time()
    stats = GraphRAGIndexStats(dry_run=dry_run)
    errors: list[str] = []

    try:
        provider = get_embedding_provider()
        session = _pg_session()
        try:
            vector_store = VectorStore(session)

            # Index complaints
            if source_type is None or source_type == "complaint":
                _index_complaints(
                    session, vector_store, provider, stats, dry_run, limit, force_reembed, errors
                )

            # Index recalls
            if source_type is None or source_type == "recall":
                _index_recalls(
                    session, vector_store, provider, stats, dry_run, limit, force_reembed, errors
                )

            if not dry_run:
                session.commit()
        except Exception as e:
            session.rollback()
            errors.append(f"Database error: {e}")
            logger.exception("GraphRAG indexing failed")
        finally:
            session.close()
    except Exception as e:
        errors.append(f"Fatal: {e}")
        logger.exception("GraphRAG indexing fatal error")

    stats.errors = errors
    stats.duration_ms = int((time.time() - start_time) * 1000)
    return stats


def _index_complaints(
    session: Session,
    vector_store: VectorStore,
    provider: EmbeddingProvider,
    stats: GraphRAGIndexStats,
    dry_run: bool,
    limit: int | None,
    force_reembed: bool,
    errors: list[str],
) -> None:
    """Index complaints into evidence documents."""
    from sqlalchemy import select

    stmt = select(Complaint).join(Vehicle)
    if limit:
        stmt = stmt.limit(limit)
    complaints = session.scalars(stmt).all()

    for complaint in complaints:
        stats.complaints_seen += 1
        try:
            vehicle = session.get(Vehicle, complaint.vehicle_id)
            if not vehicle:
                stats.documents_skipped += 1
                continue

            doc = build_complaint_document(complaint, vehicle)
            chunks = chunk_document(doc.document_id, doc.full_text)

            if not chunks:
                stats.documents_skipped += 1
                continue

            if dry_run:
                stats.documents_created += 1
                stats.chunks_created += len(chunks)
                stats.embeddings_generated += len(chunks)
                continue

            # Upsert document + chunks
            status, c_created, c_updated, c_deleted, c_unchanged = vector_store.upsert_document(
                doc, [c.__dict__ for c in chunks], force_reembed=force_reembed
            )

            if status == "created":
                stats.documents_created += 1
            elif status == "updated":
                stats.documents_updated += 1
            elif status == "unchanged":
                stats.documents_unchanged += 1

            stats.chunks_created += c_created
            stats.chunks_updated += c_updated
            stats.chunks_deleted += c_deleted
            stats.chunks_unchanged += c_unchanged

            # Generate and store embeddings
            for c in chunks:
                emb = provider.embed_text(c.text)
                vector_store.set_chunk_embedding(
                    c.chunk_id,
                    emb,
                    embedding_model=provider.model_name,
                    embedding_dimension=provider.dimension,
                )
                stats.embeddings_generated += 1

        except Exception as e:
            stats.errors_count += 1
            errors.append(f"Complaint {complaint.odi_number or complaint.id}: {e}")


def _index_recalls(
    session: Session,
    vector_store: VectorStore,
    provider: EmbeddingProvider,
    stats: GraphRAGIndexStats,
    dry_run: bool,
    limit: int | None,
    force_reembed: bool,
    errors: list[str],
) -> None:
    """Index recalls into evidence documents via RecallVehicleLink."""
    from sqlalchemy import select

    stmt = (
        select(Recall)
        .join(RecallVehicleLink, Recall.id == RecallVehicleLink.recall_id)
        .join(Vehicle, RecallVehicleLink.vehicle_id == Vehicle.id)
    )
    if limit:
        stmt = stmt.limit(limit)
    # Deduplicate recalls across vehicles
    seen_campaigns: set[str] = set()
    for recall in session.scalars(stmt).all():
        if recall.campaign_number in seen_campaigns:
            continue
        seen_campaigns.add(recall.campaign_number)

        stats.recalls_seen += 1
        try:
            # Get first linked vehicle for document construction
            links = (
                session.query(RecallVehicleLink)
                .filter(RecallVehicleLink.recall_id == recall.id)
                .all()
            )
            if not links:
                stats.documents_skipped += 1
                continue
            vehicle = session.get(Vehicle, links[0].vehicle_id)
            if not vehicle:
                stats.documents_skipped += 1
                continue

            doc = build_recall_document(recall, vehicle)
            chunks = chunk_document(doc.document_id, doc.full_text)

            if not chunks:
                stats.documents_skipped += 1
                continue

            if dry_run:
                stats.documents_created += 1
                stats.chunks_created += len(chunks)
                stats.embeddings_generated += len(chunks)
                continue

            status, c_created, c_updated, c_deleted, c_unchanged = vector_store.upsert_document(
                doc, [c.__dict__ for c in chunks], force_reembed=force_reembed
            )

            if status == "created":
                stats.documents_created += 1
            elif status == "updated":
                stats.documents_updated += 1
            elif status == "unchanged":
                stats.documents_unchanged += 1

            stats.chunks_created += c_created
            stats.chunks_updated += c_updated
            stats.chunks_deleted += c_deleted
            stats.chunks_unchanged += c_unchanged

            for c in chunks:
                emb = provider.embed_text(c.text)
                vector_store.set_chunk_embedding(
                    c.chunk_id,
                    emb,
                    embedding_model=provider.model_name,
                    embedding_dimension=provider.dimension,
                )
                stats.embeddings_generated += 1

        except Exception as e:
            stats.errors_count += 1
            errors.append(f"Recall {recall.campaign_number or recall.id}: {e}")


def retrieve_graphrag_evidence(
    question: str,
    *,
    top_k: int = 5,
    include_graph: bool = True,
    source_type: str | None = None,
    make: str | None = None,
    model: str | None = None,
    model_year: int | None = None,
) -> GraphRAGRetrievalResult:
    """
    High-level retrieval: semantic search + graph expansion.

    Flow:
    1. Validate inputs
    2. Semantic retrieval (vector search)
    3. Graph expansion (Neo4j)
    4. Build citations
    5. Assess confidence
    6. Add safety caveats

    Args:
        question: natural language question
        top_k: max chunks to retrieve (1–50)
        include_graph: expand results through Neo4j
        source_type: filter "complaint" or "recall"
        make: filter by vehicle make
        model: filter by vehicle model
        model_year: filter by vehicle year

    Returns:
        GraphRAGRetrievalResult with chunks, citations, graph paths, warnings
    """
    start_time = time.time()
    warnings: list[str] = []
    neo4j_available = True
    neo4j_error: str | None = None

    if top_k < 1:
        top_k = 1
    if top_k > 50:
        top_k = 50

    # ─── Semantic retrieval ────────────────────────────────────────────────────
    session = _pg_session()
    try:
        vector_store = VectorStore(session)
        retriever = GraphRAGRetriever(vector_store)
        chunks, citations = retriever.retrieve(
            question=question,
            top_k=top_k,
            source_type=source_type,
            make=make,
            model=model,
            model_year=model_year,
        )
    except Exception as e:
        logger.warning(f"Vector retrieval failed: {e}")
        chunks, citations = [], []
    finally:
        session.close()

    # ─── Graph expansion ──────────────────────────────────────────────────────
    graph_paths: list[GraphRAGGraphPath] = []
    if include_graph and chunks:
        try:
            neo4j_ok = verify_connectivity()
            if not neo4j_ok:
                neo4j_available = False
                neo4j_error = "Neo4j not connected"
            else:
                client = Neo4jClient()
                try:
                    chunk_dicts = [c.to_dict() for c in chunks]
                    graph_paths = expand_sources(client, chunk_dicts)
                finally:
                    client.close()
        except Exception as e:
            neo4j_available = False
            neo4j_error = str(e)
            logger.warning(f"Graph expansion failed: {e}")

    # ─── Confidence assessment ───────────────────────────────────────────────
    confidence_label, confidence_score, confidence_reasons = _assess_confidence(
        chunks, graph_paths, neo4j_available
    )

    # ─── Safety caveats ───────────────────────────────────────────────────────
    if chunks:
        warnings.append(CAVEAT_RETRIEVAL)
    if chunks or graph_paths:
        warnings.append(CAVEAT_SIMILARITY)
    if any(p.relation_source == "potentially_related_by_shared_component" for p in graph_paths):
        warnings.append(CAVEAT_SHARED_COMPONENT)
    warnings.append(CAVEAT_OFFICIAL_RECALL)

    return GraphRAGRetrievalResult(
        query=question,
        retrieval_mode="graphrag",
        retrieved_chunks=chunks,
        citations=citations,
        graph_paths=graph_paths,
        warnings=warnings,
        confidence_label=confidence_label,
        confidence_score=confidence_score,
        confidence_reasons=confidence_reasons,
        total_chunks_returned=len(chunks),
        execution_ms=int((time.time() - start_time) * 1000),
        neo4j_available=neo4j_available,
        neo4j_error=neo4j_error,
        phase="phase_6",
    )


def _assess_confidence(
    chunks: list[RetrievedChunk],
    graph_paths: list[GraphRAGGraphPath],
    neo4j_available: bool,
) -> tuple[str, float, list[str]]:
    """Assess retrieval confidence from results."""
    reasons: list[str] = []
    score = 0.3
    label = "low"

    if len(chunks) >= 3:
        reasons.append(f"Multiple relevant chunks retrieved: {len(chunks)}")
        score += 0.2
    elif len(chunks) >= 1:
        reasons.append(f"Relevant chunk retrieved: {len(chunks)}")
        score += 0.1

    avg_score = sum(c.score for c in chunks) / len(chunks) if chunks else 0.0
    if avg_score >= 0.8:
        reasons.append("High similarity scores")
        score += 0.2
    elif avg_score >= 0.6:
        reasons.append("Moderate similarity scores")
        score += 0.1

    if neo4j_available and graph_paths:
        official_paths = [
            p for p in graph_paths if p.relation_source == "official_recall_affects_vehicle"
        ]
        if official_paths:
            reasons.append("Official recall paths confirmed")
            score += 0.2

    score = min(1.0, max(0.0, score))

    if score >= 0.7:
        label = "high"
    elif score >= 0.5:
        label = "medium"

    if not chunks:
        reasons.append("No retrieval results")
        score = 0.1
        label = "low"

    return label, round(score, 2), reasons


def get_graphrag_status() -> GraphRAGStatus:
    """Get current GraphRAG index status."""
    from app.services.graphrag.embedding_provider import get_embedding_provider
    from app.services.graphrag.vector_store import _pgvector_available

    provider = get_embedding_provider()
    session = _pg_session()
    try:
        vector_store = VectorStore(session)
        doc_count = vector_store.get_document_count()
        chunk_count = vector_store.get_chunk_count()
        type_counts = vector_store.get_document_counts_by_type()

        # Detect active backend
        if _pgvector_available is None:
            from app.services.graphrag.vector_store import _check_pgvector_available

            is_pg = _check_pgvector_available(session)
            backend = "pgvector" if is_pg else "jsonb_fallback"
        elif _pgvector_available:
            backend = "pgvector"
        else:
            backend = "jsonb_fallback"

        session.commit()
        return GraphRAGStatus(
            vector_backend_available=True,
            vector_backend=backend,
            embedding_model=provider.model_name,
            embedding_dimension=provider.dimension,
            document_count=doc_count,
            chunk_count=chunk_count,
            complaints_indexed=type_counts.get("complaint", 0),
            recalls_indexed=type_counts.get("recall", 0),
        )
    except Exception as e:
        logger.warning(f"GraphRAG status check failed: {e}")
        return GraphRAGStatus(
            vector_backend_available=False,
            vector_backend="unavailable",
            embedding_model=provider.model_name,
            embedding_dimension=provider.dimension,
            error=str(e),
        )
    finally:
        session.close()
