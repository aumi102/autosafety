"""
SQL query templates for Phase 2 analytics.

Each template is a named, parameterized SQL string.
Templates use bind-safe parameter notation (Python %s is NOT used — we use named params via executor).
"""

from dataclasses import dataclass
from enum import Enum


class TemplateId(str, Enum):
    """Supported SQL template IDs."""
    TOP_COMPLAINT_COMPONENTS_BY_VEHICLE = "top_complaint_components_by_vehicle"
    COMPLAINT_COUNT_BY_VEHICLE = "complaint_count_by_vehicle"
    RECALLS_BY_VEHICLE = "recalls_by_vehicle"
    RECALL_COUNT_BY_VEHICLE = "recall_count_by_vehicle"
    VEHICLES_BY_COMPLAINT_COUNT = "vehicles_by_complaint_count"
    COMPLAINT_COUNT_BY_COMPONENT_FOR_VEHICLE = "complaint_count_by_component_for_vehicle"


@dataclass
class SqlTemplate:
    """A SQL query template with metadata."""
    id: TemplateId
    description: str
    sql: str
    params: dict  # param_name -> param_type/description


# Template library
TEMPLATES: dict[TemplateId, SqlTemplate] = {}


def _reg(tid: TemplateId, description: str, sql: str, params: dict) -> SqlTemplate:
    """Register a template."""
    t = SqlTemplate(id=tid, description=description, sql=sql, params=params)
    TEMPLATES[tid] = t
    return t


# -----------------------------------------------------------------------------
# Template 1: Top complaint components for a specific vehicle
# -----------------------------------------------------------------------------
_reg(
    TemplateId.TOP_COMPLAINT_COMPONENTS_BY_VEHICLE,
    "Top N complaint components for a vehicle",
    """
SELECT
    COALESCE(comp.name, comps.name, 'UNKNOWN') AS component,
    COUNT(c.id) AS complaint_count
FROM complaints c
JOIN vehicles v ON v.id = c.vehicle_id
LEFT JOIN components comp ON comp.id = c.component_id
LEFT JOIN components comps ON comps.normalized_name = c.original_component
WHERE v.normalized_make = :make
  AND v.normalized_model = :model
  AND v.model_year = :model_year
GROUP BY COALESCE(comp.name, comps.name, 'UNKNOWN')
ORDER BY complaint_count DESC
LIMIT :limit
""",
    {
        "make": "normalized make (uppercase)",
        "model": "normalized model (uppercase, e.g., F-150)",
        "model_year": "model year (integer)",
        "limit": "result limit (integer, default 10)",
    },
)


# -----------------------------------------------------------------------------
# Template 2: Complaint count for a specific vehicle
# -----------------------------------------------------------------------------
_reg(
    TemplateId.COMPLAINT_COUNT_BY_VEHICLE,
    "Total complaint count for a vehicle",
    """
SELECT
    v.make,
    v.model,
    v.model_year,
    COUNT(c.id) AS complaint_count
FROM complaints c
JOIN vehicles v ON v.id = c.vehicle_id
WHERE v.normalized_make = :make
  AND v.normalized_model = :model
  AND v.model_year = :model_year
GROUP BY v.make, v.model, v.model_year
""",
    {
        "make": "normalized make (uppercase)",
        "model": "normalized model (uppercase)",
        "model_year": "model year (integer)",
    },
)


