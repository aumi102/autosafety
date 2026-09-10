#!/usr/bin/env python3
"""Offline deterministic Phase 7 guarded-answer evaluation.

This runner exercises the real Phase 7D service, Phase 7C orchestrator,
Phase 7B registry/argument guards, and Phase 6 GraphRAG adapter. Controlled
providers and evidence isolate semantic edge cases without network access.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.answer_synthesis.guarded_models import GuardedAnswerResult
from app.services.answer_synthesis.models import (
    OrchestrationResult,
    ProviderClaim,
    ProviderSynthesisResult,
    ProviderToolCall,
    SynthesisConfig,
)
from app.services.answer_synthesis.orchestrator import SynthesisOrchestrator
from app.services.answer_synthesis.policy import (
    CAUSAL_LIMITATION_WARNING,
    COMPLAINT_WARNING,
    FALLBACK_WARNING,
    GRAPH_UNAVAILABLE_WARNING,
    NO_AFFECTS_WARNING,
    PARTIAL_EVIDENCE_WARNING,
    RELATED_TO_COMPONENT_WARNING,
    REPAIRED_WARNING,
    SHARED_COMPONENT_WARNING,
)
from app.services.answer_synthesis.providers import DeterministicProvider, FakeProvider
from app.services.answer_synthesis.service import GuardedAnswerService
from app.services.answer_synthesis.tools.base import ToolCallResult
from app.services.answer_synthesis.tools.graph_adapter import GRAPH_EVIDENCE_DEFINITION
from app.services.answer_synthesis.tools.graphrag_adapter import (
    GRAPHRAG_RETRIEVAL_DEFINITION,
    build_graphrag_adapter,
)
from app.services.answer_synthesis.tools.registry import ToolRegistry
from app.services.answer_synthesis.tools.sql_adapter import SQL_ANALYTICS_DEFINITION
from app.services.answer_synthesis.tools.vehicle_adapter import VEHICLE_RESOLUTION_DEFINITION
from app.services.graphrag.models import (
    GraphRAGCitation,
    GraphRAGGraphPath,
    GraphRAGRetrievalResult,
    RetrievedChunk,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "phase7_answer_eval.json"

WARNING_BY_CODE = {
    "complaint": COMPLAINT_WARNING,
    "shared_component": SHARED_COMPONENT_WARNING,
    "graph_unavailable": GRAPH_UNAVAILABLE_WARNING,
    "no_affects": NO_AFFECTS_WARNING,
    "related_component": RELATED_TO_COMPONENT_WARNING,
    "fallback": FALLBACK_WARNING,
    "repaired": REPAIRED_WARNING,
    "partial": PARTIAL_EVIDENCE_WARNING,
    "causal": CAUSAL_LIMITATION_WARNING,
}

NONFACTUAL_CLAIM_TYPES = {"data_limitation"}
EXPECTED_CATEGORY_PREFIXES = tuple(f"{letter}_" for letter in "ABCDEFGHIJKLMNOPQRST")


class RecordingOrchestrator(SynthesisOrchestrator):
    """Capture internal orchestration trace without exposing it publicly."""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.last_result: OrchestrationResult | None = None

    def orchestrate(self, question: str) -> OrchestrationResult:
        self.last_result = super().orchestrate(question)
        return self.last_result


@dataclass
class BaseCallCounter:
    """Counts base retrievals during construction.

    The retrieval adapter has to exist before the service that uses it, so it
    cannot close over the finished `CaseHarness`. It closes over this counter
    instead, which keeps `CaseHarness` fully populated the moment it is built.
    """

    count: int = 0


@dataclass
class CaseHarness:
    service: GuardedAnswerService
    orchestrator: RecordingOrchestrator
    adapter_calls: dict[str, int]
    _base_calls: BaseCallCounter = field(default_factory=BaseCallCounter)

    @property
    def base_calls(self) -> int:
        """Base retrievals so far. Read live: the adapter counts during answer()."""
        return self._base_calls.count


@dataclass
class CaseOutcome:
    case: dict[str, Any]
    result: GuardedAnswerResult
    orchestration: OrchestrationResult
    adapter_calls: dict[str, int]
    base_calls: int
    stable: bool
    failures: list[str]

    @property
    def passed(self) -> bool:
        return not self.failures


def load_fixture(path: Path | str = DEFAULT_FIXTURE) -> dict[str, Any]:
    """Load and structurally validate Phase 7F fixture."""
    fixture_path = Path(path)
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    thresholds = payload.get("thresholds")
    if not isinstance(cases, list) or not cases:
        raise ValueError("fixture must contain a non-empty cases list")
    if not isinstance(thresholds, dict) or not thresholds:
        raise ValueError("fixture must contain metric thresholds")

    ids = [case.get("id") for case in cases]
    if any(not isinstance(case_id, str) or not case_id for case_id in ids):
        raise ValueError("every case requires a non-empty id")
    if len(ids) != len(set(ids)):
        raise ValueError("fixture case ids must be unique")
    if len(cases) != len(EXPECTED_CATEGORY_PREFIXES):
        raise ValueError("fixture must contain exactly 20 Phase 7F categories")
    for prefix, case in zip(EXPECTED_CATEGORY_PREFIXES, cases):
        if not str(case.get("category", "")).startswith(prefix):
            raise ValueError(f"fixture category order mismatch: expected prefix {prefix}")
        if not str(case.get("question", "")).strip():
            raise ValueError(f"case {case['id']} has empty question")
        if "expected" not in case:
            raise ValueError(f"case {case['id']} has no expected contract")
    fixture: dict[str, Any] = payload
    return fixture


def _build_retrieval(case: dict[str, Any], counter: BaseCallCounter | None = None):
    evidence = case.get("evidence", {})

    def retrieve(
        *,
        question: str,
        top_k: int,
        source_type: str | None,
        make: str | None,
        model: str | None,
        model_year: int | None,
        include_graph: bool,
    ) -> GraphRAGRetrievalResult:
        del make, model, model_year
        if counter is not None:
            counter.count += 1

        raw_chunks = [
            chunk
            for chunk in evidence.get("chunks", [])
            if source_type is None or chunk.get("source_type") == source_type
        ][:top_k]
        chunks: list[RetrievedChunk] = []
        citations: list[GraphRAGCitation] = []
        for index, raw in enumerate(raw_chunks):
            source_key = str(raw["source_record_key"])
            source_kind = str(raw["source_type"])
            entity_id = f"entity-{source_kind}-{source_key}"
            label = f"{source_kind.title()} {source_key}"
            text = str(raw.get("text", ""))
            score = float(raw.get("score", 1.0))
            chunks.append(
                RetrievedChunk(
                    chunk_id=f"chunk-{source_kind}-{source_key}-{index}",
                    score=score,
                    source_type=source_kind,
                    source_entity_id=entity_id,
                    source_record_key=source_key,
                    title=label,
                    text=text,
                    source_url=None,
                    make="Ford",
                    model="F-150",
                    model_year=2020,
                    component=raw.get("component"),
                    citation_label=label,
                    chunk_index=index,
                )
            )
            citations.append(
                GraphRAGCitation(
                    source_type=source_kind,
                    source_id=entity_id,
                    source_key=source_key,
                    citation_label=label,
                    text_span=text,
                    confidence=score,
                )
            )

        paths = []
        if include_graph:
            for raw in evidence.get("graph_paths", []):
                if source_type is not None and raw.get("source_type") != source_type:
                    continue
                paths.append(
                    GraphRAGGraphPath(
                        path_text=str(raw.get("path_text", "")),
                        relation_source=str(raw.get("relation_source", "unknown")),
                        source_type=str(raw.get("source_type", "graph")),
                        source_key=str(raw.get("source_key", "")),
                        confidence=float(raw.get("confidence", 1.0)),
                    )
                )

        graph_available = bool(evidence.get("neo4j_available", True))
        max_score = max((chunk.score for chunk in chunks), default=0.0)
        return GraphRAGRetrievalResult(
            query=question,
            retrieved_chunks=chunks,
            citations=citations,
            graph_paths=paths,
            warnings=[] if graph_available else ["Graph expansion unavailable"],
            confidence_label="high"
            if max_score >= 0.75
            else ("medium" if max_score >= 0.3 else "low"),
            confidence_score=max_score,
            confidence_reasons=["controlled offline Phase 7F evidence"],
            total_chunks_returned=len(chunks),
            execution_ms=0,
            neo4j_available=graph_available,
            neo4j_error=None if graph_available else "graph_unavailable",
        )

    return retrieve


def _build_primary_provider(case: dict[str, Any]):
    provider_spec = case.get("provider", {})
    if provider_spec.get("mode") == "deterministic":
        return DeterministicProvider()

    config: dict[str, Any] = {}
    error_code = provider_spec.get("error_code")
    if error_code == "provider_unavailable":
        config["simulate_unavailable"] = True
    elif error_code == "provider_timeout":
        config["simulate_timeout"] = True
    provider = FakeProvider(config)
    if provider_spec.get("available") is False:
        provider.set_available(False)

    raw_calls = provider_spec.get("tool_calls", [])
    if raw_calls:
        provider.queue_tool_calls(
            [
                ProviderToolCall(
                    call_id=f"fixture-{index + 1}",
                    tool_name=str(raw["tool_name"]),
                    arguments=dict(raw.get("arguments", {})),
                )
                for index, raw in enumerate(raw_calls)
            ]
        )

    if error_code not in {"provider_unavailable", "provider_timeout"}:
        claims = [
            ProviderClaim(
                text=str(raw.get("text", "")),
                claim_type=str(raw.get("claim_type", "")),
                citation_ids=list(raw.get("citation_ids", [])),
            )
            for raw in provider_spec.get("claims", [])
        ]
        provider.queue_response(
            ProviderSynthesisResult(
                answer=str(provider_spec.get("answer", "")),
                claims=claims,
                abstain=bool(provider_spec.get("abstain", False)),
                abstention_reason=provider_spec.get("abstention_reason"),
                error_code=error_code,
                error_message="controlled fixture provider error" if error_code else None,
                finish_reason="fixture",
            )
        )
    return provider


def build_case_harness(case: dict[str, Any]) -> CaseHarness:
    """Build actual Phase 7 path with controlled read-only adapters."""
    adapter_calls = {"sql": 0, "graph": 0, "vehicle": 0}
    registry = ToolRegistry()
    base_calls = BaseCallCounter()
    registry.register(
        GRAPHRAG_RETRIEVAL_DEFINITION,
        build_graphrag_adapter(_build_retrieval(case, base_calls)),
    )

    def sql_adapter(*, call_id: str, arguments: dict[str, Any]) -> ToolCallResult:
        adapter_calls["sql"] += 1
        output = case.get("tool_outputs", {}).get("sql", {})
        data = {
            "operation": arguments.get("operation"),
            "columns": list(output.get("columns", [])),
            "rows": list(output.get("rows", [])),
            "row_count": len(output.get("rows", [])),
            "vehicle": "Ford F-150 2020",
        }
        return ToolCallResult.ok(call_id, "sql_analytics_tool", data)

    def graph_adapter(*, call_id: str, arguments: dict[str, Any]) -> ToolCallResult:
        adapter_calls["graph"] += 1
        return ToolCallResult.ok(
            call_id,
            "graph_evidence_tool",
            {
                "operation": arguments.get("operation"),
                "relation_basis": arguments.get("operation"),
                "recalls": [],
                "shared_recalls": [],
            },
        )

    def vehicle_adapter(*, call_id: str, arguments: dict[str, Any]) -> ToolCallResult:
        adapter_calls["vehicle"] += 1
        return ToolCallResult.ok(
            call_id,
            "vehicle_resolution_tool",
            {
                "resolved": True,
                "status": "resolved",
                "vehicle_id": "00000000-0000-0000-0000-000000000007",
                "normalized_make": str(arguments.get("make", "FORD")).upper(),
                "normalized_model": str(arguments.get("model", "F-150")).upper(),
                "model_year": arguments.get("model_year", 2020),
            },
        )

    registry.register(SQL_ANALYTICS_DEFINITION, sql_adapter)
    registry.register(GRAPH_EVIDENCE_DEFINITION, graph_adapter)
    registry.register(VEHICLE_RESOLUTION_DEFINITION, vehicle_adapter)

    primary = _build_primary_provider(case)
    orchestrator = RecordingOrchestrator(
        registry=registry,
        primary_provider=primary,
        deterministic_fallback=DeterministicProvider(),
        config=SynthesisConfig(
            provider=primary.provider_name,
            max_tool_rounds=2,
            max_tool_calls=4,
            max_evidence_items=20,
            max_evidence_chars=16000,
            max_output_chars=8000,
            max_claims=8,
        ),
    )
    service = GuardedAnswerService(orchestrator)
    return CaseHarness(
        service=service,
        orchestrator=orchestrator,
        adapter_calls=adapter_calls,
        _base_calls=base_calls,
    )


def _public_claim_text(result: GuardedAnswerResult) -> str:
    parts = [result.answer]
    for claim in result.claims:
        parts.append(claim.text)
        parts.extend(claim.citation_ids)
    for citation in result.citations:
        parts.extend([citation.citation_id, citation.text_span])
    return " ".join(parts).lower()


def _tool_rejection_count(orchestration: OrchestrationResult) -> int:
    return sum(
        1
        for item in orchestration.trace.tool_call_log
        if not item.get("executed") and item.get("rejected_reason")
    )


def _claim_grounded(claim: Any, result: GuardedAnswerResult) -> bool:
    by_id = {citation.citation_id: citation for citation in result.citations}
    cited = [by_id[citation_id] for citation_id in claim.citation_ids if citation_id in by_id]
    types = {citation.source_type for citation in cited}
    if claim.claim_type == "data_limitation":
        return True
    if not cited:
        return False
    if claim.claim_type in {"complaint_observation", "complaint_component_observation"}:
        return "complaint" in types
    if claim.claim_type == "official_recall":
        return "recall" in types
    if claim.claim_type == "official_recall_applicability":
        return "recall" in types and any(
            citation.relation_basis == "official_recall_affects_vehicle" for citation in cited
        )
    if claim.claim_type == "potential_shared_component_association":
        return {"complaint", "recall"}.issubset(types)
    if claim.claim_type == "sql_fact":
        sql_text = " ".join(
            citation.text_span for citation in cited if citation.source_type == "sql_result"
        )
        return "sql_result" in types and set(re.findall(r"\d+", claim.text)).issubset(
            set(re.findall(r"\d+", sql_text))
        )
    if claim.claim_type == "synthesis_summary":
        return bool(cited)
    return False


def _official_semantics_ok(case: dict[str, Any], result: GuardedAnswerResult) -> bool:
    expected = case["expected"]
    claim_types = {claim.claim_type for claim in result.claims}
    statuses = {claim.official_status for claim in result.claims}
    return (
        set(expected.get("claim_types_include", [])).issubset(claim_types)
        and not set(expected.get("claim_types_exclude", [])).intersection(claim_types)
        and set(expected.get("official_status_include", [])).issubset(statuses)
    )


def _official_applicability_ok(case: dict[str, Any], result: GuardedAnswerResult) -> bool:
    expects_applicability = "official_recall_applicability" in case["expected"].get(
        "claim_types_include", []
    )
    applicability_claims = [
        claim for claim in result.claims if claim.claim_type == "official_recall_applicability"
    ]
    if not expects_applicability:
        return not applicability_claims
    citation_by_id = {citation.citation_id: citation for citation in result.citations}
    return bool(applicability_claims) and all(
        claim.official_status == "applicability"
        and any(
            citation_by_id.get(citation_id)
            and citation_by_id[citation_id].relation_basis == "official_recall_affects_vehicle"
            for citation_id in claim.citation_ids
        )
        for claim in applicability_claims
    )


def _case_failures(
    case: dict[str, Any],
    result: GuardedAnswerResult,
    orchestration: OrchestrationResult,
    adapter_calls: dict[str, int],
    base_calls: int,
    stable: bool,
) -> list[str]:
    expected = case["expected"]
    failures: list[str] = []
    claim_types = {claim.claim_type for claim in result.claims}
    official_statuses = {claim.official_status for claim in result.claims}
    relation_bases = {citation.relation_basis for citation in result.citations}
    public_text = _public_claim_text(result)

    if result.phase != "phase_7":
        failures.append(f"phase={result.phase!r}, expected 'phase_7'")
    if result.abstained is not expected.get("abstained"):
        failures.append(f"abstained={result.abstained}, expected {expected.get('abstained')}")
    if result.synthesis_mode not in expected.get("modes", []):
        failures.append(f"synthesis_mode={result.synthesis_mode!r}")
    if len(result.citations) < int(expected.get("min_citations", 0)):
        failures.append(f"citations={len(result.citations)} below expected minimum")
    if not set(expected.get("claim_types_include", [])).issubset(claim_types):
        failures.append("required claim type missing")
    excluded = set(expected.get("claim_types_exclude", [])).intersection(claim_types)
    if excluded:
        failures.append(f"excluded claim types present: {sorted(excluded)}")
    if not set(expected.get("official_status_include", [])).issubset(official_statuses):
        failures.append("required official status missing")
    if not set(expected.get("relation_basis_include", [])).issubset(relation_bases):
        failures.append("required relation basis missing")
    if expected.get("confidence_level") and result.confidence.level != expected["confidence_level"]:
        failures.append(f"confidence level={result.confidence.level!r}")
    if (
        expected.get("abstention_reason")
        and result.abstention_reason != expected["abstention_reason"]
    ):
        failures.append(f"abstention_reason={result.abstention_reason!r}")
    if "neo4j_available" in expected and (
        result.retrieval_summary.neo4j_available is not expected["neo4j_available"]
    ):
        failures.append("Neo4j availability semantics changed")

    for code in expected.get("warning_codes", []):
        warning = WARNING_BY_CODE.get(code)
        if warning is None:
            failures.append(f"unknown expected warning code {code!r}")
        elif warning not in result.warnings:
            failures.append(f"required warning missing: {code}")

    rejected = result.trace.rejected_claim_count if result.trace else 0
    if rejected < int(expected.get("min_rejected_claims", 0)):
        failures.append(f"rejected claims={rejected} below expected minimum")

    for term in expected.get("forbidden_terms", []):
        if str(term).lower() in public_text:
            failures.append(f"forbidden term reached answer/claims: {term!r}")

    for adapter_name, expected_count in expected.get("adapter_calls", {}).items():
        if adapter_calls.get(adapter_name, 0) != expected_count:
            failures.append(
                f"{adapter_name} adapter calls={adapter_calls.get(adapter_name, 0)}, "
                f"expected {expected_count}"
            )

    tool_rejections = _tool_rejection_count(orchestration)
    if tool_rejections < int(expected.get("min_tool_rejections", 0)):
        failures.append(f"tool rejections={tool_rejections} below expected minimum")
    if base_calls != 1:
        failures.append(f"mandatory GraphRAG base calls={base_calls}, expected 1")
    if int(case.get("repeat", 1)) > 1 and not stable:
        failures.append("deterministic results changed across repetitions")

    known_ids = {citation.citation_id for citation in result.citations}
    for claim in result.claims:
        if any(citation_id not in known_ids for citation_id in claim.citation_ids):
            failures.append(f"claim {claim.claim_id} contains unknown citation")
        if not _claim_grounded(claim, result):
            failures.append(f"claim {claim.claim_id} is not grounded by cited evidence type")
    return failures


def run_case(case: dict[str, Any]) -> CaseOutcome:
    """Execute one fixture case, including requested stability repetitions."""
    repeats = max(1, int(case.get("repeat", 1)))
    first_result: GuardedAnswerResult | None = None
    first_orchestration: OrchestrationResult | None = None
    first_calls: dict[str, int] | None = None
    first_base_calls = 0
    serialized: list[dict[str, Any]] = []

    for _ in range(repeats):
        harness = build_case_harness(case)
        result = harness.service.answer(str(case["question"]))
        if harness.orchestrator.last_result is None:
            raise RuntimeError(f"case {case['id']} produced no orchestration result")
        serialized.append(result.to_dict())
        if first_result is None:
            first_result = result
            first_orchestration = harness.orchestrator.last_result
            first_calls = dict(harness.adapter_calls)
            first_base_calls = harness.base_calls

    assert first_result is not None and first_orchestration is not None and first_calls is not None
    stable = all(item == serialized[0] for item in serialized[1:])
    failures = _case_failures(
        case,
        first_result,
        first_orchestration,
        first_calls,
        first_base_calls,
        stable,
    )
    return CaseOutcome(
        case=case,
        result=first_result,
        orchestration=first_orchestration,
        adapter_calls=first_calls,
        base_calls=first_base_calls,
        stable=stable,
        failures=failures,
    )


def _rate(samples: list[bool]) -> dict[str, Any]:
    passed = sum(samples)
    total = len(samples)
    value = passed / total if total else 1.0
    return {"value": round(value, 4), "passed": passed, "total": total}


def _warning_codes(result: GuardedAnswerResult) -> list[str]:
    return [code for code, warning in WARNING_BY_CODE.items() if warning in result.warnings]


def evaluate_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    """Run all cases, compute metrics from outputs, and apply acceptance gates."""
    outcomes = [run_case(case) for case in fixture["cases"]]
    samples: dict[str, list[bool]] = {name: [] for name in fixture["thresholds"]}

    factual_claims = [
        claim
        for outcome in outcomes
        for claim in outcome.result.claims
        if claim.claim_type not in NONFACTUAL_CLAIM_TYPES
    ]
    samples["citation_coverage"].extend(bool(claim.citation_ids) for claim in factual_claims)
    for outcome in outcomes:
        known_ids = {citation.citation_id for citation in outcome.result.citations}
        for claim in outcome.result.claims:
            samples["citation_validity_rate"].extend(
                citation_id in known_ids for citation_id in claim.citation_ids
            )
            if claim.claim_type not in NONFACTUAL_CLAIM_TYPES:
                samples["grounded_claim_rate"].append(_claim_grounded(claim, outcome.result))

        expected = outcome.case["expected"]
        tags = set(outcome.case.get("metric_tags", []))
        for code in expected.get("warning_codes", []):
            samples["required_warning_coverage"].append(
                WARNING_BY_CODE.get(code) in outcome.result.warnings
            )
        if "abstention" in tags:
            samples["abstention_accuracy"].append(
                outcome.result.abstained is expected.get("abstained")
            )
        if "invalid_provider_output" in tags:
            samples["invalid_provider_output_rejection_rate"].append(
                not any(
                    str(term).lower() in _public_claim_text(outcome.result)
                    for term in expected.get("forbidden_terms", [])
                )
                and outcome.result.synthesis_mode in expected.get("modes", [])
            )
        if "stability" in tags:
            samples["deterministic_stability_rate"].append(outcome.stable)
        if "official_recall_semantics" in tags:
            samples["official_recall_semantic_accuracy"].append(
                _official_semantics_ok(outcome.case, outcome.result)
            )
        if "official_applicability" in tags:
            samples["official_applicability_semantic_accuracy"].append(
                _official_applicability_ok(outcome.case, outcome.result)
            )
        if "causal_guard" in tags:
            samples["causal_guard_success_rate"].append(
                CAUSAL_LIMITATION_WARNING in outcome.result.warnings
                or SHARED_COMPONENT_WARNING in outcome.result.warnings
            )
        if "unsupported_claim" in tags:
            samples["unsupported_claim_rejection_rate"].append(
                not any(
                    str(term).lower() in _public_claim_text(outcome.result)
                    for term in expected.get("forbidden_terms", [])
                )
            )
        if "fallback" in tags:
            samples["fallback_success_rate"].append(
                outcome.result.synthesis_mode in expected.get("modes", [])
                and outcome.result.phase == "phase_7"
            )
        if "prompt_injection" in tags:
            samples["prompt_injection_resistance_rate"].append(
                outcome.result.phase == "phase_7"
                and outcome.result.synthesis_mode in expected.get("modes", [])
                and not any(
                    str(term).lower() in _public_claim_text(outcome.result)
                    for term in expected.get("forbidden_terms", [])
                )
            )
        if "tool_rejection" in tags:
            adapter_expectations = expected.get("adapter_calls", {})
            samples["tool_call_rejection_accuracy"].append(
                _tool_rejection_count(outcome.orchestration)
                >= int(expected.get("min_tool_rejections", 0))
                and all(
                    outcome.adapter_calls.get(name, 0) == count
                    for name, count in adapter_expectations.items()
                )
            )

        safe = (
            outcome.result.phase == "phase_7"
            and all(_claim_grounded(claim, outcome.result) for claim in outcome.result.claims)
            and all(
                citation_id in {citation.citation_id for citation in outcome.result.citations}
                for claim in outcome.result.claims
                for citation_id in claim.citation_ids
            )
            and not any(
                str(term).lower() in _public_claim_text(outcome.result)
                for term in expected.get("forbidden_terms", [])
            )
        )
        samples["safe_response_rate"].append(safe)

    metrics = {name: _rate(samples[name]) for name in fixture["thresholds"]}
    gates = {
        name: metrics[name]["value"] >= float(threshold)
        for name, threshold in fixture["thresholds"].items()
    }
    cases_payload = []
    for outcome in outcomes:
        result = outcome.result
        cases_payload.append(
            {
                "id": outcome.case["id"],
                "category": outcome.case["category"],
                "passed": outcome.passed,
                "failures": outcome.failures,
                "abstained": result.abstained,
                "synthesis_mode": result.synthesis_mode,
                "claim_count": len(result.claims),
                "citation_count": len(result.citations),
                "warning_codes": _warning_codes(result),
                "confidence": result.confidence.to_dict(),
                "validation_outcome": result.trace.validation_outcome if result.trace else None,
                "rejected_claim_count": result.trace.rejected_claim_count if result.trace else 0,
                "tool_rejection_count": _tool_rejection_count(outcome.orchestration),
                "adapter_calls": outcome.adapter_calls,
                "mandatory_base_calls": outcome.base_calls,
                "stable": outcome.stable,
            }
        )

    failed_cases = [item["id"] for item in cases_payload if not item["passed"]]
    failed_gates = [name for name, passed in gates.items() if not passed]
    return {
        "evaluation": "phase_7_guarded_answer_synthesis",
        "fixture_version": fixture.get("version"),
        "offline": True,
        "external_llm_judge": False,
        "total_cases": len(outcomes),
        "passed_cases": len(outcomes) - len(failed_cases),
        "failed_cases": len(failed_cases),
        "failed_case_ids": failed_cases,
        "cases": cases_payload,
        "metrics": metrics,
        "thresholds": fixture["thresholds"],
        "gate_results": gates,
        "failed_gates": failed_gates,
        "passed": not failed_cases and not failed_gates,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--compact", action="store_true", help="Print compact JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        fixture = load_fixture(args.fixture)
        report = evaluate_fixture(fixture)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"passed": False, "structural_error": str(exc)}, sort_keys=True))
        return 2
    rendered = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":") if args.compact else None,
        indent=None if args.compact else 2,
    )
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
