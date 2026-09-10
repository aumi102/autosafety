"""
Phase 1.5: Flat-file complaint ingestion.

Solves the NHTSA EIEARS complaint API reliability issue (Ford F-150 400/empty)
by supporting local CSV file ingestion for complaints.

Source: local CSV file (sourced from NHTSA flat files or other validated exports).
Deduplication: by ODI number (nhtsa_complaint:{odi}).
"""

from __future__ import annotations

import csv
import hashlib
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
    SourceRun,
    Vehicle,
)
from app.services.ingestion.normalization import (
    normalize_make,
    normalize_model,
)

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


class ComplaintsFlatFileStats:
    """Track flat-file complaint ingestion statistics."""
    def __init__(self) -> None:
        self.vehicles_seen: int = 0
        self.complaint_rows_seen: int = 0
        self.complaint_rows_matched: int = 0
        self.complaints_inserted: int = 0
        self.complaints_skipped_duplicates: int = 0
        self.rows_skipped: int = 0
        self.errors_count: int = 0
        self.errors: list[str] = []

    def to_dict(self) -> dict:
        return {
            "vehicles_seen": self.vehicles_seen,
            "complaint_rows_seen": self.complaint_rows_seen,
            "complaint_rows_matched": self.complaint_rows_matched,
            "complaints_inserted": self.complaints_inserted,
            "complaints_skipped_duplicates": self.complaints_skipped_duplicates,
            "rows_skipped": self.rows_skipped,
            "errors_count": self.errors_count,
            "errors": self.errors,
        }


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


def _build_source_record_key(odi_number: str | None, raw_json: dict) -> str:
    """Build stable source_record_key for a complaint."""
    if odi_number:
        return f"nhtsa_complaint:{odi_number}"
    key_source = f"nhtsa_complaint:{raw_json.get('ODIURL', '')}"
    return f"nhtsa_complaint:hash:{hashlib.sha256(key_source.encode()).hexdigest()[:16]}"


def _upsert_vehicle_from_complaint(
    session: Session,
    make: str,
    model: str,
    model_year: int,
) -> Vehicle | None:
    """Upsert vehicle, return None on failure."""
    norm_make = normalize_make(make)
    norm_model = normalize_model(model)

    existing = session.query(Vehicle).filter_by(
        normalized_make=norm_make,
        normalized_model=norm_model,
        model_year=model_year,
    ).first()
    if existing:
        return existing

    vehicle = Vehicle(
        make=make.strip() if make else "",
        model=model.strip() if model else "",
        model_year=model_year,
        normalized_make=norm_make,
        normalized_model=norm_model,
    )
    session.add(vehicle)
    session.flush()
    return vehicle


def _upsert_component(session: Session, component_name: str) -> Component | None:
    """Upsert component, return None on failure."""
    if not component_name:
        return None
    norm_name = re.sub(r"\s+", " ", component_name.strip().upper())
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
    vehicle_id: uuid.UUID,
    component_id: uuid.UUID | None,
    source_run_id: uuid.UUID,
    source_record_key: str,
    odi_number: str | None,
    received_date: datetime | None,
    incident_date: datetime | None,
    original_component: str | None,
    summary: str | None,
    crash_flag: bool,
    fire_flag: bool,
    injury_flag: bool,
    death_flag: bool,
    source_url: str | None,
    raw_json: dict,
    stats: ComplaintsFlatFileStats,
) -> None:
    """Insert or skip complaint record."""
    # Check for existing complaint by source_record_key
    existing = session.query(Complaint).filter_by(
        source_record_key=source_record_key
    ).first()
    if existing:
        stats.complaints_skipped_duplicates += 1
        return

    complaint = Complaint(
        odi_number=odi_number,
        vehicle_id=vehicle_id,
        component_id=component_id,
        source_run_id=source_run_id,
        source_record_key=source_record_key,
        received_date=received_date,
        incident_date=incident_date,
        original_component=original_component,
        summary=summary,
        narrative=None,
        crash_flag=crash_flag,
        fire_flag=fire_flag,
        injury_flag=injury_flag,
        death_flag=death_flag,
        source_url=source_url,
        raw_json=raw_json,
    )
    session.add(complaint)

    # Store raw source row
    raw_row = RawSourceRow(
        source_run_id=source_run_id,
        source_name="nhtsa_complaints_flat_file_phase1_5",
        source_record_key=source_record_key,
        raw_json=raw_json,
    )
    session.add(raw_row)

    stats.complaints_inserted += 1


