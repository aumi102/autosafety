#!/usr/bin/env python3
"""Offline deterministic Phase 8 multi-turn conversation evaluation.

Each fixture turn runs the real `ConversationService` over the real Phase 7
`GuardedAnswerService`, `SynthesisOrchestrator`, tool registry, and Phase 6
GraphRAG adapter. Controlled providers and controlled evidence isolate
cross-turn semantics without network access, PostgreSQL, or Neo4j;
persistence uses in-memory SQLite.

Gates cover the Phase 8 invariants: cross-turn citation validity, prior
assistant text never counting as evidence, conversation isolation,
prompt-injection non-persistence, deterministic context bounds, retention,
and the preserved Phase 7 causality and recall-applicability guards.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.services.answer_synthesis.guarded_models import GuardedAnswerResult
from app.services.answer_synthesis.service import GuardedAnswerService
from app.services.conversation.models import (
    ConversationNotFoundError,
    ConversationTurnResult,
)
from app.services.conversation.service import (
    PROVENANCE_VIOLATION_REASON,
    ConversationService,
)
from scripts.evaluate_phase7_answers import (
    NONFACTUAL_CLAIM_TYPES,
    WARNING_BY_CODE,
    build_case_harness,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "phase8_conversation_eval.json"

CAUSAL_PHRASES = ("caused", "because of", "led to", "resulted in", "due to the complaints")


def _settings() -> Settings:
    """Deterministic evaluation settings, independent of the operator `.env`."""
    return Settings(
        _env_file=None,
        PHASE8_MAX_CONTEXT_TURNS=5,
        PHASE8_MAX_TURNS_PER_CONVERSATION=100,
        PHASE8_MAX_CONTEXT_CHARS=300,
        PHASE8_MAX_STORED_ANSWER_CHARS=8000,
        PHASE8_MAX_STORED_CITATIONS_PER_TURN=20,
        PHASE8_CONVERSATION_RETENTION_DAYS=30,
    )


class SequencedGuardedService:
    """Delegate turn N to the Nth real Phase 7 guarded service.

    Every call runs the complete Phase 7 path; only the controlled evidence
    and controlled provider differ per turn.
    """

    def __init__(self, services: Sequence[GuardedAnswerService]):
        self._services = list(services)
        self._index = 0
        self.questions: list[str] = []

    def answer(self, question: str) -> GuardedAnswerResult:
        self.questions.append(question)
        service = self._services[min(self._index, len(self._services) - 1)]
        self._index += 1
        return service.answer(question)


@dataclass
class TurnOutcome:
    index: int
    question: str
    result: ConversationTurnResult
    failures: list[str] = field(default_factory=list)


@dataclass
class CaseOutcome:
    case: dict[str, Any]
    turns: list[TurnOutcome]
    isolation_ok: bool
    deletion_ok: bool | None
    stable: bool
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures and all(not t.failures for t in self.turns)


def load_fixture(path: Path | str = DEFAULT_FIXTURE) -> dict[str, Any]:
    """Load and structurally validate the Phase 8 fixture."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
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
    for case in cases:
        turns = case.get("turns")
        if not isinstance(turns, list) or len(turns) < 2:
            raise ValueError(f"case {case['id']} must define at least two turns")
        for turn in turns:
            if not _turn_question(turn).strip():
                raise ValueError(f"case {case['id']} has an empty turn question")
            if "expected" not in turn:
                raise ValueError(f"case {case['id']} has a turn with no expected contract")
    return payload


def _turn_question(turn: dict[str, Any]) -> str:
    repeat = turn.get("question_repeat")
    if isinstance(repeat, dict):
        return str(repeat.get("text", "")) * int(repeat.get("times", 1))
    return str(turn.get("question", ""))


def _build_conversation_service(case: dict[str, Any], session_factory):
    """Build a real ConversationService over one real Phase 7 service per turn."""
    services = [build_case_harness(turn).service for turn in case["turns"]]
    guarded = SequencedGuardedService(services)
    service = ConversationService(
        session_factory=session_factory,
        guarded_service=guarded,
        settings=_settings(),
    )
    return service, guarded


def _public_text(result: GuardedAnswerResult) -> str:
    parts = [result.answer]
    for claim in result.claims:
        parts.append(claim.text)
    return " ".join(parts).lower()


