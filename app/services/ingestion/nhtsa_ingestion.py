"""
NHTSA Phase 1 ingestion service.

Ingests complaints and recalls for seeded vehicles via NHTSA API.
Idempotent: safe to re-run without duplicating records.
"""

from __future__ import annotations

import csv
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models.domain import (
    Complaint,
    Component,
    RawSourceRow,
    Recall,
    RecallVehicleLink,
    SourceRun,
    Vehicle,
)
from app.services.nhtsa_client import (
    NHTSA_API_BASE,
    NhtsaApiError,
    NhtsaComplaintRecord,
    NhtsaRecallRecord,
    NhtsaVehicle,
    fetch_complaints_by_vehicle,
    fetch_recalls_by_vehicle,
)

logger = logging.getLogger(__name__)


def utcnow():
    return datetime.now(UTC)


class IngestionStats:
    """Track ingestion statistics."""
    def __init__(self):
        self.vehicles_seen: int = 0
        self.vehicles_inserted: int = 0
        self.complaints_seen: int = 0
        self.complaints_inserted: int = 0
        self.complaints_skipped: int = 0
        self.recalls_seen: int = 0
        self.recalls_inserted: int = 0
        self.recalls_skipped: int = 0
        self.errors: list[str] = []

    def to_dict(self) -> dict:
        return {
            "vehicles_seen": self.vehicles_seen,
            "vehicles_inserted": self.vehicles_inserted,
            "complaints_seen": self.complaints_seen,
            "complaints_inserted": self.complaints_inserted,
            "complaints_skipped": self.complaints_skipped,
            "recalls_seen": self.recalls_seen,
            "recalls_inserted": self.recalls_inserted,
            "recalls_skipped": self.recalls_skipped,
            "errors": self.errors,
        }


