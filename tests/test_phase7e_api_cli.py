"""Phase 7E API/CLI integration tests.

All tests are offline. API transport uses dependency overrides; CLI uses an
in-process fake GuardedAnswerService. No PostgreSQL, Neo4j, external provider,
NHTSA API, or Internet connection is required.
"""

from __future__ import annotations

import inspect
import io
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.v1.endpoints import answer_synthesis as api_module
from app.api.v1.endpoints.answer_synthesis import (
    GuardedAnswerRequest,
    get_guarded_answer_service_dependency,
)
from app.core.config import Settings
from app.main import app
from app.services.answer_synthesis.factory import (
    AnswerSynthesisStatus,
    build_guarded_answer_service,
    get_answer_synthesis_status,
)
from app.services.answer_synthesis.guarded_models import (
    CitationValidationResult,
    ConfidenceResult,
    GuardedAnswerResult,
    GuardedCitation,
    GuardedClaim,
    GuardedTrace,
    RetrievalSummary,
)
from app.services.answer_synthesis.policy import (
    GRAPH_UNAVAILABLE_WARNING,
    PARTIAL_EVIDENCE_WARNING,
)
from app.services.answer_synthesis.providers import DeterministicProvider
from app.services.answer_synthesis.service import GuardedAnswerService
from scripts import query_phase7_answer as cli


def _result(
    *,
    mode: str = "llm",
    provider: str = "openai_compatible",
    warnings: list[str] | None = None,
    abstained: bool = False,
) -> GuardedAnswerResult:
    if abstained:
        return GuardedAnswerResult(
            query="Question",
            answer=(
                "I cannot provide a reliable answer to this question based on available evidence."
            ),
            claims=[],
            citations=[],
            warnings=warnings or [],
            confidence=ConfidenceResult(score=0.0, level="low", reasons=["no evidence"]),
            abstained=True,
            abstention_reason="no_evidence",
            synthesis_mode="abstention",
            provider=provider,
            retrieval_summary=RetrievalSummary(neo4j_available=False),
            validation=None,
            trace=GuardedTrace(
                original_provider=provider,
                provider_available=True,
                fallback_used=False,
                validation_outcome="abstention_required",
                abstention_reason="no_evidence",
            ),
        )

    fallback_used = mode == "fallback"
    repaired = mode == "repaired"
    claim = GuardedClaim(
        claim_id="claim-1",
        text="Complaint 11420001 reports a brake concern.",
        claim_type="complaint_observation",
        citation_ids=["cite-complaint-11420001"],
        support_level="supported",
        official_status="none",
        validation_status="repaired" if repaired else "accepted",
        validation_messages=["normalized"] if repaired else [],
    )
    citation = GuardedCitation(
        citation_id="cite-complaint-11420001",
        source_type="complaint",
        source_record_key="11420001",
        source_entity_id="complaint-1",
        title="Complaint 11420001",
        text_span="Brake pedal concern reported.",
        retrieval_score=0.91,
        relation_basis="complaint_mentions_component",
        tool_name="graphrag_retrieval_tool",
    )
    validation = CitationValidationResult(
        valid=True,
        factual_claim_count=1,
        cited_claim_count=1,
        valid_citation_count=1,
        citation_coverage=1.0,
        repaired=repaired,
    )
    return GuardedAnswerResult(
        query="Question",
        answer="Complaint 11420001 reports a brake concern.",
        claims=[claim],
        citations=[citation],
        warnings=warnings or ["Complaint records are public reports."],
        confidence=ConfidenceResult(score=0.72, level="medium", reasons=["citation coverage 1.00"]),
        abstained=False,
        abstention_reason=None,
        synthesis_mode=mode,
        provider=provider,
        retrieval_summary=RetrievalSummary(
            chunks_retrieved=1,
            citations_assembled=1,
            graph_paths_found=0,
            neo4j_available=True,
            tool_calls_made=1,
        ),
        validation=validation,
        trace=GuardedTrace(
            original_provider="openai_compatible",
            provider_available=True,
            fallback_used=fallback_used,
            validation_outcome="repaired" if repaired else "accepted",
            repaired_claim_count=1 if repaired else 0,
        ),
    )


class _FakeGuardedService:
    def __init__(self, result: GuardedAnswerResult | None = None, error: Exception | None = None):
        self.result = result or _result()
        self.error = error
        self.questions: list[str] = []

    def answer(self, question: str) -> GuardedAnswerResult:
        self.questions.append(question)
        if self.error:
            raise self.error
        self.result.query = question
        return self.result