def _check_turn(
    turn_spec: dict[str, Any],
    outcome: TurnOutcome,
    prior_turns: list[TurnOutcome],
    forwarded_question: str,
) -> None:
    expected = turn_spec.get("expected", {})
    result = outcome.result
    guarded = result.guarded
    failures = outcome.failures

    if "abstained" in expected and guarded.abstained != bool(expected["abstained"]):
        failures.append(f"abstained={guarded.abstained}, expected {expected['abstained']}")

    if "context_applied" in expected and result.context.context_applied != bool(
        expected["context_applied"]
    ):
        failures.append(
            "context_applied="
            f"{result.context.context_applied}, expected {expected['context_applied']}"
        )

    if "inherited_slots" in expected:
        if sorted(result.context.inherited_slots) != sorted(expected["inherited_slots"]):
            failures.append(f"inherited_slots={result.context.inherited_slots}")

    for key, value in (expected.get("entities") or {}).items():
        actual = getattr(result.context.entities, key)
        if actual != value:
            failures.append(f"entity {key}={actual!r}, expected {value!r}")

    for fragment in expected.get("resolved_question_contains", []):
        if fragment not in result.context.resolved_question:
            failures.append(f"resolved question missing {fragment!r}")

    for fragment in expected.get("forbidden_resolved_question_substrings", []):
        if fragment.lower() in result.context.resolved_question.lower():
            failures.append(f"resolved question leaked {fragment!r}")

    if "resolved_question_max_chars" in expected:
        limit = int(expected["resolved_question_max_chars"])
        if len(result.context.resolved_question) > limit:
            failures.append(f"resolved question exceeded {limit} chars")

    if expected.get("resolved_question_is_prefix_of_question"):
        if not outcome.question.startswith(result.context.resolved_question):
            failures.append("resolved question is not a prefix of the bounded user question")

    accepted_types = {c.claim_type for c in guarded.claims}
    for claim_type in expected.get("claim_types_include", []):
        if claim_type not in accepted_types:
            failures.append(f"missing claim type {claim_type}")
    for claim_type in expected.get("forbidden_claim_types", []):
        if claim_type in accepted_types:
            failures.append(f"forbidden claim type {claim_type} accepted")

    citation_keys = {c.source_record_key for c in guarded.citations}
    for key in expected.get("citation_source_keys", []):
        if key not in citation_keys:
            failures.append(f"missing citation source key {key}")
    for key in expected.get("forbidden_citation_source_keys", []):
        if key in citation_keys:
            failures.append(f"forbidden citation source key {key} present")

    public = _public_text(guarded)
    for fragment in expected.get("forbidden_answer_substrings", []):
        if fragment.lower() in public:
            failures.append(f"answer leaked {fragment!r}")

    for code in expected.get("required_warning_codes", []):
        warning = WARNING_BY_CODE.get(code)
        if warning and warning not in guarded.warnings:
            failures.append(f"missing required warning {code}")

    if "synthesis_modes" in expected and guarded.synthesis_mode not in expected["synthesis_modes"]:
        failures.append(f"synthesis_mode={guarded.synthesis_mode}")

    if expected.get("causal_claim_forbidden"):
        if any(phrase in public for phrase in CAUSAL_PHRASES):
            failures.append("causal language survived the guard")

    if expected.get("applicability_downgraded"):
        if "official_recall_applicability" in accepted_types:
            failures.append("applicability claim was not downgraded")

    if expected.get("stale_citation_rejected"):
        prior_keys = {
            c.source_record_key
            for prior in prior_turns
            for c in prior.result.guarded.citations
        }
        reused = prior_keys & {
            key
            for claim in guarded.claims
            for cid in claim.citation_ids
            for key in (
                c.source_record_key for c in guarded.citations if c.citation_id == cid
            )
        }
        if reused:
            failures.append(f"stale prior-turn evidence backed a claim: {sorted(reused)}")
        if not guarded.abstained and guarded.claims:
            # The provider cited turn-1 evidence that this turn never retrieved.
            # Phase 7 rejection plus the Phase 8 provenance gate must remove it.
            if any("11420001" in claim.text for claim in guarded.claims):
                failures.append("prior-turn claim text survived into the accepted answer")

    if expected.get("injection_probe"):
        for prior in prior_turns:
            if prior.question.lower()[:40] in forwarded_question.lower():
                failures.append("prior-turn user text was forwarded to Phase 7")


