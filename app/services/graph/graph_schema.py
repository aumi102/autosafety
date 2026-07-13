"""
Graph schema — Neo4j constraints and indexes for Phase 3.

Constraints enable safe MERGE operations with idempotent node creation.
Indexes improve lookup performance.

Phase 3 node labels and their key properties:
- VehicleMake: normalized_name (unique)
- VehicleModel: key (unique, composite make+model)
- ModelYear: key (unique, composite model+year)
- Component: normalized_name (unique)
- Complaint: odi_number (unique, nullable)
- Recall: campaign_number (unique)
"""

from neo4j import Driver
from app.services.graph.neo4j_client import Neo4jClient


# Constraints — each enforces uniqueness on the key property used for MERGE
CONSTRAINTS = [
    # VehicleMake: one node per normalized make name
    """
    CREATE CONSTRAINT vehicle_make_normalized_name IF NOT EXISTS
    FOR (m:VehicleMake) REQUIRE m.normalized_name IS UNIQUE
    """,
    # VehicleModel: one node per composite key (make+model)
    """
    CREATE CONSTRAINT vehicle_model_key IF NOT EXISTS
    FOR (m:VehicleModel) REQUIRE m.key IS UNIQUE
    """,
    # ModelYear: one node per composite key (model+year)
    """
    CREATE CONSTRAINT model_year_key IF NOT EXISTS
    FOR (y:ModelYear) REQUIRE y.key IS UNIQUE
    """,
    # Component: one node per normalized component name
    """
    CREATE CONSTRAINT component_normalized_name IF NOT EXISTS
    FOR (c:Component) REQUIRE c.normalized_name IS UNIQUE
    """,
    # Complaint: one node per ODI number
    """
    CREATE CONSTRAINT complaint_odi_number IF NOT EXISTS
    FOR (c:Complaint) REQUIRE c.odi_number IS UNIQUE
    """,
    # Recall: one node per campaign number
    """
    CREATE CONSTRAINT recall_campaign_number IF NOT EXISTS
    FOR (r:Recall) REQUIRE r.campaign_number IS UNIQUE
    """,
]

# Indexes — additional performance indexes beyond uniqueness constraints
INDEXES = [
    # Speed up VehicleMake lookups by display name
    "CREATE INDEX vehicle_make_name IF NOT EXISTS FOR (m:VehicleMake) ON (m.name)",
    # Speed up Complaint date range queries
    "CREATE INDEX complaint_received_date IF NOT EXISTS FOR (c:Complaint) ON (c.received_date)",
    # Speed up Recall date range queries
    "CREATE INDEX recall_report_received_date IF NOT EXISTS FOR (r:Recall) ON (r.report_received_date)",
    # Speed up Component category filtering
    "CREATE INDEX component_category IF NOT EXISTS FOR (c:Component) ON (c.category)",
]


def setup_schema(driver: Driver) -> tuple[int, int, list[str]]:
    """
    Create all Phase 3 constraints and indexes.

    Uses IF NOT EXISTS so subsequent runs are idempotent.

    Returns (constraints_created, indexes_created, errors).
    """
    errors: list[str] = []
    constraints_created = 0
    indexes_created = 0

    with driver.session() as session:
        # Constraints
        for cypher in CONSTRAINTS:
            try:
                session.run(cypher)
                constraints_created += 1
            except Exception as e:
                err = str(e)
                # "already exists" is fine, ignore it
                if "already exists" not in err.lower() and "constraint" not in err.lower():
                    errors.append(f"Constraint error: {err}")

        # Indexes
        for cypher in INDEXES:
            try:
                session.run(cypher)
                indexes_created += 1
            except Exception as e:
                err = str(e)
                if "already exists" not in err.lower() and "index" not in err.lower():
                    errors.append(f"Index error: {err}")

    return constraints_created, indexes_created, errors


def get_schema_cypher() -> dict[str, list[str]]:
    """Return the canonical schema Cypher for documentation."""
    return {
        "constraints": [c.strip() for c in CONSTRAINTS],
        "indexes": INDEXES,
    }
