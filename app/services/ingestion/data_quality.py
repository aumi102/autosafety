"""
Data quality reporting for ingestion.
"""

import uuid
from typing import Optional
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.models.domain import (
    Vehicle, Component, Complaint, Recall, RecallVehicleLink,
    Investigation, ManufacturerCommunication, SourceRun,
)


@dataclass
class DataQualitySummary:
    """Data quality metrics for Phase 1."""
    vehicle_count: int = 0
    component_count: int = 0
    complaint_count: int = 0
    recall_count: int = 0
    recall_vehicle_link_count: int = 0
    complaints_missing_odi: int = 0
    recalls_missing_campaign: int = 0
    complaints_missing_component: int = 0
    recalls_missing_component: int = 0
    duplicate_complaint_candidates: int = 0
    duplicate_recall_candidates: int = 0
    source_run_count: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    partial_runs: int = 0
    ingestion_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "vehicle_count": self.vehicle_count,
            "component_count": self.component_count,
            "complaint_count": self.complaint_count,
            "recall_count": self.recall_count,
            "recall_vehicle_link_count": self.recall_vehicle_link_count,
            "complaints_missing_odi": self.complaints_missing_odi,
            "recalls_missing_campaign": self.recalls_missing_campaign,
            "complaints_missing_component": self.complaints_missing_component,
            "recalls_missing_component": self.recalls_missing_component,
            "duplicate_complaint_candidates": self.duplicate_complaint_candidates,
            "duplicate_recall_candidates": self.duplicate_recall_candidates,
            "source_run_count": self.source_run_count,
            "successful_runs": self.successful_runs,
            "failed_runs": self.failed_runs,
            "partial_runs": self.partial_runs,
            "ingestion_errors": self.ingestion_errors,
        }


def get_data_quality_summary(session: Session) -> DataQualitySummary:
    """Compute data quality metrics from the database."""
    summary = DataQualitySummary()

    # Counts
    summary.vehicle_count = session.query(Vehicle).count()
    summary.component_count = session.query(Component).count()
    summary.complaint_count = session.query(Complaint).count()
    summary.recall_count = session.query(Recall).count()
    summary.recall_vehicle_link_count = session.query(RecallVehicleLink).count()

    # Missing keys
    summary.complaints_missing_odi = session.query(Complaint).filter(
        Complaint.odi_number.is_(None)
    ).count()

    summary.recalls_missing_campaign = session.query(Recall).filter(
        Recall.campaign_number.is_(None)
    ).count()

    # Missing component
    summary.complaints_missing_component = session.query(Complaint).filter(
        Complaint.component_id.is_(None)
    ).count()

    summary.recalls_missing_component = session.query(Recall).filter(
        Recall.component_id.is_(None)
    ).count()

    # Source runs
    source_runs = session.query(SourceRun).all()
    summary.source_run_count = len(source_runs)
    for run in source_runs:
        if run.status == "success":
            summary.successful_runs += 1
        elif run.status == "failed":
            summary.failed_runs += 1
            if run.error_message:
                summary.ingestion_errors.append(run.error_message)
        elif run.status == "partial_success":
            summary.partial_runs += 1

    return summary