@pytest.fixture
def api_client(monkeypatch):
    service = _FakeGuardedService()
    status = AnswerSynthesisStatus(
        synthesis_available=True,
        configured_provider="deterministic",
        active_provider="deterministic",
        provider_available=True,
        real_llm_enabled=False,
        real_llm_configured=False,
        deterministic_fallback_available=True,
        tool_calling_enabled=True,
        max_tool_rounds=2,
        max_tool_calls=4,
        graphrag_base_required=True,
        guarded_validation_enabled=True,
    )
    monkeypatch.setattr(api_module, "get_answer_synthesis_status", lambda: status)
    app.dependency_overrides[get_guarded_answer_service_dependency] = lambda: service
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, service
    app.dependency_overrides.clear()


# =============================================================================
# A. API request
# =============================================================================


class TestApiRequest:
    def test_valid_question_accepted(self, api_client):
        client, service = api_client
        response = client.post("/v1/graphrag/answer", json={"question": "Brake complaints?"})
        assert response.status_code == 200
        assert service.questions == ["Brake complaints?"]

    def test_whitespace_normalized(self, api_client):
        client, service = api_client
        response = client.post("/v1/graphrag/answer", json={"question": "  Brake complaints?  "})
        assert response.status_code == 200
        assert service.questions == ["Brake complaints?"]

    @pytest.mark.parametrize("question", ["", "   "])
    def test_empty_question_rejected(self, api_client, question):
        client, service = api_client
        response = client.post("/v1/graphrag/answer", json={"question": question})
        assert response.status_code == 422
        assert service.questions == []

    def test_oversized_question_rejected(self, api_client):
        client, service = api_client
        response = client.post("/v1/graphrag/answer", json={"question": "x" * 1001})
        assert response.status_code == 422
        assert service.questions == []

    def test_maximum_length_question_accepted(self, api_client):
        client, service = api_client
        response = client.post("/v1/graphrag/answer", json={"question": "x" * 1000})
        assert response.status_code == 200
        assert len(service.questions[0]) == 1000

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("unknown", "not-allowed"),
            ("raw_sql", "DROP TABLE complaints"),
            ("raw_cypher", "MATCH (n) DELETE n"),
            ("provider_api_key", "sk-phase7e-secret"),
            ("provider_base_url", "https://attacker.invalid"),
            ("tool_name", "arbitrary_tool"),
        ],
    )
    def test_internal_or_unknown_fields_rejected_without_reflection(self, api_client, field, value):
        client, service = api_client
        response = client.post(
            "/v1/graphrag/answer",
            json={"question": "Brake complaints?", field: value},
        )
        assert response.status_code == 422
        assert value not in response.text
        assert service.questions == []

    def test_request_contract_has_only_public_transport_fields(self):
        assert set(GuardedAnswerRequest.model_fields) == {"question", "include_trace"}


# =============================================================================
# B. API response
# =============================================================================


class TestApiResponse:
    @pytest.mark.parametrize(
        "field",
        [
            "phase",
            "answer",
            "claims",
            "citations",
            "warnings",
            "confidence",
            "abstained",
            "synthesis_mode",
            "provider",
            "validation",
            "retrieval_summary",
        ],
    )
    def test_required_result_field_returned(self, api_client, field):
        client, _ = api_client
        response = client.post("/v1/graphrag/answer", json={"question": "Brake complaints?"})
        assert field in response.json()

    def test_phase_is_phase_7(self, api_client):
        client, _ = api_client
        body = client.post("/v1/graphrag/answer", json={"question": "Brake?"}).json()
        assert body["phase"] == "phase_7"

    def test_trace_suppressed_by_default(self, api_client):
        client, _ = api_client
        body = client.post("/v1/graphrag/answer", json={"question": "Brake?"}).json()
        assert body["trace"] is None

    def test_trace_included_when_requested(self, api_client):
        client, _ = api_client
        body = client.post(
            "/v1/graphrag/answer",
            json={"question": "Brake?", "include_trace": True},
        ).json()
        assert body["trace"]["validation_outcome"] == "accepted"

    def test_response_contains_no_sensitive_internal_data(self, api_client):
        client, _ = api_client
        text = client.post(
            "/v1/graphrag/answer",
            json={"question": "Brake?", "include_trace": True},
        ).text.lower()
        for forbidden in (
            "raw_prompt",
            "authorization",
            "database_url",
            "neo4j_password",
            "traceback",
            "sk-phase7e-secret",
        ):
            assert forbidden not in text


