#!/usr/bin/env python3
"""
Phase 1 NHTSA ingestion CLI.

Usage:
    python scripts/ingest_phase1_nhtsa.py --seed data/seeds/phase1_vehicles.csv

Flags:
    --dry-run          Fetch data but don't commit
    --limit-vehicles N  Process only first N vehicles
    --complaints-only   Only ingest complaints
    --recalls-only     Only ingest recalls
"""

import argparse
import logging
import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.services.ingestion.nhtsa_ingestion import run_nhtsa_phase1_ingestion, IngestionStats


def main():
    parser = argparse.ArgumentParser(description="Phase 1 NHTSA ingestion CLI")
    parser.add_argument("--seed", required=True, help="Path to seed CSV")
    parser.add_argument("--dry-run", action="store_true", help="Fetch data but don't commit")
    parser.add_argument("--limit-vehicles", type=int, default=None, help="Limit vehicles processed")
    parser.add_argument("--complaints-only", action="store_true", help="Only ingest complaints")
    parser.add_argument("--recalls-only", action="store_true", help="Only ingest recalls")

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logger = logging.getLogger(__name__)

    # Load .env
    load_dotenv()

    # Connect to DB
    settings = get_settings()

    # Try sync URL first, fall back to async URL with sync driver
    db_url = settings.DATABASE_URL_SYNC
    if not db_url or "postgresql+asyncpg" in db_url:
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://") if db_url else None

    if not db_url:
        logger.error("DATABASE_URL_SYNC not configured. Set in .env or environment.")
        sys.exit(1)

    engine = create_engine(db_url, echo=False)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    try:
        logger.info(f"Starting Phase 1 ingestion from {args.seed}")
        if args.dry_run:
            logger.info("DRY RUN MODE - no data will be committed")
        if args.limit_vehicles:
            logger.info(f"Limited to {args.limit_vehicles} vehicles")
        if args.complaints_only:
            logger.info("Complaints only")
        if args.recalls_only:
            logger.info("Recalls only")

        source_run_id, stats = run_nhtsa_phase1_ingestion(
            session=session,
            seed_csv_path=args.seed,
            dry_run=args.dry_run,
            limit_vehicles=args.limit_vehicles,
            complaints_only=args.complaints_only,
            recalls_only=args.recalls_only,
        )

        print("\n" + "=" * 60)
        print("INGESTION SUMMARY")
        print("=" * 60)
        print(f"Source run ID: {source_run_id}")
        print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
        print()
        print(f"Vehicles seen:     {stats.vehicles_seen}")
        print(f"Vehicles inserted: {stats.vehicles_inserted}")
        print()
        print(f"Complaints seen:     {stats.complaints_seen}")
        print(f"Complaints inserted: {stats.complaints_inserted}")
        print(f"Complaints skipped:  {stats.complaints_skipped}")
        print()
        print(f"Recalls seen:     {stats.recalls_seen}")
        print(f"Recalls inserted: {stats.recalls_inserted}")
        print(f"Recalls skipped:  {stats.recalls_skipped}")

        if stats.errors:
            print()
            print(f"ERRORS ({len(stats.errors)}):")
            for err in stats.errors:
                print(f"  - {err}")

        print("=" * 60)

        if stats.errors and not args.dry_run:
            logger.warning(f"Ingestion completed with {len(stats.errors)} errors")
        else:
            logger.info("Ingestion completed successfully")

    finally:
        session.close()


if __name__ == "__main__":
    main()
