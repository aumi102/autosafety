"""Phase 11 regression tests for defects static analysis exposed.

Offline and deterministic. Each test corresponds to a real defect that mypy
found once `app/` was actually type-checked — mypy was configured from the
start but no target ever invoked it, so it had never run.
"""

from __future__ import annotations

import inspect
import typing
from typing import cast

import pytest
from app.services.answer_contract import ConfidenceLabel, GraphPath, RelationSource
from app.services.graph.graph_models import RecallNode, RecallPathResult
from app.services.hybrid.answer_composer import _relation_source
from app.services.sql_analytics.question_parser import QuestionIntent
from app.services.sql_analytics.service import VEHICLE_SCOPED_INTENTS
from app.services.sql_analytics.templates import TemplateId


def _literal_values(alias) -> set[str]:
    return set(typing.get_args(alias))


# =============================================================================
# RecallNode.to_dict was missing while an HTTP route called it
# =============================================================================


class TestRecallNodeSerialization:
    """`attr-defined` caught an AttributeError on a live route.

    `GET /v1/graph/vehicles/{id}/recall-paths` built its response with
    `[r.to_dict() for r in result.recalls]`, but `RecallNode` was a plain
    dataclass with no `to_dict`. Every call with at least one recall raised
    AttributeError. No test covered the route — only the query function beneath
    it — so it went unnoticed. `RecallPathResult.to_dict` had quietly worked
    around the gap by inlining a duplicate copy of the same mapping.
    """

    def test_recall_node_serializes(self):
        node = RecallNode(
            campaign_number="22V176000",
            report_received_date="2022-03-10",
            summary="Brake hose may rupture.",
            component="SERVICE BRAKES",
            remedy="Dealer will replace the hose.",
            units_affected=1234,
        )
        assert node.to_dict() == {
            "campaign_number": "22V176000",
            "report_received_date": "2022-03-10",
            "summary": "Brake hose may rupture.",
            "component": "SERVICE BRAKES",
            "remedy": "Dealer will replace the hose.",
            "units_affected": 1234,
        }

    def test_optional_fields_serialize_as_none(self):
        assert RecallNode(campaign_number="22V000001").to_dict() == {
            "campaign_number": "22V000001",
            "report_received_date": None,
            "summary": None,
            "component": None,
            "remedy": None,
            "units_affected": None,
        }

    def test_the_route_expression_no_longer_raises(self):
        """This is the exact expression the endpoint evaluates."""
        result = RecallPathResult(
            make="Ford",
            model="F-150",
            year=2020,
            vehicle_id="v1",
            recalls=[RecallNode(campaign_number="22V176000")],
            path_type="potentially related",
        )
        assert [r.to_dict() for r in result.recalls] == [
            RecallNode(campaign_number="22V176000").to_dict()
        ]

    def test_container_serialization_delegates_instead_of_duplicating(self):
        """The inline copy is gone, so the two can no longer drift apart."""
        node = RecallNode(campaign_number="22V176000", component="SERVICE BRAKES")
        result = RecallPathResult(
            make="Ford", model="F-150", year=2020, vehicle_id="v1",
            recalls=[node], path_type="potentially related",
        )
        assert result.to_dict()["recalls"] == [node.to_dict()]
        assert "campaign_number" not in inspect.getsource(RecallPathResult.to_dict)


# =============================================================================
# relation_source emitted values the documented contract does not allow
# =============================================================================


class TestRelationSourceContract:
    """`arg-type` caught an answer-contract violation.

    `docs/contracts/answer_contract.md` allows exactly three `relation_source`
    values. The Phase 4 hybrid composer passed the internal graph-layer
    `relation_basis` straight through, so the public answer carried strings like
    `official_recall_affects_vehicle` that the contract does not define.
    """

    def test_contract_vocabulary_is_exactly_three_values(self):
        assert _literal_values(RelationSource) == {
            "source_record",
            "normalized_join",
            "semantic_similarity",
        }

    @pytest.mark.parametrize(
        "basis",
        [
            "official_recall_affects_vehicle",
            "complaint_mentions_component",
            "potentially_related_by_shared_component",
            "sql_analytics",
            "vehicle_resolution",
            "something_new_nobody_mapped_yet",
            None,
            "",
        ],
    )
    def test_every_internal_basis_maps_into_the_contract(self, basis):
        assert _relation_source(basis) in _literal_values(RelationSource)

    def test_official_records_map_to_source_record(self):
        assert _relation_source("official_recall_affects_vehicle") == "source_record"

    def test_component_joins_map_to_normalized_join(self):
        assert _relation_source("complaint_mentions_component") == "normalized_join"
        assert _relation_source("potentially_related_by_shared_component") == "normalized_join"

    def test_graph_path_default_is_in_the_contract(self):
        assert GraphPath(path_text="x").relation_source in _literal_values(RelationSource)


# =============================================================================
# A fully implemented intent was missing from the declared type
# =============================================================================


class TestQuestionIntentVocabulary:
    """`arg-type` caught an incomplete Literal.

    `complaint_count_by_component_for_vehicle` has a template, a parser branch,
    two service branches, and a tool operation, but was absent from the
    `ParsedQuestion.intent` Literal.
    """

    def test_the_missing_intent_is_declared(self):
        assert "complaint_count_by_component_for_vehicle" in _literal_values(QuestionIntent)

    def test_every_template_id_is_a_declared_intent(self):
        declared = _literal_values(QuestionIntent)
        for template in TemplateId:
            assert template.value in declared, template.value

    def test_vehicle_scoped_intents_are_all_declared(self):
        assert VEHICLE_SCOPED_INTENTS <= _literal_values(QuestionIntent)

    def test_confidence_vocabulary_matches_the_contract(self):
        assert _literal_values(ConfidenceLabel) == {"low", "medium", "high"}


# =============================================================================
# Vehicle-scoped SQL no longer depends on an invariant held elsewhere
# =============================================================================


class TestVehicleScopedNarrowing:
    """`union-attr` caught an implicit cross-module invariant.

    `_build_template_sql` read `vehicle.model_year` for vehicle-scoped intents
    while `vehicle` was Optional. The parser does downgrade those intents to
    `clarification_needed` when the vehicle is incomplete, so it never crashed —
    but nothing local said so, and a parser change would have turned it into an
    AttributeError deep in SQL construction.
    """

    def test_service_returns_no_sql_when_a_scoped_intent_lacks_a_vehicle(self):
        from app.services.sql_analytics.question_parser import ParsedQuestion
        from app.services.sql_analytics.service import SqlAnalyticsService

        service = SqlAnalyticsService.__new__(SqlAnalyticsService)
        for intent in sorted(VEHICLE_SCOPED_INTENTS):
            parsed = ParsedQuestion(intent=cast(QuestionIntent, intent), vehicle=None, raw="x")
            sql, params, resolved = service._build_template_sql(parsed)
            assert sql is None, intent
            assert params == {}
            assert resolved is None

    def test_non_scoped_intent_still_builds_sql_without_a_vehicle(self):
        from app.services.sql_analytics.question_parser import ParsedQuestion
        from app.services.sql_analytics.service import SqlAnalyticsService

        service = SqlAnalyticsService.__new__(SqlAnalyticsService)
        parsed = ParsedQuestion(intent="vehicles_by_complaint_count", vehicle=None, raw="x")
        sql, _, resolved = service._build_template_sql(parsed)
        assert sql is not None
        assert resolved == "vehicles_by_complaint_count"
