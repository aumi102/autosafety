"""Ingestion API endpoints."""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import Optional
import uuid

from app.api.v1.endpoints.ingestion_schemas import (
    SourceRunResponse,
    SourceRunListResponse,
    Phase1RunRequest,
    Phase1RunResponse,
    IngestionStatsResponse,
    DataQualitySummaryResponse,
)
from app.services.ingestion.nhtsa_ingestion import run_nhtsa_phase1_ingestion, IngestionStats
from app.services.ingestion.data_quality import get_data_quality_summary
from app.db.models.domain import SourceRun
from app.db.session import get_async_session
from app.core.config import get_settings

# Use sync engine for CLI-style ingestion in sync FastAPI context
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

router = APIRouter(tags=["ingestion"])


def _get_sync_session():
    """Create a sync DB session for synchronous ingestion."""
    settings = get_settings()
    db_url = settings.DATABASE_URL_SYNC
    if "postgresql+asyncpg" in db_url:
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(db_url, echo=False)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


@router.post("/nhtsa/phase1/run", response_model=Phase1RunResponse)
def run_phase1_ingestion(data: Phase1RunRequest):
    """
    Run Phase 1 NHTSA ingestion.

    Fetches complaints and recalls for seeded vehicles.
    Idempotent: re-running does not duplicate records.
    """
    session = _get_sync_session()
    try:
        source_run_id, stats = run_nhtsa_phase1_ingestion(
            session=session,
            seed_csv_path=data.seed_csv,
            dry_run=data.dry_run,
            limit_vehicles=data.limit_vehicles,
            complaints_only=data.complaints_only,
            recalls_only=data.recalls_only,
        )

        # Get source run status
        source_run = session.query(SourceRun).filter_by(id=source_run_id).first()
        status = source_run.status if source_run else "unknown"

        return Phase1RunResponse(
            source_run_id=str(source_run_id),
            status=status,
            stats=IngestionStatsResponse(
                vehicles_seen=stats.vehicles_seen,
                vehicles_inserted=stats.vehicles_inserted,
                complaints_seen=stats.complaints_seen,
                complaints_inserted=stats.complaints_inserted,
                complaints_skipped=stats.complaints_skipped,
                recalls_seen=stats.recalls_seen,
                recalls_inserted=stats.recalls_inserted,
                recalls_skipped=stats.recalls_skipped,
                errors=stats.errors,
            ),
            phase="phase_1",
        )
    finally:
        session.close()


@router.get("/source-runs", response_model=SourceRunListResponse)
def list_source_runs(limit: int = 20, offset: int = 0):
    """List all ingestion source runs."""
    session = _get_sync_session()
    try:
        total = session.query(SourceRun).count()
        runs = session.query(SourceRun).order_by(
            SourceRun.started_at.desc()
        ).offset(offset).limit(limit).all()

        return SourceRunListResponse(
            source_runs=[
                SourceRunResponse(
                    id=str(run.id),
                    source_name=run.source_name,
                    source_url=run.source_url,
                    source_type=run.source_type,
                    status=run.status,
                    row_count=run.row_count,
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                    error_message=run.error_message,
                )
                for run in runs
            ],
            total=total,
        )
    finally:
        session.close()


@router.get("/source-runs/{source_run_id}", response_model=SourceRunResponse)
def get_source_run(source_run_id: str):
    """Get a specific source run by ID."""
    session = _get_sync_session()
    try:
        run = session.query(SourceRun).filter_by(id=uuid.UUID(source_run_id)).first()
        if not run:
            raise HTTPException(status_code=404, detail="Source run not found")

        return SourceRunResponse(
            id=str(run.id),
            source_name=run.source_name,
            source_url=run.source_url,
            source_type=run.source_type,
            status=run.status,
            row_count=run.row_count,
            started_at=run.started_at,
            finished_at=run.finished_at,
            error_message=run.error_message,
        )
    finally:
        session.close()


@router.get("/data-quality/summary", response_model=DataQualitySummaryResponse)
def get_quality_summary():
    """Get data quality summary for all ingested data."""
    session = _get_sync_session()
    try:
        summary = get_data_quality_summary(session)
        return DataQualitySummaryResponse(
            vehicle_count=summary.vehicle_count,
            component_count=summary.component_count,
            complaint_count=summary.complaint_count,
            recall_count=summary.recall_count,
            recall_vehicle_link_count=summary.recall_vehicle_link_count,
            complaints_missing_odi=summary.complaints_missing_odi,
            recalls_missing_campaign=summary.recalls_missing_campaign,
            complaints_missing_component=summary.complaints_missing_component,
            recalls_missing_component=summary.recalls_missing_component,
            duplicate_complaint_candidates=summary.duplicate_complaint_candidates,
            duplicate_recall_candidates=summary.duplicate_recall_candidates,
            source_run_count=summary.source_run_count,
            successful_runs=summary.successful_runs,
            failed_runs=summary.failed_runs,
            partial_runs=summary.partial_runs,
            ingestion_errors=summary.ingestion_errors,
        )
    finally:
        session.close()
