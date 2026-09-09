"""Tests for data quality reporting — mock-based."""

from app.services.ingestion.data_quality import DataQualitySummary


def test_data_quality_summary_to_dict():
    summary = DataQualitySummary(
        vehicle_count=10,
        component_count=5,
        complaint_count=100,
        recall_count=20,
        complaints_missing_odi=3,
        recalls_missing_campaign=0,
        complaints_missing_component=10,
        source_run_count=2,
        successful_runs=2,
    )
    d = summary.to_dict()
    assert d["vehicle_count"] == 10
    assert d["complaint_count"] == 100
    assert d["complaints_missing_odi"] == 3
    assert d["successful_runs"] == 2
    assert d["ingestion_errors"] == []


def test_data_quality_summary_with_errors():
    summary = DataQualitySummary(
        vehicle_count=5,
        component_count=3,
        complaint_count=50,
        recall_count=10,
        source_run_count=3,
        successful_runs=1,
        failed_runs=1,
        partial_runs=1,
        ingestion_errors=["Network timeout for Honda Accord 2020"],
    )
    d = summary.to_dict()
    assert d["failed_runs"] == 1
    assert d["partial_runs"] == 1
    assert len(d["ingestion_errors"]) == 1
