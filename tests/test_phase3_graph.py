"""Tests for Phase 3 graph service — no live Neo4j required."""

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field

from app.services.graph.graph_models import (
    GraphBuildStats,
    GraphStatus,
    VehicleNeighborhood,
    RecallPathResult,
    GraphSchemaSetupResult,
    NeighborhoodNode,
    NeighborhoodEdge,
    RecallNode,
)
from app.services.graph.graph_queries import (
    get_vehicle_neighborhood,
    get_recall_paths_for_vehicle,
    execute_arbitrary_cypher,
    _validate_identifier,
)
from app.services.graph.graph_schema import (
    CONSTRAINTS,
    INDEXES,
    get_schema_cypher,
)


# =============================================================================
# Graph schema tests
# =============================================================================

class TestGraphSchema:
    def test_constraints_contain_required_labels(self):
        """All required node labels appear in constraint Cypher."""
        constraint_text = "\n".join(CONSTRAINTS)
        for label in ["VehicleMake", "VehicleModel", "ModelYear", "Component", "Complaint", "Recall"]:
            assert label in constraint_text, f"Missing label: {label}"

    def test_constraints_use_unique(self):
        """All constraints use IS UNIQUE."""
        for c in CONSTRAINTS:
            assert "IS UNIQUE" in c, f"Missing IS UNIQUE in: {c[:60]}"

    def test_indexes_defined(self):
        """Index list is non-empty."""
        assert len(INDEXES) > 0

    def test_schema_cypher_returns_both(self):
        """get_schema_cypher returns constraints and indexes."""
        result = get_schema_cypher()
        assert "constraints" in result
        assert "indexes" in result
        assert len(result["constraints"]) == len(CONSTRAINTS)


# =============================================================================
# Graph queries tests
# =============================================================================

class TestIdentifierValidation:
    def test_valid_identifier_passthrough(self):
        assert _validate_identifier("Ford", "make") == "Ford"

    def test_valid_with_spaces(self):
        assert _validate_identifier("F-150", "model") == "F-150"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            _validate_identifier("", "make")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            _validate_identifier("   ", "make")

    def test_invalid_chars_raises(self):
        with pytest.raises(ValueError, match="Invalid characters"):
            _validate_identifier("Ford'; DROP TABLE--", "make")


class TestArbitraryCypherBlocked:
    def test_arbitrary_cypher_returns_error(self):
        """execute_arbitrary_cypher always returns an error dict."""
        mock_client = MagicMock()
        result = execute_arbitrary_cypher(mock_client, "MATCH (n) RETURN n")
        assert "error" in result
        assert result["error"] == "arbitrary_cypher_not_allowed"
        # Client should never be called
        mock_client.execute.assert_not_called()


class TestVehicleNeighborhoodQuery:
    def test_returns_none_when_no_match(self):
        """Returns None when vehicle not found in graph."""
        mock_client = MagicMock()
        mock_client.execute_single.return_value = None

        result = get_vehicle_neighborhood(mock_client, "Ford", "F-150", 2020)
        assert result is None

    def test_returns_neighborhood_when_found(self):
        """Returns VehicleNeighborhood when vehicle exists."""
        mock_client = MagicMock()
        mock_client.execute_single.return_value = {
            "make_name": "FORD",
            "make_display": "Ford",
            "model_name": "F-150",
            "model_year": 2020,
            "vehicle_id": "abc-123",
            "complaints": [],
            "recalls": [],
            "components": [],
            "complaint_count": 0,
            "recall_count": 0,
        }

        result = get_vehicle_neighborhood(mock_client, "Ford", "F-150", 2020)
        assert result is not None
        assert result.make == "FORD"
        assert result.model == "F-150"
        assert result.year == 2020
        assert result.complaint_count == 0
        assert result.recall_count == 0

    def test_passes_params_correctly(self):
        """Query is called with correct normalized params."""
        mock_client = MagicMock()
        mock_client.execute_single.return_value = None

        get_vehicle_neighborhood(mock_client, "honda", "accord", 2021)

        mock_client.execute_single.assert_called_once()
        call_args = mock_client.execute_single.call_args
        # args is (cypher, params) positional, or kwargs
        if call_args.kwargs:
            params = call_args.kwargs.get("params", {})
        else:
            params = call_args[0][1] if len(call_args[0]) > 1 else {}
        assert params["make"] == "HONDA"
        assert params["model_key"] == "HONDA:ACCORD"
        assert params["year_key"] == "ACCORD:2021"


