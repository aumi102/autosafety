"""
Schema registry for SQL analytics.

Defines which tables and columns are allowed for Phase 2 analytics.
This registry is used by the template system to build safe SQL queries.
"""

from dataclasses import dataclass


@dataclass
class ColumnDef:
    """Definition of an allowed column."""
    name: str
    description: str
    nullable: bool = True
    is_uuid: bool = False


@dataclass
class TableDef:
    """Definition of an allowed table."""
    name: str
    description: str
    columns: list[ColumnDef]
    join_hint: str | None = None
    key_column: str | None = None


# Allowed tables for Phase 2 SQL analytics
TABLES: dict[str, TableDef] = {
    "vehicles": TableDef(
        name="vehicles",
        description="Vehicle make/model/year records. Canonical vehicle identifiers.",
        columns=[
            ColumnDef("id", "UUID primary key", is_uuid=True),
            ColumnDef("make", "Original make name (e.g., Ford)"),
            ColumnDef("model", "Original model name (e.g., F-150)"),
            ColumnDef("model_year", "Model year (integer, e.g., 2020)"),
            ColumnDef("normalized_make", "Uppercase, whitespace-collapsed make name"),
            ColumnDef("normalized_model", "Uppercase, whitespace-collapsed model name"),
            ColumnDef("created_at", "Record creation timestamp"),
        ],
        key_column="id",
    ),
    "components": TableDef(
        name="components",
        description="Normalized component names (e.g., SERVICE BRAKES, ENGINE).",
        columns=[
            ColumnDef("id", "UUID primary key", is_uuid=True),
            ColumnDef("name", "Original component name"),
            ColumnDef("normalized_name", "Uppercase canonical component name"),
            ColumnDef("category", "Optional component category"),
            ColumnDef("created_at", "Record creation timestamp"),
        ],
        key_column="id",
    ),
    "complaints": TableDef(
        name="complaints",
        description="NHTSA complaint records linked to vehicles and components.",
        columns=[
            ColumnDef("id", "UUID primary key", is_uuid=True),
            ColumnDef("odi_number", "NHTSA ODI complaint number (unique)"),
            ColumnDef("vehicle_id", "FK to vehicles.id", is_uuid=True),
            ColumnDef("component_id", "FK to components.id (nullable)", is_uuid=True, nullable=True),
            ColumnDef("source_run_id", "FK to source_runs.id", is_uuid=True),
            ColumnDef("source_record_key", "Unique source record identifier"),
            ColumnDef("received_date", "Date complaint was received (YYYY-MM-DD)"),
            ColumnDef("incident_date", "Date of incident (YYYY-MM-DD)"),
            ColumnDef("original_component", "Original NHTSA component name"),
            ColumnDef("summary", "Complaint summary text"),
            ColumnDef("narrative", "Full complaint narrative (nullable)"),
            ColumnDef("crash_flag", "Crash involved (boolean)"),
            ColumnDef("fire_flag", "Fire involved (boolean)"),
            ColumnDef("injury_flag", "Injury involved (boolean)"),
            ColumnDef("death_flag", "Death involved (boolean)"),
            ColumnDef("source_url", "NHTSA ODI URL"),
            ColumnDef("raw_json", "Raw source data as JSON"),
            ColumnDef("created_at", "Record creation timestamp"),
        ],
        key_column="id",
    ),
    "recalls": TableDef(
        name="recalls",
        description="NHTSA recall campaign records.",
        columns=[
            ColumnDef("id", "UUID primary key", is_uuid=True),
            ColumnDef("campaign_number", "NHTSA campaign number"),
            ColumnDef("component_id", "FK to components.id (nullable)", is_uuid=True, nullable=True),
            ColumnDef("source_run_id", "FK to source_runs.id", is_uuid=True),
            ColumnDef("source_record_key", "Unique source record identifier"),
            ColumnDef("report_received_date", "Date recall was reported"),
            ColumnDef("original_component", "Original NHTSA component name"),
            ColumnDef("summary", "Recall summary text"),
            ColumnDef("consequence", "Consequence description"),
            ColumnDef("remedy", "Remedy description"),
            ColumnDef("notes", "Additional notes"),
            ColumnDef("units_affected", "Number of units affected"),
            ColumnDef("source_url", "NHTSA recall URL"),
            ColumnDef("raw_json", "Raw source data as JSON"),
            ColumnDef("created_at", "Record creation timestamp"),
        ],
        key_column="id",
    ),
    "recall_vehicle_links": TableDef(
        name="recall_vehicle_links",
        description="Many-to-many links between recalls and vehicles.",
        columns=[
            ColumnDef("recall_id", "FK to recalls.id", is_uuid=True),
            ColumnDef("vehicle_id", "FK to vehicles.id", is_uuid=True),
            ColumnDef("relation_source", "How the relation was determined"),
        ],
    ),
    "source_runs": TableDef(
        name="source_runs",
        description="Tracks each data ingestion run.",
        columns=[
            ColumnDef("id", "UUID primary key", is_uuid=True),
            ColumnDef("source_name", "Name of the data source"),
            ColumnDef("source_url", "URL of the data source"),
            ColumnDef("source_type", "Type (nhtsa_api, nhtsa_flat_file, etc.)"),
            ColumnDef("file_name", "File name if from file"),
            ColumnDef("file_sha256", "SHA256 of source file"),
            ColumnDef("started_at", "Ingestion start timestamp"),
            ColumnDef("finished_at", "Ingestion finish timestamp"),
            ColumnDef("status", "Status: success, failed, partial_success"),
            ColumnDef("row_count", "Number of rows ingested"),
            ColumnDef("error_message", "Error message if failed"),
            ColumnDef("metadata_json", "Additional metadata as JSON"),
        ],
        key_column="id",
    ),
}

