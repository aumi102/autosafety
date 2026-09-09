"""Phase 9 legacy `/v1/chat/*` bridge tests.

Offline and deterministic; the conversation service is an in-process fake.

Before Phase 9 these routes called `SqlAnalyticsService` (Phase 2) and
`answer_hybrid_question` (Phase 4) directly, bypassing `GuardedAnswerService`
entirely, and `POST /sessions` returned a session id that was never persisted.

Phase 9 makes them a deprecated bridge over `ConversationService`, so the
documented `docs/06_api_contract.md` shape is preserved while every request now
runs the full guarded path. These tests pin both halves of that: the bypass is
gone, and backward compatibility holds.
"""

from __future__ import annotations

import inspect
import json

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import chat as chat_module
from app.api.v1.endpoints.chat import (
    DEPRECATION_WARNING,
    LEGACY_PHASE,
    get_conversation_service_dependency,
)
from app.main import app
from app.services.answer_synthesis.guarded_models import (
    CitationValidationResult,
    ConfidenceResult,
    GuardedAnswerResult,
    GuardedCitation,
    GuardedClaim,
    GuardedTrace,
    RetrievalSummary,
)
from app.services.conversation.models import (
    ConversationEntities,
    ConversationNotFoundError,
    ConversationSummaryView,
    ConversationTurnResult,
    ResolvedContext,
    TurnCitationProvenance,
)

SESSION_ID = "11111111-2222-3333-4444-555555555555"
ABSTENTION_TEXT = (
    "I cannot provide a reliable answer to this question based on available evidence."
)


def _guarded(*, abstained: bool = False, with_sql: bool = False) -> GuardedAnswerResult:
    if abstained:
        return GuardedAnswerResult(
            query="Question",
            answer=ABSTENTION_TEXT,
            claims=[],
            citations=[],
            warnings=[],
            confidence=ConfidenceResult(score=0.0, level="low", reasons=["insufficient"]),
            abstained=True,
            abstention_reason="insufficient_evidence",
            synthesis_mode="abstention",
            provider="deterministic",
            retrieval_summary=RetrievalSummary(),
            validation=None,
            trace=GuardedTrace(
                original_provider="deterministic",
                provider_available=True,
                fallback_used=False,
                validation_outcome="abstention_required",
            ),
        )
    citations = [
        GuardedCitation(
            citation_id="cit-1",
            source_type="complaint",
            source_record_key="11420001",
            source_entity_id="ent-1",
            title="Complaint 11420001",
            text_span="Brake pedal travel reported.",
            retrieval_score=0.82,
            relation_basis="source_record",
            tool_name="graphrag_retrieval_tool",
        ),
        GuardedCitation(
            citation_id="cit-graph",
            source_type="graph_path",
            source_record_key="FORD:2020",
            text_span="Ford F-150 2020 -> SERVICE BRAKES",
            retrieval_score=0.7,
            relation_basis="official_recall_affects_vehicle",
            tool_name="graph_evidence_tool",
        ),
    ]
    if with_sql:
        citations.append(
            GuardedCitation(
                citation_id="cit-sql",
                source_type="sql_result",
                source_record_key="sql-1",
                text_span="component=SERVICE BRAKES count=3",
                retrieval_score=1.0,
                tool_name="sql_analytics_tool",
            )
        )
    return GuardedAnswerResult(
        query="Question",
        answer="Public complaint records match these filters.",
        claims=[
            GuardedClaim(
                claim_id="claim-1",
                text="Public complaint records match these filters.",
                claim_type="complaint_observation",
                citation_ids=["cit-1"],
            )
        ],
        citations=citations,
        warnings=["Complaint volume alone does not prove a safety defect."],
        confidence=ConfidenceResult(score=0.62, level="medium", reasons=["evidence present"]),
        abstained=False,
        synthesis_mode="deterministic",
        provider="deterministic",
        retrieval_summary=RetrievalSummary(citations_assembled=len(citations), tool_calls_made=2),
        validation=CitationValidationResult(valid=True, citation_coverage=1.0),
        trace=GuardedTrace(
            original_provider="deterministic",
            provider_available=True,
            fallback_used=False,
            validation_outcome="accepted",
        ),
    )


