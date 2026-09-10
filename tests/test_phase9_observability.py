"""Phase 9 execution audit (agent_runs / tool_calls) tests.

Offline and deterministic. Persistence uses in-memory SQLite; the guarded
service is an in-process stub; tool execution runs through the **real**
`ToolRegistry` so the audit hook is exercised where it actually lives.

These tests target the audit invariants: every guarded execution is recorded,
tool metadata is safe and bounded, no secret or raw payload is persisted, audit
failure never fails a user request, and audit data never feeds an answer.
"""

from __future__ import annotations

import inspect
import uuid
from typing import cast

import pytest
from app.core.config import Settings, isolated_settings
from app.db.base import Base
from app.db.models.app import AgentRun, ToolCall
from app.services.answer_synthesis.guarded_models import (
    CitationValidationResult,
    ConfidenceResult,
    GuardedAnswerResult,
    GuardedCitation,
    GuardedClaim,
    GuardedTrace,
    RetrievalSummary,
)
from app.services.answer_synthesis.tools.base import (
    ToolCallRequest,
    ToolCallResult,
    ToolDefinition,
    ToolInputField,
    ToolInputSchema,
)
from app.services.answer_synthesis.tools.registry import ToolRegistry
from app.services.conversation.service import ConversationService
from app.services.observability import (
    AuditedGuardedAnswerService,
    ExecutionAuditRecorder,
    audit_run,
)
from app.services.observability.context import AuditRecorder
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ABSTENTION_TEXT = "I cannot provide a reliable answer to this question based on available evidence."

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def recorder(session_factory):
    return ExecutionAuditRecorder(session_factory)


def _settings(**overrides) -> Settings:
    return isolated_settings(**overrides)


def _guarded_result(
    *,
    abstained: bool = False,
    mode: str = "deterministic",
    fallback: bool = False,
    validation_outcome: str = "accepted",
    tool_calls: int = 2,
) -> GuardedAnswerResult:
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
            retrieval_summary=RetrievalSummary(tool_calls_made=tool_calls),
            validation=None,
            trace=GuardedTrace(
                original_provider="deterministic",
                provider_available=True,
                fallback_used=fallback,
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
                source_record_key="11420001",
                text_span="Brake pedal travel reported.",
                retrieval_score=0.82,
                tool_name="graphrag_retrieval_tool",
            )
        ],
        warnings=["Complaint volume alone does not prove a safety defect."],
        confidence=ConfidenceResult(score=0.62, level="medium", reasons=["evidence present"]),
        abstained=False,
        synthesis_mode=mode,
        provider="deterministic",
        retrieval_summary=RetrievalSummary(citations_assembled=1, tool_calls_made=tool_calls),
        validation=CitationValidationResult(valid=True, citation_coverage=1.0),
        trace=GuardedTrace(
            original_provider="openai_compatible",
            provider_available=True,
            fallback_used=fallback,
            validation_outcome=validation_outcome,
        ),
    )


class StubGuardedService:
    def __init__(self, result=None, *, boom: bool = False):
        self._result = result or _guarded_result()
        self._boom = boom
        self.questions: list[str] = []

    def answer(self, question: str) -> GuardedAnswerResult:
        self.questions.append(question)
        if self._boom:
            raise RuntimeError("guarded failure")
        return self._result


def _runs(session_factory) -> list[AgentRun]:
    session = session_factory()
    try:
        runs: list[AgentRun] = session.query(AgentRun).order_by(AgentRun.created_at).all()
        return runs
    finally:
        session.close()


def _tool_calls(session_factory) -> list[ToolCall]:
    session = session_factory()
    try:
        calls: list[ToolCall] = session.query(ToolCall).all()
        return calls
    finally:
        session.close()


# =============================================================================
# D. AgentRun lifecycle
# =============================================================================


