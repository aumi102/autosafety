"""Tests for Phase 4 hybrid SQL + graph answers."""

from app.services.hybrid.answer_composer import (
    CAVEAT_COMPLAINT_VOLUME,
    CAVEAT_POTENTIAL_RELATION,
    compose_hybrid_answer,
)
from app.services.hybrid.hybrid_models import (
    HYBRID_INTENTS,
    GraphEvidenceItem,
    HybridAnswerResult,
)
from app.services.hybrid.hybrid_parser import (
    _detect_graph_request,
    is_hybrid_question,
    parse_hybrid_question,
)

# =============================================================================
# Hybrid parser tests
# =============================================================================


class TestHybridParser:
    def test_detects_top_complaint_with_related_recalls(self):
        q = "Which component has the most complaints for Ford F-150 2020, and are there related recalls?"
        intent = parse_hybrid_question(q)
        assert intent.hybrid_type in HYBRID_INTENTS
        assert intent.wants_graph is True
        assert intent.vehicle_extracted is True

    def test_detects_complaint_and_recall_evidence(self):
        q = "Show complaints and recall evidence for Honda Accord 2021."
        intent = parse_hybrid_question(q)
        assert intent.hybrid_type == "complaint_count_with_related_recalls"
        assert intent.wants_graph is True

    def test_detects_does_have_complaints_and_recalls(self):
        q = "Does Toyota Camry 2022 have complaints and recalls?"
        intent = parse_hybrid_question(q)
        assert intent.hybrid_type in HYBRID_INTENTS
        assert intent.wants_graph is True
        assert intent.vehicle_extracted is True

    def test_detects_recall_evidence_keyword(self):
        q = "Show recall evidence for Ford F-150 2020"
        intent = parse_hybrid_question(q)
        assert intent.wants_graph is True
        assert intent.hybrid_type in HYBRID_INTENTS

    def test_sql_only_question_returns_sql_only(self):
        q = "Top complaint components for Ford F-150 2020"
        intent = parse_hybrid_question(q)
        assert intent.hybrid_type == "sql_only"
        assert intent.wants_graph is False

    def test_explicit_skip_graph_returns_sql_only(self):
        q = "Show complaints for Ford F-150 2020 without graph"
        intent = parse_hybrid_question(q)
        assert intent.hybrid_type == "sql_only"

    def test_is_hybrid_question_true_for_hybrid(self):
        q = "Which component has the most complaints for Ford F-150 2020, and are there related recalls?"
        assert is_hybrid_question(q) is True

    def test_is_hybrid_question_false_for_sql_only(self):
        q = "How many complaints does Honda Accord 2021 have?"
        assert is_hybrid_question(q) is False

    def test_confidence_high_when_vehicle_and_graph(self):
        q = "Which component has the most complaints for Ford F-150 2020, and are there related recalls?"
        intent = parse_hybrid_question(q)
        assert intent.confidence >= 0.6

    def test_confidence_lower_for_partial_extraction(self):
        q = "Recall evidence for unknown vehicle"
        intent = parse_hybrid_question(q)
        # May not extract vehicle, confidence lower
        assert intent.confidence < 0.9


class TestGraphRequestDetection:
    def test_detects_related_recall(self):
        assert _detect_graph_request("are there related recalls") is True

    def test_detects_recall_evidence(self):
        assert _detect_graph_request("show recall evidence") is True

    def test_detects_complaints_and_recalls(self):
        assert _detect_graph_request("complaints and recalls") is True

    def test_detects_graph_evidence(self):
        assert _detect_graph_request("show graph evidence") is True

    def test_detects_skip_graph_returns_false(self):
        assert _detect_graph_request("show complaints without graph") is False

    def test_detects_no_recall_keyword(self):
        assert _detect_graph_request("how many complaints") is False


# =============================================================================
# Hybrid models tests
# =============================================================================


class TestHybridModels:
    def test_hybrid_intents_set_defined(self):
        assert "top_complaint_component_with_related_recalls" in HYBRID_INTENTS
        assert "complaint_count_with_related_recalls" in HYBRID_INTENTS
        assert "vehicle_recall_evidence" in HYBRID_INTENTS

    def test_graph_evidence_item_to_dict(self):
        item = GraphEvidenceItem(
            path_type="potentially related by shared vehicle/component",
            nodes=[{"id": "recall1", "label": "Recall"}],
            relationships=[{"type": "AFFECTS"}],
            recall_campaigns=["22V123"],
            relation_basis="potentially_related_by_shared_component",
            summary="1 recall potentially related",
        )
        d = item.to_dict()
        assert d["path_type"] == "potentially related by shared vehicle/component"
        assert d["recall_campaigns"] == ["22V123"]
        assert d["relation_basis"] == "potentially_related_by_shared_component"

    def test_hybrid_answer_result_to_dict(self):
        item = GraphEvidenceItem(
            path_type="potentially related",
            recall_campaigns=["22V456"],
            relation_basis="potentially_related_by_shared_component",
        )
        result = HybridAnswerResult(
            sql_response={"sql": {"row_count": 5}},
            graph_evidence=[item],
            neo4j_available=True,
        )
        d = result.to_dict()
        assert d["neo4j_available"] is True
        assert len(d["graph_evidence"]) == 1
        assert d["graph_evidence"][0]["recall_campaigns"] == ["22V456"]


