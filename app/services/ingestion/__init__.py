"""NHTSA ingestion service for Phase 1."""

from app.services.ingestion.data_quality import get_data_quality_summary
from app.services.ingestion.nhtsa_ingestion import IngestionStats, run_nhtsa_phase1_ingestion

__all__ = ["run_nhtsa_phase1_ingestion", "IngestionStats", "get_data_quality_summary"]
