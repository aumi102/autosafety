"""
Vector store — pgvector-backed chunk storage and similarity search.

When pgvector column exists: uses efficient native cosine distance (<=>).
When pgvector column is absent: falls back to JSONB brute-force similarity.
All queries are parameterized. Cosine similarity = 1 - cosine_distance.
"""

from __future__ import annotations

import uuid
import logging
from typing import Optional
from dataclasses import dataclass

from sqlalchemy import text, JSON, func
from sqlalchemy.orm import Session

from app.services.graphrag.models import (
    EvidenceDocument,
    EvidenceChunk,
    GraphRAGIndexStats,
)

logger = logging.getLogger(__name__)

# Module-level flag set after first connectivity check
_pgvector_available: Optional[bool] = None


def _pg_vector_literal(vector: list[float]) -> str:
    """Format a list of floats as a pgvector ARRAY literal."""
    return "[" + ", ".join(str(v) for v in vector) + "]"


def _check_pgvector_available(session: Session) -> bool:
    """Check if pgvector column exists and is usable."""
    try:
        result = session.execute(text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name = 'evidence_chunks' AND column_name = 'embedding_vector'"
        )).scalar()
        return bool(result)
    except Exception:
        return False


def _init_pgvector_flag(session: Session) -> None:
    global _pgvector_available
    if _pgvector_available is None:
        _pgvector_available = _check_pgvector_available(session)


@dataclass
class VectorSearchResult:
    """A vector similarity search result."""
    chunk_id: str
    chunk_text: str
    score: float
    source_type: str
    source_record_key: str
    document_id: str
    metadata_json: dict