def _turn_metrics(outcome: TurnOutcome, forwarded_question: str, prior: list[TurnOutcome]) -> dict:
    guarded = outcome.result.guarded
    available = {c.citation_id for c in guarded.citations}
    factual = [c for c in guarded.claims if c.claim_type not in NONFACTUAL_CLAIM_TYPES]

    valid = sum(1 for c in factual if c.citation_ids and set(c.citation_ids).issubset(available))
    covered = sum(1 for c in factual if c.citation_ids)

    prior_answers = [p.result.guarded.answer for p in prior if p.result.guarded.answer]
    prior_citation_ids = {c.citation_id for p in prior for c in p.result.guarded.citations}
    lowered = forwarded_question.lower()

    return {
        "factual_claims": len(factual),
        "valid_citation_claims": valid,
        "covered_claims": covered,
        "prior_text_leaks": sum(
            1 for answer in prior_answers if answer and answer.lower()[:40] in lowered
        ),
        "prior_citation_leaks": sum(1 for cid in prior_citation_ids if cid.lower() in lowered),
        "context_within_bounds": int(len(outcome.result.context.resolved_question) <= 1000),
    }


def _run_case(case: dict[str, Any]) -> CaseOutcome:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    service, guarded = _build_conversation_service(case, session_factory)
    conversation = service.start_conversation(case.get("id"))

    turns: list[TurnOutcome] = []
    failures: list[str] = []
    for index, turn_spec in enumerate(case["turns"]):
        question = _turn_question(turn_spec)
        result = service.answer(conversation.conversation_id, question)
        outcome = TurnOutcome(index=index, question=question[:1000], result=result)
        _check_turn(turn_spec, outcome, list(turns), guarded.questions[index])
        turns.append(outcome)

    # Conversation isolation probe: a fresh conversation in the same database
    # must inherit nothing from the case conversation.
    probe_service, _ = _build_conversation_service(case, session_factory)
    probe = probe_service.start_conversation("isolation-probe")
    probe_result = probe_service.answer(probe.conversation_id, _turn_question(case["turns"][-1]))
    isolation_ok = (
        probe_result.context.context_applied is False
        and probe_result.turn_index == 0
        and all(p.reused_from_prior_turn is False for p in probe_result.provenance)
    )
    if not isolation_ok:
        failures.append("isolation probe inherited state from another conversation")

    # Deterministic stability: the same conversation replayed produces the same
    # resolved questions and the same accepted claim texts.
    replay_service, _ = _build_conversation_service(case, session_factory)
    replay = replay_service.start_conversation("stability-replay")
    replay_turns = [
        replay_service.answer(replay.conversation_id, _turn_question(spec))
        for spec in case["turns"]
    ]
    stable = all(
        a.context.resolved_question == b.result.context.resolved_question
        and [c.text for c in a.guarded.claims] == [c.text for c in b.result.guarded.claims]
        for a, b in zip(replay_turns, turns)
    )
    if not stable:
        failures.append("replay produced a different deterministic outcome")

    deletion_ok: bool | None = None
    if case.get("delete_when_done"):
        deleted = service.delete_conversation(conversation.conversation_id)
        try:
            service.get_conversation(conversation.conversation_id)
            still_present = True
        except ConversationNotFoundError:
            still_present = False
        session = session_factory()
        try:
            from app.db.models.app import ChatMessage, ChatTurn, ChatTurnCitation

            deleted_id = uuid.UUID(conversation.conversation_id)
            residue = sum(
                session.query(model).filter_by(session_id=deleted_id).count()
                for model in (ChatTurn, ChatTurnCitation, ChatMessage)
            )
        finally:
            session.close()
        deletion_ok = bool(deleted) and not still_present and residue == 0
        if not deletion_ok:
            failures.append("conversation deletion left retrievable state behind")

    return CaseOutcome(
        case=case,
        turns=turns,
        isolation_ok=isolation_ok,
        deletion_ok=deletion_ok,
        stable=stable,
        failures=failures,
    )