def _turn(question: str, **kwargs) -> ConversationTurnResult:
    guarded = _guarded(**kwargs)
    return ConversationTurnResult(
        conversation_id=SESSION_ID,
        turn_id="99999999-8888-7777-6666-555555555555",
        turn_index=0,
        question=question,
        context=ResolvedContext(
            resolved_question=question,
            entities=ConversationEntities(make="Ford", model="F-150", model_year=2020),
        ),
        guarded=guarded,
        provenance=[
            TurnCitationProvenance(
                citation_id=c.citation_id,
                source_type=c.source_type,
                source_record_key=c.source_record_key,
                first_seen_turn_index=0,
                reused_from_prior_turn=False,
                cited_by_claim=c.citation_id == "cit-1",
            )
            for c in guarded.citations
        ],
        conversation_warnings=[],
    )


class FakeConversationService:
    """In-process stand-in. No database, no network, no provider."""

    def __init__(self, *, missing: bool = False, abstained: bool = False, with_sql: bool = False):
        self.missing = missing
        self.abstained = abstained
        self.with_sql = with_sql
        self.calls: list[tuple[str, str]] = []
        self.started: list[str | None] = []

    def start_conversation(self, title=None) -> ConversationSummaryView:
        self.started.append(title)
        return ConversationSummaryView(
            conversation_id=SESSION_ID,
            title=title,
            created_at="2025-01-01T00:00:00+00:00",
            last_activity_at="2025-01-01T00:00:00+00:00",
            turn_count=0,
        )

    def answer(self, conversation_id: str, question: str) -> ConversationTurnResult:
        self.calls.append((conversation_id, question))
        if self.missing:
            raise ConversationNotFoundError(conversation_id)
        return _turn(question, abstained=self.abstained, with_sql=self.with_sql)


@pytest.fixture
def client_factory():
    created: list[TestClient] = []

    def _make(service: FakeConversationService) -> TestClient:
        app.dependency_overrides[get_conversation_service_dependency] = lambda: service
        client = TestClient(app)
        created.append(client)
        return client

    yield _make
    app.dependency_overrides.clear()
    for client in created:
        client.close()


# =============================================================================
# C. No unsafe Phase 2/4 bypass
# =============================================================================


def _code_without_docstrings(module) -> str:
    """Module source with docstrings removed.

    The module docstring names the removed Phase 2/4 services when explaining
    what changed, so scanning raw source would match its own changelog.
    """
    import ast

    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body.pop(0)
    return ast.unparse(tree)


class TestNoLegacyBypass:
    def test_module_no_longer_imports_phase2_or_phase4_services(self):
        source = _code_without_docstrings(chat_module)
        for bypass in (
            "SqlAnalyticsService",
            "answer_hybrid_question",
            "is_hybrid_question",
            "hybrid_parser",
        ):
            assert bypass not in source, f"legacy bypass still present: {bypass}"

    def test_module_holds_no_database_engine(self):
        source = _code_without_docstrings(chat_module)
        for forbidden in ("create_engine", "sessionmaker", "_get_sync_session", "DATABASE_URL"):
            assert forbidden not in source

    def test_module_routes_through_the_conversation_service(self):
        source = inspect.getsource(chat_module)
        assert "ConversationService" in source
        assert "service.answer(" in source

    def test_message_uses_the_guarded_conversation_path(self, client_factory):
        service = FakeConversationService()
        client = client_factory(service)
        response = client.post(
            f"/v1/chat/sessions/{SESSION_ID}/messages",
            json={"content": "What brake complaints are reported for Ford F-150 2020?"},
        )
        assert response.status_code == 200
        assert service.calls == [
            (SESSION_ID, "What brake complaints are reported for Ford F-150 2020?")
        ]

    def test_session_creation_now_persists_a_real_conversation(self, client_factory):
        service = FakeConversationService()
        client = client_factory(service)
        body = client.post("/v1/chat/sessions", json={"title": "Legacy"}).json()
        assert body["id"] == SESSION_ID
        assert service.started == ["Legacy"]

    def test_abstention_is_surfaced_not_swallowed(self, client_factory):
        client = client_factory(FakeConversationService(abstained=True))
        body = client.post(
            f"/v1/chat/sessions/{SESSION_ID}/messages", json={"content": "Is this car dangerous?"}
        ).json()
        assert body["intent"] == "safety"
        assert "cannot provide a reliable answer" in body["answer"]["summary"]


