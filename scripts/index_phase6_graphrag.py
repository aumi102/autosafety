#!/usr/bin/env python3
"""
CLI: Index GraphRAG evidence documents.

Usage:
    python scripts/index_phase6_graphrag.py --dry-run --source-type all
    python scripts/index_phase6_graphrag.py --source-type all --limit 50
    python scripts/index_phase6_graphrag.py --source-type complaint --force-reembed
    python scripts/index_phase6_graphrag.py --status
"""

import argparse
import sys

from app.services.graphrag import (
    get_graphrag_status,
    index_graphrag_documents,
)


def main():
    parser = argparse.ArgumentParser(description="Index GraphRAG evidence documents")
    parser.add_argument("--dry-run", action="store_true", help="Count without writing")
    parser.add_argument(
        "--source-type",
        choices=["complaint", "recall", "all"],
        default="all",
        help="Source type to index",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max records per type")
    parser.add_argument("--force-reembed", action="store_true", help="Re-embed unchanged docs")
    parser.add_argument("--status", action="store_true", help="Show index status")

    args = parser.parse_args()

    if args.status:
        status = get_graphrag_status()
        print("GraphRAG Index Status")
        print(
            f"  Vector backend: {'available' if status.vector_backend_available else 'unavailable'}"
        )
        print(f"  Embedding model: {status.embedding_model}")
        print(f"  Embedding dimension: {status.embedding_dimension}")
        print(f"  Documents: {status.document_count}")
        print(f"  Chunks: {status.chunk_count}")
        print(f"  Complaints indexed: {status.complaints_indexed}")
        print(f"  Recalls indexed: {status.recalls_indexed}")
        if status.error:
            print(f"  Error: {status.error}")
        return 0

    print("Indexing GraphRAG documents...")
    print(f"  source_type: {args.source_type}")
    print(f"  dry_run: {args.dry_run}")
    print(f"  limit: {args.limit}")
    print(f"  force_reembed: {args.force_reembed}")

    stats = index_graphrag_documents(
        dry_run=args.dry_run,
        source_type=args.source_type if args.source_type != "all" else None,
        limit=args.limit,
        force_reembed=args.force_reembed,
    )

    print(f"\nIndexing complete ({stats.duration_ms}ms)")
    print(f"  complaints_seen: {stats.complaints_seen}")
    print(f"  recalls_seen: {stats.recalls_seen}")
    print(f"  documents_created: {stats.documents_created}")
    print(f"  documents_updated: {stats.documents_updated}")
    print(f"  documents_unchanged: {stats.documents_unchanged}")
    print(f"  documents_skipped: {stats.documents_skipped}")
    print(f"  chunks_created: {stats.chunks_created}")
    print(f"  chunks_updated: {stats.chunks_updated}")
    print(f"  chunks_deleted: {stats.chunks_deleted}")
    print(f"  chunks_unchanged: {stats.chunks_unchanged}")
    print(f"  embeddings_generated: {stats.embeddings_generated}")
    print(f"  errors_count: {stats.errors_count}")

    if stats.errors:
        print("\nErrors:")
        for err in stats.errors[:10]:
            print(f"  - {err}")

    if stats.errors_count > 0:
        print(f"\n[WARNING] {stats.errors_count} errors occurred")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