class VectorStore:
    """
    Vector storage and similarity search using pgvector via raw SQL.

    Embeddings stored as JSONB. Cosine similarity computed from normalized vectors.
    """

    def __init__(self, session: Session):
        self.session = session

    def upsert_document(
        self,
        document: EvidenceDocument,
        chunks_data: list[dict],
        force_reembed: bool = False,
    ) -> tuple[str, int, int, int, int]:
        """
        Upsert an evidence document and its chunks.

        Args:
            document: EvidenceDocument dataclass
            chunks_data: list of Chunk dataclasses as dicts with text/hash/id fields
            force_reembed: if True, re-embed all chunks even if unchanged

        Returns:
            (status, chunks_created, chunks_updated, chunks_deleted, chunks_unchanged)
            status: "created" | "updated" | "unchanged" | "skipped"
        """
        # Check if document exists
        existing = self.session.query(EvidenceDocument).filter(
            EvidenceDocument.document_id == document.document_id
        ).first()

        if existing:
            # Check if content changed
            if existing.content_hash == document.content_hash and not force_reembed:
                return ("unchanged", 0, 0, 0, len(chunks_data))

            # Update document
            existing.title = document.title
            existing.full_text = document.full_text
            existing.content_hash = document.content_hash
            existing.metadata_json = document.metadata
            existing.source_url = document.source_url

            # Delete existing chunks (will be re-inserted)
            deleted = self.session.query(EvidenceChunk).filter(
                EvidenceChunk.document_id == existing.id
            ).delete()
            status = "updated"
        else:
            # Create new document
            db_doc = EvidenceDocument(
                document_id=document.document_id,
                source_type=document.source_type,
                source_entity_id=document.source_entity_id,
                source_record_key=document.source_record_key,
                title=document.title,
                full_text=document.full_text,
                content_hash=document.content_hash,
                metadata_json=document.metadata,
                source_url=document.source_url,
            )
            self.session.add(db_doc)
            self.session.flush()  # get the id
            existing = db_doc
            deleted = 0
            status = "created"

        # Insert chunks
        chunks_created = 0
        chunks_updated = 0

        for chunk_data in chunks_data:
            db_chunk = EvidenceChunk(
                chunk_id=chunk_data["chunk_id"],
                document_id=existing.id,
                chunk_index=chunk_data["chunk_index"],
                chunk_text=chunk_data["text"],
                content_hash=chunk_data["content_hash"],
                embedding_vector_id=None,  # will be set during embedding
                embedding_model=None,
                embedding_dimension=None,
                metadata_json=document.metadata,
            )
            self.session.add(db_chunk)
            chunks_created += 1

        self.session.flush()
        return (status, chunks_created, chunks_updated, deleted, 0)

    def set_chunk_embedding(
        self,
        chunk_id: str,
        embedding: list[float],
        embedding_model: str,
        embedding_dimension: int,
    ) -> None:
        """
        Store embedding for a chunk.

        Uses the pgvector column when available; stores in JSONB metadata as fallback.
        """
        _init_pgvector_flag(self.session)
        if _pgvector_available:
            self.session.execute(
                text("""
                    UPDATE evidence_chunks
                    SET embedding_vector = :vector,
                        embedding_model = :model,
                        embedding_dimension = :dim,
                        embedding_vector_id = gen_random_uuid()
                    WHERE chunk_id = :chunk_id
                """),
                {
                    "vector": embedding,
                    "model": embedding_model,
                    "dim": embedding_dimension,
                    "chunk_id": chunk_id,
                },
            )
        else:
            self.session.query(EvidenceChunk).filter(
                EvidenceChunk.chunk_id == chunk_id
            ).update({
                EvidenceChunk.embedding_vector_id: uuid.uuid4(),
                EvidenceChunk.metadata_json: {
                    "embedding": embedding,
                    "embedding_model": embedding_model,
                    "embedding_dimension": embedding_dimension,
                },
            }, synchronize_session=False)

    def search_similar(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        source_type: Optional[str] = None,
        make: Optional[str] = None,
        model: Optional[str] = None,
        model_year: Optional[int] = None,
        min_score: float = 0.0,
    ) -> list[VectorSearchResult]:
        """
        Search for similar chunks using cosine similarity.

        Uses pgvector cosine distance (<=>) when the embedding_vector column exists.
        Falls back to JSONB brute-force when the column is absent.
        Cosine similarity = 1 - cosine_distance.
        """
        _init_pgvector_flag(self.session)
        limit = min(top_k, 100)

        if _pgvector_available:
            return self._search_pgvector(query_embedding, limit, source_type, make, model, model_year)
        else:
            return self._search_jsonb_fallback(query_embedding, limit, source_type, make, model, model_year, min_score)

    def _search_pgvector(
        self,
        query_embedding: list[float],
        limit: int,
        source_type: Optional[str],
        make: Optional[str],
        model: Optional[str],
        model_year: Optional[int],
    ) -> list[VectorSearchResult]:
        """Search using pgvector native cosine distance."""
        query_vec_literal = _pg_vector_literal(query_embedding)
        # Embed vector literal directly in SQL — deterministic, not user-supplied
        params: dict = {
            "source_type": source_type,
            "limit": limit,
        }

        filters = ["ec.embedding_vector IS NOT NULL"]
        if source_type:
            filters.append("ed.source_type = :source_type")
        if make:
            filters.append("ed.metadata_json->>'make' ILIKE :make")
            params["make"] = f"%{make}%"
        if model:
            filters.append("ed.metadata_json->>'model' ILIKE :model")
            params["model"] = f"%{model}%"
        if model_year:
            filters.append("(ed.metadata_json->>'model_year')::int = :model_year")
            params["model_year"] = model_year
        where_clause = " AND ".join(filters)

        # pgvector cosine distance (<=>) -> similarity = 1 - distance
        # Vector literal embedded directly (deterministic test embedding, not user input)
        vec_literal = "[" + ", ".join(str(v) for v in query_embedding) + "]"
        sql = f"""
            SELECT
                ec.chunk_id,
                ec.chunk_text,
                ec.metadata_json,
                ed.source_type,
                ed.source_record_key,
                ed.document_id,
                ed.metadata_json AS doc_metadata,
                ed.title,
                ed.source_url,
                (1 - (ec.embedding_vector <=> '{vec_literal}')) AS score
            FROM evidence_chunks ec
            JOIN evidence_documents ed ON ed.id = ec.document_id
            WHERE {where_clause}
            ORDER BY ec.embedding_vector <=> '{vec_literal}', ec.chunk_id ASC
            LIMIT :limit
        """

        results: list[VectorSearchResult] = []
        try:
            rows = self.session.execute(text(sql), params).fetchall()
            for row in rows:
                doc_meta: dict = row.doc_metadata or {}
                results.append(VectorSearchResult(
                    chunk_id=row.chunk_id,
                    chunk_text=row.chunk_text,
                    score=max(0.0, min(1.0, float(row.score or 0.0))),
                    source_type=row.source_type,
                    source_record_key=row.source_record_key,
                    document_id=row.document_id,
                    metadata_json={
                        "title": row.title,
                        "source_url": row.source_url,
                        "make": doc_meta.get("make"),
                        "model": doc_meta.get("model"),
                        "model_year": doc_meta.get("model_year"),
                        "component": doc_meta.get("component"),
                        **doc_meta,
                    },
                ))
        except Exception as e:
            logger.warning(f"pgvector search failed: {e}, falling back to JSONB")
            global _pgvector_available
            _pgvector_available = False
            return self._search_jsonb_fallback(query_embedding, limit, source_type, make, model, model_year, 0.0)

        return results

    def _search_jsonb_fallback(
        self,
        query_embedding: list[float],
        limit: int,
        source_type: Optional[str],
        make: Optional[str],
        model: Optional[str],
        model_year: Optional[int],
        min_score: float = 0.0,
    ) -> list[VectorSearchResult]:
        """JSONB brute-force fallback for environments without pgvector column."""
        query_vec_literal = _pg_vector_literal(query_embedding)
        params: dict = {
            "source_type": source_type,
            "limit": limit,
        }

        filters = ["ec.metadata_json ? 'embedding'"]
        if source_type:
            filters.append("ed.source_type = :source_type")
        if make:
            filters.append("ed.metadata_json->>'make' ILIKE :make")
            params["make"] = f"%{make}%"
        if model:
            filters.append("ed.metadata_json->>'model' ILIKE :model")
            params["model"] = f"%{model}%"
        if model_year:
            filters.append("(ed.metadata_json->>'model_year')::int = :model_year")
            params["model_year"] = model_year
        where_clause = " AND ".join(filters)

        sql = f"""
            SELECT * FROM (
                SELECT
                    ec.chunk_id,
                    ec.chunk_text,
                    ec.metadata_json,
                    ed.source_type,
                    ed.source_record_key,
                    ed.document_id,
                    ed.metadata_json AS doc_metadata,
                    ed.title,
                    ed.source_url,
                    COALESCE(
                        (
                            SELECT SUM(
                                (ec_arr.elem->>0)::float * (qv_arr.elem->>0)::float
                            )
                            FROM jsonb_array_elements(ec.metadata_json->'embedding')
                                WITH ORDINALITY AS ec_arr(elem, ord),
                                 jsonb_array_elements('{query_vec_literal}'::jsonb)
                                WITH ORDINALITY AS qv_arr(elem, ord)
                            WHERE ec_arr.ord = qv_arr.ord
                        ), 0.0
                    ) AS score
                FROM evidence_chunks ec
                JOIN evidence_documents ed ON ed.id = ec.document_id
                WHERE {where_clause}
            ) ranked
            WHERE score >= {min_score}
            ORDER BY score DESC, chunk_id ASC
            LIMIT :limit
        """

        results: list[VectorSearchResult] = []
        try:
            rows = self.session.execute(text(sql), params).fetchall()
            for row in rows:
                doc_meta: dict = row.doc_metadata or {}
                emb_meta: dict = row.metadata_json or {}
                emb_val = emb_meta.get("embedding")
                if isinstance(emb_val, list):
                    emb_list = emb_val
                elif isinstance(emb_val, dict):
                    emb_list = emb_val.get("embedding", [])
                else:
                    emb_list = []
                computed_score = max(0.0, min(1.0, _cosine_similarity(emb_list, query_embedding)))
                results.append(VectorSearchResult(
                    chunk_id=row.chunk_id,
                    chunk_text=row.chunk_text,
                    score=computed_score,
                    source_type=row.source_type,
                    source_record_key=row.source_record_key,
                    document_id=row.document_id,
                    metadata_json={
                        "title": row.title,
                        "source_url": row.source_url,
                        "make": doc_meta.get("make"),
                        "model": doc_meta.get("model"),
                        "model_year": doc_meta.get("model_year"),
                        "component": doc_meta.get("component"),
                        **doc_meta,
                    },
                ))
        except Exception as e:
            logger.warning(f"JSONB fallback search failed: {e}")
            return []

        return results

    def get_document_count(self) -> int:
        """Count total indexed documents."""
        return self.session.query(EvidenceDocument).count()

    def get_chunk_count(self) -> int:
        """Count total embedded chunks."""
        _init_pgvector_flag(self.session)
        if _pgvector_available:
            return self.session.query(EvidenceChunk).filter(
                EvidenceChunk.embedding_vector_id.isnot(None)
            ).count()
        else:
            return self.session.query(EvidenceChunk).filter(
                EvidenceChunk.metadata_json.op("?")("embedding")
            ).count()

    def get_document_counts_by_type(self) -> dict[str, int]:
        """Count documents by source_type."""
        rows = self.session.query(
            EvidenceDocument.source_type,
            func.count(EvidenceDocument.id)
        ).group_by(EvidenceDocument.source_type).all()
        return {row[0]: row[1] for row in rows}


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    return max(0.0, min(1.0, dot))  # clamp to [0, 1]