# =============================================================================
# Backward compatibility with the documented answer contract
# =============================================================================


class TestAnswerContractCompatibility:
    def test_response_keeps_every_documented_top_level_field(self, client_factory):
        client = client_factory(FakeConversationService())
        body = client.post(
            f"/v1/chat/sessions/{SESSION_ID}/messages", json={"content": "Brake complaints?"}
        ).json()
        for key in (
            "message_id",
            "run_id",
            "intent",
            "answer",
            "sql",
            "evidence",
            "warnings",
            "confidence",
            "debug",
            "phase",
        ):
            assert key in body

    def test_answer_has_summary_and_typed_sections(self, client_factory):
        client = client_factory(FakeConversationService())
        answer = client.post(
            f"/v1/chat/sessions/{SESSION_ID}/messages", json={"content": "Brake complaints?"}
        ).json()["answer"]
        assert answer["summary"]
        assert answer["sections"]
        for section in answer["sections"]:
            assert section["type"] in ("text", "table_summary", "evidence_summary", "caveat")

    def test_confidence_matches_the_documented_shape(self, client_factory):
        client = client_factory(FakeConversationService())
        confidence = client.post(
            f"/v1/chat/sessions/{SESSION_ID}/messages", json={"content": "Brake complaints?"}
        ).json()["confidence"]
        assert confidence["label"] in ("low", "medium", "high")
        assert 0.0 <= confidence["score"] <= 1.0
        assert isinstance(confidence["reasons"], list)

    def test_citations_and_graph_paths_are_separated(self, client_factory):
        client = client_factory(FakeConversationService())
        evidence = client.post(
            f"/v1/chat/sessions/{SESSION_ID}/messages", json={"content": "Brake complaints?"}
        ).json()["evidence"]
        assert [c["source_key"] for c in evidence["citations"]] == ["11420001"]
        assert len(evidence["graph_paths"]) == 1
        assert evidence["graph_paths"][0]["relation_source"] == "official_recall_affects_vehicle"

    def test_intent_reports_hybrid_when_the_sql_tool_contributed(self, client_factory):
        client = client_factory(FakeConversationService(with_sql=True))
        body = client.post(
            f"/v1/chat/sessions/{SESSION_ID}/messages", json={"content": "Top components?"}
        ).json()
        assert body["intent"] == "hybrid"
        assert body["sql"]["used"] is True

    def test_intent_reports_graph_rag_without_the_sql_tool(self, client_factory):
        client = client_factory(FakeConversationService())
        body = client.post(
            f"/v1/chat/sessions/{SESSION_ID}/messages", json={"content": "Brake complaints?"}
        ).json()
        assert body["intent"] == "graph_rag"
        assert body["sql"]["used"] is False


# =============================================================================
# Deprecation behavior
# =============================================================================


