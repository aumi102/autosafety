"""Vehicle API endpoints."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import get_settings
from app.db.models.domain import Vehicle, Component, Complaint, Recall, RecallVehicleLink

router = APIRouter(tags=["vehicles"])


def _get_sync_session():
    settings = get_settings()
    db_url = settings.DATABASE_URL_SYNC
    if "postgresql+asyncpg" in db_url:
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(db_url, echo=False)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


class VehicleResponse(BaseModel):
    id: str
    make: str
    model: str
    model_year: int

    class Config:
        from_attributes = True


class ComponentSummary(BaseModel):
    component: str
    complaint_count: int


class VehicleOverview(BaseModel):
    vehicle: VehicleResponse
    metrics: dict
    top_components: list[ComponentSummary]
    warnings: list[str]


class VehicleSearchResponse(BaseModel):
    vehicles: list[VehicleResponse]
    total: int
    phase: str = "phase_1"


class ComplaintResponse(BaseModel):
    id: str
    odi_number: Optional[str]
    component: Optional[str]
    summary: Optional[str]
    received_date: Optional[str]
    crash_flag: bool
    fire_flag: bool
    injury_flag: bool
    death_flag: bool
    source_url: Optional[str]


class RecallResponse(BaseModel):
    id: str
    campaign_number: Optional[str]
    component: Optional[str]
    summary: Optional[str]
    remedy: Optional[str]
    report_received_date: Optional[str]
    units_affected: Optional[int]


@router.get("/search", response_model=VehicleSearchResponse)
def search_vehicles(
    make: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    model_year: Optional[int] = Query(None),
):
    """Search vehicles by make, model, year."""
    session = _get_sync_session()
    try:
        query = session.query(Vehicle)
        if make:
            query = query.filter(Vehicle.normalized_make == make.strip().upper())
        if model:
            query = query.filter(Vehicle.normalized_model == model.strip().upper())
        if model_year:
            query = query.filter(Vehicle.model_year == model_year)

        vehicles = query.all()
        return VehicleSearchResponse(
            vehicles=[
                VehicleResponse(id=str(v.id), make=v.make, model=v.model, model_year=v.model_year)
                for v in vehicles
            ],
            total=len(vehicles),
            phase="phase_1",
        )
    finally:
        session.close()


@router.get("/{vehicle_id}/overview", response_model=VehicleOverview)
def get_vehicle_overview(vehicle_id: str):
    """Get overview metrics for a vehicle."""
    session = _get_sync_session()
    try:
        vehicle = session.query(Vehicle).filter_by(id=uuid.UUID(vehicle_id)).first()
        if not vehicle:
            raise HTTPException(status_code=404, detail="Vehicle not found")

        complaint_count = session.query(Complaint).filter_by(vehicle_id=vehicle.id).count()
        recall_ids = session.query(RecallVehicleLink.recall_id).filter_by(vehicle_id=vehicle.id).all()
        recall_count = len(recall_ids)

        # Top components by complaint count
        from sqlalchemy import func
        top_components = session.query(
            Component.name,
            func.count(Complaint.id).label("cnt")
        ).join(Complaint, Complaint.component_id == Component.id).filter(
            Complaint.vehicle_id == vehicle.id
        ).group_by(Component.name).order_by(func.count(Complaint.id).desc()).limit(5).all()

        return VehicleOverview(
            vehicle=VehicleResponse(
                id=str(vehicle.id),
                make=vehicle.make,
                model=vehicle.model,
                model_year=vehicle.model_year,
            ),
            metrics={
                "complaint_count": complaint_count,
                "recall_count": recall_count,
                "investigation_count": 0,
                "manufacturer_communication_count": 0,
            },
            top_components=[
                ComponentSummary(component=c.name, complaint_count=c.cnt)
                for c in top_components
            ],
            warnings=["Complaint volume alone does not prove a safety defect or official causality."],
        )
    finally:
        session.close()


@router.get("/{vehicle_id}/complaints", response_model=list[ComplaintResponse])
def get_vehicle_complaints(
    vehicle_id: str,
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    """Get complaints for a vehicle."""
    session = _get_sync_session()
    try:
        vehicle = session.query(Vehicle).filter_by(id=uuid.UUID(vehicle_id)).first()
        if not vehicle:
            raise HTTPException(status_code=404, detail="Vehicle not found")

        complaints = session.query(Complaint).filter_by(vehicle_id=vehicle.id).offset(offset).limit(limit).all()
        component_map = {
            c.id: c.name for c in session.query(Component).all()
        }

        return [
            ComplaintResponse(
                id=str(c.id),
                odi_number=c.odi_number,
                component=component_map.get(c.component_id),
                summary=c.summary,
                received_date=str(c.received_date) if c.received_date else None,
                crash_flag=c.crash_flag,
                fire_flag=c.fire_flag,
                injury_flag=c.injury_flag,
                death_flag=c.death_flag,
                source_url=c.source_url,
            )
            for c in complaints
        ]
    finally:
        session.close()


@router.get("/{vehicle_id}/recalls", response_model=list[RecallResponse])
def get_vehicle_recalls(
    vehicle_id: str,
    limit: int = Query(50, le=200),
):
    """Get recalls for a vehicle."""
    session = _get_sync_session()
    try:
        vehicle = session.query(Vehicle).filter_by(id=uuid.UUID(vehicle_id)).first()
        if not vehicle:
            raise HTTPException(status_code=404, detail="Vehicle not found")

        recall_ids = session.query(RecallVehicleLink.recall_id).filter_by(vehicle_id=vehicle.id).limit(limit).all()
        recall_id_list = [r.recall_id for r in recall_ids]

        recalls = session.query(Recall).filter(Recall.id.in_(recall_id_list)).all() if recall_id_list else []
        component_map = {
            c.id: c.name for c in session.query(Component).all()
        }

        return [
            RecallResponse(
                id=str(r.id),
                campaign_number=r.campaign_number,
                component=component_map.get(r.component_id),
                summary=r.summary,
                remedy=r.remedy,
                report_received_date=str(r.report_received_date) if r.report_received_date else None,
                units_affected=r.units_affected,
            )
            for r in recalls
        ]
    finally:
        session.close()
