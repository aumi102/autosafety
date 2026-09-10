"""Ingestion API endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException

# Use sync engine for CLI-style ingestion in sync FastAPI context
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.endpoints.ingestion_schemas import (
    ComplaintsFlatFileStatsResponse,
    DataQualitySummaryResponse,
    IngestionStatsResponse,
    Phase1RunRequest,
    Phase1RunResponse,
    Phase15RunRequest,
    Phase15RunResponse,
    SourceRunListResponse,
    SourceRunResponse,
)
from app.core.config import get_settings
from app.core.security import verify_admin_token
from app.db.models.domain import SourceRun
from app.services.ingestion.complaints_flat_file import (
    run_complaints_flat_file_ingestion,
)
from app.services.ingestion.data_quality import get_data_quality_summary
from app.services.ingestion.nhtsa_ingestion import run_nhtsa_phase1_ingestion

router = APIRouter(tags=["ingestion"])


def _get_sync_session() -> Session:
    """Create a sync DB session for synchronous ingestion."""
    settings = get_settings()
    db_url = settings.DATABASE_URL_SYNC
    if "postgresql+asyncpg" in db_url:
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(db_url, echo=False)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


@router.post(
    "/nhtsa/phase1/run",
    response_model=Phase1RunResponse,
    dependencies=[Depends(verify_admin_token)],
)
def run_phase1_ingestion(data: Phase1RunRequest) -> Phase1RunResponse:
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
def list_source_runs(limit: int = 20, offset: int = 0) -> SourceRunListResponse:
    """List all ingestion source runs."""
    session = _get_sync_session()
    try:
        total = session.query(SourceRun).count()
        runs = (
            session.query(SourceRun)
            .order_by(SourceRun.started_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

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
def get_source_run(source_run_id: str) -> SourceRunResponse:
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


@router.post(
    "/nhtsa/phase1-5/complaints-flat-file/run",
    response_model=Phase15RunResponse,
    dependencies=[Depends(verify_admin_token)],
)
def run_phase15_complaints_flat_file(data: Phase15RunRequest) -> Phase15RunResponse:
    """
    Run Phase 1.5 flat-file complaint ingestion.

    Accepts a server-local file path for the complaints CSV.
    For developer/local use only. Production upload/storage deferred.

    Deduplication: by ODI number.
    Only ingests records matching seeded vehicles.
    """
    session = _get_sync_session()
    try:
        source_run_id, stats = run_complaints_flat_file_ingestion(
            session=session,
            seed_csv_path=data.seed_path,
            complaints_csv_path=data.complaints_flat_file_path,
            dry_run=data.dry_run,
            limit_vehicles=data.limit_vehicles,
        )

        source_run = session.query(SourceRun).filter_by(id=source_run_id).first()
        status = source_run.status if source_run else "unknown"

        return Phase15RunResponse(
            source_run_id=str(source_run_id),
            status=status,
            stats=ComplaintsFlatFileStatsResponse(
                vehicles_seen=stats.vehicles_seen,
                complaint_rows_seen=stats.complaint_rows_seen,
                complaint_rows_matched=stats.complaint_rows_matched,
                complaints_inserted=stats.complaints_inserted,
                complaints_skipped_duplicates=stats.complaints_skipped_duplicates,
                rows_skipped=stats.rows_skipped,
                errors_count=stats.errors_count,
                errors=stats.errors,
            ),
            phase="phase_1_5",
        )
    finally:
        session.close()


@router.get("/data-quality/summary", response_model=DataQualitySummaryResponse)
def get_quality_summary() -> DataQualitySummaryResponse:
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
            complaints_missing_vehicle_link=summary.complaints_missing_vehicle_link,
            duplicate_odi_candidates=summary.duplicate_odi_candidates,
            complaints_flat_file_runs=summary.complaints_flat_file_runs,
            source_run_count=summary.source_run_count,
            successful_runs=summary.successful_runs,
            failed_runs=summary.failed_runs,
            partial_runs=summary.partial_runs,
            ingestion_errors=summary.ingestion_errors,
        )
    finally:
        session.close()