# =============================================================================
# Answer composer tests
# =============================================================================


class TestAnswerComposer:
    def test_compose_includes_sql_result(self):
        sql_resp = {
            "run_id": "test-run",
            "intent": "sql",
            "answer": {"summary": "5 complaints found", "sections": []},
            "sql": {
                "used": True,
                "query": "SELECT * FROM complaints",
                "columns": ["component", "complaint_count"],
                "rows": [{"component": "SERVICE BRAKES", "complaint_count": 5}],
                "row_count": 1,
                "execution_ms": 10,
                "validated": True,
            },
            "evidence": {"citations": [], "graph_paths": []},
            "warnings": [],
            "confidence": {"label": "medium", "score": 0.6, "reasons": []},
            "debug": {"tool_call_count": 1, "latency_ms": 10},
        }
        item = GraphEvidenceItem(
            path_type="potentially related by shared vehicle/component",
            recall_campaigns=["22V123"],
            relation_basis="potentially_related_by_shared_component",
        )
        result = HybridAnswerResult(
            sql_response=sql_resp,
            graph_evidence=[item],
            neo4j_available=True,
        )

        response = compose_hybrid_answer(result, "test question")

        assert response.intent == "hybrid"
        assert response.sql.used is True
        assert len(response.evidence.graph_paths) == 1
        assert response.confidence.label in ("low", "medium", "high")

    def test_compose_includes_complaint_volume_caveat(self):
        sql_resp = {
            "sql": {"row_count": 5, "query": "SELECT complaint_count"},
            "answer": {"summary": "5 complaints"},
        }
        result = HybridAnswerResult(sql_response=sql_resp, graph_evidence=[])
        response = compose_hybrid_answer(result, "complaints for Ford F-150")
        assert any(CAVEAT_COMPLAINT_VOLUME[:20] in w for w in response.warnings)

    def test_compose_includes_potential_relation_caveat(self):
        item = GraphEvidenceItem(
            path_type="potentially related",
            recall_campaigns=["22V123"],
            relation_basis="potentially_related_by_shared_component",
        )
        result = HybridAnswerResult(
            sql_response={"sql": {"row_count": 5}},
            graph_evidence=[item],
            neo4j_available=True,
        )
        response = compose_hybrid_answer(result, "complaints and recalls for Ford")
        assert any(CAVEAT_POTENTIAL_RELATION[:20] in w for w in response.warnings)

    def test_compose_includes_neo4j_unavailable_caveat(self):
        result = HybridAnswerResult(
            sql_response={"sql": {"row_count": 5}},
            graph_evidence=[],
            neo4j_available=False,
            neo4j_error="not connected",
        )
        response = compose_hybrid_answer(result, "complaints and recalls")
        assert response.confidence.label == "medium"
        assert len(response.warnings) > 0

    def test_compose_confidence_high_when_sql_and_graph(self):
        sql_resp = {
            "sql": {
                "row_count": 5,
                "query": "SELECT *",
                "columns": [],
                "rows": [{}],
                "execution_ms": 10,
                "validated": True,
            }
        }
        item = GraphEvidenceItem(path_type="potentially related", recall_campaigns=["22V"])
        result = HybridAnswerResult(
            sql_response=sql_resp, graph_evidence=[item], neo4j_available=True
        )
        response = compose_hybrid_answer(result, "complaints and recalls")
        assert response.confidence.label == "high"

    def test_compose_confidence_low_when_no_data(self):
        result = HybridAnswerResult(sql_response={"sql": {"row_count": 0}}, graph_evidence=[])
        response = compose_hybrid_answer(result, "complaints for unknown vehicle")
        assert response.confidence.label == "low"

    def test_compose_answer_has_sections(self):
        sql_resp = {
            "sql": {
                "row_count": 1,
                "query": "SELECT 1",
                "columns": ["x"],
                "rows": [{"x": 1}],
                "execution_ms": 5,
                "validated": True,
            },
            "answer": {"summary": "test"},
        }
        result = HybridAnswerResult(sql_response=sql_resp, graph_evidence=[])
        response = compose_hybrid_answer(result, "test")
        assert len(response.answer.sections) >= 1  # at least summary section


# =============================================================================
# Hybrid service tests
# =============================================================================


class TestHybridServiceRouting:
    def test_is_hybrid_question_wrapper(self):
        from app.services.hybrid.hybrid_service import is_hybrid_question_wrapper

        assert is_hybrid_question_wrapper("complaints and recalls for Ford F-150 2020") is True
        assert is_hybrid_question_wrapper("how many complaints") is False


# =============================================================================
# Import smoke tests
# =============================================================================


class TestImports:
    def test_hybrid_package_imports_clean(self):
        from app.services.hybrid import answer_hybrid_question
        from app.services.hybrid.hybrid_parser import parse_hybrid_question

        assert callable(answer_hybrid_question)
        assert callable(parse_hybrid_question)

    def test_hybrid_endpoint_imports_clean(self):
        from app.api.v1.endpoints.hybrid import hybrid_query

        assert callable(hybrid_query)

    def test_chat_endpoint_imports_clean(self):
        from app.api.v1.endpoints.chat import send_message

        assert callable(send_message)
