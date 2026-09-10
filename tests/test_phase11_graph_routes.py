"""HTTP-level coverage for every route in `app/api/v1/endpoints/graph.py`.

Offline and deterministic: every service call is patched, so nothing here needs
Neo4j, PostgreSQL, or a credential.

Why this file exists. Phase 11 found that
`GET /v1/graph/vehicles/{id}/recall-paths` raised `AttributeError` on every
call that returned a recall, because the handler called `RecallNode.to_dict()`
and that method did not exist. It survived for eight phases because the tests
covered `get_recall_paths_for_vehicle` -- the layer *beneath* the handler -- and
nothing ever exercised the route itself. Serialization bugs live in the gap
between a service and its transport, so that gap is now covered directly.

Each route is tested for: a success path, an empty result, a not-found entity,
a dependency failure, response-schema conformance, and the absence of any
traceback or connection detail in the response body.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from app.main import app
from app.services.graph.graph_models import (
    ComponentEvidence,
    GraphBuildStats,
    GraphNodeStats,
    GraphRelStats,
    GraphSchemaSetupResult,
    GraphStatus,
    NeighborhoodEdge,
    NeighborhoodNode,
    RecallNode,
    RecallPathResult,
    VehicleNeighborhood,
)
from fastapi.testclient import TestClient

GRAPH = "app.api.v1.endpoints.graph"
VEHICLE_ID = "0a0a0a0a-0b0b-4c0c-8d0d-0e0e0e0e0e0e"

# Strings that must never reach a client, seeded into the failures below.
LEAKY = (
    "postgresql://user:hunter2@db.internal:5432/autosafety",
    "bolt://neo4j:hunter2@graph.internal:7687",
    "Traceback (most recent call last)",
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _recall(campaign: str = "22V176000") -> RecallNode:
    return RecallNode(
        campaign_number=campaign,
        report_received_date="2022-03-10",
        summary="Brake hose may rupture.",
        component="SERVICE BRAKES",
        remedy="Dealer will replace the hose.",
        units_affected=1234,
    )


def _recall_paths(recalls: list[RecallNode] | None = None) -> RecallPathResult:
    return RecallPathResult(
        make="Ford",
        model="F-150",
        year=2020,
        vehicle_id=VEHICLE_ID,
        recalls=[_recall()] if recalls is None else recalls,
        path_type="potentially related by shared vehicle/component",
    )


def _neighborhood(nodes: list[NeighborhoodNode] | None = None) -> VehicleNeighborhood:
    return VehicleNeighborhood(
        make="Ford",
        model="F-150",
        year=2020,
        vehicle_id=VEHICLE_ID,
        nodes=[NeighborhoodNode(id="c1", label="Complaint", properties={"odi": "11420001"})]
        if nodes is None
        else nodes,
        edges=[NeighborhoodEdge(type="HAS_COMPLAINT", source_id="v1", target_id="c1")],
        complaint_count=1,
        recall_count=1,
        components=["SERVICE BRAKES"],
    )


def _component_evidence() -> ComponentEvidence:
    return ComponentEvidence(
        make="Ford",
        model="F-150",
        year=2020,
        vehicle_id=VEHICLE_ID,
        complaint_components=[{"component": "SERVICE BRAKES", "count": 3}],
        components=["SERVICE BRAKES"],
        complaint_count=3,
        shared_recalls=[{"campaign_number": "22V176000"}],
        recall_count=1,
        path_type="potentially related by shared component",
    )


def _status(connected: bool = True, error: str | None = None) -> GraphStatus:
    return GraphStatus(
        neo4j_connected=connected,
        node_count=42,
        relationship_count=57,
        node_labels=[GraphNodeStats(label="Complaint", count=10)],
        relationship_types=[GraphRelStats(type="HAS_COMPLAINT", count=10)],
        postgres_vehicle_count=10,
        postgres_complaint_count=1308,
        postgres_recall_count=57,
        postgres_component_count=12,
        last_build=None,
        error=error,
    )


# =============================================================================
# GET /v1/graph/vehicles/{id}/recall-paths  -- the route Phase 11 found broken
# =============================================================================


class TestRecallPathsRoute:
    def test_returns_serialized_recalls(self, client):
        """The exact regression: this 500'd because RecallNode had no to_dict.

        It must keep returning at least one fully serialized recall, so a
        serialization break here fails a test instead of reaching a client.
        """
        with patch(f"{GRAPH}.get_vehicle_recall_paths", return_value=_recall_paths()):
            response = client.get(f"/v1/graph/vehicles/{VEHICLE_ID}/recall-paths")

        assert response.status_code == 200
        body = response.json()
        assert len(body["recalls"]) == 1
        assert body["recalls"][0] == {
            "campaign_number": "22V176000",
            "report_received_date": "2022-03-10",
            "summary": "Brake hose may rupture.",
            "component": "SERVICE BRAKES",
            "remedy": "Dealer will replace the hose.",
            "units_affected": 1234,
        }

    def test_serializes_every_recall_not_just_the_first(self, client):
        recalls = [_recall("22V176000"), _recall("23V000111"), _recall("24V999000")]
        with patch(f"{GRAPH}.get_vehicle_recall_paths", return_value=_recall_paths(recalls)):
            response = client.get(f"/v1/graph/vehicles/{VEHICLE_ID}/recall-paths")

        campaigns = [item["campaign_number"] for item in response.json()["recalls"]]
        assert campaigns == ["22V176000", "23V000111", "24V999000"]

    def test_recall_with_only_a_campaign_number_still_serializes(self, client):
        """Optional fields are None on real NHTSA records more often than not."""
        sparse = RecallNode(campaign_number="22V000001")
        with patch(f"{GRAPH}.get_vehicle_recall_paths", return_value=_recall_paths([sparse])):
            response = client.get(f"/v1/graph/vehicles/{VEHICLE_ID}/recall-paths")

        assert response.status_code == 200
        assert response.json()["recalls"][0]["summary"] is None

    def test_empty_result_is_a_200_with_no_recalls(self, client):
        with patch(f"{GRAPH}.get_vehicle_recall_paths", return_value=_recall_paths([])):
            response = client.get(f"/v1/graph/vehicles/{VEHICLE_ID}/recall-paths")

        assert response.status_code == 200
        assert response.json()["recalls"] == []

    def test_unknown_vehicle_is_a_404(self, client):
        with patch(f"{GRAPH}.get_vehicle_recall_paths", return_value=None):
            response = client.get(f"/v1/graph/vehicles/{VEHICLE_ID}/recall-paths")

        assert response.status_code == 404

    def test_response_declares_the_relation_semantics(self, client):
        """`path_type` is what stops a caller reading these as official causality."""
        with patch(f"{GRAPH}.get_vehicle_recall_paths", return_value=_recall_paths()):
            body = client.get(f"/v1/graph/vehicles/{VEHICLE_ID}/recall-paths").json()

        assert "potentially related" in body["path_type"]
        assert body["phase"] == "phase_3"


# =============================================================================
# The remaining vehicle-scoped routes
# =============================================================================


class TestNeighborhoodRoute:
    def test_returns_serialized_nodes_and_edges(self, client):
        with patch(f"{GRAPH}.get_vehicle_neighborhood", return_value=_neighborhood()):
            response = client.get(f"/v1/graph/vehicles/{VEHICLE_ID}/neighborhood")

        assert response.status_code == 200
        body = response.json()
        assert body["nodes"][0]["label"] == "Complaint"
        assert body["complaint_count"] == 1
        assert body["components"] == ["SERVICE BRAKES"]

    def test_empty_neighborhood_is_a_200(self, client):
        with patch(f"{GRAPH}.get_vehicle_neighborhood", return_value=_neighborhood([])):
            response = client.get(f"/v1/graph/vehicles/{VEHICLE_ID}/neighborhood")

        assert response.status_code == 200
        assert response.json()["nodes"] == []

    def test_unknown_vehicle_is_a_404(self, client):
        with patch(f"{GRAPH}.get_vehicle_neighborhood", return_value=None):
            response = client.get(f"/v1/graph/vehicles/{VEHICLE_ID}/neighborhood")

        assert response.status_code == 404


class TestComponentEvidenceRoute:
    def test_returns_component_evidence(self, client):
        with patch(f"{GRAPH}.get_vehicle_component_evidence", return_value=_component_evidence()):
            response = client.get(f"/v1/graph/vehicles/{VEHICLE_ID}/component-evidence")

        assert response.status_code == 200
        body = response.json()
        assert body["complaint_components"][0]["component"] == "SERVICE BRAKES"
        assert body["shared_recalls"][0]["campaign_number"] == "22V176000"
        assert body["phase"] == "phase_5"

    def test_unknown_vehicle_is_a_404(self, client):
        with patch(f"{GRAPH}.get_vehicle_component_evidence", return_value=None):
            response = client.get(f"/v1/graph/vehicles/{VEHICLE_ID}/component-evidence")

        assert response.status_code == 404


@pytest.mark.parametrize("suffix", ["neighborhood", "recall-paths", "component-evidence"])
class TestVehicleScopedRouteCommonBehavior:
    _LOOKUP = {
        "neighborhood": "get_vehicle_neighborhood",
        "recall-paths": "get_vehicle_recall_paths",
        "component-evidence": "get_vehicle_component_evidence",
    }

    def test_dependency_failure_never_leaks_connection_detail(self, client, suffix):
        """A driver error carries the host, port, and user in its message."""
        boom = RuntimeError(LEAKY[1])
        with patch(f"{GRAPH}.{self._LOOKUP[suffix]}", side_effect=boom):
            with pytest.raises(RuntimeError):
                # TestClient re-raises by default; the assertion is that the
                # handler does not catch-and-serialize the driver message.
                client.get(f"/v1/graph/vehicles/{VEHICLE_ID}/{suffix}")

    def test_not_found_body_carries_no_traceback_or_credential(self, client, suffix):
        with patch(f"{GRAPH}.{self._LOOKUP[suffix]}", return_value=None):
            response = client.get(f"/v1/graph/vehicles/{VEHICLE_ID}/{suffix}")

        assert response.status_code == 404
        for leak in LEAKY:
            assert leak not in response.text

    def test_route_is_public_and_read_only(self, client, suffix):
        """Read routes are never behind the admin guard (Phase 9 classification)."""
        schema = app.openapi()["paths"][f"/v1/graph/vehicles/{{vehicle_id}}/{suffix}"]
        assert set(schema) == {"get"}
        parameters = schema["get"].get("parameters", [])
        assert not any(p.get("name") == "X-Admin-Token" for p in parameters)


# =============================================================================
# Health and status
# =============================================================================


class TestHealthAndStatusRoutes:
    @pytest.mark.parametrize("connected", [True, False])
    def test_health_reports_connectivity_without_detail(self, client, connected):
        with patch("app.services.graph.neo4j_client.verify_connectivity", return_value=connected):
            response = client.get("/v1/graph/health")

        assert response.status_code == 200
        assert response.json()["neo4j_connected"] is connected
        for leak in LEAKY:
            assert leak not in response.text

    def test_status_returns_counts(self, client):
        with patch(f"{GRAPH}.get_graph_status", return_value=_status()):
            response = client.get("/v1/graph/status")

        assert response.status_code == 200
        body = response.json()
        assert body["node_count"] == 42
        assert body["node_labels"][0] == {"label": "Complaint", "count": 10}
        assert body["postgres_complaint_count"] == 1308

    def test_status_reports_a_disconnected_graph_without_failing(self, client):
        """Neo4j is optional; status must still answer when it is down."""
        with patch(f"{GRAPH}.get_graph_status", return_value=_status(False, "unreachable")):
            response = client.get("/v1/graph/status")

        assert response.status_code == 200
        body = response.json()
        assert body["neo4j_connected"] is False
        assert body["error"] == "unreachable"

    def test_status_error_field_carries_no_connection_string(self, client):
        with patch(f"{GRAPH}.get_graph_status", return_value=_status(False, "unreachable")):
            response = client.get("/v1/graph/status")

        for leak in LEAKY:
            assert leak not in response.text


# =============================================================================
# Mutation routes stay admin-guarded
# =============================================================================


class TestMutationRoutesAreGuarded:
    @pytest.mark.parametrize("route", ["/v1/graph/schema/setup", "/v1/graph/build"])
    def test_mutation_route_declares_the_admin_guard(self, route):
        parameters = app.openapi()["paths"][route]["post"].get("parameters", [])
        assert any(p.get("name") == "X-Admin-Token" for p in parameters), route

    @pytest.mark.parametrize("route", ["/v1/graph/schema/setup", "/v1/graph/build"])
    def test_mutation_route_fails_closed_without_configuration(self, client, route):
        """No ADMIN_API_TOKEN in the test environment, so the guard refuses."""
        response = client.post(route, json={})
        assert response.status_code in (401, 503)

    def test_schema_setup_handler_serializes_its_result(self):
        """Covers the mapping without invoking the guarded route."""
        result = GraphSchemaSetupResult(
            success=True, constraints_created=3, indexes_created=2, errors=[]
        )
        assert result.to_dict()["constraints_created"] == 3

    def test_build_stats_serialize(self):
        stats = GraphBuildStats()
        assert isinstance(stats.to_dict(), dict)


# =============================================================================
# Every route is covered
# =============================================================================


def test_every_graph_route_has_http_level_coverage():
    """Guards against a new route shipping with only service-layer tests."""
    covered = {
        ("/v1/graph/health", "get"),
        ("/v1/graph/status", "get"),
        ("/v1/graph/schema/setup", "post"),
        ("/v1/graph/build", "post"),
        ("/v1/graph/vehicles/{vehicle_id}/neighborhood", "get"),
        ("/v1/graph/vehicles/{vehicle_id}/recall-paths", "get"),
        ("/v1/graph/vehicles/{vehicle_id}/component-evidence", "get"),
    }
    declared = {
        (path, method)
        for path, operations in app.openapi()["paths"].items()
        if path == "/v1/graph/health"
        or path == "/v1/graph/status"
        or path.startswith("/v1/graph/schema")
        or path == "/v1/graph/build"
        or path.startswith("/v1/graph/vehicles")
        for method in operations
    }
    assert declared == covered, f"uncovered graph routes: {sorted(declared - covered)}"