def _load_seed_vehicles(seed_csv_path: str) -> list[tuple[str, str, int]]:
    """Load seed vehicles from CSV. Returns list of (make, model, model_year)."""
    vehicles = []
    with open(Path(seed_csv_path), newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            vehicles.append((
                row["make"].strip(),
                row["model"].strip(),
                int(row["model_year"].strip()),
            ))
    return vehicles


def run_complaints_flat_file_ingestion(
    session: Session,
    seed_csv_path: str,
    complaints_csv_path: str,
    dry_run: bool = False,
    limit_vehicles: int | None = None,
) -> tuple[uuid.UUID | None, ComplaintsFlatFileStats]:
    """
    Ingest complaints from a local CSV file for seeded vehicles.

    Args:
        session: SQLAlchemy session
        seed_csv_path: Path to seed CSV with make,model,model_year
        complaints_csv_path: Path to complaints CSV file
        dry_run: If True, parse and count but don't commit to DB
        limit_vehicles: Limit number of distinct seed vehicles to process

    Returns:
        (source_run_id or None, ComplaintsFlatFileStats)
    """
    stats = ComplaintsFlatFileStats()

    # Validate inputs
    seed_path = Path(seed_csv_path)
    if not seed_path.exists():
        stats.errors.append(f"Seed CSV not found: {seed_csv_path}")
        return None, stats

    complaints_path = Path(complaints_csv_path)
    if not complaints_path.exists():
        stats.errors.append(f"Complaints CSV not found: {complaints_csv_path}")
        return None, stats

    # Load seed vehicles
    seed_vehicles = _load_seed_vehicles(seed_csv_path)
    if limit_vehicles:
        seed_vehicles = seed_vehicles[:limit_vehicles]

    # Build normalized seed set for fast lookup
    # Map: (normalized_make, normalized_model_stripped) -> list of (original_make, original_model, year)
    seed_map: dict[tuple[str, str], list[tuple]] = {}
    for make, model, year in seed_vehicles:
        norm_make = normalize_make(make)
        norm_model_stripped = _strip_model_for_matching(normalize_model(model))
        key = (norm_make, norm_model_stripped)
        if key not in seed_map:
            seed_map[key] = []
        seed_map[key].append((make, model, year))
        stats.vehicles_seen += 1

    # Track which seed vehicles we've seen complaint data for
    seen_vehicle_keys: set[tuple] = set()

    # Stream through complaints CSV, collect matched rows first
    matched_rows: list[dict] = []
    errors_by_row: list[tuple[int, str]] = []

    with open(complaints_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row_num, row in enumerate(reader, start=2):  # header is row 1
            stats.complaint_rows_seen += 1

            try:
                # Extract and validate required fields
                make = row.get("make", "").strip()
                model = row.get("model", "").strip()
                year_str = row.get("model_year", "").strip()

                if not make or not model or not year_str:
                    errors_by_row.append((row_num, "Missing make/model/model_year"))
                    stats.rows_skipped += 1
                    continue

                try:
                    model_year = int(year_str)
                except ValueError:
                    errors_by_row.append((row_num, f"Invalid model_year: {year_str}"))
                    stats.rows_skipped += 1
                    continue

                # Check if this vehicle matches a seed vehicle
                norm_make = normalize_make(make)
                norm_model = normalize_model(model)
                norm_model_stripped = _strip_model_for_matching(norm_model)
                seed_key = (norm_make, norm_model_stripped)

                if seed_key not in seed_map:
                    stats.rows_skipped += 1
                    continue

                # Verify year matches one of the seed years for this make/model
                matching_years = [y for (_, _, y) in seed_map[seed_key]]
                if model_year not in matching_years:
                    stats.rows_skipped += 1
                    continue

                stats.complaint_rows_matched += 1

                # Extract other fields defensively
                odi_number = row.get("odi_number", "").strip() or None
                component = row.get("component", "").strip() or None
                summary = row.get("summary", "").strip() or None
                crash = row.get("crash", "N").strip().upper() == "Y"
                fire = row.get("fire", "N").strip().upper() == "Y"
                injury = row.get("injury", "N").strip().upper() == "Y"
                death = row.get("death", "N").strip().upper() == "Y"
                received_date_str = row.get("received_date", "").strip()
                incident_date_str = row.get("incident_date", "").strip()
                source_url = row.get("source_url", "").strip() or None

                received_date = _parse_date(received_date_str) if received_date_str else None
                incident_date = _parse_date(incident_date_str) if incident_date_str else None

                raw_json = dict(row)

                matched_rows.append({
                    "make": make,
                    "model": model,
                    "model_year": model_year,
                    "odi_number": odi_number,
                    "component": component,
                    "summary": summary,
                    "crash_flag": crash,
                    "fire_flag": fire,
                    "injury_flag": injury,
                    "death_flag": death,
                    "received_date": received_date,
                    "incident_date": incident_date,
                    "source_url": source_url,
                    "raw_json": raw_json,
                })

                seen_vehicle_keys.add(seed_key)

            except Exception as e:
                errors_by_row.append((row_num, f"Parse error: {e}"))
                stats.errors_count += 1
                stats.rows_skipped += 1

    # In dry-run, return counts without DB writes
    if dry_run:
        for err_row, err_msg in errors_by_row[:10]:  # limit error messages
            stats.errors.append(f"Row {err_row}: {err_msg}")
        return None, stats

    # Create source run
    source_run = SourceRun(
        source_name="nhtsa_complaints_flat_file_phase1_5",
        source_url=None,
        source_type="nhtsa_flat_file",
        file_name=str(complaints_path.name),
        status="running",
        metadata_json={
            "seed_csv": seed_csv_path,
            "complaints_csv": complaints_csv_path,
            "dry_run": dry_run,
            "matched_rows": len(matched_rows),
        },
    )
    session.add(source_run)
    session.flush()
    source_run_id = source_run.id

    try:
        # Track upserted vehicles to avoid re-queries
        vehicle_cache: dict[tuple, uuid.UUID] = {}

        for row in matched_rows:
            # Get or create vehicle
            cache_key = (normalize_make(row["make"]), _strip_model_for_matching(normalize_model(row["model"])), row["model_year"])
            if cache_key in vehicle_cache:
                vehicle_id = vehicle_cache[cache_key]
            else:
                vehicle = _upsert_vehicle_from_complaint(
                    session, row["make"], row["model"], row["model_year"]
                )
                if not vehicle:
                    stats.errors_count += 1
                    stats.errors.append(f"Failed to upsert vehicle: {row['make']} {row['model']} {row['model_year']}")
                    continue
                vehicle_id = vehicle.id
                vehicle_cache[cache_key] = vehicle_id

            # Upsert component
            component_id = None
            if row["component"]:
                comp = _upsert_component(session, row["component"])
                component_id = comp.id if comp else None

            # Build source_record_key
            source_record_key = _build_source_record_key(row["odi_number"], row["raw_json"])

            _upsert_complaint(
                session=session,
                vehicle_id=vehicle_id,
                component_id=component_id,
                source_run_id=source_run_id,
                source_record_key=source_record_key,
                odi_number=row["odi_number"],
                received_date=row["received_date"],
                incident_date=row["incident_date"],
                original_component=row["component"],
                summary=row["summary"],
                crash_flag=row["crash_flag"],
                fire_flag=row["fire_flag"],
                injury_flag=row["injury_flag"],
                death_flag=row["death_flag"],
                source_url=row["source_url"],
                raw_json=row["raw_json"],
                stats=stats,
            )

        # Record errors in stats
        for err_row, err_msg in errors_by_row[:100]:
            stats.errors.append(f"Row {err_row}: {err_msg}")

        session.commit()

        # Update source run
        source_run.row_count = stats.complaints_inserted
        source_run.status = "success" if not stats.errors else "partial_success"
        source_run.finished_at = utcnow()
        session.commit()

    except Exception as e:
        stats.errors.append(f"Fatal error: {e}")
        source_run.status = "failed"
        source_run.error_message = str(e)
        source_run.finished_at = utcnow()
        session.commit()
        logger.exception("Phase 1.5 flat-file ingestion failed")

    return source_run_id, stats


def _strip_model_for_matching(model: str) -> str:
    """
    Strip model name for F-150 / F150 / F 150 equivalence matching.
    Removes dashes and spaces, uppercases for case-insensitive matching.
    E.g. "F-150" -> "F150", "f 150" -> "F150", "CAMRY" -> "CAMRY"
    """
    return model.upper().replace("-", "").replace(" ", "")
