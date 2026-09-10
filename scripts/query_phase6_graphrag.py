#!/usr/bin/env python3
"""
CLI: Query GraphRAG semantic retrieval.

Usage:
    python scripts/query_phase6_graphrag.py \
        --question "brake complaints for Ford F-150 2020" --top-k 5
    python scripts/query_phase6_graphrag.py --question "recall evidence for Honda" --no-graph
"""

import argparse
import sys

from app.services.graphrag import retrieve_graphrag_evidence


def main():
    parser = argparse.ArgumentParser(description="Query GraphRAG semantic retrieval")
    parser.add_argument("--question", "-q", required=True, help="Question to retrieve evidence for")
    parser.add_argument("--top-k", type=int, default=5, help="Max chunks to retrieve (1-50)")
    parser.add_argument("--no-graph", action="store_true", help="Skip graph expansion")
    parser.add_argument(
        "--source-type", choices=["complaint", "recall"], default=None, help="Filter by source type"
    )
    parser.add_argument("--make", default=None, help="Filter by vehicle make")
    parser.add_argument("--model", default=None, help="Filter by vehicle model")
    parser.add_argument("--model-year", type=int, default=None, help="Filter by vehicle year")

    args = parser.parse_args()

    print(f"Query: {args.question}")
    print(f"  top_k: {args.top_k}")
    print(f"  include_graph: {not args.no_graph}")
    print(f"  source_type: {args.source_type}")
    print(f"  make: {args.make}")
    print(f"  model: {args.model}")
    print(f"  model_year: {args.model_year}")

    result = retrieve_graphrag_evidence(
        question=args.question,
        top_k=args.top_k,
        include_graph=not args.no_graph,
        source_type=args.source_type,
        make=args.make,
        model=args.model,
        model_year=args.model_year,
    )

    print(f"\nResults ({result.execution_ms}ms):")
    print(f"  Chunks returned: {result.total_chunks_returned}")
    print(f"  Graph paths: {len(result.graph_paths)}")
    print(f"  Neo4j available: {result.neo4j_available}")
    print(f"  Confidence: {result.confidence_label} ({result.confidence_score})")
    for r in result.confidence_reasons:
        print(f"    - {r}")

    print("\nWarnings:")
    for w in result.warnings:
        print(f"  ! {w}")

    if result.retrieved_chunks:
        print("\nRetrieved Evidence:")
        for i, chunk in enumerate(result.retrieved_chunks, 1):
            print(f"\n  [{i}] {chunk.citation_label}")
            print(f"      Source: {chunk.source_type} | {chunk.source_record_key}")
            print(f"      Score: {chunk.score:.4f}")
            print(f"      Text: {chunk.text[:200]}{'...' if len(chunk.text) > 200 else ''}")
            if chunk.source_url:
                print(f"      URL: {chunk.source_url}")

    if result.graph_paths:
        print("\nGraph Paths:")
        for i, path in enumerate(result.graph_paths[:10], 1):
            print(f"\n  [{i}] {path.path_text}")
            print(f"      Relation: {path.relation_source} (confidence: {path.confidence})")

    if result.neo4j_error:
        print(f"\nGraph error: {result.neo4j_error}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