def _normalize_text(text: str | None) -> str:
    """Normalize text: upper, strip, collapse whitespace."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip().upper())


def _upsert_vehicle(session: Session, make: str, model: str, model_year: int) -> Vehicle:
    """Get or create vehicle, return canonical vehicle."""
    norm_make = _normalize_text(make)
    norm_model = _normalize_text(model)

    existing = session.query(Vehicle).filter_by(
        normalized_make=norm_make,
        normalized_model=norm_model,
        model_year=model_year,
    ).first()

    if existing:
        return existing

    vehicle = Vehicle(
        make=make.strip(),
        model=model.strip(),
        model_year=model_year,
        normalized_make=norm_make,
        normalized_model=norm_model,
    )
    session.add(vehicle)
    session.flush()
    return vehicle


def _upsert_component(session: Session, component_name: str) -> Component | None:
    """Get or create component, return canonical component."""
    if not component_name:
        return None

    norm_name = _normalize_text(component_name)
    if not norm_name:
        return None

    existing = session.query(Component).filter_by(normalized_name=norm_name).first()

    if existing:
        return existing

    component = Component(
        name=component_name.strip(),
        normalized_name=norm_name,
    )
    session.add(component)
    session.flush()
    return component


def _upsert_complaint(
    session: Session,
    record: NhtsaComplaintRecord,
    vehicle_id: uuid.UUID,
    source_run_id: uuid.UUID,
    stats: IngestionStats,
) -> None:
    """Insert or skip complaint record."""
    stats.complaints_seen += 1

    # Build source_record_key from ODI number or hash
    if record.odi_number:
        source_record_key = f"nhtsa_complaint:{record.odi_number}"
    else:
        import hashlib
        key_source = f"nhtsa_complaint:{record.raw_json.get('ODIURL', '')}"
        source_record_key = f"nhtsa_complaint:hash:{hashlib.sha256(key_source.encode()).hexdigest()[:16]}"

    # Check if already exists
    existing = session.query(Complaint).filter_by(source_record_key=source_record_key).first()
    if existing:
        stats.complaints_skipped += 1
        return

    component = _upsert_component(session, record.component) if record.component else None

    # Parse dates
    received_date = _parse_date(record.received_date)
    incident_date = _parse_date(record.incident_date)

    complaint = Complaint(
        odi_number=record.odi_number,
        vehicle_id=vehicle_id,
        component_id=component.id if component else None,
        source_run_id=source_run_id,
        source_record_key=source_record_key,
        received_date=received_date,
        incident_date=incident_date,
        original_component=record.component,
        summary=record.summary,
        narrative=None,
        crash_flag=(record.crash == "Y"),
        fire_flag=(record.fire == "Y"),
        injury_flag=(record.injury == "Y"),
        death_flag=(record.death == "Y"),
        source_url=record.source_url,
        raw_json=record.raw_json,
    )
    session.add(complaint)

    # Also store raw source row
    raw_row = RawSourceRow(
        source_run_id=source_run_id,
        source_name="nhtsa_complaints",
        source_record_key=source_record_key,
        raw_json=record.raw_json,
    )
    session.add(raw_row)

    stats.complaints_inserted += 1


def _upsert_recall(
    session: Session,
    record: NhtsaRecallRecord,
    vehicle_id: uuid.UUID,
    source_run_id: uuid.UUID,
    stats: IngestionStats,
) -> None:
    """Insert or skip recall record."""
    stats.recalls_seen += 1

    if not record.campaign_number:
        stats.errors.append("Recall missing campaign_number, skipped")
        return

    source_record_key = f"nhtsa_recall:{record.campaign_number}"

    # Check if already exists
    existing = session.query(Recall).filter_by(source_record_key=source_record_key).first()
    if existing:
        stats.recalls_skipped += 1
        # Still create link if missing
        link = session.query(RecallVehicleLink).filter_by(
            recall_id=existing.id, vehicle_id=vehicle_id
        ).first()
        if not link:
            link = RecallVehicleLink(recall_id=existing.id, vehicle_id=vehicle_id)
            session.add(link)
        return

    component = _upsert_component(session, record.component) if record.component else None

    report_date = _parse_date(record.report_received_date)

    recall = Recall(
        campaign_number=record.campaign_number,
        component_id=component.id if component else None,
        source_run_id=source_run_id,
        source_record_key=source_record_key,
        report_received_date=report_date,
        original_component=record.component,
        summary=record.summary,
        consequence=record.consequence,
        remedy=record.remedy,
        notes=record.notes,
        units_affected=record.units_affected,
        source_url=record.source_url,
        raw_json=record.raw_json,
    )
    session.add(recall)
    session.flush()

    # Create vehicle link
    link = RecallVehicleLink(recall_id=recall.id, vehicle_id=vehicle_id)
    session.add(link)

    # Store raw source row
    raw_row = RawSourceRow(
        source_run_id=source_run_id,
        source_name="nhtsa_recalls",
        source_record_key=source_record_key,
        raw_json=record.raw_json,
    )
    session.add(raw_row)

    stats.recalls_inserted += 1


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse NHTSA date string (YYYYMMDD) to date."""
    if not date_str:
        return None
    try:
        if len(date_str) == 8 and date_str.isdigit():
            return datetime(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
    except (ValueError, TypeError):
        pass
    return None


def run_nhtsa_phase1_ingestion(
    session: Session,
    seed_csv_path: str,
    dry_run: bool = False,
    limit_vehicles: int | None = None,
    complaints_only: bool = False,
    recalls_only: bool = False,
) -> tuple[uuid.UUID | None, IngestionStats]:
    """
    Run Phase 1 NHTSA ingestion.

    Args:
        session: SQLAlchemy session
        seed_csv_path: Path to seed CSV with make,model,model_year
        dry_run: If True, fetch data but don't commit to DB
        limit_vehicles: Limit number of vehicles to process
        complaints_only: Only ingest complaints
        recalls_only: Only ingest recalls

    Returns:
        (source_run_id or None, IngestionStats)
    """
    stats = IngestionStats()

    # Load seed vehicles first (no DB writes yet)
    seed_path = Path(seed_csv_path)
    if not seed_path.exists():
        stats.errors.append(f"Seed CSV not found: {seed_csv_path}")
        return None, stats

    seed_vehicles: list[tuple] = []
    with open(seed_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            seed_vehicles.append((
                row["make"].strip(),
                row["model"].strip(),
                int(row["model_year"].strip()),
            ))

    if limit_vehicles:
        seed_vehicles = seed_vehicles[:limit_vehicles]

    stats.vehicles_seen = len(seed_vehicles)

    # Pre-fetch all data before any DB writes
    # This ensures dry-run validates network + parsing before touching the DB
    fetched_complaints: dict[tuple, list] = {}
    fetched_recalls: dict[tuple, list] = {}

    for make, model, model_year in seed_vehicles:
        nhtsa_vehicle = NhtsaVehicle(make=make, model=model, model_year=model_year)
        key = (make, model, model_year)

        if not recalls_only:
            try:
                fetched_complaints[key] = fetch_complaints_by_vehicle(nhtsa_vehicle)
            except NhtsaApiError as e:
                stats.errors.append(f"Complaints fetch failed for {make} {model} {model_year}: {e}")
                fetched_complaints[key] = []

        if not complaints_only:
            try:
                fetched_recalls[key] = fetch_recalls_by_vehicle(nhtsa_vehicle)
            except NhtsaApiError as e:
                stats.errors.append(f"Recalls fetch failed for {make} {model} {model_year}: {e}")
                fetched_recalls[key] = []

    # All fetches done. If dry-run, return now without touching DB
    if dry_run:
        # Count what would be inserted
        for key in seed_vehicles:
            stats.vehicles_inserted = len(seed_vehicles)  # all would be upserted
        for records in fetched_complaints.values():
            stats.complaints_seen += len(records)
        for records in fetched_recalls.values():
            stats.recalls_seen += len(records)
        return None, stats

    # Now write to DB
    source_run = SourceRun(
        source_name="nhtsa_api_phase1",
        source_url=NHTSA_API_BASE,
        source_type="nhtsa_api",
        status="running",
        metadata_json={
            "seed_csv": seed_csv_path,
            "dry_run": dry_run,
            "complaints_only": complaints_only,
            "recalls_only": recalls_only,
        },
    )
    session.add(source_run)
    session.flush()
    source_run_id = source_run.id

    try:
        for make, model, model_year in seed_vehicles:
            key = (make, model, model_year)

            # Upsert vehicle
            try:
                vehicle = _upsert_vehicle(session, make, model, model_year)
                stats.vehicles_inserted += 1
            except Exception as e:
                stats.errors.append(f"Vehicle upsert failed for {make} {model} {model_year}: {e}")
                continue

            # Insert complaints
            for record in fetched_complaints.get(key, []):
                _upsert_complaint(session, record, vehicle.id, source_run_id, stats)

            # Insert recalls
            for record in fetched_recalls.get(key, []):
                _upsert_recall(session, record, vehicle.id, source_run_id, stats)

        session.commit()

        # Update source run
        total_rows = stats.complaints_inserted + stats.recalls_inserted
        source_run.row_count = total_rows
        source_run.status = "success" if not stats.errors else "partial_success"
        source_run.finished_at = utcnow()
        session.commit()

    except Exception as e:
        stats.errors.append(f"Fatal error: {e}")
        source_run.status = "failed"
        source_run.error_message = str(e)
        source_run.finished_at = utcnow()
        session.commit()
        logger.exception("Phase 1 ingestion failed")

    return source_run_id, stats