class TestAgentRunLifecycle:
    def test_guarded_execution_creates_a_run(self, session_factory, recorder):
        service = AuditedGuardedAnswerService(StubGuardedService(), recorder, surface="api_guarded")
        service.answer("Brake complaints for Ford F-150 2020?")
        runs = _runs(session_factory)
        assert len(runs) == 1
        assert runs[0].phase == "phase_9"
        assert runs[0].surface == "api_guarded"

    def test_completed_run_records_status_and_timing(self, session_factory, recorder):
        AuditedGuardedAnswerService(StubGuardedService(), recorder).answer("q")
        run = _runs(session_factory)[0]
        assert run.status == "completed"
        assert run.started_at is not None
        assert run.finished_at is not None
        assert run.latency_ms is not None and run.latency_ms >= 0

    def test_run_records_provider_and_synthesis_mode(self, session_factory, recorder):
        AuditedGuardedAnswerService(StubGuardedService(), recorder).answer("q")
        run = _runs(session_factory)[0]
        assert run.provider == "deterministic"
        assert run.synthesis_mode == "deterministic"

    def test_run_records_confidence(self, session_factory, recorder):
        AuditedGuardedAnswerService(StubGuardedService(), recorder).answer("q")
        run = _runs(session_factory)[0]
        assert run.confidence_level == "medium"
        assert run.confidence_score == 6200  # 0.62 * 10000

    def test_run_records_validation_outcome(self, session_factory, recorder):
        AuditedGuardedAnswerService(StubGuardedService(), recorder).answer("q")
        assert _runs(session_factory)[0].validation_outcome == "accepted"

    def test_run_records_tool_call_count(self, session_factory, recorder):
        AuditedGuardedAnswerService(StubGuardedService(), recorder).answer("q")
        assert _runs(session_factory)[0].tool_call_count == 2

    def test_run_records_fallback(self, session_factory, recorder):
        result = _guarded_result(mode="fallback", fallback=True)
        AuditedGuardedAnswerService(StubGuardedService(result), recorder).answer("q")
        run = _runs(session_factory)[0]
        assert run.fallback_used is True
        assert run.synthesis_mode == "fallback"

    def test_run_records_abstention(self, session_factory, recorder):
        stub = StubGuardedService(_guarded_result(abstained=True))
        AuditedGuardedAnswerService(stub, recorder).answer("q")
        run = _runs(session_factory)[0]
        assert run.abstained is True
        assert run.abstention_reason == "insufficient_evidence"
        assert run.validation_outcome == "abstention_required"

    def test_run_records_warnings(self, session_factory, recorder):
        AuditedGuardedAnswerService(StubGuardedService(), recorder).answer("q")
        assert _runs(session_factory)[0].warnings

    def test_failed_execution_is_recorded_as_failed(self, session_factory, recorder):
        service = AuditedGuardedAnswerService(StubGuardedService(boom=True), recorder)
        with pytest.raises(RuntimeError):
            service.answer("q")
        run = _runs(session_factory)[0]
        assert run.status == "failed"
        assert run.error_code == "guarded_answer_failed"

    def test_each_execution_gets_its_own_run(self, session_factory, recorder):
        service = AuditedGuardedAnswerService(StubGuardedService(), recorder)
        service.answer("q1")
        service.answer("q2")
        assert len({r.id for r in _runs(session_factory)}) == 2

    def test_wrapper_returns_the_guarded_result_unchanged(self, recorder):
        expected = _guarded_result()
        service = AuditedGuardedAnswerService(StubGuardedService(expected), recorder)
        assert service.answer("q") is expected

    def test_wrapper_forwards_the_question_unchanged(self, recorder):
        inner = StubGuardedService()
        AuditedGuardedAnswerService(inner, recorder).answer("Brake complaints?")
        assert inner.questions == ["Brake complaints?"]

    def test_disabled_recorder_writes_nothing(self, session_factory):
        recorder = ExecutionAuditRecorder(session_factory, enabled=False)
        AuditedGuardedAnswerService(StubGuardedService(), recorder).answer("q")
        assert _runs(session_factory) == []


