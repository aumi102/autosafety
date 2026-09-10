"""Phase 10 tests for the audit-read API and operator diagnostics.

Offline and deterministic. Settings come from explicit `isolated_settings()`
fixtures, never the operator `.env`; the audit reader is driven by an in-memory
SQLite session factory, so no test here needs Docker or a real secret.

The contract being pinned:

    audit routes with no admin token configured  -> 503 (fail closed)
    audit routes with a wrong token              -> 401
    audit routes with the right token            -> 200, safe fields only
    public readiness                             -> never leaks a connection detail
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core import security as security_module
from app.core.config import Settings, isolated_settings
from app.core.security import ADMIN_TOKEN_HEADER
from app.db.base import Base
from app.db.models.app import AgentRun, ToolCall
from app.main import app
from app.services.ops import audit_reader as audit_reader_module
from app.services.ops.audit_reader import (
    AGENT_RUN_SAFE_FIELDS,
    MAX_LIMIT,
    TOOL_CALL_SAFE_FIELDS,
    AuditQuery,
    ExecutionAuditReader,
)
from app.services.ops.probes import DependencyStatus, ReadinessReport, _classify
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

VALID_TOKEN = "phase10-admin-token-value-32char"

AUDIT_ROUTES = (
    "/v1/agent-runs",
    "/v1/agent-runs/summary",
    "/v1/agent-runs/0a0a0a0a-0b0b-4c0c-8d0d-0e0e0e0e0e0e",
)

# Values that must never reach a response, seeded into the rows below.
SECRET_MARKERS = (
    "sk-live-must-not-appear",
    "postgresql://user:pw@host/db",
    "bolt://neo4j:pw@host",
    "SELECT * FROM complaints",
    "MATCH (v:Vehicle) RETURN v",
)


def _settings(**overrides) -> Settings:
    return isolated_settings(**overrides)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(
        security_module,
        "get_settings",
        lambda: _settings(ADMIN_API_TOKEN=SecretStr(VALID_TOKEN)),
    )


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.setattr(security_module, "get_settings", lambda: _settings())


@pytest.fixture
def seeded_reader(monkeypatch):
    """An audit reader over an in-memory database holding two runs.

    The seeded UUIDs deliberately contain hex letters. SQLite gives the
    PostgreSQL `UUID` column NUMERIC affinity, so a UUID whose hex digits are
    all decimal (``1111...``) is silently stored as a float and fails to load
    back. That is a SQLite typing artifact, not a product behavior.
    """
    # StaticPool + check_same_thread: TestClient runs the app on another thread,
    # and a default in-memory SQLite pool would hand that thread a fresh, empty
    # database. One shared connection keeps the seeded rows visible.
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    run_id = uuid.UUID("aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa")
    older_id = uuid.UUID("bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb")
    now = datetime.now(UTC)

    with factory() as session:
        session.add(
            AgentRun(
                id=run_id,
                status="completed",
                phase="phase_9",
                surface="api_conversation",
                provider="deterministic",
                model="deterministic-v1",
                synthesis_mode="deterministic",
                started_at=now,
                finished_at=now,
                latency_ms=120,
                tool_call_count=1,
                fallback_used=False,
                abstained=False,
                confidence_score=8500,
                confidence_level="high",
                validation_outcome="accepted",
                # Fields the API must never surface, seeded with markers.
                intent=SECRET_MARKERS[3],
                warnings=[SECRET_MARKERS[0]],
            )
        )
        session.add(
            AgentRun(
                id=older_id,
                status="failed",
                phase="phase_9",
                surface="api_conversation",
                provider="openai_compatible",
                started_at=now - timedelta(days=10),
                fallback_used=True,
                abstained=True,
                error_code="provider_unavailable",
            )
        )
        session.add(
            ToolCall(
                agent_run_id=run_id,
                tool_name="graphrag_retrieval_tool",
                operation="retrieve_complaints_only",
                call_id="base-abc123",
                status="ok",
                success=True,
                evidence_item_count=17,
                started_at=now,
                completed_at=now,
                # Legacy Phase 9 columns; seeded to prove they are never read.
                input_json={"sql": SECRET_MARKERS[3], "cypher": SECRET_MARKERS[4]},
                output_json={"api_key": SECRET_MARKERS[0], "db": SECRET_MARKERS[1]},
            )
        )
        session.commit()

    reader = ExecutionAuditReader(session_factory=factory)
    monkeypatch.setattr(audit_reader_module, "build_audit_reader", lambda **_: reader)
    monkeypatch.setattr(
        "app.api.v1.endpoints.agent_runs.build_audit_reader", lambda **_: reader
    )
    return reader, run_id, older_id


# =============================================================================
# Authorization — the audit trail reuses the Phase 9 guard
# =============================================================================


class TestAuditAuthorization:
    @pytest.mark.parametrize("route", AUDIT_ROUTES)
    def test_unavailable_when_no_admin_token_is_configured(self, route, unconfigured):
        """An audit log must not become public because a token was forgotten."""
        with TestClient(app) as client:
            response = client.get(route)
        assert response.status_code == 503
        assert response.json()["detail"]["error"]["code"] == "ADMIN_PROTECTION_UNAVAILABLE"

    @pytest.mark.parametrize("route", AUDIT_ROUTES)
    def test_rejects_a_missing_token(self, route, configured):
        with TestClient(app) as client:
            response = client.get(route)
        assert response.status_code == 401

    @pytest.mark.parametrize("route", AUDIT_ROUTES)
    def test_rejects_a_wrong_token(self, route, configured):
        with TestClient(app) as client:
            response = client.get(route, headers={ADMIN_TOKEN_HEADER: "wrong-token-long-enough"})
        assert response.status_code == 401

    @pytest.mark.parametrize("route", AUDIT_ROUTES)
    def test_token_is_not_accepted_via_query_string(self, route, configured):
        with TestClient(app) as client:
            response = client.get(f"{route}?x_admin_token={VALID_TOKEN}")
        assert response.status_code == 401

    def test_no_second_auth_mechanism_is_introduced(self):
        """Every audit route must resolve the one shared guard."""
        schema = app.openapi()
        for route in ("/v1/agent-runs", "/v1/agent-runs/summary", "/v1/ops/diagnostics"):
            parameters = schema["paths"][route]["get"].get("parameters", [])
            assert any(p.get("name") == ADMIN_TOKEN_HEADER for p in parameters), route


# =============================================================================
# Reading runs
# =============================================================================


class TestAuditRead:
    def test_list_returns_runs_newest_first(self, configured, seeded_reader):
        _, run_id, older_id = seeded_reader
        with TestClient(app) as client:
            response = client.get("/v1/agent-runs", headers={ADMIN_TOKEN_HEADER: VALID_TOKEN})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert [r["run_id"] for r in body["agent_runs"]] == [str(run_id), str(older_id)]

    def test_detail_includes_safe_tool_call_metadata(self, configured, seeded_reader):
        _, run_id, _ = seeded_reader
        with TestClient(app) as client:
            response = client.get(
                f"/v1/agent-runs/{run_id}", headers={ADMIN_TOKEN_HEADER: VALID_TOKEN}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["confidence_score"] == 0.85
        assert len(body["tool_calls"]) == 1
        call = body["tool_calls"][0]
        assert call["tool_name"] == "graphrag_retrieval_tool"
        assert call["operation"] == "retrieve_complaints_only"
        assert call["evidence_item_count"] == 17
        assert call["call_id"] == "base-abc123"

    def test_unknown_run_returns_a_safe_404(self, configured, seeded_reader):
        with TestClient(app) as client:
            response = client.get(
                "/v1/agent-runs/cccccccc-3333-4333-8333-cccccccccccc",
                headers={ADMIN_TOKEN_HEADER: VALID_TOKEN},
            )
        assert response.status_code == 404
        assert response.json()["detail"]["error"]["code"] == "AGENT_RUN_NOT_FOUND"

    def test_malformed_run_id_is_rejected_before_any_query(self, configured, seeded_reader):
        with TestClient(app) as client:
            response = client.get(
                "/v1/agent-runs/not-a-uuid", headers={ADMIN_TOKEN_HEADER: VALID_TOKEN}
            )
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "params,expected",
        [
            ({"status": "completed"}, 1),
            ({"status": "failed"}, 1),
            ({"provider": "deterministic"}, 1),
            ({"fallback_used": "true"}, 1),
            ({"abstained": "false"}, 1),
            ({"since_hours": 24}, 1),
        ],
    )
    def test_filters_narrow_the_result_set(self, configured, seeded_reader, params, expected):
        with TestClient(app) as client:
            response = client.get(
                "/v1/agent-runs", params=params, headers={ADMIN_TOKEN_HEADER: VALID_TOKEN}
            )
        assert response.status_code == 200
        assert response.json()["total"] == expected

    def test_summary_counts_the_recent_window(self, configured, seeded_reader):
        with TestClient(app) as client:
            response = client.get(
                "/v1/agent-runs/summary", headers={ADMIN_TOKEN_HEADER: VALID_TOKEN}
            )
        body = response.json()
        assert body["window_hours"] == 24
        assert body["total_runs"] == 1
        assert body["completed"] == 1
        assert body["tool_calls"] == 1


# =============================================================================
# Bounds
# =============================================================================


class TestAuditBounds:
    @pytest.mark.parametrize("limit", [0, MAX_LIMIT + 1, 100000])
    def test_limit_outside_the_permitted_range_is_rejected(self, configured, seeded_reader, limit):
        with TestClient(app) as client:
            response = client.get(
                "/v1/agent-runs",
                params={"limit": limit},
                headers={ADMIN_TOKEN_HEADER: VALID_TOKEN},
            )
        assert response.status_code == 422

    def test_reader_clamps_out_of_range_values_defensively(self):
        """Even bypassing the API layer, the reader will not run an unbounded scan."""
        assert AuditQuery(limit=10_000).bounded().limit == MAX_LIMIT
        assert AuditQuery(limit=0).bounded().limit == 1
        assert AuditQuery(offset=-5).bounded().offset == 0
        assert AuditQuery(since_hours=10**9).bounded().since_hours == 90 * 24


# =============================================================================
# Privacy — the allowlist is the contract
# =============================================================================


class TestAuditPrivacy:
    def test_projection_exposes_only_allowlisted_fields(self, seeded_reader):
        reader, run_id, _ = seeded_reader
        run = reader.get_run(run_id)
        assert set(run) == set(AGENT_RUN_SAFE_FIELDS) | {"tool_calls"}
        assert set(run["tool_calls"][0]) == set(TOOL_CALL_SAFE_FIELDS)

    def test_never_exposes_prompts_responses_or_payload_columns(self, seeded_reader):
        reader, run_id, _ = seeded_reader
        run = reader.get_run(run_id)
        for field in ("intent", "warnings", "input_json", "output_json"):
            assert field not in run
            assert field not in run["tool_calls"][0]

    @pytest.mark.parametrize("marker", SECRET_MARKERS)
    def test_seeded_secrets_never_reach_a_response(self, configured, seeded_reader, marker):
        """The rows deliberately contain credentials, SQL, and Cypher."""
        _, run_id, _ = seeded_reader
        headers = {ADMIN_TOKEN_HEADER: VALID_TOKEN}
        with TestClient(app) as client:
            bodies = [
                client.get("/v1/agent-runs", headers=headers).text,
                client.get(f"/v1/agent-runs/{run_id}", headers=headers).text,
                client.get("/v1/agent-runs/summary", headers=headers).text,
            ]
        for body in bodies:
            assert marker not in body

    def test_admin_token_never_appears_in_a_response_or_the_schema(
        self, configured, seeded_reader
    ):
        with TestClient(app) as client:
            response = client.get("/v1/agent-runs", headers={ADMIN_TOKEN_HEADER: VALID_TOKEN})
        assert VALID_TOKEN not in response.text
        assert VALID_TOKEN not in str(app.openapi())


# =============================================================================
# Readiness and diagnostics
# =============================================================================


class TestReadiness:
    def test_readiness_is_public(self, unconfigured, monkeypatch):
        """A load balancer cannot present an admin token."""
        monkeypatch.setattr(
            "app.api.v1.endpoints.ops.readiness",
            lambda: ReadinessReport(
                ready=True,
                dependencies=[DependencyStatus("postgresql", True, True, latency_ms=1)],
            ),
        )
        with TestClient(app) as client:
            response = client.get("/v1/ops/readiness")
        assert response.status_code == 200
        assert response.json()["ready"] is True

    def test_readyz_reports_503_when_a_required_dependency_is_down(self, monkeypatch):
        """The original stub returned 'ready' unconditionally."""
        monkeypatch.setattr(
            "app.main.readiness",
            lambda: ReadinessReport(
                ready=False,
                dependencies=[
                    DependencyStatus("postgresql", False, True, detail="unreachable")
                ],
            ),
        )
        with TestClient(app) as client:
            response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json()["ready"] is False

    def test_readyz_is_200_when_required_dependencies_are_up(self, monkeypatch):
        monkeypatch.setattr(
            "app.main.readiness",
            lambda: ReadinessReport(
                ready=True,
                dependencies=[DependencyStatus("postgresql", True, True)],
            ),
        )
        with TestClient(app) as client:
            response = client.get("/readyz")
        assert response.status_code == 200

    def test_optional_dependencies_do_not_make_the_service_unready(self, monkeypatch):
        """Neo4j degrades answer quality; it does not stop the service answering."""
        monkeypatch.setattr(
            "app.main.readiness",
            lambda: ReadinessReport(
                ready=True,
                dependencies=[
                    DependencyStatus("postgresql", True, True),
                    DependencyStatus("neo4j", False, False, detail="unreachable"),
                ],
            ),
        )
        with TestClient(app) as client:
            response = client.get("/readyz")
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "exc,expected",
        [
            (TimeoutError("boom"), "timeout"),
            (ConnectionRefusedError("nope"), "unreachable"),
            (ValueError("other"), "error"),
        ],
    )
    def test_exception_classes_are_reduced_to_coarse_labels(self, exc, expected):
        assert _classify(exc) == expected

    def test_probe_failures_never_return_driver_text(self, monkeypatch):
        """SQLAlchemy and the Neo4j driver put host, port and user into messages."""
        from app.services.ops import probes

        secret = "postgresql://user:hunter2@db.internal:5432/autosafety"
        monkeypatch.setattr(
            probes,
            "probe_postgresql",
            lambda *_: probes._timed(
                "postgresql", True, lambda: (_ for _ in ()).throw(RuntimeError(secret))
            ),
        )
        report = probes.readiness()
        assert secret not in str(report.to_dict())
        assert "hunter2" not in str(report.to_dict())


class TestDiagnostics:
    def test_diagnostics_requires_admin(self, unconfigured):
        with TestClient(app) as client:
            response = client.get("/v1/ops/diagnostics")
        assert response.status_code == 503

    def test_diagnostics_reports_credential_presence_not_value(self, configured, monkeypatch):
        secret = "sk-diagnostics-must-not-leak"
        monkeypatch.setattr(
            "app.api.v1.endpoints.ops.get_settings",
            lambda: _settings(PHASE7_PROVIDER_API_KEY=SecretStr(secret)),
        )
        monkeypatch.setattr(
            "app.api.v1.endpoints.ops.readiness",
            lambda: ReadinessReport(
                ready=True, dependencies=[DependencyStatus("postgresql", True, True)]
            ),
        )
        with TestClient(app) as client:
            response = client.get(
                "/v1/ops/diagnostics", headers={ADMIN_TOKEN_HEADER: VALID_TOKEN}
            )
        assert response.status_code == 200
        assert response.json()["provider_credential_configured"] is True
        assert secret not in response.text
