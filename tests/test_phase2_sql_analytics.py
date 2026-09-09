"""Tests for Phase 2 SQL analytics — no live network or LLM calls."""

import uuid

import pytest
from app.db.base import Base
from app.db.models.app import *  # noqa: F401, F403
from app.db.models.domain import *  # noqa: F401, F403
from app.services.sql_analytics.executor import execute_readonly_sql, validate_template_sql
from app.services.sql_analytics.question_parser import (
    _extract_limit,
    _extract_make,
    _extract_model,
    _extract_year,
    parse_question,
)
from app.services.sql_analytics.service import SqlAnalyticsService, answer_sql_analytics_question
from app.services.sql_analytics.templates import (
    TEMPLATES,
    TemplateId,
    get_template,
)
from app.services.sql_safety import validate_sql
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# =============================================================================
# Question parser tests
# =============================================================================

class TestExtractMake:
    def test_ford(self):
        assert _extract_make("Ford F-150 2020") == "Ford"

    def test_honda(self):
        assert _extract_make("Honda Accord 2021") == "Honda"

    def test_toyota(self):
        assert _extract_make("toyota camry") == "Toyota"

    def test_not_found(self):
        assert _extract_make("some random text") is None


class TestExtractModel:
    def test_f150_variant_f150(self):
        assert _extract_model("Ford F150 2020") == "F-150"

    def test_f150_variant_f_150(self):
        assert _extract_model("Ford F-150 2020") == "F-150"

    def test_f150_variant_f_150_with_space(self):
        assert _extract_model("Ford F 150") == "F-150"

    def test_accord(self):
        assert _extract_model("Honda Accord 2021") == "Accord"

    def test_camry(self):
        assert _extract_model("Toyota Camry 2022") == "Camry"

    def test_not_found(self):
        assert _extract_model("random text") is None


class TestExtractYear:
    def test_simple_year(self):
        assert _extract_year("Ford F-150 2020") == 2020

    def test_year_near_vehicle(self):
        assert _extract_year("The 2021 Honda Accord has issues") == 2021

    def test_no_year(self):
        assert _extract_year("random text no year") is None


class TestExtractLimit:
    def test_top_n(self):
        assert _extract_limit("top 5 components") == 5

    def test_top_n_variation(self):
        assert _extract_limit("show top 10 complaints") == 10

    def test_list_n(self):
        assert _extract_limit("list 20 recalls") == 20

    def test_no_limit(self):
        assert _extract_limit("how many complaints") is None

    def test_limit_capped(self):
        assert _extract_limit("top 5000") is None  # exceeds cap


class TestParseQuestion:
    def test_ford_f150_2020_top_components(self):
        result = parse_question("Top complaint components for Ford F-150 2020")
        assert result.intent == "top_complaint_components_by_vehicle"
        assert result.vehicle is not None
        assert result.vehicle.make == "Ford"
        assert result.vehicle.model == "F-150"
        assert result.vehicle.model_year == 2020
        assert result.vehicle.normalized_make == "FORD"
        assert result.vehicle.normalized_model == "F-150"
        assert result.confidence >= 0.8

    def test_f150_variant_f150(self):
        result = parse_question("How many complaints does Ford F150 2021 have?")
        assert result.intent == "complaint_count_by_vehicle"
        assert result.vehicle is not None
        assert result.vehicle.model == "F-150"
        assert result.vehicle.model_year == 2021

    def test_f150_variant_f_150_with_space(self):
        result = parse_question("F 150 Ford complaints 2020")
        assert result.intent == "top_complaint_components_by_vehicle"
        assert result.vehicle is not None
        assert result.vehicle.model == "F-150"

    def test_honda_accord_2021(self):
        result = parse_question("List recalls for Honda Accord 2021")
        assert result.intent == "recalls_by_vehicle"
        assert result.vehicle is not None
        assert result.vehicle.make == "Honda"
        assert result.vehicle.model == "Accord"
        assert result.vehicle.model_year == 2021

    def test_toyota_camry_2022_recall_count(self):
        result = parse_question("How many recalls does Toyota Camry 2022 have?")
        assert result.intent == "recall_count_by_vehicle"
        assert result.vehicle.model_year == 2022

    def test_vehicles_by_complaint_count(self):
        result = parse_question("Which vehicles have the most complaints?")
        assert result.intent == "vehicles_by_complaint_count"
        assert result.vehicle is None  # no specific vehicle
        assert result.confidence >= 0.3

    def test_complaint_count_by_component(self):
        result = parse_question("Complaint count by component for Toyota Camry 2022")
        assert result.intent == "complaint_count_by_component_for_vehicle"

    def test_missing_year_clarification(self):
        result = parse_question("Top complaint components for Ford F-150")
        assert result.intent == "clarification_needed"

    def test_missing_vehicle_unknown(self):
        result = parse_question("random gibberish question")
        assert result.intent == "unknown"

    def test_case_insensitive(self):
        result = parse_question("TOYOTA CAMRY 2021 COMPLAINTS")
        assert result.vehicle is not None
        assert result.vehicle.make == "Toyota"

    def test_limit_extracted(self):
        result = parse_question("Top 5 complaint components for Ford F-150 2020")
        assert result.limit == 5


