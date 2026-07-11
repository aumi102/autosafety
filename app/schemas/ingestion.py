"""Pydantic schemas for ingestion API."""

from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class SourceRunResponse(BaseModel):
    id: str
    source_name: str
    source_url: Optional[str]
    source_type: str
    status: str
    row_count: int
    started_at: datetime
    finished_at: Optional[datetime]
    error_message: Optional[str]

    class Config:
        from_attributes = True


class SourceRunListResponse(BaseModel):
    source_runs: list[SourceRunResponse]
    total: int


class IngestionStatsResponse(BaseModel):
    vehicles_seen: int
    vehicles_inserted: int
    complaints_seen: int
    complaints_inserted: int
    complaints_skipped: int
    recalls_seen: int
    recalls_inserted: int
    recalls_skipped: int
    errors: list[str]


class Phase1RunRequest(BaseModel):
    seed_csv: str = "data/seeds/phase1_vehicles.csv"
    dry_run: bool = False
    limit_vehicles: Optional[int] = None
    complaints_only: bool = False
    recalls_only: bool = False


class Phase1RunResponse(BaseModel):
    source_run_id: str
    status: str
    stats: IngestionStatsResponse
    phase: str = "phase_1"


class DataQualitySummaryResponse(BaseModel):
    vehicle_count: int
    component_count: int
    complaint_count: int
    recall_count: int
    recall_vehicle_link_count: int
    complaints_missing_odi: int
    recalls_missing_campaign: int
    complaints_missing_component: int
    recalls_missing_component: int
    duplicate_complaint_candidates: int
    duplicate_recall_candidates: int
    source_run_count: int
    successful_runs: int
    failed_runs: int
    partial_runs: int
    ingestion_errors: list[str]
