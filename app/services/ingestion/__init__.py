"""NHTSA ingestion service for Phase 1."""

from app.services.ingestion.nhtsa_ingestion import run_nhtsa_phase1_ingestion, IngestionStats
from app.services.ingestion.data_quality import get_data_quality_summary

__all__ = ["run_nhtsa_phase1_ingestion", "IngestionStats", "get_data_quality_summary"]
