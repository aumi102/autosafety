"""Tests for Phase 1.5 flat-file complaint ingestion — no live network."""

import tempfile

import pytest
from app.db.base import Base

# Import ALL models so SQLite resolves all FK references
from app.db.models.app import *  # noqa: F401, F403
from app.db.models.domain import *  # noqa: F401, F403
from app.db.models.domain import Complaint, RawSourceRow, SourceRun, Vehicle
from app.services.ingestion.complaints_flat_file import (
    ComplaintsFlatFileStats,
    _build_source_record_key,
    _strip_model_for_matching,
    run_complaints_flat_file_ingestion,
)
from app.services.ingestion.data_quality import get_data_quality_summary
from app.services.ingestion.normalization import (
    models_equivalent,
    normalize_make,
    normalize_model,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# =============================================================================
# Test fixtures
# =============================================================================


@pytest.fixture
def in_memory_db():
    """Create an in-memory SQLite DB for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def seed_csv():
    """Create a temporary seed CSV."""
    content = (
        "make,model,model_year\n"
        "Ford,F-150,2020\n"
        "Ford,F-150,2021\n"
        "Honda,Accord,2021\n"
        "Toyota,Camry,2022\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
        f.write(content)
        return f.name


@pytest.fixture
def complaints_csv():
    """Create a temporary complaints CSV with known rows."""
    content = (
        "make,model,model_year,odi_number,component,summary,crash,fire,injury,death,received_date,incident_date,source_url\n"
        "Ford,F-150,2020,11420001,SERVICE BRAKES,Brake issue,N,N,N,N,20230415,20230320,https://api.nhtsa.gov/complaints/complaint?odi=11420001\n"
        "Ford,F150,2021,11430002,ELECTRICAL SYSTEM,Electrical issue,N,N,N,N,20230510,20230428,https://api.nhtsa.gov/complaints/complaint?odi=11430002\n"
        "Honda,Accord,2021,11440003,ENGINE,Engine rattle,N,N,Y,N,20230620,20230601,https://api.nhtsa.gov/complaints/complaint?odi=11440003\n"
        "Toyota,Camry,2022,11450004,STEERING,Steering shake,N,N,N,N,20230701,20230615,https://api.nhtsa.gov/complaints/complaint?odi=11450004\n"
        "Chevrolet,Silverado,2021,11460005,STRUCTURE,Seat crack,N,N,N,N,20230715,20230701,https://api.nhtsa.gov/complaints/complaint?odi=11460005\n"
        "Ford,,2020,11470006,SERVICE BRAKES,Missing model,N,N,N,N,20230720,20230710,\n"
        ",Tacoma,2021,11480007,ENGINE,Missing make,N,N,N,N,20230725,20230715,\n"
        "Ford,F-150,2020,11420001,SERVICE BRAKES,Duplicate ODI,N,N,N,N,20230415,20230320,https://api.nhtsa.gov/complaints/complaint?odi=11420001\n"
        "Toyota,Camry,2022,11450009,VISIBILITY,Wiper issue,N,N,N,N,20230801,20230725,https://api.nhtsa.gov/complaints/complaint?odi=11450009\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
        f.write(content)
        return f.name


# =============================================================================
# Model normalization tests
# =============================================================================


class TestModelNormalization:
    """F-150 / F150 / F 150 equivalence."""

    def test_f150_variants_strip_dashes_and_spaces(self):
        assert _strip_model_for_matching("F-150") == "F150"
        assert _strip_model_for_matching("F150") == "F150"
        assert _strip_model_for_matching("F 150") == "F150"
        assert _strip_model_for_matching("f-150") == "F150"

    def test_models_equivalent_f150_variants(self):
        assert models_equivalent("F-150", "F150") is True
        assert models_equivalent("F150", "F-150") is True
        assert models_equivalent("F 150", "F150") is True
        assert models_equivalent("f-150", "F150") is True
        assert models_equivalent("f-150", "camry") is False

    def test_models_equivalent_case_insensitive(self):
        assert models_equivalent("CAMRY", "camry") is True
        assert models_equivalent("ACCORD", "Accord") is True

    def test_normalize_model_preserves_dashes_for_display(self):
        """Normalization keeps dashes for stored model name, stripping is only for matching."""
        assert normalize_model("F-150") == "F-150"
        assert normalize_model("F 150") == "F 150"
        assert normalize_model("F150") == "F150"

    def test_normalize_make_case_and_whitespace(self):
        assert normalize_make("ford") == "FORD"
        assert normalize_make("  Ford  ") == "FORD"
        assert normalize_make("") == ""
        assert normalize_make(None) == ""


# =============================================================================
# Source record key tests
# =============================================================================


class TestSourceRecordKey:
    def test_odi_number_key(self):
        key = _build_source_record_key("11420001", {})
        assert key == "nhtsa_complaint:11420001"

    def test_fallback_hash_key(self):
        key = _build_source_record_key(
            None, {"ODIURL": "https://api.nhtsa.gov/complaints/complaint?odi=123"}
        )
        assert key.startswith("nhtsa_complaint:hash:")
        assert len(key) > len("nhtsa_complaint:hash:")


# =============================================================================
# Flat-file parser tests (dry-run)
# =============================================================================


class TestFlatFileParser:
    def test_parses_valid_rows(self, in_memory_db, seed_csv, complaints_csv):
        source_run_id, stats = run_complaints_flat_file_ingestion(
            session=in_memory_db,
            seed_csv_path=seed_csv,
            complaints_csv_path=complaints_csv,
            dry_run=True,
        )

        # 9 rows total in CSV
        assert stats.complaint_rows_seen == 9
        # 6 seed-matching rows (malformed rows fail parsing before match check)
        assert stats.complaint_rows_matched == 6
        # rows_skipped: 1 Chevrolet (non-seed) + 2 malformed (missing model or make) = 3
        assert stats.rows_skipped == 3

    def test_skips_non_seed_vehicles(self, in_memory_db, seed_csv, complaints_csv):
        _, stats = run_complaints_flat_file_ingestion(
            session=in_memory_db,
            seed_csv_path=seed_csv,
            complaints_csv_path=complaints_csv,
            dry_run=True,
        )
        # Silverado is not in seed
        assert stats.rows_skipped >= 1

    def test_handles_malformed_rows(self, in_memory_db, seed_csv, complaints_csv):
        _, stats = run_complaints_flat_file_ingestion(
            session=in_memory_db,
            seed_csv_path=seed_csv,
            complaints_csv_path=complaints_csv,
            dry_run=True,
        )
        # Missing model, missing make = 2 malformed
        assert stats.rows_skipped >= 2

    def test_missing_seed_file(self, in_memory_db):
        _, stats = run_complaints_flat_file_ingestion(
            session=in_memory_db,
            seed_csv_path="/nonexistent/seed.csv",
            complaints_csv_path="/nonexistent/complaints.csv",
            dry_run=True,
        )
        assert len(stats.errors) > 0

    def test_dry_run_does_not_commit(self, in_memory_db, seed_csv, complaints_csv):
        source_run_id, stats = run_complaints_flat_file_ingestion(
            session=in_memory_db,
            seed_csv_path=seed_csv,
            complaints_csv_path=complaints_csv,
            dry_run=True,
        )
        assert source_run_id is None
        # No DB records written
        assert in_memory_db.query(Vehicle).count() == 0
        assert in_memory_db.query(Complaint).count() == 0
        assert in_memory_db.query(SourceRun).count() == 0


# =============================================================================
# Live ingestion tests
# =============================================================================


class TestLiveFlatFileIngestion:
    def test_inserts_complaints(self, in_memory_db, seed_csv, complaints_csv):
        source_run_id, stats = run_complaints_flat_file_ingestion(
            session=in_memory_db,
            seed_csv_path=seed_csv,
            complaints_csv_path=complaints_csv,
            dry_run=False,
        )

        assert source_run_id is not None
        # Complaints inserted: 6 matched (2 Fords + 1 Honda + 2 Toyotas + 1 dup) - but dup skipped
        # Ford F-150 2020 (1), Ford F150 2021 (1), Honda Accord 2021 (1),
        # Toyota Camry 2022 (2, different ODI).
        # = 5 unique complaints
        # 6 matched rows: 2 Fords + 1 Honda + 2 Toyotas + 1 dup
        # 1 duplicate ODI 11420001 skipped; rest inserted
        assert stats.complaints_inserted == 5
        assert stats.complaints_skipped_duplicates == 1

    def test_upserts_vehicles(self, in_memory_db, seed_csv, complaints_csv):
        run_complaints_flat_file_ingestion(
            session=in_memory_db,
            seed_csv_path=seed_csv,
            complaints_csv_path=complaints_csv,
            dry_run=False,
        )
        # Vehicles: Ford F-150 2020, Ford F-150 2021, Honda Accord 2021, Toyota Camry 2022
        vehicles = in_memory_db.query(Vehicle).all()
        assert len(vehicles) == 4

    def test_idempotent_run(self, in_memory_db, seed_csv, complaints_csv):
        # First run
        _, stats1 = run_complaints_flat_file_ingestion(
            session=in_memory_db,
            seed_csv_path=seed_csv,
            complaints_csv_path=complaints_csv,
            dry_run=False,
        )
        assert stats1.complaints_inserted == 5
        assert stats1.complaints_skipped_duplicates == 1

        # Second run — all should be skipped as duplicates
        _, stats2 = run_complaints_flat_file_ingestion(
            session=in_memory_db,
            seed_csv_path=seed_csv,
            complaints_csv_path=complaints_csv,
            dry_run=False,
        )
        assert stats2.complaints_inserted == 0
        # All 6 matched rows (including 1 with duplicate ODI) are now in DB
        assert stats2.complaints_skipped_duplicates == 6

    def test_stores_raw_source_rows(self, in_memory_db, seed_csv, complaints_csv):
        run_complaints_flat_file_ingestion(
            session=in_memory_db,
            seed_csv_path=seed_csv,
            complaints_csv_path=complaints_csv,
            dry_run=False,
        )
        raw_rows = (
            in_memory_db.query(RawSourceRow)
            .filter(RawSourceRow.source_name == "nhtsa_complaints_flat_file_phase1_5")
            .all()
        )
        assert len(raw_rows) == 5

    def test_source_run_created(self, in_memory_db, seed_csv, complaints_csv):
        source_run_id, _ = run_complaints_flat_file_ingestion(
            session=in_memory_db,
            seed_csv_path=seed_csv,
            complaints_csv_path=complaints_csv,
            dry_run=False,
        )
        run = in_memory_db.query(SourceRun).filter_by(id=source_run_id).first()
        assert run is not None
        assert run.source_name == "nhtsa_complaints_flat_file_phase1_5"
        assert run.source_type == "nhtsa_flat_file"
        # partial_success because fixture has 2 malformed rows that produce error entries
        assert run.status == "partial_success"

    def test_limit_vehicles(self, in_memory_db, seed_csv, complaints_csv):
        _, stats = run_complaints_flat_file_ingestion(
            session=in_memory_db,
            seed_csv_path=seed_csv,
            complaints_csv_path=complaints_csv,
            dry_run=False,
            limit_vehicles=2,
        )
        # Only first 2 seed vehicles (Ford F-150 2020, Ford F-150 2021)
        # So: Ford F-150 2020 (1), Ford F150 2021 (1), rest skipped
        assert stats.vehicles_seen == 2


# =============================================================================
# Data quality tests
# =============================================================================


class TestDataQualityUpdates:
    def test_complaints_flat_file_runs_tracked(self, in_memory_db, seed_csv, complaints_csv):
        run_complaints_flat_file_ingestion(
            session=in_memory_db,
            seed_csv_path=seed_csv,
            complaints_csv_path=complaints_csv,
            dry_run=False,
        )

        summary = get_data_quality_summary(in_memory_db)
        assert len(summary.complaints_flat_file_runs) == 1
        # partial_success: the fixture has 2 malformed rows (missing model,
        # missing make).
        assert summary.complaints_flat_file_runs[0]["status"] == "partial_success"
        assert summary.complaints_flat_file_runs[0]["row_count"] == 5
        assert summary.complaints_flat_file_runs[0]["row_count"] == 5

    def test_complaints_missing_vehicle_link_zero(self, in_memory_db, seed_csv, complaints_csv):
        run_complaints_flat_file_ingestion(
            session=in_memory_db,
            seed_csv_path=seed_csv,
            complaints_csv_path=complaints_csv,
            dry_run=False,
        )
        summary = get_data_quality_summary(in_memory_db)
        assert summary.complaints_missing_vehicle_link == 0

    def test_complaints_missing_odi_counted(self, in_memory_db, seed_csv):
        # Create a complaint without ODI
        vehicle = Vehicle(
            make="Ford",
            model="F-150",
            model_year=2020,
            normalized_make="FORD",
            normalized_model="F-150",
        )
        in_memory_db.add(vehicle)
        in_memory_db.flush()

        complaint = Complaint(
            vehicle_id=vehicle.id,
            source_record_key="test:no-odi",
            raw_json={},
        )
        in_memory_db.add(complaint)
        in_memory_db.commit()

        summary = get_data_quality_summary(in_memory_db)
        assert summary.complaints_missing_odi >= 1


# =============================================================================
# Stats class tests
# =============================================================================


class TestComplaintsFlatFileStats:
    def test_to_dict(self):
        stats = ComplaintsFlatFileStats()
        stats.vehicles_seen = 4
        stats.complaint_rows_seen = 9
        stats.complaint_rows_matched = 6
        stats.complaints_inserted = 5
        stats.complaints_skipped_duplicates = 1
        stats.rows_skipped = 3
        stats.errors_count = 1
        stats.errors.append("Row 5: bad data")

        d = stats.to_dict()
        assert d["vehicles_seen"] == 4
        assert d["complaint_rows_seen"] == 9
        assert d["complaint_rows_matched"] == 6
        assert d["complaints_inserted"] == 5
        assert d["complaints_skipped_duplicates"] == 1
        assert d["rows_skipped"] == 3
        assert d["errors_count"] == 1
        assert "Row 5: bad data" in d["errors"]