# Join paths for common analytics
JOIN_PATHS = {
    ("complaints", "vehicles"): """
-- complaints JOIN vehicles
FROM complaints c
JOIN vehicles v ON v.id = c.vehicle_id
""",
    ("complaints", "components"): """
-- complaints JOIN components
FROM complaints c
LEFT JOIN components comp ON comp.id = c.component_id
""",
    ("complaints", "vehicles", "components"): """
-- complaints JOIN vehicles JOIN components
FROM complaints c
JOIN vehicles v ON v.id = c.vehicle_id
LEFT JOIN components comp ON comp.id = c.component_id
""",
    ("recalls", "vehicles"): """
-- recalls JOIN recall_vehicle_links JOIN vehicles
FROM recalls r
JOIN recall_vehicle_links rvl ON rvl.recall_id = r.id
JOIN vehicles v ON v.id = rvl.vehicle_id
""",
    ("recalls", "components"): """
-- recalls JOIN components
FROM recalls r
LEFT JOIN components comp ON comp.id = r.component_id
""",
}

# Common metrics that templates can reference
METRICS = {
    "complaint_count": "COUNT(c.id)",
    "recall_count": "COUNT(DISTINCT r.id)",
    "vehicle_count": "COUNT(DISTINCT v.id)",
    "component_count": "COUNT(DISTINCT comp.id)",
    "total_injury_count": "SUM(CASE WHEN c.injury_flag = true THEN 1 ELSE 0 END)",
    "total_death_count": "SUM(CASE WHEN c.death_flag = true THEN 1 ELSE 0 END)",
    "total_crash_count": "SUM(CASE WHEN c.crash_flag = true THEN 1 ELSE 0 END)",
}


def get_allowed_tables() -> list[str]:
    """Return list of allowed table names."""
    return list(TABLES.keys())


def get_table_columns(table_name: str) -> list[ColumnDef]:
    """Return column definitions for a table."""
    table = TABLES.get(table_name)
    return table.columns if table else []


def validate_table_access(table_name: str) -> bool:
    """Check if a table is in the allowed set."""
    return table_name.lower() in TABLES
