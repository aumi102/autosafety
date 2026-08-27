"""
SQL Analytics adapter — Phase 7B.

Wraps Phase 2 SqlAnalyticsService through a structured tool interface.
Reuses predefined templates. No raw SQL. No mutation.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from app.services.answer_synthesis.tools.base import (
    ToolDefinition,
    ToolInputField,
    ToolInputSchema,
    ToolCallResult,
)


# Supported operations mapped to actual Phase 2 template intents
SUPPORTED_OPERATIONS = [
    "top_complaint_components_by_vehicle",
    "complaint_count_by_vehicle",
    "recalls_by_vehicle",
    "recall_count_by_vehicle",
    "vehicles_by_complaint_count",
    "complaint_count_by_component_for_vehicle",
]


SQL_ANALYTICS_DEFINITION = ToolDefinition(
    name="sql_analytics_tool",
    description=(
        "Retrieve structured SQL analytics from vehicle safety complaints and recalls. "
        "Operations return tables of counts, rankings, and lists. "
        "No raw SQL — uses predefined templates only."
    ),
    input_schema=ToolInputSchema(fields={
        "operation": ToolInputField(
            type="enum",
            description="Analytics operation to execute",
            required=True,
            enum_values=SUPPORTED_OPERATIONS,
        ),
        "make": ToolInputField(
            type="string",
            description="Vehicle make (e.g. Ford)",
            required=False,
            max_length=50,
        ),
        "model": ToolInputField(
            type="string",
            description="Vehicle model (e.g. F-150)",
            required=False,
            max_length=50,
        ),
        "model_year": ToolInputField(
            type="integer",
            description="Vehicle model year (e.g. 2020)",
            required=False,
            min_value=1990,
            max_value=2030,
        ),
        "component": ToolInputField(
            type="string",
            description="Component name for component-specific queries",
            required=False,
            max_length=100,
        ),
        "limit": ToolInputField(
            type="integer",
            description="Maximum rows to return (default 10, max 50)",
            required=False,
            default=10,
            min_value=1,
            max_value=50,
        ),
    }),
    read_only=True,
    max_result_items=50,
    timeout_seconds=15,
)


def build_sql_analytics_adapter(
    session_factory: Callable,
) -> Callable[..., ToolCallResult]:
    """
    Build a SQL analytics tool adapter.

    Args:
        session_factory: callable returning a SQLAlchemy session.
                        The session is closed after each call.
    """
    def adapter(*, call_id: str, arguments: dict[str, Any]) -> ToolCallResult:
        from sqlalchemy.exc import SQLAlchemyError

        operation = arguments.get("operation")
        make = arguments.get("make")
        model = arguments.get("model")
        model_year = arguments.get("model_year")
        component = arguments.get("component")
        limit = arguments.get("limit", 10)

        # Build a natural-language question from structured arguments
        # so we can reuse the existing Phase 2 parser and templates
        question = _build_question(operation, make, model, model_year, component, limit)

        session = session_factory()
        try:
            from app.services.sql_analytics.service import SqlAnalyticsService
            service = SqlAnalyticsService(session)
            response = service.answer(question)

            # Extract SQL result data for the evidence bundle
            sql_data = response.sql
            rows = sql_data.rows or []
            columns = sql_data.columns or []

            # Bound to max_result_items
            bounded_rows = rows[:limit]
            truncated = len(rows) > limit

            # Build sanitized result dict
            result_data = {
                "operation": operation,
                "columns": columns,
                "rows": bounded_rows,
                "row_count": len(bounded_rows),
                "total_row_count": sql_data.row_count or 0,
                "execution_ms": sql_data.execution_ms or 0,
                "validated": sql_data.validated,
                "intent": response.intent,
            }

            warnings = list(response.warnings or [])
            return ToolCallResult.ok(call_id, "sql_analytics_tool", result_data, warnings, truncated)

        except SQLAlchemyError as e:
            return ToolCallResult.error(
                call_id, "sql_analytics_tool",
                "db_error",
                f"Database error: {type(e).__name__}",
            )
        except Exception as e:
            return ToolCallResult.error(
                call_id, "sql_analytics_tool",
                "adapter_error",
                f"SQL analytics adapter error: {type(e).__name__}",
            )
        finally:
            session.close()

    return adapter


def _build_question(
    operation: str,
    make: Optional[str],
    model: Optional[str],
    model_year: Optional[int],
    component: Optional[str],
    limit: int,
) -> str:
    """Build a natural-language question from structured arguments, reusing Phase 2 parser."""
    if operation == "top_complaint_components_by_vehicle":
        vehicle = _vehicle_str(make, model, model_year)
        return f"Top complaint components for {vehicle}"
    elif operation == "complaint_count_by_vehicle":
        vehicle = _vehicle_str(make, model, model_year)
        return f"How many complaints does {vehicle} have?"
    elif operation == "recalls_by_vehicle":
        vehicle = _vehicle_str(make, model, model_year)
        return f"List recalls for {vehicle}"
    elif operation == "recall_count_by_vehicle":
        vehicle = _vehicle_str(make, model, model_year)
        return f"How many recalls does {vehicle} have?"
    elif operation == "vehicles_by_complaint_count":
        suffix = f" {make}" if make else ""
        return f"Which vehicles{suffix} have the most complaints?"
    elif operation == "complaint_count_by_component_for_vehicle":
        vehicle = _vehicle_str(make, model, model_year)
        return f"Complaint count by component for {vehicle}"
    else:
        return f"Top complaint components for {make or 'any'} {model or 'vehicle'}"


def _vehicle_str(make: Optional[str], model: Optional[str], year: Optional[int]) -> str:
    parts = [p for p in [make, model, str(year) if year else None] if p]
    return " ".join(parts) if parts else "unknown vehicle"
