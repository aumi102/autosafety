"""Phase 8 API/CLI integration and maintenance-protection tests.

All tests are offline. API transport uses dependency overrides; the CLI uses
an in-process fake ConversationService. No PostgreSQL, Neo4j, external
provider, NHTSA API, or Internet connection is required.
"""

from __future__ import annotations

import inspect
import io
import json
import uuid
from collections.abc import Callable
from typing import cast

import pytest
from app.api.v1.endpoints import conversations as api_module
from app.api.v1.endpoints.conversations import (
    ConversationMessageRequest,
    get_conversation_service_dependency,
)
from app.core.config import isolated_settings
from app.main import app
from app.services.answer_synthesis.guarded_models import (
    CitationValidationResult,
    ConfidenceResult,
    GuardedAnswerLike,
    GuardedAnswerResult,
    GuardedCitation,
    GuardedClaim,
    GuardedTrace,
    RetrievalSummary,
)
from app.services.conversation.factory import build_conversation_service
from app.services.conversation.models import (
    ConversationEntities,
    ConversationLimitError,
    ConversationNotFoundError,
    ConversationSummaryView,
    ConversationTurnResult,
    ConversationTurnView,
    ResolvedContext,
    TurnCitationProvenance,
)
from fastapi import HTTPException
from fastapi.testclient import TestClient
from scripts import query_phase8_conversation as cli
from sqlalchemy.orm import Session

CONVERSATION_ID = "11111111-2222-3333-4444-555555555555"
ABSTENTION_TEXT = (
    "I cannot provide a reliable answer to this question based on available evidence."
)

# =============================================================================
# Fakes
# =============================================================================


def _guarded(abstained: bool = False) -> GuardedAnswerResult:
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
                abstention_reason="insufficient_evidence",
            ),
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
        citations=[
            GuardedCitation(
                citation_id="cit-1",
                source_type="complaint",
                source_record_key="ODI-1001",
                source_entity_id="ent-1",
                title="Complaint ODI-1001",
                source_url=None,
                text_span="Brake pedal travel reported.",
                retrieval_score=0.82,
                relation_basis="source_record",
                tool_name="graphrag_retrieval_tool",
            )
        ],
        warnings=["Complaint volume alone does not prove a safety defect."],
        confidence=ConfidenceResult(score=0.62, level="medium", reasons=["evidence present"]),
        abstained=False,
        abstention_reason=None,
        synthesis_mode="deterministic",
        provider="deterministic",
        retrieval_summary=RetrievalSummary(citations_assembled=1),
        validation=CitationValidationResult(valid=True, citation_coverage=1.0),
        trace=GuardedTrace(
            original_provider="deterministic",
            provider_available=True,
            fallback_used=False,
            validation_outcome="accepted",
        ),
    )


def _turn_result(question: str = "Brake complaints for Ford F-150 2020?") -> ConversationTurnResult:
    return ConversationTurnResult(
        conversation_id=CONVERSATION_ID,
        turn_id="99999999-8888-7777-6666-555555555555",
        turn_index=0,
        question=question,
        context=ResolvedContext(
            resolved_question=question,
            entities=ConversationEntities(make="Ford", model="F-150", model_year=2020),
            context_applied=False,
            inherited_slots=[],
            context_turn_index=None,
            context_chars=0,
            turns_considered=0,
        ),
        guarded=_guarded(),
        provenance=[
            TurnCitationProvenance(
                citation_id="cit-1",
                source_type="complaint",
                source_record_key="ODI-1001",
                first_seen_turn_index=0,
                reused_from_prior_turn=False,
                cited_by_claim=True,
            )
        ],
        conversation_warnings=[],
    )