class TestDeprecation:
    def test_openapi_marks_both_routes_deprecated(self):
        paths = app.openapi()["paths"]
        assert paths["/v1/chat/sessions"]["post"]["deprecated"] is True
        assert paths["/v1/chat/sessions/{session_id}/messages"]["post"]["deprecated"] is True

    def test_conversation_routes_are_not_deprecated(self):
        paths = app.openapi()["paths"]
        assert paths["/v1/conversations"]["post"].get("deprecated") is not True

    def test_response_carries_deprecation_headers(self, client_factory):
        client = client_factory(FakeConversationService())
        response = client.post(
            f"/v1/chat/sessions/{SESSION_ID}/messages", json={"content": "Brake complaints?"}
        )
        assert response.headers["Deprecation"] == "true"
        assert "/v1/conversations" in response.headers["Link"]
        assert "deprecated" in response.headers["Warning"].lower()

    def test_session_creation_carries_deprecation_headers(self, client_factory):
        client = client_factory(FakeConversationService())
        response = client.post("/v1/chat/sessions", json={})
        assert response.headers["Deprecation"] == "true"

    def test_error_responses_also_carry_deprecation_headers(self, client_factory):
        """Raising HTTPException discards the injected Response, so errors must
        set the headers explicitly or clients only see them on success."""
        client = client_factory(FakeConversationService(missing=True))
        response = client.post(
            f"/v1/chat/sessions/{SESSION_ID}/messages", json={"content": "hi"}
        )
        assert response.status_code == 404
        assert response.headers["Deprecation"] == "true"
        assert "/v1/conversations" in response.headers["Link"]

    def test_deprecation_notice_appears_in_warnings_and_phase(self, client_factory):
        client = client_factory(FakeConversationService())
        body = client.post(
            f"/v1/chat/sessions/{SESSION_ID}/messages", json={"content": "Brake complaints?"}
        ).json()
        assert DEPRECATION_WARNING in body["warnings"]
        assert body["phase"] == LEGACY_PHASE
        assert body["debug"]["successor"] == "/v1/conversations"


# =============================================================================
# Safety and error mapping
# =============================================================================


class TestLegacySafety:
    def test_raw_sql_is_never_returned(self, client_factory):
        client = client_factory(FakeConversationService(with_sql=True))
        body = client.post(
            f"/v1/chat/sessions/{SESSION_ID}/messages", json={"content": "Top components?"}
        ).json()
        assert body["sql"]["query"] is None
        assert body["sql"]["rows"] is None
        rendered = json.dumps(body).lower()
        for forbidden in ("select ", "insert ", "match (", "merge "):
            assert forbidden not in rendered

    def test_response_leaks_no_secret(self, client_factory):
        client = client_factory(FakeConversationService())
        rendered = json.dumps(
            client.post(
                f"/v1/chat/sessions/{SESSION_ID}/messages", json={"content": "Brake complaints?"}
            ).json()
        ).lower()
        for forbidden in ("api_key", "password", "postgresql://", "bolt://", "authorization"):
            assert forbidden not in rendered

    def test_unknown_session_returns_404(self, client_factory):
        client = client_factory(FakeConversationService(missing=True))
        response = client.post(
            f"/v1/chat/sessions/{SESSION_ID}/messages", json={"content": "hi"}
        )
        assert response.status_code == 404
        assert response.json()["detail"]["error"]["code"] == "CHAT_SESSION_NOT_FOUND"

    def test_malformed_session_id_returns_404(self, client_factory):
        client = client_factory(FakeConversationService(missing=True))
        assert (
            client.post("/v1/chat/sessions/not-a-uuid/messages", json={"content": "hi"}).status_code
            == 404
        )

    def test_empty_content_is_rejected(self, client_factory):
        client = client_factory(FakeConversationService())
        assert (
            client.post(
                f"/v1/chat/sessions/{SESSION_ID}/messages", json={"content": "   "}
            ).status_code
            == 422
        )

    def test_oversized_content_is_rejected(self, client_factory):
        client = client_factory(FakeConversationService())
        assert (
            client.post(
                f"/v1/chat/sessions/{SESSION_ID}/messages", json={"content": "x" * 1001}
            ).status_code
            == 422
        )

    def test_legacy_routes_are_not_admin_gated(self):
        paths = app.openapi()["paths"]
        for path in ("/v1/chat/sessions", "/v1/chat/sessions/{session_id}/messages"):
            parameters = paths[path]["post"].get("parameters", [])
            assert not any(p.get("name") == "X-Admin-Token" for p in parameters)