# =============================================================================
# C. Guarded semantics through API
# =============================================================================


class TestGuardedTransportSemantics:
    @pytest.mark.parametrize(
        ("result", "expected_mode", "expected_abstained"),
        [
            (_result(mode="llm", provider="openai_compatible"), "llm", False),
            (_result(mode="deterministic", provider="deterministic"), "deterministic", False),
            (_result(mode="fallback", provider="deterministic"), "fallback", False),
            (_result(mode="repaired", provider="openai_compatible"), "repaired", False),
            (_result(abstained=True), "abstention", True),
        ],
    )
    def test_mode_and_abstention_preserved(self, result, expected_mode, expected_abstained):
        service = _FakeGuardedService(result)
        app.dependency_overrides[get_guarded_answer_service_dependency] = lambda: service
        try:
            with TestClient(app) as client:
                response = client.post("/v1/graphrag/answer", json={"question": "Question"})
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 200
        assert response.json()["synthesis_mode"] == expected_mode
        assert response.json()["abstained"] is expected_abstained

    @pytest.mark.parametrize("warning", [PARTIAL_EVIDENCE_WARNING, GRAPH_UNAVAILABLE_WARNING])
    def test_guarded_warning_preserved(self, warning):
        service = _FakeGuardedService(_result(warnings=[warning]))
        app.dependency_overrides[get_guarded_answer_service_dependency] = lambda: service
        try:
            with TestClient(app) as client:
                body = client.post("/v1/graphrag/answer", json={"question": "Question"}).json()
        finally:
            app.dependency_overrides.clear()
        assert warning in body["warnings"]


# =============================================================================
# D. Status endpoint
# =============================================================================


class TestStatusEndpoint:
    def test_status_reports_safe_phase7_configuration(self, api_client):
        client, _ = api_client
        response = client.get("/v1/graphrag/answer/status")
        assert response.status_code == 200
        body = response.json()
        assert body["phase"] == "phase_7"
        assert body["configured_provider"] == "deterministic"
        assert body["provider_available"] is True
        assert body["deterministic_fallback_available"] is True
        assert body["graphrag_base_required"] is True
        assert body["guarded_validation_enabled"] is True

    def test_configured_provider_separate_from_availability(self, api_client, monkeypatch):
        client, _ = api_client
        status = AnswerSynthesisStatus(
            synthesis_available=True,
            configured_provider="openai_compatible",
            active_provider="deterministic",
            provider_available=False,
            real_llm_enabled=False,
            real_llm_configured=False,
            deterministic_fallback_available=True,
            tool_calling_enabled=True,
            max_tool_rounds=2,
            max_tool_calls=4,
            graphrag_base_required=True,
            guarded_validation_enabled=True,
        )
        monkeypatch.setattr(api_module, "get_answer_synthesis_status", lambda: status)
        body = client.get("/v1/graphrag/answer/status").json()
        assert body["configured_provider"] == "openai_compatible"
        assert body["active_provider"] == "deterministic"
        assert body["provider_available"] is False

    def test_status_exposes_safe_tool_budgets(self, api_client):
        client, _ = api_client
        body = client.get("/v1/graphrag/answer/status").json()
        assert body["tool_calling_enabled"] is True
        assert body["max_tool_rounds"] == 2
        assert body["max_tool_calls"] == 4

    def test_status_contains_no_secrets(self, api_client):
        client, _ = api_client
        text = client.get("/v1/graphrag/answer/status").text.lower()
        for forbidden in ("api_key", "authorization", "database_url", "neo4j_password"):
            assert forbidden not in text

    def test_status_does_not_probe_external_provider(self, api_client, monkeypatch):
        import httpx

        client, _ = api_client

        class _ForbiddenClient:
            def __init__(self, *args, **kwargs):
                raise AssertionError("network client must not be constructed")

        monkeypatch.setattr(httpx, "Client", _ForbiddenClient)
        assert client.get("/v1/graphrag/answer/status").status_code == 200


# =============================================================================
# E. Failure mapping
# =============================================================================