class FakeConversationService:
    """In-process stand-in for ConversationService. No database, no network."""

    def __init__(self, *, missing: bool = False, limit_reached: bool = False, boom: bool = False):
        self.missing = missing
        self.limit_reached = limit_reached
        self.boom = boom
        self.calls: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def _check(self, conversation_id: str) -> None:
        if self.boom:
            raise RuntimeError("internal detail that must not leak")
        if self.missing:
            raise ConversationNotFoundError(conversation_id)

    def start_conversation(self, title=None) -> ConversationSummaryView:
        if self.boom:
            raise RuntimeError("internal detail that must not leak")
        return ConversationSummaryView(
            conversation_id=CONVERSATION_ID,
            title=title,
            created_at="2025-01-01T00:00:00+00:00",
            last_activity_at="2025-01-01T00:00:00+00:00",
            turn_count=0,
        )

    def get_conversation(self, conversation_id: str) -> ConversationSummaryView:
        self._check(conversation_id)
        return ConversationSummaryView(
            conversation_id=conversation_id,
            title="Brake review",
            created_at="2025-01-01T00:00:00+00:00",
            last_activity_at="2025-01-01T00:05:00+00:00",
            turn_count=2,
        )

    def list_turns(self, conversation_id: str, limit: int = 50) -> list[ConversationTurnView]:
        self._check(conversation_id)
        return [
            ConversationTurnView(
                turn_id="99999999-8888-7777-6666-555555555555",
                turn_index=0,
                question="Brake complaints for Ford F-150 2020?",
                resolved_question="Brake complaints for Ford F-150 2020?",
                context_applied=False,
                entities=ConversationEntities(make="Ford", model="F-150", model_year=2020),
                answer="Public complaint records match these filters.",
                synthesis_mode="deterministic",
                provider="deterministic",
                abstained=False,
                abstention_reason=None,
                confidence_score=0.62,
                confidence_level="medium",
                claim_count=1,
                citation_count=1,
                warnings=[],
                created_at="2025-01-01T00:00:00+00:00",
            )
        ]

    def answer(self, conversation_id: str, question: str) -> ConversationTurnResult:
        self.calls.append((conversation_id, question))
        self._check(conversation_id)
        if self.limit_reached:
            raise ConversationLimitError("bounded turn limit reached")
        return _turn_result(question)

    def delete_conversation(self, conversation_id: str) -> bool:
        if self.boom:
            raise RuntimeError("internal detail that must not leak")
        self.deleted.append(conversation_id)
        return not self.missing


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
# O. API contract
# =============================================================================