# =============================================================================
# E. ToolCall recording through the real registry
# =============================================================================


def _echo_definition(name: str = "echo_tool") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="Deterministic offline test tool.",
        input_schema=ToolInputSchema(
            fields={
                "operation": ToolInputField(
                    type="enum",
                    description="Operation name.",
                    required=True,
                    enum_values=["retrieve_complaints_only", "retrieve_recalls_only"],
                )
            }
        ),
    )


def _registry(fail: bool = False) -> ToolRegistry:
    registry = ToolRegistry()

    def adapter(*, call_id: str, arguments: dict) -> ToolCallResult:
        if fail:
            return ToolCallResult.error(call_id, "echo_tool", "adapter_error", "boom")
        return ToolCallResult.ok(call_id, "echo_tool", {"items": [{"a": 1}, {"b": 2}], "note": "x"})

    registry.register(_echo_definition(), adapter)
    return registry


class TestToolCallRecording:
    def _run_tool(
        self, recorder, registry, call_id="orch-abc123", operation="retrieve_recalls_only"
    ):
        context = recorder.start_run(surface="test")
        with audit_run(context):
            result = registry.execute(
                ToolCallRequest(
                    call_id=call_id, tool_name="echo_tool", arguments={"operation": operation}
                )
            )
        return context, result

    def test_successful_tool_execution_is_recorded(self, session_factory, recorder):
        self._run_tool(recorder, _registry())
        calls = _tool_calls(session_factory)
        assert len(calls) == 1
        assert calls[0].tool_name == "echo_tool"
        assert calls[0].success is True
        assert calls[0].status == "success"

    def test_application_owned_call_id_is_recorded(self, session_factory, recorder):
        self._run_tool(recorder, _registry(), call_id="orch-deadbeef")
        assert _tool_calls(session_factory)[0].call_id == "orch-deadbeef"

    def test_allowlisted_operation_is_recorded(self, session_factory, recorder):
        self._run_tool(recorder, _registry(), operation="retrieve_complaints_only")
        assert _tool_calls(session_factory)[0].operation == "retrieve_complaints_only"

    def test_evidence_item_count_is_recorded_without_content(self, session_factory, recorder):
        self._run_tool(recorder, _registry())
        call = _tool_calls(session_factory)[0]
        assert call.evidence_item_count == 2
        assert call.input_json == {}
        assert call.output_json == {}

    def test_tool_call_is_linked_to_its_run(self, session_factory, recorder):
        context, _ = self._run_tool(recorder, _registry())
        assert _tool_calls(session_factory)[0].agent_run_id == context.run_id

    def test_failed_tool_execution_is_still_recorded(self, session_factory, recorder):
        self._run_tool(recorder, _registry(fail=True))
        call = _tool_calls(session_factory)[0]
        assert call.success is False
        assert call.status == "error"
        assert call.error_code == "adapter_error"

    def test_rejected_unknown_tool_is_recorded(self, session_factory, recorder):
        context = recorder.start_run(surface="test")
        with audit_run(context):
            _registry().execute(
                ToolCallRequest(call_id="orch-x", tool_name="not_a_tool", arguments={})
            )
        call = _tool_calls(session_factory)[0]
        assert call.success is False
        assert call.error_code == "validation_error"

    def test_invalid_arguments_are_recorded_as_validation_error(self, session_factory, recorder):
        context = recorder.start_run(surface="test")
        with audit_run(context):
            _registry().execute(
                ToolCallRequest(
                    call_id="orch-y", tool_name="echo_tool", arguments={"operation": "not_allowed"}
                )
            )
        assert _tool_calls(session_factory)[0].error_code == "validation_error"

    def test_timing_is_recorded(self, session_factory, recorder):
        self._run_tool(recorder, _registry())
        call = _tool_calls(session_factory)[0]
        assert call.started_at is not None
        assert call.completed_at is not None
        assert call.latency_ms is not None and call.latency_ms >= 0

    def test_no_run_in_scope_records_nothing(self, session_factory, recorder):
        _registry().execute(
            ToolCallRequest(
                call_id="orch-z",
                tool_name="echo_tool",
                arguments={"operation": "retrieve_recalls_only"},
            )
        )
        assert _tool_calls(session_factory) == []

    def test_tool_result_is_returned_unchanged_when_audited(self, recorder):
        context = recorder.start_run(surface="test")
        with audit_run(context):
            result = _registry().execute(
                ToolCallRequest(
                    call_id="orch-w",
                    tool_name="echo_tool",
                    arguments={"operation": "retrieve_recalls_only"},
                )
            )
        assert result.success is True
        assert result.data is not None
        assert result.data["items"] == [{"a": 1}, {"b": 2}]


