"""
Semantic retriever — vector similarity search + citation assembly.

Combines embedding generation, vector search, and citation metadata construction.
"""

from __future__ import annotations

import logging

from app.services.graphrag.embedding_provider import EmbeddingProvider, get_embedding_provider
from app.services.graphrag.models import GraphRAGCitation, RetrievedChunk
from app.services.graphrag.vector_store import VectorSearchResult, VectorStore

logger = logging.getLogger(__name__)


class GraphRAGRetriever:
    """
    Semantic retrieval over indexed evidence documents.

    Flow:
    1. Generate query embedding
    2. Search vector store
    3. Deduplicate source records
    4. Build citation metadata
    """

    def __init__(
        self,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider | None = None,
    ):
        self.vector_store = vector_store
        self.provider = embedding_provider or get_embedding_provider()

    def retrieve(
        self,
        question: str,
        top_k: int = 5,
        source_type: str | None = None,
        make: str | None = None,
        model: str | None = None,
        model_year: int | None = None,
    ) -> tuple[list[RetrievedChunk], list[GraphRAGCitation]]:
        """
        Retrieve relevant chunks and citations for a question.

        Args:
            question: natural language question
            top_k: maximum chunks to return
            source_type: filter "complaint" or "recall"
            make: filter by vehicle make
            model: filter by vehicle model
            model_year: filter by vehicle year

        Returns:
            (retrieved_chunks, citations)
        """
        if top_k < 1:
            top_k = 1
        if top_k > 50:
            top_k = 50

        # Generate query embedding
        query_embedding = self.provider.embed_text(question)

        # Search vector store
        results = self.vector_store.search_similar(
            query_embedding=query_embedding,
            top_k=top_k,
            source_type=source_type,
            make=make,
            model=model,
            model_year=model_year,
            min_score=0.0,
        )

        # Deduplicate: keep highest-scoring chunk per source_record_key
        seen_keys: dict[str, RetrievedChunk] = {}
        for row in results:
            key = f"{row.source_type}:{row.source_record_key}"
            if key not in seen_keys:
                chunk = self._result_to_chunk(row)
                seen_keys[key] = chunk

        chunks = list(seen_keys.values())

        # Build citations
        citations = self._build_citations(chunks)

        return chunks, citations

    def _result_to_chunk(self, result: VectorSearchResult) -> RetrievedChunk:
        """Convert a vector search result to a RetrievedChunk."""
        meta = result.metadata_json or {}
        title = meta.get("title", f"{result.source_type} {result.source_record_key}")
        return RetrievedChunk(
            chunk_id=result.chunk_id,
            score=result.score,
            source_type=result.source_type,
            source_entity_id=result.document_id,
            source_record_key=result.source_record_key,
            title=title,
            text=result.chunk_text,
            source_url=meta.get("source_url"),
            make=meta.get("make"),
            model=meta.get("model"),
            model_year=meta.get("model_year"),
            component=meta.get("component"),
            citation_label=self._make_citation_label(
                result.source_type,
                result.source_record_key,
                meta.get("make"),
                meta.get("model"),
            ),
        )

    def _build_citations(self, chunks: list[RetrievedChunk]) -> list[GraphRAGCitation]:
        """Build citation metadata from retrieved chunks."""
        citations: list[GraphRAGCitation] = []
        for chunk in chunks:
            citations.append(
                GraphRAGCitation(
                    source_type=chunk.source_type,
                    source_id=chunk.source_entity_id,
                    source_key=chunk.source_record_key,
                    citation_label=chunk.citation_label,
                    text_span=chunk.text[:200] if chunk.text else None,
                    confidence=chunk.score,
                )
            )
        return citations

    def _make_citation_label(
        self,
        source_type: str,
        source_key: str,
        make: str | None,
        model: str | None,
    ) -> str:
        """Build a human-readable citation label."""
        vehicle = f"{make or '?'} {model or '?'}".strip()
        if source_type == "complaint":
            return f"Complaint {source_key}" + (f" - {vehicle}" if vehicle != "?" else "")
        elif source_type == "recall":
            return f"Recall {source_key}" + (f" - {vehicle}" if vehicle != "?" else "")
        return f"{source_type} {source_key}"
