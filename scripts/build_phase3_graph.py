#!/usr/bin/env python3
"""
Phase 3 graph build CLI.

Usage:
    python scripts/build_phase3_graph.py --setup-schema
    python scripts/build_phase3_graph.py --build
    python scripts/build_phase3_graph.py --dry-run
    python scripts/build_phase3_graph.py --limit-vehicles 10
    python scripts/build_phase3_graph.py --status
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Load .env before any app imports
from dotenv import load_dotenv

env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)


def setup_schema_cmd() -> int:
    """Run schema setup."""
    from app.services.graph import setup_graph_schema

    print("Setting up Neo4j schema (constraints + indexes)...")
    result = setup_graph_schema()
    print(f"  Constraints created: {result.constraints_created}")
    print(f"  Indexes created:      {result.indexes_created}")
    if result.errors:
        for err in result.errors:
            print(f"  ERROR: {err}")
        return 1
    print("  Schema setup complete.")
    return 0


def build_cmd(dry_run: bool, limit_vehicles: int | None) -> int:
    """Run graph build."""
    from app.services.graph import build_graph_from_postgres

    mode = "DRY RUN" if dry_run else "LIVE BUILD"
    limit_str = f" (limit: {limit_vehicles} vehicles)" if limit_vehicles else ""
    print(f"Graph {mode}{limit_str}...")
    start = time.time()
    stats = build_graph_from_postgres(dry_run=dry_run, limit_vehicles=limit_vehicles)
    elapsed = time.time() - start

    print(f"  Vehicle makes seen:    {stats.vehicle_makes_seen}")
    print(f"  Vehicle models seen:  {stats.vehicle_models_seen}")
    print(f"  Model years seen:     {stats.model_years_seen}")
    print(f"  Components seen:      {stats.components_seen}")
    print(f"  Complaints seen:      {stats.complaints_seen}")
    print(f"  Recalls seen:         {stats.recalls_seen}")
    print(f"  Nodes merged:         {stats.nodes_merged}")
    print(f"  Relationships merged: {stats.relationships_merged}")
    print(f"  Rows skipped:         {stats.rows_skipped}")
    print(f"  Errors:               {stats.errors_count}")
    print(f"  Duration:             {elapsed:.1f}s")

    if stats.errors:
        for err in stats.errors[:10]:
            print(f"    {err}")

    if stats.errors_count > 0 and not dry_run:
        print("\nWARNING: Some records had errors but build continued.")
        return 1

    return 0


def status_cmd() -> int:
    """Check graph status."""
    from app.services.graph import get_graph_status
    from app.services.graph.neo4j_client import verify_connectivity

    print("Graph status:")
    connected = verify_connectivity()
    print(f"  Neo4j connected: {connected}")

    if not connected:
        print("  Cannot check status — Neo4j not reachable.")
        print("  Start Neo4j: docker compose up neo4j -d")
        return 1

    status = get_graph_status()
    print(f"  Graph nodes:       {status.node_count}")
    print(f"  Graph rels:        {status.relationship_count}")
    print(f"  PG vehicles:       {status.postgres_vehicle_count}")
    print(f"  PG complaints:     {status.postgres_complaint_count}")
    print(f"  PG recalls:        {status.postgres_recall_count}")
    print(f"  PG components:     {status.postgres_component_count}")

    if status.node_labels:
        print("  Node labels:")
        for nl in status.node_labels:
            print(f"    {nl.label}: {nl.count}")

    if status.relationship_types:
        print("  Relationship types:")
        for rt in status.relationship_types:
            print(f"    {rt.type}: {rt.count}")

    if status.error:
        print(f"  Status error: {status.error}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 3 Graph Build CLI")
    parser.add_argument(
        "--setup-schema", action="store_true", help="Setup Neo4j constraints and indexes"
    )
    parser.add_argument(
        "--build", action="store_true", help="Build graph projection from PostgreSQL to Neo4j"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Count records without writing to Neo4j"
    )
    parser.add_argument(
        "--limit-vehicles", type=int, default=None, help="Limit number of vehicles to process"
    )
    parser.add_argument("--status", action="store_true", help="Check graph status")

    args = parser.parse_args()

    # No action specified
    if not any([args.setup_schema, args.build, args.dry_run, args.status]):
        parser.print_help()
        return 0

    try:
        if args.setup_schema:
            return setup_schema_cmd()

        if args.dry_run:
            return build_cmd(dry_run=True, limit_vehicles=args.limit_vehicles)

        if args.build:
            return build_cmd(dry_run=False, limit_vehicles=args.limit_vehicles)

        if args.status:
            return status_cmd()

    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