# =============================================================================
# Idempotency / retries
# =============================================================================


class TestIdempotency:
    def test_replayed_call_id_does_not_duplicate_a_row(self, session_factory, recorder):
        registry = _registry()
        request = ToolCallRequest(
            call_id="orch-same",
            tool_name="echo_tool",
            arguments={"operation": "retrieve_recalls_only"},
        )
        context = recorder.start_run(surface="test")
        with audit_run(context):
            registry.execute(request)
            registry.execute(request)
        assert len(_tool_calls(session_factory)) == 1

    def test_distinct_call_ids_produce_distinct_rows(self, session_factory, recorder):
        registry = _registry()
        context = recorder.start_run(surface="test")
        with audit_run(context):
            for call_id in ("orch-1", "orch-2"):
                registry.execute(
                    ToolCallRequest(
                        call_id=call_id,
                        tool_name="echo_tool",
                        arguments={"operation": "retrieve_recalls_only"},
                    )
                )
        assert len(_tool_calls(session_factory)) == 2

    def test_same_call_id_in_a_different_run_is_recorded(self, session_factory, recorder):
        registry = _registry()
        request = ToolCallRequest(
            call_id="orch-shared",
            tool_name="echo_tool",
            arguments={"operation": "retrieve_recalls_only"},
        )
        for _ in range(2):
            context = recorder.start_run(surface="test")
            with audit_run(context):
                registry.execute(request)
        assert len(_tool_calls(session_factory)) == 2


# =============================================================================
# F. Conversation integration
# =============================================================================


class TestConversationIntegration:
    def _service(self, session_factory, recorder, guarded=None):
        return ConversationService(
            session_factory=session_factory,
            guarded_service=guarded or StubGuardedService(),
            settings=_settings(),
            audit_recorder=recorder,
        )

    def test_conversation_turn_creates_a_run(self, session_factory, recorder):
        service = self._service(session_factory, recorder)
        conversation = service.start_conversation()
        service.answer(conversation.conversation_id, "Brake complaints for Ford F-150 2020?")
        runs = _runs(session_factory)
        assert len(runs) == 1
        assert runs[0].surface == "api_conversation"

    def test_run_is_linked_to_conversation_and_turn(self, session_factory, recorder):
        service = self._service(session_factory, recorder)
        conversation = service.start_conversation()
        result = service.answer(conversation.conversation_id, "Brake complaints?")
        run = _runs(session_factory)[0]
        assert str(run.conversation_id) == result.conversation_id
        assert str(run.turn_id) == result.turn_id

    def test_each_turn_gets_its_own_run(self, session_factory, recorder):
        service = self._service(session_factory, recorder)
        conversation = service.start_conversation()
        service.answer(conversation.conversation_id, "Brake complaints for Ford F-150 2020?")
        service.answer(conversation.conversation_id, "What about recalls?")
        assert len(_runs(session_factory)) == 2

    def test_runs_from_different_conversations_stay_separate(self, session_factory, recorder):
        service = self._service(session_factory, recorder)
        first = service.start_conversation()
        second = service.start_conversation()
        service.answer(first.conversation_id, "Brake complaints?")
        service.answer(second.conversation_id, "Recalls?")
        conversation_ids = {str(r.conversation_id) for r in _runs(session_factory)}
        assert conversation_ids == {first.conversation_id, second.conversation_id}

    def test_conversation_service_without_a_recorder_still_answers(self, session_factory):
        service = ConversationService(
            session_factory=session_factory,
            guarded_service=StubGuardedService(),
            settings=_settings(),
        )
        conversation = service.start_conversation()
        result = service.answer(conversation.conversation_id, "Brake complaints?")
        assert result.guarded.abstained is False
        assert _runs(session_factory) == []

    def test_deleting_a_conversation_removes_its_runs(self, session_factory, recorder):
        service = self._service(session_factory, recorder)
        conversation = service.start_conversation()
        service.answer(conversation.conversation_id, "Brake complaints?")
        session = session_factory()
        try:
            session.query(AgentRun).filter_by(
                conversation_id=uuid.UUID(conversation.conversation_id)
            ).delete()
            session.commit()
            assert session.query(AgentRun).count() == 0
        finally:
            session.close()


