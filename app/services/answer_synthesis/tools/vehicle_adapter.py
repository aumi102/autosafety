"""
Vehicle Resolution adapter — Phase 7B.

Deterministic lookup of vehicle make/model/year to internal identifiers.
Read-only. No raw SQL. No fuzzy LLM-generated queries.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services.answer_synthesis.tools.base import (
    ToolCallResult,
    ToolDefinition,
    ToolInputField,
    ToolInputSchema,
)

VEHICLE_RESOLUTION_DEFINITION = ToolDefinition(
    name="vehicle_resolution_tool",
    description=(
        "Resolve make, model, and model year to an internal vehicle identifier. "
        "Deterministic lookup — not fuzzy. Returns resolved status. "
        "Use this before calling other tools that require a vehicle_id."
    ),
    input_schema=ToolInputSchema(
        fields={
            "make": ToolInputField(
                type="string",
                description="Vehicle make (e.g. Ford)",
                required=True,
                max_length=50,
            ),
            "model": ToolInputField(
                type="string",
                description="Vehicle model (e.g. F-150)",
                required=True,
                max_length=50,
            ),
            "model_year": ToolInputField(
                type="integer",
                description="Vehicle model year (e.g. 2020)",
                required=True,
                min_value=1990,
                max_value=2030,
            ),
        }
    ),
    read_only=True,
    max_result_items=10,
    timeout_seconds=10,
)


def build_vehicle_resolution_adapter(
    session_factory: Callable | None = None,
) -> Callable[..., ToolCallResult]:
    """
    Build a vehicle resolution tool adapter.

    Uses PostgreSQL ORM lookup — deterministic, parameterized, no raw SQL.
    Reuses an application-owned session factory; never creates an engine per
    request or tool call.
    """
    if session_factory is None:
        from sqlalchemy.orm import sessionmaker

        from app.db.session import get_sync_engine

        session_factory = sessionmaker(bind=get_sync_engine(), expire_on_commit=False)

    def adapter(*, call_id: str, arguments: dict[str, Any]) -> ToolCallResult:
        make = arguments.get("make", "").strip()
        model = arguments.get("model", "").strip()
        model_year = arguments.get("model_year")

        try:
            from app.db.models.domain import Vehicle
            from app.services.ingestion.normalization import normalize_make, normalize_model

            session = session_factory()

            try:
                norm_make = normalize_make(make)
                norm_model = normalize_model(model)

                vehicle = (
                    session.query(Vehicle)
                    .filter(
                        Vehicle.normalized_make == norm_make,
                        Vehicle.normalized_model == norm_model,
                        Vehicle.model_year == model_year,
                    )
                    .first()
                )

                if vehicle:
                    return ToolCallResult.ok(
                        call_id,
                        "vehicle_resolution_tool",
                        {
                            "resolved": True,
                            "status": "resolved",
                            "vehicle_id": str(vehicle.id),
                            "normalized_make": vehicle.normalized_make,
                            "normalized_model": vehicle.normalized_model,
                            "model_year": vehicle.model_year,
                            "make": vehicle.make,
                            "model": vehicle.model,
                        },
                    )

                # Check if vehicle exists with different year
                candidates = (
                    session.query(Vehicle)
                    .filter(
                        Vehicle.normalized_make == norm_make,
                        Vehicle.normalized_model == norm_model,
                    )
                    .all()
                )

                if len(candidates) == 1:
                    # Ambiguous — model/year combo not found but model exists
                    return ToolCallResult.ok(
                        call_id,
                        "vehicle_resolution_tool",
                        {
                            "resolved": False,
                            "status": "year_not_found",
                            "vehicle_id": None,
                            "normalized_make": norm_make,
                            "normalized_model": norm_model,
                            "model_year": model_year,
                            "available_years": [v.model_year for v in candidates],
                            "message": f"Model exists but year {model_year} not found. Available years: {[v.model_year for v in candidates]}",
                        },
                    )
                elif len(candidates) > 1:
                    # Ambiguous
                    return ToolCallResult.ok(
                        call_id,
                        "vehicle_resolution_tool",
                        {
                            "resolved": False,
                            "status": "ambiguous",
                            "vehicle_id": None,
                            "normalized_make": norm_make,
                            "normalized_model": norm_model,
                            "model_year": model_year,
                            "available_years": [v.model_year for v in candidates],
                        },
                    )
                else:
                    # Not found
                    return ToolCallResult.ok(
                        call_id,
                        "vehicle_resolution_tool",
                        {
                            "resolved": False,
                            "status": "not_found",
                            "vehicle_id": None,
                            "normalized_make": norm_make,
                            "normalized_model": norm_model,
                            "model_year": model_year,
                        },
                    )

            finally:
                session.close()

        except ImportError as e:
            return ToolCallResult.error(
                call_id,
                "vehicle_resolution_tool",
                "import_error",
                f"Vehicle model unavailable: {type(e).__name__}",
            )
        except Exception as e:
            return ToolCallResult.error(
                call_id,
                "vehicle_resolution_tool",
                "adapter_error",
                f"Vehicle resolution failed: {type(e).__name__}",
            )

    return adapter