class TestConversationApiContract:
    def test_create_conversation_returns_201_and_identity(self, client_factory):
        client = client_factory(FakeConversationService())
        response = client.post("/v1/conversations", json={"title": "Brake review"})
        assert response.status_code == 201
        body = response.json()
        assert body["conversation_id"] == CONVERSATION_ID
        assert body["phase"] == "phase_8"
        assert body["turn_count"] == 0

    def test_create_conversation_accepts_no_title(self, client_factory):
        client = client_factory(FakeConversationService())
        assert client.post("/v1/conversations", json={}).status_code == 201

    def test_create_conversation_rejects_unknown_fields(self, client_factory):
        client = client_factory(FakeConversationService())
        response = client.post("/v1/conversations", json={"title": "x", "provider": "openai"})
        assert response.status_code == 422

    def test_get_conversation_returns_safe_metadata(self, client_factory):
        client = client_factory(FakeConversationService())
        body = client.get(f"/v1/conversations/{CONVERSATION_ID}").json()
        assert body["conversation_id"] == CONVERSATION_ID
        assert body["turn_count"] == 2
        assert set(body) == {
            "conversation_id",
            "title",
            "created_at",
            "last_activity_at",
            "turn_count",
            "phase",
        }

    def test_message_returns_phase8_envelope_over_phase7_answer(self, client_factory):
        client = client_factory(FakeConversationService())
        response = client.post(
            f"/v1/conversations/{CONVERSATION_ID}/messages",
            json={"question": "Brake complaints for Ford F-150 2020?"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["phase"] == "phase_8"
        assert body["guarded_answer"]["phase"] == "phase_7"
        assert body["guarded_answer"]["claims"][0]["citation_ids"] == ["cit-1"]
        assert body["provenance"][0]["first_seen_turn_index"] == 0

    def test_message_hides_trace_by_default(self, client_factory):
        client = client_factory(FakeConversationService())
        body = client.post(
            f"/v1/conversations/{CONVERSATION_ID}/messages",
            json={"question": "Brake complaints for Ford F-150 2020?"},
        ).json()
        assert body["guarded_answer"]["trace"] is None

    def test_message_can_include_trace_on_request(self, client_factory):
        client = client_factory(FakeConversationService())
        body = client.post(
            f"/v1/conversations/{CONVERSATION_ID}/messages",
            json={"question": "Brake complaints for Ford F-150 2020?", "include_trace": True},
        ).json()
        assert body["guarded_answer"]["trace"]["validation_outcome"] == "accepted"

    def test_message_rejects_empty_question(self, client_factory):
        client = client_factory(FakeConversationService())
        response = client.post(
            f"/v1/conversations/{CONVERSATION_ID}/messages", json={"question": "  "}
        )
        assert response.status_code == 422

    def test_message_rejects_oversized_question(self, client_factory):
        client = client_factory(FakeConversationService())
        response = client.post(
            f"/v1/conversations/{CONVERSATION_ID}/messages", json={"question": "x" * 1001}
        )
        assert response.status_code == 422

    def test_message_rejects_request_selected_internals(self, client_factory):
        client = client_factory(FakeConversationService())
        for payload in (
            {"question": "hi", "provider": "openai_compatible"},
            {"question": "hi", "api_key": "sk-test"},
            {"question": "hi", "tools": ["sql_analytics_tool"]},
            {"question": "hi", "max_tool_calls": 99},
        ):
            assert client.post(
                f"/v1/conversations/{CONVERSATION_ID}/messages", json=payload
            ).status_code == 422

    def test_list_turns_returns_bounded_scoped_turns(self, client_factory):
        client = client_factory(FakeConversationService())
        body = client.get(f"/v1/conversations/{CONVERSATION_ID}/turns").json()
        assert body["conversation_id"] == CONVERSATION_ID
        assert body["turn_count"] == 1
        assert body["turns"][0]["entities"]["make"] == "Ford"

    def test_list_turns_rejects_out_of_range_limit(self, client_factory):
        client = client_factory(FakeConversationService())
        assert client.get(f"/v1/conversations/{CONVERSATION_ID}/turns?limit=0").status_code == 422
        oversized = client.get(f"/v1/conversations/{CONVERSATION_ID}/turns?limit=5000")
        assert oversized.status_code == 422

    def test_delete_conversation_reports_deletion(self, client_factory):
        service = FakeConversationService()
        client = client_factory(service)
        body = client.delete(f"/v1/conversations/{CONVERSATION_ID}").json()
        assert body["deleted"] is True
        assert service.deleted == [CONVERSATION_ID]

    def test_status_endpoint_reports_safe_posture(self, client_factory):
        client = client_factory(FakeConversationService())
        body = client.get("/v1/conversations/status/config").json()
        assert body["phase"] == "phase_8"
        assert body["cross_turn_provenance_enforced"] is True
        assert body["prior_assistant_text_used_as_evidence"] is False
        assert "api_key" not in json.dumps(body).lower()


class TestConversationApiErrors:
    def test_unknown_conversation_returns_404(self, client_factory):
        client = client_factory(FakeConversationService(missing=True))
        response = client.get(f"/v1/conversations/{CONVERSATION_ID}")
        assert response.status_code == 404
        assert response.json()["detail"]["error"]["code"] == "CONVERSATION_NOT_FOUND"

    def test_message_to_unknown_conversation_returns_404(self, client_factory):
        client = client_factory(FakeConversationService(missing=True))
        response = client.post(
            f"/v1/conversations/{CONVERSATION_ID}/messages", json={"question": "hi"}
        )
        assert response.status_code == 404

    def test_turn_limit_returns_409(self, client_factory):
        client = client_factory(FakeConversationService(limit_reached=True))
        response = client.post(
            f"/v1/conversations/{CONVERSATION_ID}/messages", json={"question": "hi"}
        )
        assert response.status_code == 409
        assert response.json()["detail"]["error"]["code"] == "CONVERSATION_TURN_LIMIT_REACHED"

    def test_delete_unknown_conversation_returns_404(self, client_factory):
        client = client_factory(FakeConversationService(missing=True))
        assert client.delete(f"/v1/conversations/{CONVERSATION_ID}").status_code == 404

    def test_internal_failure_never_leaks_detail(self, client_factory):
        client = client_factory(FakeConversationService(boom=True))
        response = client.post(
            f"/v1/conversations/{CONVERSATION_ID}/messages", json={"question": "hi"}
        )
        assert response.status_code == 500
        rendered = json.dumps(response.json()).lower()
        assert "internal detail that must not leak" not in rendered
        assert "traceback" not in rendered

    def test_dependency_construction_failure_returns_503(self, monkeypatch):
        def boom():
            raise RuntimeError("no database")

        monkeypatch.setattr(api_module, "get_conversation_service", boom)
        with pytest.raises(HTTPException) as exc:
            api_module.get_conversation_service_dependency()
        assert exc.value.status_code == 503
        assert "no database" not in json.dumps(exc.value.detail)


class TestConversationApiSecurity:
    def test_response_never_exposes_secrets_or_internals(self, client_factory):
        client = client_factory(FakeConversationService())
        rendered = json.dumps(
            client.post(
                f"/v1/conversations/{CONVERSATION_ID}/messages",
                json={"question": "Brake complaints for Ford F-150 2020?", "include_trace": True},
            ).json()
        ).lower()
        for forbidden in (
            "api_key",
            "password",
            "postgresql://",
            "bolt://",
            "redis://",
            "authorization",
            "select ",
            "match (",
            "prompt",
        ):
            assert forbidden not in rendered

    def test_request_schema_forbids_extra_fields(self):
        assert ConversationMessageRequest.model_config["extra"] == "forbid"

    def test_endpoint_module_holds_no_business_logic(self):
        source = inspect.getsource(api_module)
        for forbidden in ("create_engine", "sessionmaker", "GraphDatabase", "resolve_context("):
            assert forbidden not in source

    def test_cross_conversation_ids_are_passed_through_unmodified(self, client_factory):
        service = FakeConversationService()
        client = client_factory(service)
        other = str(uuid.uuid4())
        client.post(f"/v1/conversations/{other}/messages", json={"question": "hi"})
        assert service.calls == [(other, "hi")]


# =============================================================================
# CLI
# =============================================================================


class TestConversationCli:
    def _run(self, argv, service=None):
        out, err = io.StringIO(), io.StringIO()
        code = cli.run(
            argv,
            service_factory=lambda: service or FakeConversationService(),
            stdout=out,
            stderr=err,
        )
        return code, out.getvalue(), err.getvalue()

    def test_single_turn_emits_json_document(self):
        code, out, _ = self._run(["-q", "Brake complaints for Ford F-150 2020?"])
        assert code == 0
        payload = json.loads(out)
        assert payload["phase"] == "phase_8"
        assert payload["turn_count"] == 1
        assert payload["turns"][0]["guarded_answer"]["phase"] == "phase_7"

    def test_multi_turn_answers_each_question_in_order(self):
        service = FakeConversationService()
        code, out, _ = self._run(
            ["-q", "Brake complaints for Ford F-150 2020?", "-q", "What about recalls?"],
            service=service,
        )
        assert code == 0
        assert [q for _, q in service.calls] == [
            "Brake complaints for Ford F-150 2020?",
            "What about recalls?",
        ]
        assert json.loads(out)["turn_count"] == 2

    def test_existing_conversation_id_is_reused(self):
        service = FakeConversationService()
        self._run(["-q", "hi", "--conversation-id", CONVERSATION_ID], service=service)
        assert service.calls[0][0] == CONVERSATION_ID

    def test_trace_hidden_by_default(self):
        _, out, _ = self._run(["-q", "hi"])
        assert json.loads(out)["turns"][0]["guarded_answer"]["trace"] is None

    def test_trace_included_on_request(self):
        _, out, _ = self._run(["-q", "hi", "--include-trace"])
        assert json.loads(out)["turns"][0]["guarded_answer"]["trace"] is not None

    def test_delete_when_done_removes_the_conversation(self):
        service = FakeConversationService()
        _, out, _ = self._run(["-q", "hi", "--delete-when-done"], service=service)
        assert json.loads(out)["deleted"] is True
        assert service.deleted

    def test_empty_question_is_rejected(self):
        code, _, err = self._run(["-q", "   "])
        assert code == 2
        assert "non-empty" in err

    def test_oversized_question_is_rejected(self):
        code, _, err = self._run(["-q", "x" * 1001])
        assert code == 2
        assert "1000 characters" in err

    def test_missing_conversation_exits_two(self):
        code, _, err = self._run(
            ["-q", "hi", "--conversation-id", CONVERSATION_ID],
            service=FakeConversationService(missing=True),
        )
        assert code == 2
        assert "not found" in err

    def test_turn_limit_exits_two(self):
        code, _, err = self._run(["-q", "hi"], service=FakeConversationService(limit_reached=True))
        assert code == 2
        assert "bounded turn limit" in err

    def test_internal_failure_prints_no_detail(self):
        code, _, err = self._run(["-q", "hi"], service=FakeConversationService(boom=True))
        assert code == 1
        assert "internal detail that must not leak" not in err

    def test_pretty_output_is_valid_json(self):
        _, out, _ = self._run(["-q", "hi", "--pretty"])
        assert json.loads(out)["phase"] == "phase_8"
        assert "\n" in out

    def test_cli_never_selects_a_provider(self):
        source = inspect.getsource(cli)
        for forbidden in ("--provider", "--api-key", "--model", "api_key"):
            assert forbidden not in source


# =============================================================================
# Q. Maintenance/admin route protection
# =============================================================================


# Maintenance/admin route protection moved to tests/test_phase9_admin_security.py
# when Phase 9 changed the guard from fail-open to fail-closed.


# =============================================================================
# Wiring
# =============================================================================


class TestFactoryWiring:
    def test_factory_accepts_injected_dependencies(self):
        service = build_conversation_service(
            isolated_settings(),
            session_factory=cast("Callable[[], Session]", lambda: None),
            guarded_service=cast("GuardedAnswerLike", object()),
        )
        assert service is not None

    def test_factory_never_reads_request_input(self):
        signature = inspect.signature(build_conversation_service)
        assert set(signature.parameters) == {
            "settings",
            "session_factory",
            "guarded_service",
            "audit_recorder",
        }

    def test_factory_does_not_construct_its_own_engine_inline(self):
        source = inspect.getsource(build_conversation_service)
        assert "create_engine(" not in source

    def test_conversation_endpoint_imports_clean(self):
        from app.api.v1.endpoints.conversations import send_conversation_message

        assert callable(send_conversation_message)

    def test_legacy_chat_endpoint_is_unchanged(self):
        from app.api.v1.endpoints.chat import send_message

        assert callable(send_message)