# =============================================================================
# SQL template tests
# =============================================================================

class TestTemplates:
    def test_all_templates_registered(self):
        ids = list(TemplateId)
        assert len(ids) == 6
        for tid in ids:
            assert tid in TEMPLATES

    def test_top_complaint_components_template(self):
        t = get_template(TemplateId.TOP_COMPLAINT_COMPONENTS_BY_VEHICLE)
        assert t.id == TemplateId.TOP_COMPLAINT_COMPONENTS_BY_VEHICLE
        assert "complaints" in t.sql.lower()
        assert "vehicles" in t.sql.lower()
        assert ":make" in t.sql
        assert ":model" in t.sql
        assert ":model_year" in t.sql

    def test_complaint_count_template(self):
        t = get_template(TemplateId.COMPLAINT_COUNT_BY_VEHICLE)
        assert "COUNT" in t.sql

    def test_recalls_by_vehicle_template(self):
        t = get_template(TemplateId.RECALLS_BY_VEHICLE)
        assert "recall_vehicle_links" in t.sql.lower()

    def test_vehicles_by_complaint_count_template(self):
        t = get_template(TemplateId.VEHICLES_BY_COMPLAINT_COUNT)
        assert "ORDER BY" in t.sql and "complaint_count" in t.sql


# =============================================================================
# SQL safety tests for templates
# =============================================================================

class TestTemplateSafety:
    def test_all_templates_pass_validation(self):
        for tid, template in TEMPLATES.items():
            result = validate_template_sql(template.sql)
            assert result.valid, f"Template {tid} failed validation: {result.reason}"

    def test_unsafe_sql_blocked(self):
        result = validate_sql("DROP TABLE vehicles")
        assert result.valid is False

    def test_insert_sql_blocked(self):
        result = validate_sql("INSERT INTO vehicles VALUES (1)")
        assert result.valid is False

    def test_multiple_statements_blocked(self):
        result = validate_sql("SELECT * FROM vehicles; DROP TABLE vehicles")
        assert result.valid is False

    def test_select_allowed(self):
        result = validate_sql("SELECT * FROM vehicles LIMIT 10")
        assert result.valid is True


# =============================================================================
# Executor tests
# =============================================================================