class TestFailureMapping:
    def test_invalid_request_is_422(self, api_client):
        client, _ = api_client
        assert client.post("/v1/graphrag/answer", json={}).status_code == 422

    def test_controlled_abstention_is_200(self):
        service = _FakeGuardedService(_result(abstained=True))
        app.dependency_overrides[get_guarded_answer_service_dependency] = lambda: service
        try:
            with TestClient(app) as client:
                response = client.post("/v1/graphrag/answer", json={"question": "Unknown?"})
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 200
        assert response.json()["abstained"] is True

    def test_external_provider_fallback_is_200(self):
        service = _FakeGuardedService(_result(mode="fallback", provider="deterministic"))
        app.dependency_overrides[get_guarded_answer_service_dependency] = lambda: service
        try:
            with TestClient(app) as client:
                response = client.post("/v1/graphrag/answer", json={"question": "Question"})
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 200
        assert response.json()["synthesis_mode"] == "fallback"

    def test_unexpected_service_exception_is_safe_500(self):
        secret = "sk-do-not-leak-phase7e"
        service = _FakeGuardedService(error=RuntimeError(f"failure {secret}"))
        app.dependency_overrides[get_guarded_answer_service_dependency] = lambda: service
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.post("/v1/graphrag/answer", json={"question": "Question"})
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 500
        assert response.json()["detail"]["error"]["code"] == "ANSWER_SYNTHESIS_FAILED"
        assert secret not in response.text
        assert "traceback" not in response.text.lower()

    def test_dependency_failure_is_safe_503(self, monkeypatch):
        app.dependency_overrides.clear()

        def _fail():
            raise RuntimeError("postgresql://secret@host/db")

        monkeypatch.setattr(api_module, "get_guarded_answer_service", _fail)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/v1/graphrag/answer", json={"question": "Question"})
        assert response.status_code == 503
        assert response.json()["detail"]["error"]["code"] == "ANSWER_SYNTHESIS_UNAVAILABLE"
        assert "secret@host" not in response.text


# =============================================================================
# F. Dependency wiring
# =============================================================================


class TestDependencyWiring:
    def test_factory_builds_guarded_service_with_application_dependencies(self):
        settings = Settings(_env_file=None)

        def session_factory():
            return None

        def retrieval_fn(**_kwargs):
            return None

        service = build_guarded_answer_service(
            settings,
            sql_session_factory=session_factory,
            neo4j_available=False,
            graphrag_retrieval_fn=retrieval_fn,
            primary_provider=DeterministicProvider(),
        )
        assert isinstance(service, GuardedAnswerService)
        registry = service._orchestrator._registry
        names = {definition.name for definition in registry.list_definitions()}
        assert "graphrag_retrieval_tool" in names
        assert "sql_analytics_tool" in names
        assert "graph_evidence_tool" not in names

    def test_endpoint_calls_guarded_service_not_orchestrator(self):
        source = inspect.getsource(api_module.guarded_answer)
        assert "service.answer(" in source
        assert ".orchestrate(" not in source

    def test_endpoint_does_not_call_sql_or_neo4j_directly(self):
        source = inspect.getsource(api_module.guarded_answer).lower()
        assert "sqlalchemy" not in source
        assert "neo4j" not in source
        assert "raw_sql" not in source
        assert "raw_cypher" not in source

    def test_provider_and_tools_not_selected_from_request(self):
        assert set(GuardedAnswerRequest.model_fields) == {"question", "include_trace"}


# =============================================================================
# G. CLI
# =============================================================================