# =============================================================================
# G. Failure behavior — audit never fails the request
# =============================================================================


class TestAuditFailurePolicy:
    def test_recorder_failure_does_not_break_the_answer(self, session_factory):
        class BrokenFactory:
            def __call__(self):
                raise RuntimeError("database unavailable")

        recorder = ExecutionAuditRecorder(BrokenFactory())
        service = AuditedGuardedAnswerService(StubGuardedService(), recorder)
        result = service.answer("Brake complaints?")
        assert result.abstained is False

    def test_tool_audit_failure_does_not_break_tool_execution(self):
        class BrokenFactory:
            def __call__(self):
                raise RuntimeError("database unavailable")

        recorder = ExecutionAuditRecorder(BrokenFactory())
        context = recorder.start_run(surface="test")
        # start_run failed, so nothing is in scope; execution must still work.
        with audit_run(context):
            result = _registry().execute(
                ToolCallRequest(
                    call_id="orch-a",
                    tool_name="echo_tool",
                    arguments={"operation": "retrieve_recalls_only"},
                )
            )
        assert result.success is True

    def test_conversation_answer_survives_audit_failure(self, session_factory):
        class BrokenRecorder:
            enabled = True

            def start_run(self, **kwargs):
                raise RuntimeError("audit down")

            def finish_run(self, *args, **kwargs):
                raise RuntimeError("audit down")

            def link_conversation_turn(self, *args, **kwargs):
                raise RuntimeError("audit down")

        service = ConversationService(
            session_factory=session_factory,
            guarded_service=StubGuardedService(),
            settings=_settings(),
            audit_recorder=cast("AuditRecorder", BrokenRecorder()),
        )
        conversation = service.start_conversation()
        result = service.answer(conversation.conversation_id, "Brake complaints?")
        assert result.guarded.abstained is False

    def test_finishing_an_unknown_run_is_a_no_op(self, recorder):
        recorder.finish_run(uuid.uuid4(), _guarded_result(), latency_ms=1)
        recorder.fail_run(uuid.uuid4(), "nope")
        recorder.link_conversation_turn(uuid.uuid4(), conversation_id=uuid.uuid4())


# =============================================================================
# Audit privacy
# =============================================================================


