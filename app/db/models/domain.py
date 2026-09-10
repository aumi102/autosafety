import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class SourceRun(Base):
    __tablename__ = "source_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_name: Mapped[str] = mapped_column(Text, nullable=True)
    file_sha256: Mapped[str] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class RawSourceRow(Base):
    __tablename__ = "raw_source_rows"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_record_key: Mapped[str] = mapped_column(Text, nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    __table_args__ = (UniqueConstraint("source_name", "source_record_key"),)


class Vehicle(Base):
    __tablename__ = "vehicles"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    make: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    model_year: Mapped[int] = mapped_column(Integer, nullable=False)
    normalized_make: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_model: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    __table_args__ = (UniqueConstraint("normalized_make", "normalized_model", "model_year"),)


class Component(Base):
    __tablename__ = "components"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class Complaint(Base):
    __tablename__ = "complaints"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    odi_number: Mapped[str] = mapped_column(Text, unique=True, nullable=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id"), nullable=False
    )
    component_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("components.id"), nullable=True
    )
    source_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_runs.id"), nullable=True
    )
    source_record_key: Mapped[str] = mapped_column(Text, nullable=False)
    received_date: Mapped[datetime] = mapped_column(Date, nullable=True)
    incident_date: Mapped[datetime] = mapped_column(Date, nullable=True)
    original_component: Mapped[str] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    narrative: Mapped[str] = mapped_column(Text, nullable=True)
    crash_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    fire_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    injury_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    death_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class Recall(Base):
    __tablename__ = "recalls"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_number: Mapped[str] = mapped_column(Text, nullable=False)
    component_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("components.id"), nullable=True
    )
    source_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_runs.id"), nullable=True
    )
    source_record_key: Mapped[str] = mapped_column(Text, nullable=False)
    report_received_date: Mapped[datetime] = mapped_column(Date, nullable=True)
    original_component: Mapped[str] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    consequence: Mapped[str] = mapped_column(Text, nullable=True)
    remedy: Mapped[str] = mapped_column(Text, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    units_affected: Mapped[int] = mapped_column(Integer, nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    __table_args__ = (UniqueConstraint("campaign_number", "source_record_key"),)


class RecallVehicleLink(Base):
    __tablename__ = "recall_vehicle_links"
    recall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recalls.id", ondelete="CASCADE"), primary_key=True
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="CASCADE"), primary_key=True
    )
    relation_source: Mapped[str] = mapped_column(Text, nullable=False, default="source_record")


class Investigation(Base):
    __tablename__ = "investigations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    investigation_number: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    component_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("components.id"), nullable=True
    )
    source_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_runs.id"), nullable=True
    )
    source_record_key: Mapped[str] = mapped_column(Text, nullable=False)
    open_date: Mapped[datetime] = mapped_column(Date, nullable=True)
    close_date: Mapped[datetime] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=True)
    investigation_type: Mapped[str] = mapped_column(Text, nullable=True)
    original_component: Mapped[str] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class InvestigationVehicleLink(Base):
    __tablename__ = "investigation_vehicle_links"
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), primary_key=True
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="CASCADE"), primary_key=True
    )
    relation_source: Mapped[str] = mapped_column(Text, nullable=False, default="source_record")


class ManufacturerCommunication(Base):
    __tablename__ = "manufacturer_communications"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    communication_number: Mapped[str] = mapped_column(Text, nullable=False)
    component_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("components.id"), nullable=True
    )
    source_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_runs.id"), nullable=True
    )
    source_record_key: Mapped[str] = mapped_column(Text, nullable=False)
    communication_date: Mapped[datetime] = mapped_column(Date, nullable=True)
    communication_type: Mapped[str] = mapped_column(Text, nullable=True)
    original_component: Mapped[str] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    __table_args__ = (UniqueConstraint("communication_number", "source_record_key"),)


class ManufacturerCommunicationVehicleLink(Base):
    __tablename__ = "manufacturer_communication_vehicle_links"
    communication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("manufacturer_communications.id", ondelete="CASCADE"),
        primary_key=True,
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="CASCADE"), primary_key=True
    )
    relation_source: Mapped[str] = mapped_column(Text, nullable=False, default="source_record")


class Citation(Base):
    __tablename__ = "citations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    field_name: Mapped[str] = mapped_column(Text, nullable=True)
    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    text_span: Mapped[str] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