class TestCli:
    def test_question_required(self):
        with pytest.raises(SystemExit) as exc:
            cli.run([], stdout=io.StringIO(), stderr=io.StringIO())
        assert exc.value.code == 2

    def test_valid_question_calls_guarded_service_and_outputs_json(self):
        service = _FakeGuardedService()
        stdout = io.StringIO()
        code = cli.run(
            ["--question", "  Brake complaints?  "],
            service_factory=lambda: service,
            stdout=stdout,
            stderr=io.StringIO(),
        )
        body = json.loads(stdout.getvalue())
        assert code == 0
        assert service.questions == ["Brake complaints?"]
        assert body["phase"] == "phase_7"
        assert "trace" not in body

    def test_pretty_mode_outputs_valid_indented_json(self):
        stdout = io.StringIO()
        code = cli.run(
            ["--question", "Brake?", "--pretty"],
            service_factory=lambda: _FakeGuardedService(),
            stdout=stdout,
            stderr=io.StringIO(),
        )
        assert code == 0
        assert '\n  "answer"' in stdout.getvalue()
        assert json.loads(stdout.getvalue())["phase"] == "phase_7"

    def test_include_trace_flag_preserves_sanitized_trace(self):
        stdout = io.StringIO()
        code = cli.run(
            ["--question", "Brake?", "--include-trace"],
            service_factory=lambda: _FakeGuardedService(),
            stdout=stdout,
            stderr=io.StringIO(),
        )
        assert code == 0
        assert json.loads(stdout.getvalue())["trace"]["validation_outcome"] == "accepted"

    def test_abstention_exits_zero(self):
        stdout = io.StringIO()
        code = cli.run(
            ["--question", "Unknown?"],
            service_factory=lambda: _FakeGuardedService(_result(abstained=True)),
            stdout=stdout,
            stderr=io.StringIO(),
        )
        assert code == 0
        assert json.loads(stdout.getvalue())["abstained"] is True

    def test_configuration_failure_nonzero_without_traceback_or_secret(self):
        stderr = io.StringIO()

        def _fail_factory():
            raise RuntimeError("sk-cli-secret")

        code = cli.run(
            ["--question", "Brake?"],
            service_factory=_fail_factory,
            stdout=io.StringIO(),
            stderr=stderr,
        )
        assert code != 0
        assert "sk-cli-secret" not in stderr.getvalue()
        assert "traceback" not in stderr.getvalue().lower()

    def test_internal_failure_nonzero_without_traceback_or_secret(self):
        stderr = io.StringIO()
        service = _FakeGuardedService(error=RuntimeError("postgresql://secret@host/db"))
        code = cli.run(
            ["--question", "Brake?"],
            service_factory=lambda: service,
            stdout=io.StringIO(),
            stderr=stderr,
        )
        assert code != 0
        assert "secret@host" not in stderr.getvalue()
        assert "traceback" not in stderr.getvalue().lower()

    def test_whitespace_only_question_rejected(self):
        stderr = io.StringIO()
        code = cli.run(
            ["--question", "   "],
            service_factory=lambda: _FakeGuardedService(),
            stdout=io.StringIO(),
            stderr=stderr,
        )
        assert code == 2
        assert "question cannot be empty" in stderr.getvalue()


# =============================================================================
# H. Security and route compatibility
# =============================================================================


class TestSecurityAndCompatibility:
    def test_phase6_retrieval_route_preserved(self):
        paths = set(app.openapi()["paths"])
        assert "/v1/graphrag/retrieve" in paths
        assert "/v1/graphrag/answer" in paths
        assert "/v1/graphrag/answer/status" in paths

    def test_no_system_prompt_or_provider_response_endpoint(self):
        phase7_paths = [
            path.lower() for path in app.openapi()["paths"] if "graphrag/answer" in path
        ]
        assert all("prompt" not in path for path in phase7_paths)
        assert all("provider-response" not in path for path in phase7_paths)

    def test_phase7e_endpoint_has_no_db_graph_mutation_logic(self):
        source = inspect.getsource(api_module).lower()
        for forbidden in ("drop table", "delete from", "update ", "merge (", "create ("):
            assert forbidden not in source

    def test_no_raw_phase7c_result_endpoint(self):
        source = inspect.getsource(api_module)
        assert "OrchestrationResult" not in source
        assert "ProviderSynthesisResult" not in source

    def test_status_helper_never_serializes_api_key(self):
        settings = Settings(
            _env_file=None,
            PHASE7_SYNTHESIS_PROVIDER="openai_compatible",
            PHASE7_SYNTHESIS_MODEL="configured-model",
            PHASE7_SYNTHESIS_ALLOW_EXTERNAL=True,
            PHASE7_PROVIDER_API_KEY=SecretStr("sk-status-secret"),
            PHASE7_PROVIDER_BASE_URL="https://example.invalid/v1",
        )
        status = get_answer_synthesis_status(settings)
        serialized = json.dumps(status.to_dict())
        assert status.provider_available is True
        assert status.active_provider == "openai_compatible"
        assert "sk-status-secret" not in serialized
        assert "api_key" not in serialized

    def test_phase7e_transport_does_not_embed_phase7f_evaluation(self):
        endpoint_source = inspect.getsource(api_module)
        cli_source = inspect.getsource(cli)
        assert "evaluate_phase7_answers" not in endpoint_source
        assert "evaluate_phase7_answers" not in cli_source
