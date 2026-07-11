#!/usr/bin/env python3
"""
Phase 1 / Phase 1.5 NHTSA ingestion CLI.

Usage:
    # Phase 1 - API-based (complaints + recalls)
    python scripts/ingest_phase1_nhtsa.py --seed data/seeds/phase1_vehicles.csv

    # Phase 1 - complaints only via API
    python scripts/ingest_phase1_nhtsa.py --seed data/seeds/phase1_vehicles.csv \
        --complaints-only --complaints-source api

    # Phase 1.5 - complaints via flat-file (recommended for reliability)
    python scripts/ingest_phase1_nhtsa.py --seed data/seeds/phase1_vehicles.csv \
        --complaints-only --complaints-source flat-file \
        --complaints-flat-file tests/fixtures/nhtsa_complaints_sample.csv --dry-run

    # Phase 1.5 - live flat-file ingestion
    python scripts/ingest_phase1_nhtsa.py --seed data/seeds/phase1_vehicles.csv \
        --complaints-only --complaints-source flat-file \
        --complaints-flat-file tests/fixtures/nhtsa_complaints_sample.csv
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
from app.services.ingestion.complaints_flat_file import (
    run_complaints_flat_file_ingestion, ComplaintsFlatFileStats,
)


def _print_stats_phase1(stats: IngestionStats, source_run_id, dry_run: bool):
    print("\n" + "=" * 60)
    print("PHASE 1 INGESTION SUMMARY (API-based)")
    print("=" * 60)
    print(f"Source run ID: {source_run_id}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
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


def _print_stats_phase1_5(stats: ComplaintsFlatFileStats, source_run_id, dry_run: bool):
    print("\n" + "=" * 60)
    print("PHASE 1.5 INGESTION SUMMARY (flat-file complaints)")
    print("=" * 60)
    print(f"Source run ID: {source_run_id}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print()
    print(f"Vehicles seen:            {stats.vehicles_seen}")
    print(f"Complaint rows seen:      {stats.complaint_rows_seen}")
    print(f"Complaint rows matched:   {stats.complaint_rows_matched}")
    print(f"Complaints inserted:      {stats.complaints_inserted}")
    print(f"Complaints skipped (dup): {stats.complaints_skipped_duplicates}")
    print(f"Rows skipped (no match): {stats.rows_skipped}")
    print(f"Errors count:             {stats.errors_count}")
    if stats.errors:
        print()
        for err in stats.errors[:20]:
            print(f"  - {err}")
        if len(stats.errors) > 20:
            print(f"  ... and {len(stats.errors) - 20} more errors")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Phase 1 / Phase 1.5 NHTSA ingestion CLI")
    parser.add_argument("--seed", required=True, help="Path to seed CSV")
    parser.add_argument("--dry-run", action="store_true", help="Fetch/parse data but don't commit")
    parser.add_argument("--limit-vehicles", type=int, default=None, help="Limit vehicles processed")
    parser.add_argument("--complaints-only", action="store_true", help="Only ingest complaints")
    parser.add_argument("--recalls-only", action="store_true", help="Only ingest recalls")
    # Phase 1.5 flat-file options
    parser.add_argument("--complaints-flat-file", default=None,
                        help="Path to local complaints CSV file (Phase 1.5 flat-file mode)")
    parser.add_argument("--complaints-source", default="auto",
                        choices=["api", "flat-file", "auto"],
                        help="Complaints source: api (Phase 1), flat-file (Phase 1.5), auto (prefer flat-file if --complaints-flat-file given)")

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
        # Determine complaints source
        use_flat_file = False
        if args.complaints_source == "flat-file":
            use_flat_file = True
        elif args.complaints_source == "auto":
            use_flat_file = bool(args.complaints_flat_file)

        if use_flat_file:
            # Phase 1.5 flat-file path
            if not args.complaints_flat_file:
                logger.error("--complaints-flat-file is required for flat-file mode")
                sys.exit(1)

            logger.info(f"Phase 1.5: Using flat-file complaints from {args.complaints_flat_file}")
            if args.dry_run:
                logger.info("DRY RUN MODE - no data will be committed")
            if args.limit_vehicles:
                logger.info(f"Limited to {args.limit_vehicles} vehicles")
            if args.recalls_only:
                logger.warning("--recalls-only is ignored in flat-file mode (flat-file currently only supports complaints)")

            source_run_id, stats = run_complaints_flat_file_ingestion(
                session=session,
                seed_csv_path=args.seed,
                complaints_csv_path=args.complaints_flat_file,
                dry_run=args.dry_run,
                limit_vehicles=args.limit_vehicles,
            )
            _print_stats_phase1_5(stats, source_run_id, args.dry_run)
        else:
            # Phase 1 API path
            logger.info("Phase 1: Using API-based ingestion")
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
            _print_stats_phase1(stats, source_run_id, args.dry_run)

    finally:
        session.close()


if __name__ == "__main__":
    main()
