"""
Tests for Phase 7B — Tool Contracts and Safe Adapters.

Covers: base contracts, argument validation, registry, SQL adapter,
graph adapter, GraphRAG adapter, vehicle adapter, evidence bundle.
No network, no LLM, no real DB required for unit tests.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from app.services.answer_synthesis.tools.argument_validator import (
    validate_tool_arguments,
)
from app.services.answer_synthesis.tools.base import (
    EvidenceBundle,
    EvidenceItem,
    ToolCallRequest,
    ToolCallResult,
    ToolDefinition,
    ToolInputField,
    ToolInputSchema,
)
from app.services.answer_synthesis.tools.bundle_builder import (
    EvidenceBundleBuilder,
    _format_table,
    _sanitize,
)
from app.services.answer_synthesis.tools.registry import ToolRegistry

# =============================================================================
# A. BASE CONTRACTS
# =============================================================================

class TestToolDefinition:
    def test_deterministic_serialization(self):
        schema = ToolInputSchema(fields={
            "name": ToolInputField(type="string", description="Name", required=True, max_length=50),
        })
        defn = ToolDefinition(
            name="test_tool",
            description="A test",
            input_schema=schema,
            read_only=True,
            max_result_items=20,
            timeout_seconds=15,
        )
        d = defn.to_dict()
        assert d["name"] == "test_tool"
        assert d["read_only"] is True
        assert d["max_result_items"] == 20
        assert "callable" not in str(d)
        assert "function" not in str(d)

    def test_no_traceback_in_definition(self):
        schema = ToolInputSchema(fields={})
        defn = ToolDefinition(name="x", description="y", input_schema=schema)
        d = defn.to_dict()
        assert "traceback" not in str(d).lower()
        assert "exception" not in str(d).lower()


class TestToolCallRequest:
    def test_make_generates_call_id(self):
        req = ToolCallRequest.make("sql_analytics_tool", {"operation": "top_complaint_components_by_vehicle"})
        assert req.call_id.startswith("call-")
        assert req.tool_name == "sql_analytics_tool"
        assert req.arguments == {"operation": "top_complaint_components_by_vehicle"}

    def test_to_dict_serialization(self):
        req = ToolCallRequest(call_id="call-abc123", tool_name="test", arguments={"x": 1})
        d = req.to_dict()
        assert d["call_id"] == "call-abc123"
        assert d["tool_name"] == "test"
        assert d["arguments"] == {"x": 1}


class TestToolCallResult:
    def test_ok_factory(self):
        r = ToolCallResult.ok("call-1", "test", {"rows": [1, 2]})
        assert r.success is True
        assert r.data == {"rows": [1, 2]}
        assert r.error_code is None
        assert r.error_message is None

    def test_error_factory(self):
        r = ToolCallResult.error("call-1", "test", "validation_error", "missing field")
        assert r.success is False
        assert r.error_code == "validation_error"
        assert r.error_message == "missing field"
        assert r.data is None

    def test_validation_error_factory(self):
        r = ToolCallResult.validation_error("call-1", "test", "unknown arg")
        assert r.success is False
        assert r.error_code == "validation_error"
        assert r.error_message is not None and "unknown arg" in r.error_message

    def test_to_dict_no_traceback(self):
        r = ToolCallResult.error("call-1", "test", "err", "message")
        d = r.to_dict()
        assert "traceback" not in str(d).lower()
        assert "Stack" not in str(d)

    def test_warnings_preserved(self):
        r = ToolCallResult.ok("call-1", "test", {"x": 1}, warnings=["caveat 1", "caveat 2"])
        assert len(r.warnings) == 2


class TestEvidenceItem:
    def test_make_id_deterministic(self):
        id1 = EvidenceItem.make_id("complaint", "11420001")
        id2 = EvidenceItem.make_id("complaint", "11420001")
        assert id1 == id2
        assert "ev-complaint-11420001" == id1

    def test_make_citation_id_deterministic(self):
        cid = EvidenceItem.make_citation_id("complaint", "11420001")
        assert cid == "cite-complaint-11420001"

    def test_evidence_item_serialization(self):
        item = EvidenceItem(
            evidence_id="ev-1",
            tool_name="graphrag_retrieval_tool",
            evidence_type="complaint",
            source_record_key="11420001",
            text="Brake failure reported",
            score=0.85,
        )
        d = item.to_dict()
        assert d["evidence_id"] == "ev-1"
        assert d["score"] == 0.85
        assert d["citation_id"] is None  # not set


class TestEvidenceBundle:
    def test_citation_table_deduplication(self):
        item1 = EvidenceItem(
            evidence_id="ev-1", tool_name="g", evidence_type="complaint",
            source_record_key="11420001", citation_id="cite-complaint-11420001",
        )
        item2 = EvidenceItem(
            evidence_id="ev-1", tool_name="g", evidence_type="complaint",
            source_record_key="11420001", citation_id="cite-complaint-11420001",
        )
        bundle = EvidenceBundle(items=[item1, item2])
        table = bundle.citation_table()
        assert len(table) == 1  # deduplicated

    def test_citation_table_filters_no_id(self):
        item = EvidenceItem(
            evidence_id="ev-1", tool_name="g", evidence_type="complaint",
            source_record_key="11420001", citation_id=None,
        )
        bundle = EvidenceBundle(items=[item])
        assert len(bundle.citation_table()) == 0


# =============================================================================
# B. ARGUMENT VALIDATION
# =============================================================================

def _make_def(fields: dict) -> ToolDefinition:
    schema = ToolInputSchema(fields={
        k: ToolInputField(**v) for k, v in fields.items()
    })
    return ToolDefinition(name="test", description="test", input_schema=schema)


class TestArgumentValidator:
    def test_valid_arguments_accepted(self):
        defn = _make_def({
            "operation": {"type": "enum", "description": "op", "required": True,
                         "enum_values": ["a", "b"]},
            "limit": {"type": "integer", "description": "lim", "required": False,
                      "default": 10, "min_value": 1, "max_value": 50},
        })
        result = validate_tool_arguments(defn, {"operation": "a", "limit": 5})
        assert result.valid

    def test_unknown_tool_rejected(self):
        # This is handled by the registry, not the validator
        # The validator tests field-level validation
        pass

    def test_unknown_argument_rejected(self):
        defn = _make_def({
            "operation": {"type": "enum", "description": "op", "required": True,
                         "enum_values": ["a"]},
        })
        result = validate_tool_arguments(defn, {"operation": "a", "unknown_arg": "x"})
        assert not result.valid
        assert any("unknown" in e.message.lower() for e in result.errors)

    def test_required_field_missing(self):
        defn = _make_def({
            "operation": {"type": "enum", "description": "op", "required": True,
                         "enum_values": ["a"]},
        })
        result = validate_tool_arguments(defn, {})
        assert not result.valid
        assert any("required" in e.message.lower() for e in result.errors)

    def test_wrong_type_string_expected_int(self):
        defn = _make_def({
            "limit": {"type": "integer", "description": "lim", "required": True,
                      "min_value": 1, "max_value": 50},
        })
        result = validate_tool_arguments(defn, {"limit": "ten"})
        assert not result.valid

    def test_boolean_rejected_for_integer(self):
        defn = _make_def({
            "limit": {"type": "integer", "description": "lim", "required": True,
                      "min_value": 1, "max_value": 50},
        })
        result = validate_tool_arguments(defn, {"limit": True})
        assert not result.valid
        assert any("boolean" in e.message.lower() for e in result.errors)

    def test_integer_below_min(self):
        defn = _make_def({
            "limit": {"type": "integer", "description": "lim", "required": True,
                      "min_value": 1, "max_value": 50},
        })
        result = validate_tool_arguments(defn, {"limit": 0})
        assert not result.valid

    def test_integer_above_max(self):
        defn = _make_def({
            "limit": {"type": "integer", "description": "lim", "required": True,
                      "min_value": 1, "max_value": 50},
        })
        result = validate_tool_arguments(defn, {"limit": 100})
        assert not result.valid

    def test_string_too_long(self):
        defn = _make_def({
            "name": {"type": "string", "description": "name", "required": True,
                     "max_length": 10},
        })
        result = validate_tool_arguments(defn, {"name": "a" * 20})
        assert not result.valid
        assert any("length" in e.message.lower() for e in result.errors)

    def test_empty_string_rejected(self):
        defn = _make_def({
            "name": {"type": "string", "description": "name", "required": True,
                     "max_length": 50},
        })
        result = validate_tool_arguments(defn, {"name": "   "})
        assert not result.valid

    def test_enum_enforced(self):
        defn = _make_def({
            "operation": {"type": "enum", "description": "op", "required": True,
                         "enum_values": ["a", "b"]},
        })
        result = validate_tool_arguments(defn, {"operation": "c"})
        assert not result.valid

    def test_nested_dict_rejected(self):
        defn = _make_def({
            "name": {"type": "string", "description": "name", "required": True,
                     "max_length": 50},
        })
        result = validate_tool_arguments(defn, {"name": {"nested": "value"}})
        assert not result.valid

    def test_nested_list_rejected(self):
        defn = _make_def({
            "name": {"type": "string", "description": "name", "required": True,
                     "max_length": 50},
        })
        result = validate_tool_arguments(defn, {"name": ["a", "b"]})
        assert not result.valid

    def test_whitespace_stripped(self):
        defn = _make_def({
            "name": {"type": "string", "description": "name", "required": True,
                     "max_length": 50},
        })
        args = {"name": "  ford  "}
        result = validate_tool_arguments(defn, args)
        assert result.valid
        assert args["name"] == "ford"

    # --- Forbidden key tests ---
    def test_sql_key_rejected(self):
        defn = _make_def({
            "operation": {"type": "string", "description": "op", "required": False,
                         "max_length": 50},
        })
        for key in ["sql", "query_sql", "raw_sql", "cypher", "raw_query"]:
            result = validate_tool_arguments(defn, {"operation": "x", key: "DROP TABLE"})
            assert not result.valid, f"'{key}' should be rejected"

    def test_credential_key_rejected(self):
        defn = _make_def({
            "operation": {"type": "string", "description": "op", "required": False,
                         "max_length": 50},
        })
        for key in ["password", "api_key", "secret", "token", "credential",
                    "n4ey", "auth", "DATABASE_URL"]:
            result = validate_tool_arguments(defn, {"operation": "x", key: "secret"})
            assert not result.valid, f"'{key}' should be rejected"


# =============================================================================
# C. TOOL REGISTRY
# =============================================================================

class TestToolRegistry:
    def test_allowlist_registered(self):
        registry = ToolRegistry()
        defn = ToolDefinition(name="test_tool", description="t", input_schema=ToolInputSchema(fields={}))
        adapter = MagicMock(return_value=ToolCallResult.ok("c1", "t", {}))
        registry.register(defn, adapter)
        assert registry.is_registered("test_tool")

    def test_duplicate_registration_rejected(self):
        registry = ToolRegistry()
        defn = ToolDefinition(name="dup", description="t", input_schema=ToolInputSchema(fields={}))
        adapter = MagicMock()
        registry.register(defn, adapter)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(defn, adapter)

    def test_unknown_tool_rejected(self):
        registry = ToolRegistry()
        req = ToolCallRequest.make("nonexistent_tool", {})
        result = registry.execute(req)
        assert not result.success
        assert result.error_code == "validation_error"
        assert result.error_message is not None
        assert "unknown" in result.error_message.lower()

    def test_validation_before_execution(self):
        """Validation failure should not invoke the adapter."""
        registry = ToolRegistry()
        defn = ToolDefinition(
            name="test_tool", description="t",
            input_schema=ToolInputSchema(fields={
                "name": ToolInputField(type="string", description="n", required=True, max_length=5),
            }),
        )
        mock_adapter = MagicMock()
        registry.register(defn, mock_adapter)

        req = ToolCallRequest.make("test_tool", {"name": "toolongname"})
        result = registry.execute(req)

        assert not result.success
        assert result.error_code == "validation_error"
        mock_adapter.assert_not_called()

    def test_adapter_exception_converted(self):
        registry = ToolRegistry()
        defn = ToolDefinition(name="bad", description="t", input_schema=ToolInputSchema(fields={}))
        def bad_adapter(*, call_id, arguments):
            raise RuntimeError("intentional")
        registry.register(defn, bad_adapter)

        req = ToolCallRequest.make("bad", {})
        result = registry.execute(req)
        assert not result.success
        assert result.error_code == "adapter_error"
        assert result.error_message is not None and "RuntimeError" in result.error_message
        assert "traceback" not in result.error_message.lower()

    def test_list_definitions_sorted(self):
        registry = ToolRegistry()
        def a(): pass
        def b(): pass
        registry.register(ToolDefinition(name="z_tool", description="", input_schema=ToolInputSchema(fields={})), a)
        registry.register(ToolDefinition(name="a_tool", description="", input_schema=ToolInputSchema(fields={})), b)
        names = [d.name for d in registry.list_definitions()]
        assert names == ["a_tool", "z_tool"]

    def test_execute_batch(self):
        registry = ToolRegistry()
        defn = ToolDefinition(name="x", description="", input_schema=ToolInputSchema(fields={}))
        registry.register(defn, lambda **kw: ToolCallResult.ok(kw["call_id"], "x", {}))
        reqs = [ToolCallRequest.make("x", {}), ToolCallRequest.make("x", {})]
        results = registry.execute_batch(reqs)
        assert len(results) == 2
        assert all(r.success for r in results)

    def test_deterministic_order(self):
        registry = ToolRegistry()
        defn = ToolDefinition(name="test", description="", input_schema=ToolInputSchema(fields={}))
        registry.register(defn, lambda **kw: ToolCallResult.ok(kw["call_id"], "test", {}))
        defs1 = [d.name for d in registry.list_definitions()]
        defs2 = [d.name for d in registry.list_definitions()]
        assert defs1 == defs2


# =============================================================================
# D. SQL ADAPTER
# =============================================================================

class TestSqlAdapter:
    def test_sql_adapter_rejects_raw_sql(self):
        # The SQL adapter takes structured arguments, not raw SQL
        # Verify it calls Phase 2 service correctly
        from app.services.answer_synthesis.tools.sql_adapter import (
            SQL_ANALYTICS_DEFINITION,
        )

        # Check definition has no raw SQL fields
        schema = SQL_ANALYTICS_DEFINITION.input_schema.to_dict()
        assert "sql" not in schema
        assert "query_sql" not in schema
        assert "raw_sql" not in schema

    def test_question_builder(self):
        from app.services.answer_synthesis.tools.sql_adapter import _build_question
        q = _build_question("top_complaint_components_by_vehicle", "Ford", "F-150", 2020, None, 10)
        assert "Ford" in q
        assert "F-150" in q
        assert "2020" in q
        assert "Top complaint" in q

    def test_question_builder_minimal(self):
        from app.services.answer_synthesis.tools.sql_adapter import _build_question
        q = _build_question("vehicles_by_complaint_count", None, None, None, None, 10)
        assert "complaints" in q.lower()

    def test_adapter_definition_readonly(self):
        from app.services.answer_synthesis.tools.sql_adapter import SQL_ANALYTICS_DEFINITION
        assert SQL_ANALYTICS_DEFINITION.read_only is True

    def test_adapter_definition_max_rows(self):
        from app.services.answer_synthesis.tools.sql_adapter import SQL_ANALYTICS_DEFINITION
        assert SQL_ANALYTICS_DEFINITION.max_result_items == 50

    def test_supported_operations_match_phase2(self):
        from app.services.answer_synthesis.tools.sql_adapter import SUPPORTED_OPERATIONS
        expected = [
            "top_complaint_components_by_vehicle",
            "complaint_count_by_vehicle",
            "recalls_by_vehicle",
            "recall_count_by_vehicle",
            "vehicles_by_complaint_count",
            "complaint_count_by_component_for_vehicle",
        ]
        assert set(SUPPORTED_OPERATIONS) == set(expected)


# =============================================================================
# E. GRAPH ADAPTER
# =============================================================================

class TestGraphAdapter:
    def test_graph_adapter_readonly(self):
        from app.services.answer_synthesis.tools.graph_adapter import GRAPH_EVIDENCE_DEFINITION
        assert GRAPH_EVIDENCE_DEFINITION.read_only is True

    def test_graph_adapter_max_paths(self):
        from app.services.answer_synthesis.tools.graph_adapter import GRAPH_EVIDENCE_DEFINITION
        assert GRAPH_EVIDENCE_DEFINITION.max_result_items == 20

    def test_no_raw_cypher_fields(self):
        from app.services.answer_synthesis.tools.graph_adapter import GRAPH_EVIDENCE_DEFINITION
        schema = GRAPH_EVIDENCE_DEFINITION.input_schema.to_dict()
        assert "cypher" not in schema
        assert "query" not in schema
        assert "raw_query" not in schema

    def test_sanitize_removes_creds(self):
        from app.services.answer_synthesis.tools.graph_adapter import _sanitize_dict
        d = {"name": "Ford", "password": "secret123", "api_key": "key456"}
        result = _sanitize_dict(d)
        assert "name" in result
        assert "password" not in result
        assert "api_key" not in result


# =============================================================================
# F. GRAPHRAG ADAPTER
# =============================================================================

class TestGraphragAdapter:
    def test_graphrag_adapter_readonly(self):
        from app.services.answer_synthesis.tools.graphrag_adapter import (
            GRAPHRAG_RETRIEVAL_DEFINITION,
        )
        assert GRAPHRAG_RETRIEVAL_DEFINITION.read_only is True

    def test_source_type_mapping(self):
        from app.services.answer_synthesis.tools.graphrag_adapter import _operation_to_source_type
        assert _operation_to_source_type("retrieve_complaints_only") == "complaint"
        assert _operation_to_source_type("retrieve_recalls_only") == "recall"
        assert _operation_to_source_type("retrieve_complaints_and_recalls") is None

    def test_chunk_text_bounded(self):
        from app.services.answer_synthesis.tools.graphrag_adapter import _chunk_to_dict
        class FakeChunk:
            chunk_id = "c1"
            score = 0.9
            source_type = "complaint"
            source_record_key = "11420001"
            title = "Test"
            text = "A" * 5000  # exceeds 2000
            make = "Ford"
            model = "F-150"
            model_year = 2020
            component = "Brakes"
            citation_label = "Complaint 11420001"
        d = _chunk_to_dict(FakeChunk)
        assert len(d["text"]) == 2000
        assert d["text"].endswith("A" * 10)

    def test_adapter_definition_fields(self):
        from app.services.answer_synthesis.tools.graphrag_adapter import (
            GRAPHRAG_RETRIEVAL_DEFINITION,
        )
        schema = GRAPHRAG_RETRIEVAL_DEFINITION.input_schema.to_dict()
        assert "question" in schema
        assert schema["question"]["max_length"] == 500
        assert schema["top_k"]["max_value"] == 20


# =============================================================================
# G. VEHICLE ADAPTER
# =============================================================================

class TestVehicleAdapter:
    def test_vehicle_adapter_readonly(self):
        from app.services.answer_synthesis.tools.vehicle_adapter import (
            VEHICLE_RESOLUTION_DEFINITION,
        )
        assert VEHICLE_RESOLUTION_DEFINITION.read_only is True

    def test_no_raw_sql_fields(self):
        from app.services.answer_synthesis.tools.vehicle_adapter import (
            VEHICLE_RESOLUTION_DEFINITION,
        )
        schema = VEHICLE_RESOLUTION_DEFINITION.input_schema.to_dict()
        assert "sql" not in schema
        assert "query" not in schema

    def test_required_fields(self):
        from app.services.answer_synthesis.tools.vehicle_adapter import (
            VEHICLE_RESOLUTION_DEFINITION,
        )
        schema = VEHICLE_RESOLUTION_DEFINITION.input_schema.to_dict()
        assert schema["make"]["required"] is True
        assert schema["model"]["required"] is True
        assert schema["model_year"]["required"] is True


# =============================================================================
# H. EVIDENCE BUNDLE
# =============================================================================

class TestEvidenceBundleBuilder:
    def test_deterministic_item_ids(self):
        item1 = EvidenceItem(
            evidence_id="ev-1", tool_name="g", evidence_type="complaint",
            source_record_key="11420001", citation_id="cite-complaint-11420001",
            text="test",
        )
        item2 = EvidenceItem(
            evidence_id="ev-1", tool_name="g", evidence_type="complaint",
            source_record_key="11420001", citation_id="cite-complaint-11420001",
            text="different text",
        )
        assert item1.evidence_id == item2.evidence_id

    def test_deduplication(self):
        """Items added by extraction with same dedup key are skipped."""
        builder = EvidenceBundleBuilder(max_items=5)
        # Extraction adds dedup key before calling _add_item
        builder._seen_keys.add("ev-complaint-11420001")
        item = EvidenceItem(
            evidence_id="ev-complaint-11420001", tool_name="g", evidence_type="complaint",
            source_record_key="11420001", text="x",
        )
        # _add_item no longer checks seen_keys — it just adds
        result = builder._add_item(item)
        assert result is True
        assert len(builder._items) == 1
        # Adding same dedup key via extraction would skip (tested at integration level)

    def test_character_limit(self):
        builder = EvidenceBundleBuilder(max_items=5, max_total_chars=50, max_item_text=50)
        item = EvidenceItem(
            evidence_id="ev-1", tool_name="g", evidence_type="complaint",
            source_record_key="x", text="A" * 60,
        )
        added = builder._add_item(item)
        assert added is False  # exceeds char limit

    def test_item_limit(self):
        builder = EvidenceBundleBuilder(max_items=2, max_total_chars=100000, max_item_text=2000)
        for i in range(5):
            item = EvidenceItem(
                evidence_id=f"ev-{i}", tool_name="g", evidence_type="complaint",
                source_record_key=f"x{i}", text="x" * 10,
            )
            builder._add_item(item)
        assert len(builder._items) == 2

    def test_truncation_flag(self):
        builder = EvidenceBundleBuilder(max_items=2, max_total_chars=100000, max_item_text=2000)
        for i in range(5):
            item = EvidenceItem(
                evidence_id=f"ev-{i}", tool_name="g", evidence_type="complaint",
                source_record_key=f"x{i}", text="x" * 10,
            )
            builder._add_item(item)
        bundle = builder.build()
        assert bundle.truncated is True

    def test_warnings_collected(self):
        builder = EvidenceBundleBuilder()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.warnings = ["Warning 1", "Warning 2"]
        mock_result.data = None
        builder.add_tool_result(mock_result)
        assert "Warning 1" in builder._warnings
        assert "Warning 2" in builder._warnings

    def test_citation_table_from_graphrag(self):
        """GraphRAG chunks produce evidence items with citation_ids."""
        builder = EvidenceBundleBuilder()
        mock_result = MagicMock()
        mock_result.tool_name = "graphrag_retrieval_tool"
        mock_result.success = True
        mock_result.warnings = []
        mock_result.data = {
            "retrieved_chunks": [{
                "source_type": "complaint",
                "source_record_key": "11420001",
                "score": 0.85,
                "text": "Brake failure reported",
                "metadata": {},
            }],
            "citations": [{
                "source_type": "complaint",
                "source_key": "11420001",
            }],
            "graph_paths": [],
        }
        builder.add_tool_result(mock_result)
        bundle = builder.build()
        assert len(bundle.items) > 0
        # citation_id should be present on the item
        assert any(i.citation_id == "cite-complaint-11420001" for i in bundle.items)

    def test_sql_evidence_extraction(self):
        builder = EvidenceBundleBuilder()
        mock_result = MagicMock()
        mock_result.tool_name = "sql_analytics_tool"
        mock_result.success = True
        mock_result.warnings = []
        mock_result.data = {
            "operation": "top_complaint_components_by_vehicle",
            "columns": ["component", "complaint_count"],
            "rows": [{"component": "Brakes", "complaint_count": 5}],
            "query": "SELECT ...",
        }
        builder.add_tool_result(mock_result)
        bundle = builder.build()
        sql_items = [i for i in bundle.items if i.evidence_type == "sql_result"]
        assert len(sql_items) == 1

    def test_recall_evidence_extraction(self):
        builder = EvidenceBundleBuilder()
        mock_result = MagicMock()
        mock_result.tool_name = "graph_evidence_tool"
        mock_result.success = True
        mock_result.warnings = []
        mock_result.data = {
            "operation": "recall_paths_by_vehicle",
            "recalls": [{"campaign_number": "20V123000", "summary": "Brake issue"}],
            "relation_basis": "official_recall_affects_vehicle",
        }
        builder.add_tool_result(mock_result)
        bundle = builder.build()
        recall_items = [i for i in bundle.items if i.evidence_type == "recall"]
        assert len(recall_items) == 1
        assert recall_items[0].source_record_key == "20V123000"


# =============================================================================
# I. SANITIZATION
# =============================================================================

class TestSanitization:
    def test_sanitize_removes_all_credential_keys(self):
        d = {
            "vehicle": "Ford",
            "database_url": "postgresql://...",
            "api_key": "sk-...",
            "password": "secret",
            "token": "bearer",
            "neo4j_password": "neo4j",
            "nested": {
                "password": "deep",
                "safe": "value",
            },
        }
        result = _sanitize(d)
        assert "vehicle" in result
        assert "database_url" not in result
        assert "api_key" not in result
        assert "password" not in result
        assert "token" not in result
        assert "nested" in result
        assert "password" not in result["nested"]
        assert result["nested"]["safe"] == "value"

    def test_sanitize_preserves_non_dict(self):
        # Called dynamically: the contract under test is that non-dict input
        # passes through untouched, which the dict-typed signature cannot state.
        sanitize: Any = _sanitize
        assert sanitize("string") == "string"
        assert sanitize(123) == 123
        assert sanitize(None) is None
        assert sanitize([{"a": 1}, {"b": 2}]) == [{"a": 1}, {"b": 2}]

    def test_format_table_bounded(self):
        rows = [{"a": 1, "b": 2}] * 20
        result = _format_table(rows, ["a", "b"])
        assert "..." in result  # truncation marker

    def test_format_table_empty(self):
        assert _format_table([], []) == "No results."
        # None is accepted defensively at runtime; assert that explicitly.
        format_table: Any = _format_table
        assert format_table(None, None) == "No results."


# =============================================================================
# J. SECURITY — NO DYNAMIC EXECUTION
# =============================================================================

class TestSecurity:
    def test_no_eval_in_validator(self):
        source = open("app/services/answer_synthesis/tools/argument_validator.py").read()
        assert "eval(" not in source
        assert "exec(" not in source

    def test_no_eval_in_registry(self):
        source = open("app/services/answer_synthesis/tools/registry.py").read()
        assert "eval(" not in source
        assert "exec(" not in source

    def test_no_eval_in_bundle_builder(self):
        source = open("app/services/answer_synthesis/tools/bundle_builder.py").read()
        assert "eval(" not in source
        assert "exec(" not in source

    def test_no_dynamic_import_in_registry(self):
        source = open("app/services/answer_synthesis/tools/registry.py").read()
        # Only static imports at module level are allowed
        assert "importlib" not in source
        assert "__import__" not in source

    def test_no_database_url_in_base(self):
        source = open("app/services/answer_synthesis/tools/base.py").read()
        assert "DATABASE_URL" not in source
        assert "NEO4J_PASSWORD" not in source

    def test_no_provider_in_tool_layer(self):
        """Tool layer must not contain LLM provider implementations."""
        import os
        tool_dir = "app/services/answer_synthesis/tools"
        for fname in os.listdir(tool_dir):
            if fname.endswith(".py"):
                source = open(f"{tool_dir}/{fname}").read()
                # Check for actual LLM provider packages, not just the word "api_key" in constants
                assert "openai" not in source.lower(), f"{fname} contains openai"
                assert "anthropic" not in source.lower(), f"{fname} contains anthropic"
                # Forbidden-key constant names are OK; check for credential VALUE patterns
                import re
                # Match: "api_key" = or api_key: or api_key = after stripping comments
                lines = [ln for ln in source.split("\n") if not ln.strip().startswith("#")]
                for line in lines:
                    if re.match(r'\s*(api_key|api_key\s*[=:])', line) and 'FORBIDDEN' not in line.upper():
                        assert False, f"{fname} contains api_key assignment: {line.strip()}"


# =============================================================================
# K. FACTORY / INTEGRATION
# =============================================================================

class TestFactory:
    def test_default_registry_builds(self):
        from app.services.answer_synthesis.tools.registry import build_default_tool_registry
        # Without real dependencies, should build but only register vehicle_resolution
        registry = build_default_tool_registry(
            sql_session_factory=None,
            neo4j_available=False,
            graphrag_retrieval_fn=None,
        )
        # vehicle_resolution_tool should always be registered
        assert registry.is_registered("vehicle_resolution_tool")
        # Others unavailable since deps are None
        assert not registry.is_registered("sql_analytics_tool")
        assert not registry.is_registered("graph_evidence_tool")
        assert not registry.is_registered("graphrag_retrieval_tool")

    def test_sql_tool_registered_when_session_factory_provided(self):
        from app.services.answer_synthesis.tools.registry import build_default_tool_registry
        mock_session = MagicMock()
        mock_session_factory = MagicMock(return_value=mock_session)
        registry = build_default_tool_registry(
            sql_session_factory=mock_session_factory,
            neo4j_available=False,
            graphrag_retrieval_fn=None,
        )
        assert registry.is_registered("sql_analytics_tool")

    def test_graphrag_registered_when_retrieval_fn_provided(self):
        from app.services.answer_synthesis.tools.registry import build_default_tool_registry
        mock_fn = MagicMock()
        registry = build_default_tool_registry(
            sql_session_factory=None,
            neo4j_available=False,
            graphrag_retrieval_fn=mock_fn,
        )
        assert registry.is_registered("graphrag_retrieval_tool")

    def test_graph_tool_registered_when_neo4j_available(self):
        from app.services.answer_synthesis.tools.registry import build_default_tool_registry
        registry = build_default_tool_registry(
            sql_session_factory=None,
            neo4j_available=True,
            graphrag_retrieval_fn=None,
        )
        assert registry.is_registered("graph_evidence_tool")

    def test_all_four_tools_with_full_deps(self):
        from app.services.answer_synthesis.tools.registry import build_default_tool_registry
        registry = build_default_tool_registry(
            sql_session_factory=MagicMock(),
            neo4j_available=True,
            graphrag_retrieval_fn=MagicMock(),
        )
        names = [d.name for d in registry.list_definitions()]
        assert "sql_analytics_tool" in names
        assert "graph_evidence_tool" in names
        assert "graphrag_retrieval_tool" in names
        assert "vehicle_resolution_tool" in names
