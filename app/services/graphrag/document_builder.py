"""
Document builder — canonical evidence documents from PostgreSQL domain entities.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.db.models.domain import Complaint, Recall, Vehicle


@dataclass
class EvidenceDocument:
    """
    Canonical evidence document representing one source entity.

    Fields:
        document_id: stable unique ID, e.g. "complaint:123456789"
        source_type: "complaint" | "recall"
        source_entity_id: PostgreSQL UUID as string
        source_record_key: ODI number or campaign number
        title: human-readable title
        full_text: deterministic text representation
        make: vehicle make
        model: vehicle model
        model_year: vehicle year
        component: normalized component name
        source_url: traceable source URL
        received_date: date field for complaints
        report_received_date: date field for recalls
        metadata: additional fields
        content_hash: SHA-256 of full_text for idempotent upsert
    """
    document_id: str
    source_type: str
    source_entity_id: str
    source_record_key: str
    title: str
    full_text: str
    make: str | None
    model: str | None
    model_year: int | None
    component: str | None
    source_url: str | None
    received_date: str | None
    report_received_date: str | None
    metadata: dict
    content_hash: str


def _date_str(d: date | datetime | None) -> str | None:
    """Convert a date/datetime to ISO string or None."""
    if d is None:
        return None
    if hasattr(d, "isoformat"):
        return d.isoformat()[:10]
    return str(d)


def _skip_empty(label: str, value: str | None) -> str:
    """Return 'label: value' only when value is non-empty."""
    if value and value.strip():
        return f"{label}: {value.strip()}"
    return ""


def _build_complaint_text(
    make: str,
    model: str,
    model_year: int,
    odi_number: str,
    component: str | None,
    received_date: str | None,
    incident_date: str | None,
    summary: str | None,
    crash_flag: bool,
    fire_flag: bool,
    injury_flag: bool,
    death_flag: bool,
) -> str:
    """Build canonical complaint text deterministically."""
    lines = [
        f"Vehicle: {make} {model} {model_year}",
        "Record type: Consumer complaint",
    ]

    if component and component.strip():
        lines.append(f"Component: {component.strip()}")

    lines.append(f"ODI number: {odi_number}")

    if received_date:
        lines.append(f"Received date: {received_date}")
    if incident_date:
        lines.append(f"Incident date: {incident_date}")

    if summary and summary.strip():
        # Truncate long narratives
        text = summary.strip()
        if len(text) > 2000:
            text = text[:2000].rstrip() + " ..."
        lines.append(f"Summary: {text}")

    flags = []
    if crash_flag:
        flags.append("Crash")
    if fire_flag:
        flags.append("Fire")
    if injury_flag:
        flags.append("Injury")
    if death_flag:
        flags.append("Death")
    if flags:
        lines.append(f"Incidents: {', '.join(flags)}")

    return "\n".join(line for line in lines if line)


def _build_recall_text(
    make: str,
    model: str,
    model_year: int,
    campaign_number: str,
    component: str | None,
    report_received_date: str | None,
    consequence: str | None,
    remedy: str | None,
    units_affected: int | None,
    summary: str | None,
) -> str:
    """Build canonical recall text deterministically."""
    lines = [
        f"Vehicle: {make} {model} {model_year}",
        "Record type: Recall",
        f"Campaign: {campaign_number}",
    ]

    if component and component.strip():
        lines.append(f"Component: {component.strip()}")

    if report_received_date:
        lines.append(f"Report received date: {report_received_date}")

    if units_affected:
        lines.append(f"Units affected: {units_affected:,}")

    if consequence and consequence.strip():
        text = consequence.strip()
        if len(text) > 1000:
            text = text[:1000].rstrip() + " ..."
        lines.append(f"Consequence: {text}")

    if remedy and remedy.strip():
        text = remedy.strip()
        if len(text) > 1000:
            text = text[:1000].rstrip() + " ..."
        lines.append(f"Remedy: {text}")

    if summary and summary.strip():
        text = summary.strip()
        if len(text) > 1000:
            text = text[:1000].rstrip() + " ..."
        lines.append(f"Summary: {text}")

    return "\n".join(line for line in lines if line)


def build_complaint_document(
    complaint: Complaint,
    vehicle: Vehicle,
) -> EvidenceDocument:
    """
    Build a canonical EvidenceDocument from a Complaint and its Vehicle.

    Deterministic: same complaint always produces same document_id and content_hash.
    """
    from app.services.graphrag.models import (
        content_hash,
        document_id_for_complaint,
    )

    odi = complaint.odi_number or f"local_{complaint.id}"
    make = vehicle.make or "Unknown"
    model = vehicle.model or "Unknown"
    model_year = vehicle.model_year or 0
    component = complaint.original_component

    full_text = _build_complaint_text(
        make=make,
        model=model,
        model_year=model_year,
        odi_number=odi,
        component=component,
        received_date=_date_str(complaint.received_date),
        incident_date=_date_str(complaint.incident_date),
        summary=complaint.summary,
        crash_flag=complaint.crash_flag or False,
        fire_flag=complaint.fire_flag or False,
        injury_flag=complaint.injury_flag or False,
        death_flag=complaint.death_flag or False,
    )

    title = f"Complaint {odi} — {make} {model} {model_year}"
    if component and component.strip():
        title += f" ({component.strip()})"

    return EvidenceDocument(
        document_id=document_id_for_complaint(odi),
        source_type="complaint",
        source_entity_id=str(complaint.id),
        source_record_key=odi,
        title=title,
        full_text=full_text,
        make=make,
        model=model,
        model_year=model_year,
        component=component,
        source_url=complaint.source_url,
        received_date=_date_str(complaint.received_date),
        report_received_date=None,
        metadata={
            "incident_date": _date_str(complaint.incident_date),
            "crash_flag": complaint.crash_flag,
            "fire_flag": complaint.fire_flag,
            "injury_flag": complaint.injury_flag,
            "death_flag": complaint.death_flag,
            "make": make,
            "model": model,
            "model_year": model_year,
            "component": component,
            "received_date": _date_str(complaint.received_date),
        },
        content_hash=content_hash(full_text),
    )


def build_recall_document(
    recall: Recall,
    vehicle: Vehicle,
) -> EvidenceDocument:
    """
    Build a canonical EvidenceDocument from a Recall and its Vehicle.

    Deterministic: same recall always produces same document_id and content_hash.
    """
    from app.services.graphrag.models import (
        content_hash,
        document_id_for_recall,
    )

    campaign = recall.campaign_number or f"local_{recall.id}"
    make = vehicle.make or "Unknown"
    model = vehicle.model or "Unknown"
    model_year = vehicle.model_year or 0
    component = recall.original_component

    full_text = _build_recall_text(
        make=make,
        model=model,
        model_year=model_year,
        campaign_number=campaign,
        component=component,
        report_received_date=_date_str(recall.report_received_date),
        consequence=recall.consequence,
        remedy=recall.remedy,
        units_affected=recall.units_affected,
        summary=recall.summary,
    )

    title = f"Recall {campaign} — {make} {model} {model_year}"
    if component and component.strip():
        title += f" ({component.strip()})"

    return EvidenceDocument(
        document_id=document_id_for_recall(campaign),
        source_type="recall",
        source_entity_id=str(recall.id),
        source_record_key=campaign,
        title=title,
        full_text=full_text,
        make=make,
        model=model,
        model_year=model_year,
        component=component,
        source_url=recall.source_url,
        received_date=None,
        report_received_date=_date_str(recall.report_received_date),
        metadata={
            "units_affected": recall.units_affected,
            "consequence": recall.consequence,
            "remedy": recall.remedy,
            "make": make,
            "model": model,
            "model_year": model_year,
            "component": component,
            "report_received_date": _date_str(recall.report_received_date),
        },
        content_hash=content_hash(full_text),
    )
