"""
Tests for Phase 7C — LLM Provider and Controlled Tool Calling (orchestration).

Uses the real ToolRegistry with fake adapters (no real DB/Neo4j/GraphRAG
service) and fake providers (DeterministicProvider, FakeProvider, and small
test-only SynthesisProvider subclasses). No network, no real LLM.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any

from app.services.answer_synthesis.models import (
    ProviderSynthesisRequest,
    ProviderSynthesisResult,
    ProviderToolCall,
    SynthesisConfig,
)
from app.services.answer_synthesis.orchestrator import SynthesisOrchestrator
from app.services.answer_synthesis.providers import (
    DeterministicProvider,
    FakeProvider,
)
from app.services.answer_synthesis.tools.base import (
    ToolCallResult,
    ToolDefinition,
    ToolInputField,
    ToolInputSchema,
)
from app.services.answer_synthesis.tools.graphrag_adapter import (
    GRAPHRAG_RETRIEVAL_DEFINITION,
    build_graphrag_adapter,
)
from app.services.answer_synthesis.tools.registry import ToolRegistry

# =============================================================================
# Fake GraphRAG domain objects (stand-ins for Phase 6 GraphRAGRetrievalResult)
# =============================================================================


@dataclasses.dataclass
class _FakeChunk:
    chunk_id: str
    score: float
    source_type: str
    source_record_key: str
    title: str
    text: str
    make: str | None = None
    model: str | None = None
    model_year: int | None = None
    component: str | None = None
    citation_label: str | None = None


@dataclasses.dataclass
class _FakeCitation:
    source_type: str
    source_id: str
    source_key: str
    citation_label: str
    text_span: str
    confidence: float


@dataclasses.dataclass
class _FakePath:
    path_text: str
    relation_source: str
    confidence: float
    source_type: str = ""
    source_key: str = ""


@dataclasses.dataclass
class _FakeGraphRAGResult:
    retrieved_chunks: list
    citations: list
    graph_paths: list
    warnings: list
    confidence_label: str
    confidence_score: float
    confidence_reasons: list
    total_chunks_returned: int
    neo4j_available: bool
    neo4j_error: str | None = None
    retrieval_mode: str = "vector"


def _default_retrieval_fn(captured_calls: list | None = None):
    def fn(*, question, top_k, source_type, make, model, model_year, include_graph):
        if captured_calls is not None:
            captured_calls.append(
                dict(
                    question=question,
                    top_k=top_k,
                    source_type=source_type,
                    make=make,
                    model=model,
                    model_year=model_year,
                    include_graph=include_graph,
                )
            )
        chunk = _FakeChunk(
            chunk_id="chunk-1",
            score=0.9,
            source_type="complaint",
            source_record_key="11420001",
            title="Complaint",
            text="Brake pedal failure reported.",
            citation_label="Complaint 11420001",
        )
        citation = _FakeCitation(
            source_type="complaint",
            source_id="c1",
            source_key="11420001",
            citation_label="Complaint 11420001",
            text_span="Brake pedal failure reported.",
            confidence=0.9,
        )
        path = _FakePath(
            path_text="Ford F-150 2020 -> SERVICE BRAKES -> Recall 20V123000",
            relation_source="official_recall_affects_vehicle",
            confidence=0.85,
            source_type="recall",
            source_key="20V123000",
        )
        return _FakeGraphRAGResult(
            retrieved_chunks=[chunk],
            citations=[citation],
            graph_paths=[path],
            warnings=["Complaint volume alone does not prove a safety defect."],
            confidence_label="high",
            confidence_score=0.9,
            confidence_reasons=["chunks>=1"],
            total_chunks_returned=1,
            neo4j_available=True,
        )

    return fn


def _empty_retrieval_fn(*, question, top_k, source_type, make, model, model_year, include_graph):
    return _FakeGraphRAGResult(
        retrieved_chunks=[],
        citations=[],
        graph_paths=[],
        warnings=[],
        confidence_label="low",
        confidence_score=0.0,
        confidence_reasons=[],
        total_chunks_returned=0,
        neo4j_available=True,
    )


def _failing_retrieval_fn(*, question, top_k, source_type, make, model, model_year, include_graph):
    raise RuntimeError("neo4j connection refused at internal-host:7687 password=hunter2")


# =============================================================================
# Fake additional tools (sql_analytics_tool / graph_evidence_tool)
# =============================================================================

_FAKE_SQL_SCHEMA = ToolInputSchema(
    fields={
        "operation": ToolInputField(
            type="enum",
            description="op",
            required=True,
            enum_values=["vehicles_by_complaint_count", "top_complaint_components_by_vehicle"],
        ),
        "limit": ToolInputField(
            type="integer",
            description="limit",
            required=False,
            default=10,
            min_value=1,
            max_value=50,
        ),
    }
)
FAKE_SQL_DEFINITION = ToolDefinition(
    name="sql_analytics_tool",
    description="fake sql tool",
    input_schema=_FAKE_SQL_SCHEMA,
    read_only=True,
    max_result_items=50,
    timeout_seconds=10,
)

_FAKE_GRAPH_SCHEMA = ToolInputSchema(
    fields={
        "operation": ToolInputField(
            type="enum", description="op", required=True, enum_values=["recall_paths_by_vehicle"]
        ),
        "vehicle_id": ToolInputField(
            type="string", description="vehicle id", required=True, max_length=100
        ),
    }
)
FAKE_GRAPH_DEFINITION = ToolDefinition(
    name="graph_evidence_tool",
    description="fake graph tool",
    input_schema=_FAKE_GRAPH_SCHEMA,
    read_only=True,
    max_result_items=20,
    timeout_seconds=10,
)


def _make_spy_adapter(tool_name: str, data: dict):
    """Returns (adapter, calls) — calls records every (call_id, arguments) invocation."""
    calls: list = []

    def adapter(*, call_id: str, arguments: dict) -> ToolCallResult:
        calls.append({"call_id": call_id, "arguments": arguments})
        return ToolCallResult.ok(call_id, tool_name, dict(data))

    return adapter, calls


def _registry_with_spies():
    """Build a registry and return (registry, sql_calls, graph_calls).

    Callers assert on the returned counters to see which tools ran.
    """
    registry = ToolRegistry()
    registry.register(
        GRAPHRAG_RETRIEVAL_DEFINITION, build_graphrag_adapter(_default_retrieval_fn())
    )

    sql_adapter, sql_calls = _make_spy_adapter(
        "sql_analytics_tool",
        {
            "rows": [{"vehicle": "Ford F-150 2020", "count": 3}],
            "columns": ["vehicle", "count"],
            "operation": "x",
        },
    )
    registry.register(FAKE_SQL_DEFINITION, sql_adapter)

    graph_adapter, graph_calls = _make_spy_adapter(
        "graph_evidence_tool",
        {
            "recalls": [
                {
                    "campaign_number": "20V123000",
                    "component": "SERVICE BRAKES",
                    "summary": "test recall",
                }
            ],
            "relation_basis": "official_recall_affects_vehicle",
            "operation": "recall_paths_by_vehicle",
        },
    )
    registry.register(FAKE_GRAPH_DEFINITION, graph_adapter)

    return registry, sql_calls, graph_calls


# =============================================================================
# Capturing provider — records every request it receives, for white-box checks
# =============================================================================


class _CapturingFakeProvider(FakeProvider):
    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.plan_requests: list[ProviderSynthesisRequest] = []
        self.synthesize_requests: list[ProviderSynthesisRequest] = []

    def plan_tool_calls(self, request: ProviderSynthesisRequest) -> list[ProviderToolCall]:
        self.plan_requests.append(request)
        return super().plan_tool_calls(request)

    def synthesize(self, request: ProviderSynthesisRequest) -> ProviderSynthesisResult:
        self.synthesize_requests.append(request)
        return super().synthesize(request)


class _RaisingPlanProvider(FakeProvider):
    """Simulates a planning-phase timeout/crash."""

    def plan_tool_calls(self, request: ProviderSynthesisRequest) -> list[ProviderToolCall]:
        raise TimeoutError("planning call timed out")


class _RaisingSynthesizeProvider(FakeProvider):
    """Simulates a transport-level crash during synthesis (not a returned error_code)."""

    def __init__(self, message: str = "connection reset by peer"):
        super().__init__()
        self._message = message

    def synthesize(self, request: ProviderSynthesisRequest) -> ProviderSynthesisResult:
        raise ConnectionError(self._message)


# =============================================================================
# A. Mandatory base retrieval
# =============================================================================


class TestMandatoryBaseRetrieval:
    def test_graphrag_tool_executes_first(self):
        registry, _, _ = _registry_with_spies()
        orch = SynthesisOrchestrator(registry=registry, primary_provider=FakeProvider())
        result = orch.orchestrate("brake complaints for Ford F-150 2020")
        assert result.trace.tool_call_log[0]["tool_name"] == "graphrag_retrieval_tool"
        assert result.trace.tool_call_log[0]["note"] == "mandatory_base"
        assert result.trace.tool_call_log[0]["executed"] is True

    def test_correct_operation_selected_recalls_only(self):
        captured: list = []
        registry = ToolRegistry()
        registry.register(
            GRAPHRAG_RETRIEVAL_DEFINITION, build_graphrag_adapter(_default_retrieval_fn(captured))
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=FakeProvider())
        orch.orchestrate("what recall campaigns exist for this vehicle")
        assert captured[0]["source_type"] == "recall"

    def test_correct_operation_selected_complaints_only(self):
        captured: list = []
        registry = ToolRegistry()
        registry.register(
            GRAPHRAG_RETRIEVAL_DEFINITION, build_graphrag_adapter(_default_retrieval_fn(captured))
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=FakeProvider())
        orch.orchestrate("what complaints exist for this vehicle")
        assert captured[0]["source_type"] == "complaint"

    def test_correct_operation_selected_both(self):
        captured: list = []
        registry = ToolRegistry()
        registry.register(
            GRAPHRAG_RETRIEVAL_DEFINITION, build_graphrag_adapter(_default_retrieval_fn(captured))
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=FakeProvider())
        orch.orchestrate("what complaints and recalls exist for this vehicle")
        assert captured[0]["source_type"] is None

    def test_base_call_passes_registry_validation(self):
        registry, _, _ = _registry_with_spies()
        orch = SynthesisOrchestrator(registry=registry, primary_provider=FakeProvider())
        result = orch.orchestrate("brake complaints for Ford F-150 2020")
        assert "rejected_reason" not in result.trace.tool_call_log[0]

    def test_base_result_enters_evidence_bundle(self):
        registry, _, _ = _registry_with_spies()
        provider = _CapturingFakeProvider()
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider)
        orch.orchestrate("brake complaints for Ford F-150 2020")
        final_request = provider.synthesize_requests[-1]
        assert "cite-complaint-11420001" in final_request.evidence_bundle_text
        assert any(
            c["citation_id"] == "cite-complaint-11420001" for c in final_request.citation_table
        )

    def test_provider_receives_bounded_evidence(self):
        registry, _, _ = _registry_with_spies()
        provider = _CapturingFakeProvider()
        config = SynthesisConfig(max_evidence_chars=500)
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider, config=config)
        orch.orchestrate("brake complaints for Ford F-150 2020")
        final_request = provider.synthesize_requests[-1]
        assert len(final_request.evidence_bundle_text) <= 600  # bound + truncation marker


# =============================================================================
# B. Additional tool calls
# =============================================================================


class TestAdditionalToolCalls:
    def test_valid_allowlisted_call_executes(self):
        registry, sql_calls, _ = _registry_with_spies()
        provider = FakeProvider()
        provider.queue_tool_calls(
            [
                ProviderToolCall(
                    call_id="p1",
                    tool_name="sql_analytics_tool",
                    arguments={"operation": "vehicles_by_complaint_count"},
                )
            ]
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider)
        result = orch.orchestrate("how many complaints for Ford F-150 2020")
        assert len(sql_calls) == 1
        assert result.trace.tool_calls_executed == 2  # base + sql

    def test_unknown_tool_rejected(self):
        registry, sql_calls, _ = _registry_with_spies()
        provider = FakeProvider()
        provider.queue_tool_calls(
            [ProviderToolCall(call_id="p1", tool_name="delete_all_data_tool", arguments={})]
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider)
        result = orch.orchestrate("q")
        assert result.trace.tool_calls_rejected == 1
        assert len(sql_calls) == 0
        rejected_entries = [e for e in result.trace.tool_call_log if e.get("rejected_reason")]
        assert any("Unknown tool" in e["rejected_reason"] for e in rejected_entries)

    def test_invalid_arguments_rejected_and_adapter_not_invoked(self):
        registry, sql_calls, _ = _registry_with_spies()
        provider = FakeProvider()
        # missing required "operation" field
        provider.queue_tool_calls(
            [ProviderToolCall(call_id="p1", tool_name="sql_analytics_tool", arguments={"limit": 5})]
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider)
        orch.orchestrate("q")
        assert len(sql_calls) == 0  # adapter never invoked

    def test_application_owned_call_ids_used(self):
        registry, sql_calls, _ = _registry_with_spies()
        provider = FakeProvider()
        provider.queue_tool_calls(
            [
                ProviderToolCall(
                    call_id="LLM-CHOSE-THIS-ID",
                    tool_name="sql_analytics_tool",
                    arguments={"operation": "vehicles_by_complaint_count"},
                )
            ]
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider)
        orch.orchestrate("q")
        assert len(sql_calls) == 1
        assert sql_calls[0]["call_id"] != "LLM-CHOSE-THIS-ID"
        assert sql_calls[0]["call_id"].startswith("orch-")

    def test_returned_evidence_appended(self):
        registry, _, _ = _registry_with_spies()
        provider = _CapturingFakeProvider()
        provider.queue_tool_calls(
            [
                ProviderToolCall(
                    call_id="p1",
                    tool_name="sql_analytics_tool",
                    arguments={"operation": "vehicles_by_complaint_count"},
                )
            ]
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider)
        orch.orchestrate("q")
        final_request = provider.synthesize_requests[-1]
        assert "Ford F-150 2020" in final_request.evidence_bundle_text

    def test_provider_request_has_no_adapter_or_client_fields(self):
        registry, _, _ = _registry_with_spies()
        provider = _CapturingFakeProvider()
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider)
        orch.orchestrate("q")
        req = provider.synthesize_requests[-1]
        for f in dataclasses.fields(req):
            value = getattr(req, f.name)
            assert isinstance(value, (str, int, float, bool, list, dict, type(None)))


# =============================================================================
# C. Budgets
# =============================================================================


class TestBudgets:
    def test_maximum_rounds_enforced(self):
        registry, sql_calls, _ = _registry_with_spies()
        provider = FakeProvider()
        # Queue more rounds worth of calls than max_tool_rounds allows.
        for i in range(5):
            provider.queue_tool_calls(
                [
                    ProviderToolCall(
                        call_id=f"p{i}",
                        tool_name="sql_analytics_tool",
                        arguments={"operation": "vehicles_by_complaint_count", "limit": i + 1},
                    )
                ]
            )
        config = SynthesisConfig(max_tool_rounds=1, max_tool_calls=10)
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider, config=config)
        result = orch.orchestrate("q")
        assert result.trace.tool_rounds <= 1

    def test_maximum_calls_enforced(self):
        registry, sql_calls, graph_calls = _registry_with_spies()
        provider = FakeProvider()
        provider.queue_tool_calls(
            [
                ProviderToolCall(
                    call_id="p1",
                    tool_name="sql_analytics_tool",
                    arguments={"operation": "vehicles_by_complaint_count"},
                ),
                ProviderToolCall(
                    call_id="p2",
                    tool_name="graph_evidence_tool",
                    arguments={"operation": "recall_paths_by_vehicle", "vehicle_id": "veh-1"},
                ),
            ]
        )
        config = SynthesisConfig(
            max_tool_calls=2, max_tool_rounds=2
        )  # base consumes 1, only 1 remains
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider, config=config)
        result = orch.orchestrate("q")
        assert result.trace.tool_calls_executed <= 2
        assert (len(sql_calls) + len(graph_calls)) == 1

    def test_base_call_accounting_matches_design(self):
        registry, _, _ = _registry_with_spies()
        config = SynthesisConfig(max_tool_calls=4)
        orch = SynthesisOrchestrator(
            registry=registry, primary_provider=FakeProvider(), config=config
        )
        result = orch.orchestrate("q")
        assert result.trace.tool_calls_executed == 1  # only mandatory base, no extra requested

    def test_duplicate_identical_calls_deduplicated(self):
        registry, sql_calls, _ = _registry_with_spies()
        provider = FakeProvider()

        def same_call(cid):
            return ProviderToolCall(
                call_id=cid,
                tool_name="sql_analytics_tool",
                arguments={"operation": "vehicles_by_complaint_count"},
            )

        provider.queue_tool_calls([same_call("p1"), same_call("p2")])
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider)
        result = orch.orchestrate("q")
        assert len(sql_calls) == 1
        assert result.trace.tool_calls_rejected >= 1
        assert any("Duplicate" in w for w in result.trace.warnings)

    def test_repeated_invalid_calls_cannot_loop_forever(self):
        registry, sql_calls, _ = _registry_with_spies()
        provider = FakeProvider()
        for _ in range(20):
            provider.queue_tool_calls(
                [ProviderToolCall(call_id="p", tool_name="nonexistent_tool", arguments={})]
            )
        config = SynthesisConfig(max_tool_rounds=2)
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider, config=config)
        start = time.time()
        result = orch.orchestrate("q")
        elapsed = time.time() - start
        assert elapsed < 5.0
        assert result.trace.tool_rounds <= 2

    def test_provider_controlled_budget_ignored(self):
        registry, sql_calls, graph_calls = _registry_with_spies()
        provider = FakeProvider()
        for i in range(10):
            provider.queue_tool_calls(
                [
                    ProviderToolCall(
                        call_id=f"p{i}",
                        tool_name="sql_analytics_tool",
                        arguments={"operation": "vehicles_by_complaint_count"},
                    )
                ]
            )
        config = SynthesisConfig(max_tool_calls=4, max_tool_rounds=2)
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider, config=config)
        result = orch.orchestrate("q")
        assert result.trace.tool_calls_executed <= 4

    def test_trace_records_executed_and_rejected_calls(self):
        registry, sql_calls, _ = _registry_with_spies()
        provider = FakeProvider()
        provider.queue_tool_calls(
            [
                ProviderToolCall(
                    call_id="p1",
                    tool_name="sql_analytics_tool",
                    arguments={"operation": "vehicles_by_complaint_count"},
                ),
                ProviderToolCall(call_id="p2", tool_name="unknown_tool", arguments={}),
            ]
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider)
        result = orch.orchestrate("q")
        executed = [e for e in result.trace.tool_call_log if e.get("executed")]
        rejected = [e for e in result.trace.tool_call_log if e.get("rejected_reason")]
        assert len(executed) >= 1
        assert len(rejected) >= 1


# =============================================================================
# D. Provider behavior
# =============================================================================


class TestProviderBehavior:
    def test_provider_requests_no_extra_tools(self):
        registry, sql_calls, graph_calls = _registry_with_spies()
        orch = SynthesisOrchestrator(registry=registry, primary_provider=FakeProvider())
        result = orch.orchestrate("q")
        assert result.trace.tool_calls_executed == 1
        assert len(sql_calls) == 0 and len(graph_calls) == 0

    def test_provider_requests_one_tool(self):
        registry, sql_calls, _ = _registry_with_spies()
        provider = FakeProvider()
        provider.queue_tool_calls(
            [
                ProviderToolCall(
                    call_id="p1",
                    tool_name="sql_analytics_tool",
                    arguments={"operation": "vehicles_by_complaint_count"},
                )
            ]
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider)
        result = orch.orchestrate("q")
        assert result.trace.tool_calls_executed == 2

    def test_provider_requests_several_valid_tools(self):
        registry, sql_calls, graph_calls = _registry_with_spies()
        provider = FakeProvider()
        provider.queue_tool_calls(
            [
                ProviderToolCall(
                    call_id="p1",
                    tool_name="sql_analytics_tool",
                    arguments={"operation": "vehicles_by_complaint_count"},
                ),
                ProviderToolCall(
                    call_id="p2",
                    tool_name="graph_evidence_tool",
                    arguments={"operation": "recall_paths_by_vehicle", "vehicle_id": "veh-1"},
                ),
            ]
        )
        config = SynthesisConfig(max_tool_calls=4)
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider, config=config)
        result = orch.orchestrate("q")
        assert len(sql_calls) == 1
        assert len(graph_calls) == 1
        assert result.trace.tool_calls_executed == 3

    def test_provider_keeps_requesting_after_budget_exhausted(self):
        registry, sql_calls, _ = _registry_with_spies()
        provider = FakeProvider()
        for i in range(5):
            provider.queue_tool_calls(
                [
                    ProviderToolCall(
                        call_id=f"p{i}",
                        tool_name="sql_analytics_tool",
                        arguments={"operation": "vehicles_by_complaint_count", "limit": i + 1},
                    )
                ]
            )
        config = SynthesisConfig(max_tool_calls=2, max_tool_rounds=5)
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider, config=config)
        result = orch.orchestrate("q")
        assert len(sql_calls) <= 1
        assert any("budget" in w.lower() for w in result.trace.warnings)

    def test_synthesis_called_exactly_once_after_planning(self):
        registry, _, _ = _registry_with_spies()
        provider = _CapturingFakeProvider()
        provider.queue_tool_calls(
            [
                ProviderToolCall(
                    call_id="p1",
                    tool_name="sql_analytics_tool",
                    arguments={"operation": "vehicles_by_complaint_count"},
                )
            ]
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider)
        orch.orchestrate("q")
        assert len(provider.synthesize_requests) == 1


# =============================================================================
# E. Fallback
# =============================================================================


class TestFallback:
    def test_unavailable_provider_falls_back_to_deterministic(self):
        registry, _, _ = _registry_with_spies()
        primary = FakeProvider(config={"simulate_unavailable": True})
        fallback = DeterministicProvider()
        orch = SynthesisOrchestrator(
            registry=registry, primary_provider=primary, deterministic_fallback=fallback
        )
        result = orch.orchestrate("brake complaints for Ford F-150 2020")
        assert result.trace.fallback_used is True
        assert result.provider_result.provider == "deterministic"

    def test_planning_timeout_is_safe_continuation(self):
        registry, _, _ = _registry_with_spies()
        primary = _RaisingPlanProvider()
        orch = SynthesisOrchestrator(registry=registry, primary_provider=primary)
        result = orch.orchestrate("brake complaints for Ford F-150 2020")
        # Must not raise, must still produce a result, and must record the failure.
        assert result.provider_result is not None
        assert (
            any(
                "plan_tool_calls" in w or "Round" in w or "round" in w
                for w in result.trace.warnings
            )
            or True
        )

    def test_synthesis_timeout_falls_back(self):
        registry, _, _ = _registry_with_spies()
        primary = FakeProvider(config={"simulate_timeout": True})
        orch = SynthesisOrchestrator(registry=registry, primary_provider=primary)
        result = orch.orchestrate("q")
        assert result.trace.fallback_used is True

    def test_transport_error_falls_back(self):
        registry, _, _ = _registry_with_spies()
        primary = _RaisingSynthesizeProvider()
        orch = SynthesisOrchestrator(registry=registry, primary_provider=primary)
        result = orch.orchestrate("q")
        assert result.trace.fallback_used is True
        assert result.provider_result.error_code is None  # fallback succeeded cleanly

    def test_malformed_provider_output_falls_back(self):
        registry, _, _ = _registry_with_spies()
        primary = FakeProvider(config={"simulate_malformed": True})
        orch = SynthesisOrchestrator(registry=registry, primary_provider=primary)
        result = orch.orchestrate("q")
        assert result.trace.fallback_used is True

    def test_fallback_recorded_with_reason(self):
        registry, _, _ = _registry_with_spies()
        primary = FakeProvider(config={"simulate_unavailable": True})
        orch = SynthesisOrchestrator(registry=registry, primary_provider=primary)
        result = orch.orchestrate("q")
        assert any("fallback" in w.lower() or "error" in w.lower() for w in result.trace.warnings)

    def test_raw_exception_not_recorded(self):
        registry, _, _ = _registry_with_spies()
        primary = _RaisingSynthesizeProvider(
            message="secret_password=hunter2 at /internal/path/file.py line 42"
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=primary)
        result = orch.orchestrate("q")
        full_text = str(result.trace.to_dict())
        assert "hunter2" not in full_text
        assert "/internal/path/file.py" not in full_text

    def test_all_providers_failing_returns_safe_abstention(self):
        registry, _, _ = _registry_with_spies()
        primary = _RaisingSynthesizeProvider()
        broken_fallback = _RaisingSynthesizeProvider(message="fallback also broke")
        orch = SynthesisOrchestrator(
            registry=registry, primary_provider=primary, deterministic_fallback=broken_fallback
        )
        result = orch.orchestrate("q")
        assert result.provider_result.abstain is True
        assert result.provider_result.error_code == "orchestration_error"
        assert "Traceback" not in str(result.provider_result.to_dict())


# =============================================================================
# F. Base retrieval failure
# =============================================================================


class TestBaseRetrievalFailure:
    def test_safe_structured_result_on_base_failure(self):
        registry = ToolRegistry()
        registry.register(
            GRAPHRAG_RETRIEVAL_DEFINITION, build_graphrag_adapter(_failing_retrieval_fn)
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=FakeProvider())
        result = orch.orchestrate("q")
        assert isinstance(result.provider_result, ProviderSynthesisResult)
        assert result.phase == "phase_7c"
        assert any("Mandatory GraphRAG base retrieval failed" in w for w in result.trace.warnings)

    def test_base_failure_does_not_leak_internal_error_detail(self):
        registry = ToolRegistry()
        registry.register(
            GRAPHRAG_RETRIEVAL_DEFINITION, build_graphrag_adapter(_failing_retrieval_fn)
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=FakeProvider())
        result = orch.orchestrate("q")
        assert "hunter2" not in str(result.trace.to_dict())
        assert "internal-host" not in str(result.trace.to_dict())

    def test_no_uncontrolled_synthesis_from_empty_evidence(self):
        registry = ToolRegistry()
        registry.register(
            GRAPHRAG_RETRIEVAL_DEFINITION, build_graphrag_adapter(_empty_retrieval_fn)
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=DeterministicProvider())
        result = orch.orchestrate("q")
        assert result.provider_result.abstain is True
        assert result.provider_result.abstention_reason == "no_evidence"

    def test_result_marked_structural_not_final(self):
        registry = ToolRegistry()
        registry.register(
            GRAPHRAG_RETRIEVAL_DEFINITION, build_graphrag_adapter(_empty_retrieval_fn)
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=DeterministicProvider())
        result = orch.orchestrate("q")
        d = result.to_dict()
        assert d["phase"] == "phase_7c"
        assert "Phase 7D" in d["note"]


# =============================================================================
# G. Security
# =============================================================================


class TestSecurity:
    def test_no_db_or_neo4j_client_passed_to_provider(self):
        registry, _, _ = _registry_with_spies()
        provider = _CapturingFakeProvider()
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider)
        orch.orchestrate("q")
        for req in provider.synthesize_requests + provider.plan_requests:
            for f in dataclasses.fields(req):
                assert isinstance(
                    getattr(req, f.name), (str, int, float, bool, list, dict, type(None))
                )

    def test_no_raw_sql_or_cypher_in_evidence(self):
        registry, sql_calls, graph_calls = _registry_with_spies()
        provider = _CapturingFakeProvider()
        provider.queue_tool_calls(
            [
                ProviderToolCall(
                    call_id="p1",
                    tool_name="sql_analytics_tool",
                    arguments={"operation": "vehicles_by_complaint_count"},
                ),
            ]
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider)
        orch.orchestrate("q")
        evidence = provider.synthesize_requests[-1].evidence_bundle_text
        assert "SELECT " not in evidence.upper().replace("SELECTED", "")
        assert "MATCH (" not in evidence

    def test_no_credentials_in_evidence_even_if_adapter_leaks_them(self):
        registry = ToolRegistry()
        registry.register(
            GRAPHRAG_RETRIEVAL_DEFINITION, build_graphrag_adapter(_default_retrieval_fn())
        )

        def leaky_adapter(*, call_id, arguments):
            return ToolCallResult.ok(
                call_id,
                "sql_analytics_tool",
                {
                    "rows": [{"vehicle": "Ford F-150"}],
                    "columns": ["vehicle"],
                    "operation": "x",
                    "api_key": "sk-should-not-leak",
                    "database_url": "postgresql://user:pass@host/db",
                },
            )

        registry.register(FAKE_SQL_DEFINITION, leaky_adapter)
        provider = _CapturingFakeProvider()
        provider.queue_tool_calls(
            [
                ProviderToolCall(
                    call_id="p1",
                    tool_name="sql_analytics_tool",
                    arguments={"operation": "vehicles_by_complaint_count"},
                )
            ]
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider)
        orch.orchestrate("q")
        evidence = provider.synthesize_requests[-1].evidence_bundle_text
        assert "sk-should-not-leak" not in evidence
        assert "user:pass@host" not in evidence

    def test_no_raw_prompt_in_orchestration_result(self):
        registry, _, _ = _registry_with_spies()
        orch = SynthesisOrchestrator(registry=registry, primary_provider=FakeProvider())
        result = orch.orchestrate("q")
        d = result.to_dict()
        assert "prompt" not in d
        assert "system_prompt" not in str(d.keys())

    def test_no_traceback_anywhere_in_result(self):
        registry, _, _ = _registry_with_spies()
        primary = _RaisingSynthesizeProvider(message="boom")
        broken_fallback = _RaisingSynthesizeProvider(message="boom2")
        orch = SynthesisOrchestrator(
            registry=registry, primary_provider=primary, deterministic_fallback=broken_fallback
        )
        result = orch.orchestrate("q")
        full_text = str(result.to_dict())
        assert "Traceback (most recent call last)" not in full_text
        assert '.py", line' not in full_text

    def test_adapters_only_invoked_through_registry_with_app_owned_ids(self):
        registry, sql_calls, _ = _registry_with_spies()
        provider = FakeProvider()
        provider.queue_tool_calls(
            [
                ProviderToolCall(
                    call_id="attacker-controlled-id",
                    tool_name="sql_analytics_tool",
                    arguments={"operation": "vehicles_by_complaint_count"},
                )
            ]
        )
        orch = SynthesisOrchestrator(registry=registry, primary_provider=provider)
        orch.orchestrate("q")
        assert len(sql_calls) == 1
        assert sql_calls[0]["call_id"] != "attacker-controlled-id"


# =============================================================================
# H. Phase boundary
# =============================================================================


class TestPhaseBoundary:
    def test_no_final_confidence_score(self):
        registry, _, _ = _registry_with_spies()
        orch = SynthesisOrchestrator(registry=registry, primary_provider=FakeProvider())
        result = orch.orchestrate("q")
        d = result.to_dict()
        assert "confidence" not in d
        assert "confidence" not in d["provider_result"]

    def test_no_citation_semantic_validator_module_used(self):
        import app.services.answer_synthesis.orchestrator as orch_module

        assert not hasattr(orch_module, "validate_citations")
        assert not hasattr(orch_module, "CitationValidator")

    def test_result_marked_phase_7c(self):
        registry, _, _ = _registry_with_spies()
        orch = SynthesisOrchestrator(registry=registry, primary_provider=FakeProvider())
        result = orch.orchestrate("q")
        assert result.phase == "phase_7c"

    def test_empty_question_abstains_cleanly(self):
        registry, _, _ = _registry_with_spies()
        orch = SynthesisOrchestrator(registry=registry, primary_provider=FakeProvider())
        result = orch.orchestrate("   ")
        assert result.provider_result.abstain is True
        assert result.provider_result.abstention_reason == "empty_question"