@pytest.fixture
def in_memory_db():
    """Create in-memory SQLite DB with test data."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    # Insert test data
    vehicle = Vehicle(
        id=uuid.uuid4(),
        make="Ford",
        model="F-150",
        model_year=2020,
        normalized_make="FORD",
        normalized_model="F-150",
    )
    session.add(vehicle)
    session.flush()

    component = Component(
        id=uuid.uuid4(),
        name="SERVICE BRAKES",
        normalized_name="SERVICE BRAKES",
    )
    session.add(component)
    session.flush()

    for i in range(3):
        complaint = Complaint(
            id=uuid.uuid4(),
            vehicle_id=vehicle.id,
            component_id=component.id,
            source_record_key=f"test_complaint_{i}",
            raw_json={},
        )
        session.add(complaint)

    session.commit()
    yield session
    session.close()


class TestExecutor:
    def test_execute_select(self, in_memory_db):
        result = execute_readonly_sql(
            in_memory_db,
            "SELECT make, model, model_year FROM vehicles LIMIT 10",
            max_rows=10,
        )
        assert result.validated is True
        assert result.columns is not None
        assert len(result.rows) == 1
        assert result.row_count == 1
        assert result.truncated is False

    def test_execute_with_params(self, in_memory_db):
        vehicle = in_memory_db.query(Vehicle).first()
        # SQLite UUID storage strips dashes; normalize both sides for comparison
        result = execute_readonly_sql(
            in_memory_db,
            "SELECT * FROM complaints WHERE REPLACE(CAST(vehicle_id AS TEXT), '-', '') = REPLACE(:vehicle_id, '-', '') LIMIT 10",
            params={"vehicle_id": str(vehicle.id)},
            max_rows=10,
        )
        assert result.validated is True
        assert result.row_count == 3

    def test_blocks_write_sql(self, in_memory_db):
        result = execute_readonly_sql(
            in_memory_db,
            "INSERT INTO vehicles (make) VALUES ('Test')",
            max_rows=10,
        )
        assert result.validated is False
        assert result.rows is None

    def test_blocks_drop(self, in_memory_db):
        result = execute_readonly_sql(
            in_memory_db,
            "DROP TABLE vehicles",
            max_rows=10,
        )
        assert result.validated is False

    def test_enforces_max_rows(self, in_memory_db):
        result = execute_readonly_sql(
            in_memory_db,
            "SELECT * FROM complaints",
            max_rows=2,
        )
        assert result.row_count == 2
        assert result.truncated is True


# =============================================================================
# Analytics service tests
# =============================================================================

class TestSqlAnalyticsService:
    def test_top_components_ford_f150_2020(self, in_memory_db):
        service = SqlAnalyticsService(in_memory_db)
        result = service.answer("Top complaint components for Ford F-150 2020")
        assert result.intent == "sql"
        assert result.sql.used is True
        assert result.sql.query is not None
        assert result.sql.row_count >= 0
        assert any("complaint volume" in w.lower() for w in result.warnings)
        assert result.confidence.label in ("low", "medium", "high")

    def test_complaint_count_honda_accord_2021(self, in_memory_db):
        service = SqlAnalyticsService(in_memory_db)
        result = service.answer("How many complaints does Honda Accord 2021 have?")
        # No Honda data in DB, so row_count = 0
        assert result.intent == "sql"
        assert result.sql.used is True

    def test_vehicles_by_complaint_count(self, in_memory_db):
        service = SqlAnalyticsService(in_memory_db)
        result = service.answer("Which vehicles have the most complaints?")
        assert result.intent == "sql"
        assert result.sql.used is True
        # Ford F-150 has 3 complaints
        assert result.sql.row_count >= 1

    def test_clarification_needed_missing_year(self, in_memory_db):
        service = SqlAnalyticsService(in_memory_db)
        result = service.answer("Top complaint components for Ford F-150")
        assert result.intent == "clarification"
        assert result.sql.used is False

    def test_unknown_intent(self, in_memory_db):
        service = SqlAnalyticsService(in_memory_db)
        result = service.answer("random nonsense question xyz")
        assert result.intent == "clarification"

    def test_recall_query(self, in_memory_db):
        service = SqlAnalyticsService(in_memory_db)
        result = service.answer("List recalls for Ford F-150 2020")
        assert result.intent == "sql"
        assert result.sql.used is True
        # No recalls in DB

    def test_recall_count_query(self, in_memory_db):
        service = SqlAnalyticsService(in_memory_db)
        result = service.answer("How many recalls does Ford F-150 2020 have?")
        assert result.intent == "sql"
        assert result.sql.used is True

    def test_caveat_in_warnings(self, in_memory_db):
        service = SqlAnalyticsService(in_memory_db)
        result = service.answer("How many complaints does Ford F-150 2020 have?")
        assert any("defect" in w.lower() for w in result.warnings)

    def test_confidence_reflects_results(self, in_memory_db):
        service = SqlAnalyticsService(in_memory_db)
        # With data
        result1 = service.answer("Top complaint components for Ford F-150 2020")
        assert result1.confidence.label in ("low", "medium", "high")
        # Without data
        result2 = service.answer("How many complaints does Honda Accord 2021 have?")
        assert result2.confidence.label == "low"

    def test_recall_caveat_present(self, in_memory_db):
        service = SqlAnalyticsService(in_memory_db)
        result = service.answer("List recalls for Ford F-150 2020")
        assert any("recall" in w.lower() or "causality" in w.lower() for w in result.warnings)

    def test_tool_calls_tracked(self, in_memory_db):
        service = SqlAnalyticsService(in_memory_db)
        result = service.answer("Top complaint components for Ford F-150 2020")
        assert result.tool_call_count >= 1
        assert result.latency_ms >= 0


# =============================================================================
# Convenience function tests
# =============================================================================

class TestAnswerFunction:
    def test_answer_sql_analytics_question(self, in_memory_db):
        result = answer_sql_analytics_question(
            in_memory_db,
            "Top complaint components for Ford F-150 2020"
        )
        assert result.intent == "sql"
        assert result.run_id is not None
        assert result.sql.query is not None
