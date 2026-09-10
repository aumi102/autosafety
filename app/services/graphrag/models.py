"""
GraphRAG models — Pydantic/dataclass types and SQLAlchemy persistence models.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ─── Enums ────────────────────────────────────────────────────────────────────


class SourceType(str, Enum):
    COMPLAINT = "complaint"
    RECALL = "recall"


# ─── Pydantic/dataclass response types ────────────────────────────────────────


@dataclass
class GraphRAGIndexStats:
    """Statistics from a GraphRAG indexing run."""

    complaints_seen: int = 0
    recalls_seen: int = 0
    documents_created: int = 0
    documents_updated: int = 0
    documents_unchanged: int = 0
    documents_skipped: int = 0
    chunks_created: int = 0
    chunks_updated: int = 0
    chunks_deleted: int = 0
    chunks_unchanged: int = 0
    embeddings_generated: int = 0
    errors_count: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0
    dry_run: bool = False

    def to_dict(self) -> dict:
        return {
            "complaints_seen": self.complaints_seen,
            "recalls_seen": self.recalls_seen,
            "documents_created": self.documents_created,
            "documents_updated": self.documents_updated,
            "documents_unchanged": self.documents_unchanged,
            "documents_skipped": self.documents_skipped,
            "chunks_created": self.chunks_created,
            "chunks_updated": self.chunks_updated,
            "chunks_deleted": self.chunks_deleted,
            "chunks_unchanged": self.chunks_unchanged,
            "embeddings_generated": self.embeddings_generated,
            "errors_count": self.errors_count,
            "errors": self.errors,
            "duration_ms": self.duration_ms,
            "dry_run": self.dry_run,
        }


@dataclass
class RetrievedChunk:
    """A single retrieved semantic chunk."""

    chunk_id: str
    score: float
    source_type: str  # "complaint" | "recall"
    source_entity_id: str  # UUID as string
    source_record_key: str  # ODI number or campaign number
    title: str
    text: str
    source_url: str | None
    make: str | None
    model: str | None
    model_year: int | None
    component: str | None
    citation_label: str
    chunk_index: int = 0

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "score": self.score,
            "source_type": self.source_type,
            "source_entity_id": self.source_entity_id,
            "source_record_key": self.source_record_key,
            "title": self.title,
            "text": self.text,
            "source_url": self.source_url,
            "make": self.make,
            "model": self.model,
            "model_year": self.model_year,
            "component": self.component,
            "citation_label": self.citation_label,
            "chunk_index": self.chunk_index,
        }


@dataclass
class GraphRAGCitation:
    """A citation derived from a retrieved chunk."""

    source_type: str
    source_id: str
    source_key: str
    citation_label: str
    text_span: str | None = None
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_key": self.source_key,
            "citation_label": self.citation_label,
            "text_span": self.text_span,
            "confidence": self.confidence,
        }


@dataclass
class GraphRAGGraphPath:
    """A graph path from Neo4j expansion."""

    path_text: str
    relation_source: str  # complaint_mentions_component | official_recall_affects_vehicle | etc.
    source_type: str
    source_key: str
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {
            "path_text": self.path_text,
            "relation_source": self.relation_source,
            "source_type": self.source_type,
            "source_key": self.source_key,
            "confidence": self.confidence,
        }


@dataclass
class GraphRAGRetrievalResult:
    """Result of a GraphRAG retrieval query."""

    query: str
    retrieval_mode: str = "graphrag"
    retrieved_chunks: list[RetrievedChunk] = field(default_factory=list)
    citations: list[GraphRAGCitation] = field(default_factory=list)
    graph_paths: list[GraphRAGGraphPath] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence_label: str = "low"
    confidence_score: float = 0.0
    confidence_reasons: list[str] = field(default_factory=list)
    total_chunks_returned: int = 0
    execution_ms: int = 0
    neo4j_available: bool = True
    neo4j_error: str | None = None
    phase: str = "phase_6"

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "retrieval_mode": self.retrieval_mode,
            "retrieved_chunks": [c.to_dict() for c in self.retrieved_chunks],
            "citations": [c.to_dict() for c in self.citations],
            "graph_paths": [p.to_dict() for p in self.graph_paths],
            "warnings": self.warnings,
            "confidence": {
                "label": self.confidence_label,
                "score": self.confidence_score,
                "reasons": self.confidence_reasons,
            },
            "total_chunks_returned": self.total_chunks_returned,
            "execution_ms": self.execution_ms,
            "neo4j_available": self.neo4j_available,
            "neo4j_error": self.neo4j_error,
            "phase": self.phase,
        }


@dataclass
class GraphRAGStatus:
    """Status of the GraphRAG index."""

    vector_backend_available: bool
    embedding_model: str
    embedding_dimension: int
    vector_backend: str = "unknown"  # "pgvector" | "jsonb_fallback" | "unavailable"
    document_count: int = 0
    chunk_count: int = 0
    complaints_indexed: int = 0
    recalls_indexed: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "vector_backend_available": self.vector_backend_available,
            "vector_backend": self.vector_backend,
            "embedding_model": self.embedding_model,
            "embedding_dimension": self.embedding_dimension,
            "document_count": self.document_count,
            "chunk_count": self.chunk_count,
            "complaints_indexed": self.complaints_indexed,
            "recalls_indexed": self.recalls_indexed,
            "error": self.error,
            "phase": "phase_6",
        }


# ─── SQLAlchemy persistence models ──────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EvidenceDocument(Base):
    """
    Canonical evidence document for a single source entity (complaint or recall).
    One document per ODI number (complaint) or campaign number (recall).
    """

    __tablename__ = "evidence_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "complaint" | "recall"
    source_entity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_record_key: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )  # ODI number or campaign number
    title: Mapped[str] = mapped_column(Text, nullable=False)
    full_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    chunks: Mapped[list[EvidenceChunk]] = relationship(
        "EvidenceChunk", back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("source_type", "source_record_key", name="uq_doc_source_key"),
        Index("ix_evidence_documents_source_type", "source_type"),
    )


class EvidenceChunk(Base):
    """
    A deterministically chunked piece of an evidence document.
    Each chunk is embedded for semantic similarity search.
    """

    __tablename__ = "evidence_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chunk_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # pgvector column — type registered at migration time
    embedding_vector_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    document: Mapped[EvidenceDocument] = relationship("EvidenceDocument", back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunk_doc_index"),
        Index("ix_evidence_chunks_document_id", "document_id"),
    )


# ─── Hash utilities ────────────────────────────────────────────────────────────


def content_hash(text: str) -> str:
    """Stable SHA-256 hash of text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def document_id_for_complaint(odi_number: str) -> str:
    """Stable document ID for a complaint."""
    return f"complaint:{odi_number}"


def document_id_for_recall(campaign_number: str) -> str:
    """Stable document ID for a recall."""
    return f"recall:{campaign_number}"


def chunk_id(document_id: str, chunk_index: int, chunk_text: str) -> str:
    """Stable chunk ID from document + index + text."""
    raw = f"{document_id}:{chunk_index}:{chunk_text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
