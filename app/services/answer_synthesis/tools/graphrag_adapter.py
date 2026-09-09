"""
GraphRAG Retrieval adapter — Phase 7B.

Wraps Phase 6 retrieve_graphrag_evidence() through a structured tool interface.
GraphRAG is read-only. No synthesis. No embeddings exposed. Max 20 chunks.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services.answer_synthesis.tools.base import (
    ToolCallResult,
    ToolDefinition,
    ToolInputField,
    ToolInputSchema,
)

SUPPORTED_OPERATIONS = [
    "retrieve_complaints_and_recalls",
    "retrieve_complaints_only",
    "retrieve_recalls_only",
]


GRAPHRAG_RETRIEVAL_DEFINITION = ToolDefinition(
    name="graphrag_retrieval_tool",
    description=(
        "Retrieve semantic complaint and recall evidence from the indexed vehicle safety corpus. "
        "Uses vector similarity search over embedded evidence chunks. "
        "Returns chunks, citations, graph paths, and confidence. "
        "No raw embeddings exposed. No synthesis performed."
    ),
    input_schema=ToolInputSchema(fields={
        "operation": ToolInputField(
            type="enum",
            description="Retrieval operation: retrieve both, complaints only, or recalls only",
            required=True,
            enum_values=SUPPORTED_OPERATIONS,
        ),
        "question": ToolInputField(
            type="string",
            description="Natural language question (max 500 characters)",
            required=True,
            max_length=500,
        ),
        "top_k": ToolInputField(
            type="integer",
            description="Maximum chunks to retrieve (default 5, max 20)",
            required=False,
            default=5,
            min_value=1,
            max_value=20,
        ),
        "make": ToolInputField(
            type="string",
            description="Vehicle make filter (e.g. Ford)",
            required=False,
            max_length=50,
        ),
        "model": ToolInputField(
            type="string",
            description="Vehicle model filter (e.g. F-150)",
            required=False,
            max_length=50,
        ),
        "model_year": ToolInputField(
            type="integer",
            description="Vehicle model year filter",
            required=False,
            min_value=1990,
            max_value=2030,
        ),
        "include_graph": ToolInputField(
            type="boolean",
            description="Include Neo4j graph expansion (default true)",
            required=False,
            default=True,
        ),
    }),
    read_only=True,
    max_result_items=20,
    timeout_seconds=15,
)


def build_graphrag_adapter(
    retrieval_fn: Callable[..., Any],
) -> Callable[..., ToolCallResult]:
    """
    Build a GraphRAG retrieval tool adapter.

    Args:
        retrieval_fn: callable matching `retrieve_graphrag_evidence` signature.
                     Must accept: question, top_k, source_type, make, model,
                     model_year, include_graph
                     Must return: GraphRAGRetrievalResult
    """
    def adapter(*, call_id: str, arguments: dict[str, Any]) -> ToolCallResult:
        operation = arguments.get("operation")
        question = arguments.get("question", "")
        top_k = arguments.get("top_k", 5)
        make = arguments.get("make")
        model = arguments.get("model")
        model_year = arguments.get("model_year")
        include_graph = arguments.get("include_graph", True)

        # Map operation to source_type
        source_type = _operation_to_source_type(str(operation or ""))

        try:
            result = retrieval_fn(
                question=question,
                top_k=top_k,
                source_type=source_type,
                make=make,
                model=model,
                model_year=model_year,
                include_graph=include_graph,
            )

            # Bound chunks to max_result_items
            chunks = result.retrieved_chunks[:GRAPHRAG_RETRIEVAL_DEFINITION.max_result_items]
            truncated_chunks = len(result.retrieved_chunks) > GRAPHRAG_RETRIEVAL_DEFINITION.max_result_items

            # Build citation table
            citations = []
            for c in result.citations[:GRAPHRAG_RETRIEVAL_DEFINITION.max_result_items]:
                citations.append({
                    "source_type": c.source_type,
                    "source_id": c.source_id,
                    "source_key": c.source_key,
                    "citation_label": c.citation_label,
                    "text_span": (c.text_span or "")[:500],
                    "confidence": c.confidence,
                })

            # Bound graph paths
            graph_paths = result.graph_paths[:20]
            truncated_paths = len(result.graph_paths) > 20

            result_data = {
                "operation": operation,
                "question": question,
                "retrieval_mode": result.retrieval_mode,
                "retrieved_chunks": [_chunk_to_dict(c) for c in chunks],
                "citations": citations,
                "graph_paths": [_path_to_dict(p) for p in graph_paths],
                "total_chunks_returned": len(chunks),
                "neo4j_available": result.neo4j_available,
                "neo4j_error": result.neo4j_error,
                "confidence_label": result.confidence_label,
                "confidence_score": result.confidence_score,
                "confidence_reasons": result.confidence_reasons,
            }

            warnings = list(result.warnings or [])
            truncated = truncated_chunks or truncated_paths

            return ToolCallResult.ok(call_id, "graphrag_retrieval_tool", result_data, warnings, truncated)

        except Exception as e:
            return ToolCallResult.error(
                call_id, "graphrag_retrieval_tool",
                "retrieval_error",
                f"GraphRAG retrieval failed: {type(e).__name__}",
            )

    return adapter


def _operation_to_source_type(operation: str) -> str | None:
    """Map tool operation to Phase 6 source_type filter."""
    if operation == "retrieve_complaints_only":
        return "complaint"
    elif operation == "retrieve_recalls_only":
        return "recall"
    return None  # "retrieve_complaints_and_recalls"


def _chunk_to_dict(chunk) -> dict:
    """Serialize a RetrievedChunk safely."""
    return {
        "chunk_id": chunk.chunk_id,
        "score": chunk.score,
        "source_type": chunk.source_type,
        "source_record_key": chunk.source_record_key,
        "title": chunk.title,
        "text": chunk.text[:2000],  # bound text
        "make": chunk.make,
        "model": chunk.model,
        "model_year": chunk.model_year,
        "component": chunk.component,
        "citation_label": chunk.citation_label,
    }


def _path_to_dict(path) -> dict:
    """Serialize a GraphRAGGraphPath safely."""
    return {
        "path_text": path.path_text,
        "relation_source": path.relation_source,
        "source_type": getattr(path, 'source_type', path.relation_source),
        "source_key": getattr(path, 'source_key', ''),
        "confidence": path.confidence,
    }