class TestAuditPrivacy:
    def test_no_secret_or_payload_is_persisted(self, session_factory, recorder):
        registry = _registry()
        context = recorder.start_run(surface="test")
        with audit_run(context):
            registry.execute(
                ToolCallRequest(
                    call_id="orch-p",
                    tool_name="echo_tool",
                    arguments={
                        "operation": "retrieve_recalls_only",
                        "question": "secret user question about Ford F-150",
                    },
                )
            )
        recorder.finish_run(context.run_id, _guarded_result(), latency_ms=5)

        session = session_factory()
        try:
            rendered = " ".join(
                repr({c.name: getattr(row, c.name) for c in row.__table__.columns})
                for model in (AgentRun, ToolCall)
                for row in session.query(model).all()
            ).lower()
        finally:
            session.close()

        for forbidden in (
            "api_key",
            "password",
            "postgresql://",
            "bolt://",
            "redis://",
            "authorization",
            "secret user question",
            "select ",
            "match (",
            "traceback",
        ):
            assert forbidden not in rendered, forbidden

    def test_tool_arguments_are_not_persisted(self, session_factory, recorder):
        registry = _registry()
        context = recorder.start_run(surface="test")
        with audit_run(context):
            registry.execute(
                ToolCallRequest(
                    call_id="orch-q",
                    tool_name="echo_tool",
                    arguments={"operation": "retrieve_recalls_only", "top_k": 5},
                )
            )
        call = _tool_calls(session_factory)[0]
        assert call.input_json == {}
        assert "top_k" not in repr(call.operation)

    def test_recorder_never_writes_raw_prompt_or_response_fields(self):
        source = inspect.getsource(ExecutionAuditRecorder)
        for forbidden in ("raw_prompt", "provider_response", "prompt=", "messages="):
            assert forbidden not in source

    def test_recorder_contains_no_sql_or_cypher_text(self):
        source = inspect.getsource(ExecutionAuditRecorder)
        for forbidden in ("SELECT ", "INSERT INTO", "MATCH (", "MERGE ", "DELETE FROM"):
            assert forbidden not in source

    def test_error_codes_are_bounded_and_not_tracebacks(self, session_factory, recorder):
        context = recorder.start_run(surface="test")
        assert context is not None
        recorder.fail_run(context.run_id, "x" * 500)
        run = _runs(session_factory)[0]
        assert run.error_code is not None and len(run.error_code) <= 64


# =============================================================================
# Audit is not memory
# =============================================================================


class TestAuditIsNotMemory:
    def test_audit_never_influences_the_guarded_question(self, session_factory, recorder):
        inner = StubGuardedService()
        service = AuditedGuardedAnswerService(inner, recorder)
        service.answer("Brake complaints?")
        service.answer("Brake complaints?")
        assert inner.questions == ["Brake complaints?", "Brake complaints?"]

    def test_recorder_exposes_no_read_path_for_answering(self):
        members = inspect.getmembers(ExecutionAuditRecorder, inspect.isfunction)
        methods = {name for name, _ in members}
        for reader in ("get_run", "recent_runs", "history", "load", "recall"):
            assert reader not in methods

    def test_conversation_service_never_reads_audit_rows(self):
        source = inspect.getsource(ConversationService)
        assert "AgentRun" not in source
        assert "ToolCall" not in source

    def test_no_unauthenticated_api_route_exposes_audit_rows(self):
        """Superseded in scope by Phase 10, not weakened.

        Phase 9 shipped with no audit read surface at all, so this asserted that
        no route path mentioned `agent-runs`. Phase 10 added the admin-only
        `GET /v1/agent-runs/*` that `docs/06_api_contract.md` had deferred
        pending authorization and redaction rules, both of which Phase 9 itself
        defined. The invariant that still matters is the one kept here: audit
        rows must be unreachable without the fail-closed admin guard.
        """
        from app.core.security import ADMIN_TOKEN_HEADER
        from app.main import app

        schema = app.openapi()["paths"]
        audit_paths = [
            path
            for path in schema
            if "agent-run" in path or "agent_runs" in path or "tool-call" in path
        ]
        assert audit_paths, "expected the Phase 10 audit routes to exist"
        for path in audit_paths:
            for operation in schema[path].values():
                parameters = operation.get("parameters", [])
                assert any(
                    parameter.get("name") == ADMIN_TOKEN_HEADER for parameter in parameters
                ), f"{path} does not resolve the admin guard"
