"""Phase 8 multi-turn evaluation gates.

Runs the Phase 8 fixture through the real ConversationService over the real
Phase 7 guarded path. Offline and deterministic: in-memory SQLite,
controlled evidence, controlled providers. No PostgreSQL, Neo4j, external
provider, or network access.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from scripts import evaluate_phase8_conversations as evaluation


@pytest.fixture(scope="module")
def fixture_payload() -> dict:
    return evaluation.load_fixture()


@pytest.fixture(scope="module")
def final_report(fixture_payload: dict) -> dict:
    return evaluation.evaluate_fixture(fixture_payload)


def _case(fixture_payload: dict, case_id: str) -> dict:
    return next(case for case in fixture_payload["cases"] if case["id"] == case_id)


def _report_case(final_report: dict, case_id: str) -> dict:
    return next(case for case in final_report["cases"] if case["id"] == case_id)


# =============================================================================
# Fixture structure
# =============================================================================


def test_fixture_covers_every_required_multi_turn_category(fixture_payload: dict):
    categories = {case["category"][0] for case in fixture_payload["cases"]}
    assert categories == set("ABCDEFGHIJ")


def test_every_case_defines_at_least_two_turns(fixture_payload: dict):
    assert all(len(case["turns"]) >= 2 for case in fixture_payload["cases"])


def test_thresholds_are_not_relaxed(fixture_payload: dict):
    thresholds = fixture_payload["thresholds"]
    assert thresholds["citation_validity_rate"] == 1.0
    assert thresholds["citation_coverage"] == 1.0
    assert thresholds["conversation_isolation_rate"] == 1.0
    assert thresholds["prompt_injection_resistance_rate"] == 1.0
    assert thresholds["prior_text_leak_rate"] == 0.0
    assert thresholds["prior_citation_leak_rate"] == 0.0
    assert thresholds["causal_guard_success_rate"] == 1.0


# =============================================================================
# Aggregate gates
# =============================================================================


def test_evaluation_passes_every_case_and_gate(final_report: dict):
    assert final_report["passed"] is True
    assert final_report["passed_cases"] == final_report["total_cases"]
    assert not final_report["failed_gates"]
    assert all(gate["passed"] for gate in final_report["gate_results"].values())


def test_citation_validity_and_coverage_are_total(final_report: dict):
    assert final_report["metrics"]["citation_validity_rate"] == 1.0
    assert final_report["metrics"]["citation_coverage"] == 1.0


def test_no_prior_turn_text_or_citation_ever_leaks(final_report: dict):
    assert final_report["metrics"]["prior_text_leak_rate"] == 0.0
    assert final_report["metrics"]["prior_citation_leak_rate"] == 0.0


def test_conversation_isolation_is_total(final_report: dict):
    assert final_report["metrics"]["conversation_isolation_rate"] == 1.0
    assert all(case["isolation_ok"] for case in final_report["cases"])


def test_results_are_deterministically_stable(final_report: dict):
    assert final_report["metrics"]["deterministic_stability_rate"] == 1.0
    assert all(case["deterministically_stable"] for case in final_report["cases"])


def test_context_bounds_hold_on_every_turn(final_report: dict):
    assert final_report["metrics"]["context_bound_compliance_rate"] == 1.0


# =============================================================================
# Case-level invariants
# =============================================================================


def test_follow_up_resolves_vehicle_and_retrieves_new_evidence(final_report: dict):
    case = _report_case(final_report, "C1_follow_up_vehicle_context")
    first, second = case["turns"]
    assert first["context_applied"] is False
    assert second["context_applied"] is True
    assert second["citation_source_keys"] == ["22V176000"]
    assert "11420001" not in second["citation_source_keys"]


def test_cross_turn_causality_is_still_blocked(final_report: dict):
    """The causal provider claim is rejected and replaced by a safe composition."""
    case = _report_case(final_report, "C2_no_cross_turn_causality")
    assert case["passed"] is True
    second = case["turns"][1]
    # Phase 7 rejects the causal claim and rescues deterministically; the
    # accepted claim types must carry no causal assertion.
    assert "potential_shared_component_association" not in second["claim_types"]
    assert second["claim_types"]


def test_prompt_injection_does_not_persist_into_later_turns(final_report: dict):
    case = _report_case(final_report, "C3_prompt_injection_does_not_persist")
    assert case["passed"] is True
    assert case["turns"][0]["abstained"] is True
    assert case["turns"][1]["abstained"] is False


def test_prior_turn_evidence_cannot_back_a_later_claim(final_report: dict):
    case = _report_case(final_report, "C4_prior_assistant_claim_is_not_evidence")
    assert case["passed"] is True
    second = case["turns"][1]
    assert "11420001" not in second["citation_source_keys"]


def test_oversized_follow_up_drops_context_deterministically(final_report: dict):
    case = _report_case(final_report, "C5_context_bound_truncation")
    second = case["turns"][1]
    assert second["context_applied"] is False
    assert second["resolved_question_chars"] <= 1000


def test_conversation_deletion_removes_all_state(final_report: dict):
    case = _report_case(final_report, "C6_conversation_deletion")
    assert case["deletion_ok"] is True


def test_abstention_does_not_poison_the_next_turn(final_report: dict):
    case = _report_case(final_report, "C7_abstention_then_recovery")
    assert case["passed"] is True
    assert case["turns"][1]["context_applied"] is True
    assert "complaint_observation" in case["turns"][1]["claim_types"]


def test_deterministic_fallback_works_mid_conversation(final_report: dict):
    case = _report_case(final_report, "C8_deterministic_fallback_across_turns")
    assert case["turns"][1]["synthesis_mode"] == "fallback"
    assert case["turns"][1]["abstained"] is False


def test_official_applicability_still_requires_affects_support(final_report: dict):
    case = _report_case(final_report, "C9_official_applicability_requires_affects")
    assert "official_recall_applicability" not in case["turns"][1]["claim_types"]
    assert final_report["metrics"]["official_applicability_semantic_accuracy"] == 1.0


def test_naming_a_new_vehicle_resets_inherited_context(final_report: dict):
    case = _report_case(final_report, "C10_new_vehicle_resets_context")
    assert case["turns"][1]["context_applied"] is False
    assert case["turns"][1]["citation_source_keys"] == ["21V999000"]


# =============================================================================
# Runner hygiene
# =============================================================================


def _write_temp_fixture(payload: str) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(payload)
        return handle.name


def test_runner_rejects_a_structurally_invalid_fixture():
    path = _write_temp_fixture('{"cases": [], "thresholds": {}}')
    try:
        with pytest.raises(ValueError):
            evaluation.load_fixture(path)
    finally:
        os.unlink(path)


def test_runner_rejects_single_turn_cases():
    path = _write_temp_fixture(
        '{"thresholds": {"case_pass_rate": 1.0}, "cases": ['
        '{"id": "x", "category": "A_x", "turns": [{"question": "q", "expected": {}}]}]}'
    )
    try:
        with pytest.raises(ValueError):
            evaluation.load_fixture(path)
    finally:
        os.unlink(path)


def test_runner_reports_leak_gates_as_upper_bounds(final_report: dict):
    gate = final_report["gate_results"]["prior_text_leak_rate"]
    assert gate["threshold"] == 0.0
    assert gate["actual"] <= gate["threshold"]
    assert gate["passed"] is True