class TestRecallPathsQuery:
    def test_returns_none_when_no_match(self):
        """Returns None when vehicle not found."""
        mock_client = MagicMock()
        mock_client.execute_single.return_value = None

        result = get_recall_paths_for_vehicle(mock_client, "Ford", "F-150", 2020)
        assert result is None

    def test_returns_potentially_related_path_type(self):
        """path_type is 'potentially related', not 'caused by'."""
        mock_client = MagicMock()
        mock_client.execute_single.return_value = {
            "make_name": "FORD",
            "model_name": "F-150",
            "model_year": 2020,
            "vehicle_id": "abc-123",
            "recalls": [],
        }

        result = get_recall_paths_for_vehicle(mock_client, "Ford", "F-150", 2020)
        assert result is not None
        assert "potentially" in result.path_type.lower()
        assert "caused" not in result.path_type.lower()
        assert "official" not in result.path_type.lower()


# =============================================================================
# Graph models tests
# =============================================================================

class TestGraphBuildStats:
    def test_to_dict_includes_all_fields(self):
        stats = GraphBuildStats(
            vehicle_makes_seen=3,
            vehicle_models_seen=5,
            model_years_seen=7,
            components_seen=10,
            complaints_seen=20,
            recalls_seen=4,
            nodes_merged=100,
            relationships_merged=150,
            rows_skipped=2,
            errors_count=1,
            errors=["test error"],
            duration_ms=500,
            dry_run=False,
        )
        d = stats.to_dict()
        assert d["vehicle_makes_seen"] == 3
        assert d["nodes_merged"] == 100
        assert d["relationships_merged"] == 150
        assert d["dry_run"] is False
        assert "test error" in d["errors"]

    def test_dry_run_true_preserved(self):
        stats = GraphBuildStats(dry_run=True, components_seen=5)
        d = stats.to_dict()
        assert d["dry_run"] is True


class TestVehicleNeighborhoodModel:
    def test_to_dict(self):
        nodes = [
            NeighborhoodNode(id="ford", label="VehicleMake", properties={"name": "Ford"}),
            NeighborhoodNode(id="f-150", label="VehicleModel", properties={"name": "F-150"}),
        ]
        edges = [
            NeighborhoodEdge(type="HAS_MODEL", source_id="ford", target_id="f-150"),
        ]
        nh = VehicleNeighborhood(
            make="Ford", model="F-150", year=2020, vehicle_id="abc",
            nodes=nodes, edges=edges,
            complaint_count=5, recall_count=2, components=["SERVICE BRAKES"],
        )
        d = nh.to_dict()
        assert d["make"] == "Ford"
        assert len(d["nodes"]) == 2
        assert len(d["edges"]) == 1
        assert d["complaint_count"] == 5


class TestRecallPathResult:
    def test_to_dict(self):
        recalls = [
            RecallNode(
                campaign_number="22V123",
                report_received_date="2022-03-01",
                summary="Brake issue",
                component="SERVICE BRAKES",
                remedy="Dealer update",
                units_affected=5000,
            ),
        ]
        result = RecallPathResult(
            make="Ford", model="F-150", year=2020, vehicle_id="abc",
            recalls=recalls,
            path_type="potentially related by shared vehicle/component",
        )
        d = result.to_dict()
        assert d["recalls"][0]["campaign_number"] == "22V123"
        assert "potentially" in d["path_type"]


# =============================================================================
# Graph service tests (mocked)
# =============================================================================

class TestGraphServiceSetup:
    def test_setup_schema_returns_result(self):
        """setup_graph_schema returns GraphSchemaSetupResult."""
        with patch("app.services.graph.graph_service.Neo4jClient") as MockClient:
            mock_instance = MagicMock()
            MockClient.return_value = mock_instance
            mock_instance.driver = MagicMock()
            mock_instance.close = MagicMock()

            with patch("app.services.graph.graph_service.setup_schema") as mock_setup:
                mock_setup.return_value = (6, 4, [])
                from app.services.graph.graph_service import setup_graph_schema
                result = setup_graph_schema()
                assert result.constraints_created == 6
                assert result.indexes_created == 4
                assert result.success is True

    def test_setup_schema_catches_connection_error(self):
        """setup_graph_schema returns error on connection failure."""
        with patch("app.services.graph.graph_service.Neo4jClient") as MockClient:
            mock_instance = MagicMock()
            MockClient.return_value = mock_instance
            mock_instance.driver = MagicMock()
            mock_instance.close = MagicMock()

            # Patch at the usage site in graph_service
            with patch("app.services.graph.graph_service.setup_schema") as mock_setup:
                mock_setup.side_effect = Exception("Connection refused")
                from app.services.graph.graph_service import setup_graph_schema
                result = setup_graph_schema()
                assert result.success is False
                assert len(result.errors) > 0