def _rate(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else round(numerator / denominator, 4)


def evaluate_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    outcomes = [_run_case(case) for case in fixture["cases"]]

    totals = {
        "factual_claims": 0,
        "valid_citation_claims": 0,
        "covered_claims": 0,
        "prior_text_leaks": 0,
        "prior_citation_leaks": 0,
        "context_within_bounds": 0,
        "turns": 0,
    }
    for outcome in outcomes:
        service_questions = [t.result.context.resolved_question for t in outcome.turns]
        for index, turn in enumerate(outcome.turns):
            metrics = _turn_metrics(turn, service_questions[index], outcome.turns[:index])
            totals["turns"] += 1
            for key in (
                "factual_claims",
                "valid_citation_claims",
                "covered_claims",
                "prior_text_leaks",
                "prior_citation_leaks",
                "context_within_bounds",
            ):
                totals[key] += metrics[key]

    injection_cases = [o for o in outcomes if o.case["category"].startswith("C_")]
    causal_cases = [o for o in outcomes if o.case["category"].startswith("B_")]
    applicability_cases = [o for o in outcomes if o.case["category"].startswith("I_")]

    metrics = {
        "case_pass_rate": _rate(sum(1 for o in outcomes if o.passed), len(outcomes)),
        "citation_validity_rate": _rate(
            totals["valid_citation_claims"], totals["factual_claims"]
        ),
        "citation_coverage": _rate(totals["covered_claims"], totals["factual_claims"]),
        "conversation_isolation_rate": _rate(
            sum(1 for o in outcomes if o.isolation_ok), len(outcomes)
        ),
        "prompt_injection_resistance_rate": _rate(
            sum(1 for o in injection_cases if o.passed), len(injection_cases)
        ),
        "prior_text_leak_rate": _rate(totals["prior_text_leaks"], totals["turns"]),
        "prior_citation_leak_rate": _rate(totals["prior_citation_leaks"], totals["turns"]),
        "causal_guard_success_rate": _rate(
            sum(1 for o in causal_cases if o.passed), len(causal_cases)
        ),
        "context_bound_compliance_rate": _rate(totals["context_within_bounds"], totals["turns"]),
        "official_applicability_semantic_accuracy": _rate(
            sum(1 for o in applicability_cases if o.passed), len(applicability_cases)
        ),
        "deterministic_stability_rate": _rate(
            sum(1 for o in outcomes if o.stable), len(outcomes)
        ),
    }

    gates: dict[str, Any] = {}
    failed_gates: list[str] = []
    for name, threshold in fixture["thresholds"].items():
        actual = metrics.get(name)
        if actual is None:
            continue
        # Leak-rate gates are upper bounds; every other gate is a lower bound.
        passed = actual <= threshold if name.endswith("_leak_rate") else actual >= threshold
        gates[name] = {"threshold": threshold, "actual": actual, "passed": passed}
        if not passed:
            failed_gates.append(name)

    cases_payload = []
    for outcome in outcomes:
        cases_payload.append(
            {
                "id": outcome.case["id"],
                "category": outcome.case["category"],
                "passed": outcome.passed,
                "isolation_ok": outcome.isolation_ok,
                "deletion_ok": outcome.deletion_ok,
                "deterministically_stable": outcome.stable,
                "case_failures": outcome.failures,
                "turns": [
                    {
                        "index": turn.index,
                        "context_applied": turn.result.context.context_applied,
                        "inherited_slots": turn.result.context.inherited_slots,
                        "resolved_question_chars": len(turn.result.context.resolved_question),
                        "abstained": turn.result.guarded.abstained,
                        "abstention_reason": turn.result.guarded.abstention_reason,
                        "synthesis_mode": turn.result.guarded.synthesis_mode,
                        "claim_types": [c.claim_type for c in turn.result.guarded.claims],
                        "citation_source_keys": [
                            c.source_record_key for c in turn.result.guarded.citations
                        ],
                        "provenance_reused": [
                            p.reused_from_prior_turn for p in turn.result.provenance
                        ],
                        "failures": turn.failures,
                    }
                    for turn in outcome.turns
                ],
            }
        )

    failed_cases = [c["id"] for c in cases_payload if not c["passed"]]
    return {
        "phase": "phase_8",
        "fixture_version": fixture.get("version"),
        "total_cases": len(outcomes),
        "total_turns": totals["turns"],
        "passed_cases": len(outcomes) - len(failed_cases),
        "failed_cases": len(failed_cases),
        "failed_case_ids": failed_cases,
        "cases": cases_payload,
        "metrics": metrics,
        "thresholds": fixture["thresholds"],
        "gate_results": gates,
        "failed_gates": failed_gates,
        "provenance_violation_reason": PROVENANCE_VIOLATION_REASON,
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