# -----------------------------------------------------------------------------
# Template 3: List recalls for a vehicle
# -----------------------------------------------------------------------------
_reg(
    TemplateId.RECALLS_BY_VEHICLE,
    "All recalls for a vehicle",
    """
SELECT
    r.campaign_number,
    COALESCE(comp.name, comps.name, r.original_component) AS component,
    r.summary,
    r.consequence,
    r.remedy,
    r.units_affected,
    r.report_received_date
FROM recalls r
JOIN recall_vehicle_links rvl ON rvl.recall_id = r.id
JOIN vehicles v ON v.id = rvl.vehicle_id
LEFT JOIN components comp ON comp.id = r.component_id
LEFT JOIN components comps ON comps.normalized_name = r.original_component
WHERE v.normalized_make = :make
  AND v.normalized_model = :model
  AND v.model_year = :model_year
ORDER BY r.report_received_date DESC NULLS LAST
LIMIT :limit
""",
    {
        "make": "normalized make (uppercase)",
        "model": "normalized model (uppercase)",
        "model_year": "model year (integer)",
        "limit": "result limit (integer, default 50)",
    },
)


# -----------------------------------------------------------------------------
# Template 4: Recall count for a vehicle
# -----------------------------------------------------------------------------
_reg(
    TemplateId.RECALL_COUNT_BY_VEHICLE,
    "Total recall count for a vehicle",
    """
SELECT
    v.make,
    v.model,
    v.model_year,
    COUNT(DISTINCT r.id) AS recall_count
FROM recalls r
JOIN recall_vehicle_links rvl ON rvl.recall_id = r.id
JOIN vehicles v ON v.id = rvl.vehicle_id
WHERE v.normalized_make = :make
  AND v.normalized_model = :model
  AND v.model_year = :model_year
GROUP BY v.make, v.model, v.model_year
""",
    {
        "make": "normalized make (uppercase)",
        "model": "normalized model (uppercase)",
        "model_year": "model year (integer)",
    },
)


# -----------------------------------------------------------------------------
# Template 5: Vehicles ordered by complaint count
# -----------------------------------------------------------------------------
_reg(
    TemplateId.VEHICLES_BY_COMPLAINT_COUNT,
    "Vehicles ranked by complaint count (all or by make)",
    """
SELECT
    v.make,
    v.model,
    v.model_year,
    COUNT(c.id) AS complaint_count
FROM vehicles v
LEFT JOIN complaints c ON c.vehicle_id = v.id
WHERE (:make IS NULL OR v.normalized_make = :make)
GROUP BY v.make, v.model, v.model_year
ORDER BY complaint_count DESC
LIMIT :limit
""",
    {
        "make": "normalized make filter (optional, uppercase or NULL for all)",
        "limit": "result limit (integer, default 20)",
    },
)


# -----------------------------------------------------------------------------
# Template 6: Complaint count by component for a vehicle
# -----------------------------------------------------------------------------
_reg(
    TemplateId.COMPLAINT_COUNT_BY_COMPONENT_FOR_VEHICLE,
    "Complaint counts grouped by component for a vehicle",
    """
SELECT
    COALESCE(comp.name, comps.name, 'UNKNOWN') AS component,
    COUNT(c.id) AS complaint_count,
    SUM(CASE WHEN c.crash_flag = true THEN 1 ELSE 0 END) AS crash_count,
    SUM(CASE WHEN c.injury_flag = true THEN 1 ELSE 0 END) AS injury_count,
    SUM(CASE WHEN c.death_flag = true THEN 1 ELSE 0 END) AS death_count
FROM complaints c
JOIN vehicles v ON v.id = c.vehicle_id
LEFT JOIN components comp ON comp.id = c.component_id
LEFT JOIN components comps ON comps.normalized_name = c.original_component
WHERE v.normalized_make = :make
  AND v.normalized_model = :model
  AND v.model_year = :model_year
GROUP BY COALESCE(comp.name, comps.name, 'UNKNOWN')
ORDER BY complaint_count DESC
LIMIT :limit
""",
    {
        "make": "normalized make (uppercase)",
        "model": "normalized model (uppercase)",
        "model_year": "model year (integer)",
        "limit": "result limit (integer, default 20)",
    },
)


def get_template(tid: TemplateId) -> SqlTemplate:
    """Get a template by ID."""
    return TEMPLATES[tid]


def get_all_templates() -> dict[TemplateId, SqlTemplate]:
    """Get all templates."""
    return TEMPLATES