class TestGraphServiceStatus:
    def test_get_status_returns_graph_status(self):
        """get_graph_status returns GraphStatus with neo4j info."""
        with patch("app.services.graph.graph_service.Neo4jClient") as MockClient:
            mock_instance = MagicMock()
            MockClient.return_value = mock_instance
            # execute_single called twice: first for node count, second for rel count
            mock_instance.execute_single.side_effect = [
                {"count": 42},   # node count
                {"count": 100},  # relationship count
            ]
            mock_instance.execute.return_value = [
                {"label": "VehicleMake", "count": 5},
                {"label": "Complaint", "count": 20},
            ]
            mock_instance.close = MagicMock()

            with patch("app.services.graph.graph_service._pg_counts") as mock_pg:
                mock_pg.return_value = {
                    "vehicles": 5, "complaints": 5,
                    "recalls": 37, "components": 5,
                }
                from app.services.graph.graph_service import get_graph_status
                status = get_graph_status()
                assert status.neo4j_connected is True
                assert status.node_count == 42
                assert status.relationship_count == 100


# =============================================================================
# Graph builder tests (mocked Neo4j client)
# =============================================================================

class TestGraphBuilderDryRun:
    def test_dry_run_counts_without_writing(self):
        """Dry run counts PostgreSQL records without writing to Neo4j."""
        import uuid
        from unittest.mock import MagicMock, patch

        vid = uuid.uuid4()
        cid = uuid.uuid4()

        mock_vehicle = MagicMock()
        mock_vehicle.id = vid
        mock_vehicle.normalized_make = "FORD"
        mock_vehicle.normalized_model = "F-150"

        mock_component = MagicMock()
        mock_component.id = cid
        mock_component.name = "SERVICE BRAKES"
        mock_component.normalized_name = "SERVICE BRAKES"
        mock_component.category = None

        # Patch _fetch_vehicles and _build_component_map at source
        with patch("app.services.graph.graph_builder._fetch_vehicles") as mock_fv:
            with patch("app.services.graph.graph_builder._build_component_map") as mock_bcm:
                mock_fv.return_value = iter([mock_vehicle])
                mock_bcm.return_value = {
                    "SERVICE BRAKES": {
                        "id": str(cid), "name": "SERVICE BRAKES",
                        "normalized_name": "SERVICE BRAKES", "category": None,
                    }
                }
                mock_neo4j = MagicMock()

                from app.services.graph.graph_builder import build_graph
                stats = build_graph(
                    MagicMock(), mock_neo4j, dry_run=True, limit_vehicles=None
                )

                assert stats.vehicle_models_seen >= 1
                assert stats.components_seen == 1
                mock_neo4j.execute.assert_not_called()


class TestGraphBuilderHandlesMissingData:
    def test_vehicle_without_complaints_succeeds(self):
        """Vehicle with no complaints doesn't crash build."""
        import uuid
        from unittest.mock import MagicMock, patch

        vid = uuid.uuid4()
        mock_vehicle = MagicMock()
        mock_vehicle.id = vid
        mock_vehicle.normalized_make = "TESLA"
        mock_vehicle.normalized_model = "MODEL 3"

        with patch("app.services.graph.graph_builder._fetch_vehicles") as mock_fv:
            with patch("app.services.graph.graph_builder._build_component_map") as mock_bcm:
                with patch("app.services.graph.graph_builder._build_recall_map") as mock_brm:
                    mock_fv.return_value = iter([mock_vehicle])
                    mock_bcm.return_value = {}
                    mock_brm.return_value = {}

                    mock_session = MagicMock()
                    mock_session.get.return_value = None
                    mock_session.query.return_value.filter.return_value.all.return_value = []

                    mock_neo4j = MagicMock()

                    from app.services.graph.graph_builder import build_graph
                    stats = build_graph(
                        mock_session, mock_neo4j, dry_run=False, limit_vehicles=None
                    )

                    assert stats.errors_count == 0
                    assert stats.vehicle_models_seen >= 1


# =============================================================================
# Import smoke tests
# =============================================================================

class TestImports:
    def test_graph_service_imports_clean(self):
        """All graph service modules import without errors."""
        from app.services.graph import (
            setup_graph_schema,
            build_graph_from_postgres,
            get_graph_status,
            get_vehicle_neighborhood,
            get_vehicle_recall_paths,
        )
        # Verify symbols exist and are callable
        assert callable(setup_graph_schema)
        assert callable(build_graph_from_postgres)
        assert callable(get_graph_status)

    def test_neo4j_client_imports_clean(self):
        """Neo4j client module imports cleanly."""
        from app.services.graph.neo4j_client import Neo4jClient, verify_connectivity
        assert Neo4jClient is not None
        assert callable(verify_connectivity)

    def test_graph_models_import_clean(self):
        """Graph models import cleanly."""
        from app.services.graph.graph_models import (
            GraphBuildStats, GraphStatus, VehicleNeighborhood,
            RecallPathResult, GraphSchemaSetupResult,
        )
        assert GraphBuildStats is not None
        assert VehicleNeighborhood is not None
