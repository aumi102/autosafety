#!/usr/bin/env python3
"""
Phase 6 GraphRAG evaluation runner.

Computes Recall@1, Recall@3, and MRR over the evaluation fixture.
Uses the DeterministicTestProvider for reproducible results.

Usage:
    python scripts/evaluate_phase6_graphrag.py
    python scripts/evaluate_phase6_graphrag.py --fixture tests/fixtures/phase6_graphrag_eval.json --verbose
"""

import argparse
import json
import sys
from pathlib import Path

# ─── Metrics ────────────────────────────────────────────────────────────────────

def recall_at_k(
    retrieved_source_keys: list[str],
    expected_source_keys: list[str],
    k: int,
) -> float:
    """
    Fraction of queries where expected source(s) appear in top-k results.
    Only computed for queries with non-empty expected_source_keys.
    """
    if not expected_source_keys:
        return None
    top_k = retrieved_source_keys[:k]
    hits = sum(1 for ek in expected_source_keys if _matches(ek, top_k))
    return hits / len(expected_source_keys)


def _matches(expected: str, retrieved: list[str]) -> bool:
    """Check if expected source key matches any retrieved key."""
    for r in retrieved:
        if expected == r:
            return True
        # Wildcard: "complaint:*" matches any complaint key
        if expected.endswith(":*"):
            prefix = expected[:-1]
            if r.startswith(prefix):
                return True
    return False


def mrr(
    retrieved_source_keys: list[str],
    expected_source_keys: list[str],
) -> float | None:
    """
    Mean Reciprocal Rank: average of 1/rank for first expected hit.
    None if no expected keys.
    """
    if not expected_source_keys:
        return None
    for rank, rk in enumerate(retrieved_source_keys, 1):
        if _matches_any(rk, expected_source_keys):
            return 1.0 / rank
    return 0.0


def _matches_any(retrieved: str, expected_list: list[str]) -> bool:
    for ek in expected_list:
        if _matches(ek, [retrieved]):
            return True
    return False


# ─── Retrieval runner ─────────────────────────────────────────────────────────

def run_retrieval(question: str, top_k: int, filters: dict) -> list[str]:
    """
    Run retrieval against the indexed corpus.
    Returns list of source keys in rank order.
    """
    try:
        from app.services.graphrag import retrieve_graphrag_evidence
        result = retrieve_graphrag_evidence(
            question=question,
            top_k=top_k,
            include_graph=False,
            source_type=filters.get("source_type"),
            make=filters.get("make"),
            model=filters.get("model"),
            model_year=filters.get("model_year"),
        )
        return [f"{c.source_type}:{c.source_record_key}" for c in result.retrieved_chunks]
    except Exception as e:
        print(f"    [WARN] Retrieval failed: {e}", file=sys.stderr)
        return []


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Evaluate Phase 6 GraphRAG retrieval")
    parser.add_argument(
        "--fixture",
        default="tests/fixtures/phase6_graphrag_eval.json",
        help="Path to evaluation fixture JSON",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Show per-query details")
    args = parser.parse_args()

    fixture_path = Path(args.fixture)
    if not fixture_path.exists():
        print(f"ERROR: fixture not found: {fixture_path}", file=sys.stderr)
        return 1

    with open(fixture_path, encoding="utf-8") as f:
        fixture = json.load(f)

    queries = fixture.get("queries", [])
    caveat = fixture.get("caveat", "")
    print("=== Phase 6 GraphRAG Evaluation ===")
    print(f"Provider: {fixture.get('provider', 'unknown')}")
    print(f"Dimension: {fixture.get('dimension', 'unknown')}")
    print(f"Queries: {len(queries)}")
    if caveat:
        print(f"Caveat: {caveat}")
    print()

    if not queries:
        print("ERROR: No queries in fixture", file=sys.stderr)
        return 1

    # Track metrics only for queries with non-empty expected keys
    recall1_scores: list[float] = []
    recall3_scores: list[float] = []
    mrr_scores: list[float] = []
    total_evaluable = 0

    query_results: list[dict] = []

    for i, q in enumerate(queries, 1):
        query_text = q.get("query", "")
        top_k = q.get("top_k", 5)
        filters = q.get("filters", {})
        expected = q.get("expected_source_keys", [])

        if args.verbose:
            print(f"Query {i}: {query_text}")
            print(f"  top_k={top_k}, filters={filters}")
            print(f"  expected={expected}")

        retrieved = run_retrieval(query_text, top_k, filters)

        r1 = recall_at_k(retrieved, expected, 1)
        r3 = recall_at_k(retrieved, expected, 3)
        mrr_val = mrr(retrieved, expected)

        if args.verbose:
            print(f"  retrieved: {retrieved}")
            print(f"  Recall@1: {r1}")
            print(f"  Recall@3: {r3}")
            print(f"  MRR: {mrr_val}")
            print()

        qr = {
            "query": query_text,
            "expected": expected,
            "retrieved": retrieved,
            "recall_at_1": r1,
            "recall_at_3": r3,
            "mrr": mrr_val,
        }
        query_results.append(qr)

        if expected:  # only score queries with known expectations
            total_evaluable += 1
            if r1 is not None:
                recall1_scores.append(r1)
            if r3 is not None:
                recall3_scores.append(r3)
            if mrr_val is not None:
                mrr_scores.append(mrr_val)

    # Summary
    print("=== Summary ===")
    print(f"Total queries: {len(queries)}")
    print(f"Evaluable queries (non-empty expected keys): {total_evaluable}")

    if total_evaluable == 0:
        print("WARNING: No evaluable queries — cannot compute metrics.", file=sys.stderr)
        print("This is expected when fixture expected_source_keys are empty.")
        return 0

    avg_r1 = sum(recall1_scores) / len(recall1_scores) if recall1_scores else 0.0
    avg_r3 = sum(recall3_scores) / len(recall3_scores) if recall3_scores else 0.0
    avg_mrr = sum(mrr_scores) / len(mrr_scores) if mrr_scores else 0.0

    print(f"Recall@1: {avg_r1:.4f}  (computed over {len(recall1_scores)} queries)")
    print(f"Recall@3: {avg_r3:.4f}  (computed over {len(recall3_scores)} queries)")
    print(f"MRR:      {avg_mrr:.4f}  (computed over {len(mrr_scores)} queries)")

    print()
    print("=== Caveats ===")
    print("- Metrics reflect deterministic lexical embeddings (token overlap), not semantic understanding.")
    print("- Tiny local dataset — do not extrapolate to production quality.")
    print("- 5-query fixture is insufficient for statistical significance.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
