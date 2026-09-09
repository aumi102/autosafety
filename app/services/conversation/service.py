"""
Phase 8 conversation service.

Application-owned, session-scoped multi-turn answering built strictly on
top of the final Phase 7 guarded contract:

    conversation
      -> bounded prior-turn entity state
      -> deterministic context resolution
      -> GuardedAnswerService (mandatory GraphRAG, tools, validation)
      -> cross-turn provenance enforcement
      -> bounded persisted turn

`GuardedAnswerService` remains the sole authority on claims, citations,
warnings, confidence, and abstention. This service never synthesizes an
answer, never validates a claim itself, never relaxes a Phase 7 decision,
and never forwards prior conversation text to retrieval or to a provider.

Prior assistant output is not evidence. A stored citation is lineage only:
it can show that a source record was seen before, but it can never back a
claim in a later turn. Every accepted claim in every turn is validated
against the citations that turn actually retrieved.
"""

from __future__ import annotations

import logging
import time
import uuid as uuid_module
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.services.answer_synthesis.guarded_models import (
    ConfidenceResult,
    GuardedAnswerResult,
    GuardedTrace,
    RetrievalSummary,
)
from app.services.answer_synthesis.policy import UNCITED_ALLOWED_CLAIM_TYPES
from app.services.answer_synthesis.service import GuardedAnswerService
from app.services.conversation.context import resolve_context
from app.services.conversation.models import (
    MAX_QUESTION_CHARS,
    MAX_TITLE_CHARS,
    MAX_TURNS_RETURNED,
    ConversationEntities,
    ConversationLimitError,
    ConversationNotFoundError,
    ConversationSummaryView,
    ConversationTurnResult,
    ConversationTurnView,
    ResolvedContext,
    TurnCitationProvenance,
)
from app.services.conversation.repository import ConversationRepository
from app.services.observability.context import audit_run

logger = logging.getLogger(__name__)

ABSTENTION_ANSWER = (
    "I cannot provide a reliable answer to this question based on available evidence."
)
PROVENANCE_VIOLATION_REASON = "cross_turn_provenance_violation"
CONTEXT_APPLIED_WARNING = (
    "This follow-up reused vehicle context from an earlier turn. Prior answers were "
    "not used as evidence; every claim above was validated against evidence retrieved "
    "for this turn."
)
PROVENANCE_VIOLATION_WARNING = (
    "An answer claim referenced evidence that was not retrieved for this turn, so the "
    "answer was withheld."
)


class ConversationService:
    """Bounded, session-scoped multi-turn guarded answering."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        guarded_service: GuardedAnswerService,
        settings: Settings | None = None,
        audit_recorder: object | None = None,
    ):
        self._session_factory = session_factory
        self._guarded = guarded_service
        self._settings = settings or get_settings()
        # Phase 9 execution audit. Optional: when absent nothing is recorded and
        # behavior is identical. Audit never changes an answer.
        self._audit = audit_recorder

    # ---------------------------------------------------------------- lifecycle

    def start_conversation(self, title: str | None = None) -> ConversationSummaryView:
        """Create an empty conversation. Title is bounded operator-visible text."""
        with self._unit_of_work() as session:
            repo = ConversationRepository(session)
            conversation = repo.create_conversation((title or "").strip()[:MAX_TITLE_CHARS] or None)
            return repo.summarize(conversation)

    def get_conversation(self, conversation_id: str) -> ConversationSummaryView:
        with self._unit_of_work() as session:
            repo = ConversationRepository(session)
            conversation = repo.get_conversation(conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(conversation_id)
            return repo.summarize(conversation)

    def list_turns(
        self, conversation_id: str, limit: int = MAX_TURNS_RETURNED
    ) -> list[ConversationTurnView]:
        with self._unit_of_work() as session:
            repo = ConversationRepository(session)
            conversation = repo.get_conversation(conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(conversation_id)
            bounded = max(1, min(int(limit or MAX_TURNS_RETURNED), MAX_TURNS_RETURNED))
            return [
                repo.turn_view(turn, repo.assistant_message_text(turn.assistant_message_id))
                for turn in repo.list_turns(conversation.id, bounded)
            ]

    def delete_conversation(self, conversation_id: str) -> bool:
        """Hard-delete a conversation and every dependent row it owns."""
        with self._unit_of_work() as session:
            return ConversationRepository(session).delete_conversation(conversation_id)

    def purge_expired(self) -> int:
        """Delete conversations idle past the configured retention window."""
        with self._unit_of_work() as session:
            return ConversationRepository(session).purge_expired(
                int(self._settings.PHASE8_CONVERSATION_RETENTION_DAYS)
            )

    # ------------------------------------------------------------------ answer

    def answer(self, conversation_id: str, question: str) -> ConversationTurnResult:
        """Answer one conversational turn through the full Phase 7 guarded path."""
        question = (question or "").strip()[:MAX_QUESTION_CHARS]

        # Read phase. The connection is released before the guarded call so a
        # slow provider round never holds a pooled connection open.
        with self._unit_of_work() as session:
            repo = ConversationRepository(session)
            conversation = repo.get_conversation(conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(conversation_id)
            resolved_conversation_id = conversation.id
            turn_count = repo.count_turns(resolved_conversation_id)
            max_turns = int(self._settings.PHASE8_MAX_TURNS_PER_CONVERSATION)
            if max_turns > 0 and turn_count >= max_turns:
                raise ConversationLimitError(
                    f"conversation reached its bounded limit of {max_turns} turns"
                )
            prior_entities = repo.prior_entities(
                resolved_conversation_id, int(self._settings.PHASE8_MAX_CONTEXT_TURNS)
            )

        context = resolve_context(
            question,
            prior_entities,
            max_context_turns=int(self._settings.PHASE8_MAX_CONTEXT_TURNS),
            max_context_chars=int(self._settings.PHASE8_MAX_CONTEXT_CHARS),
        )

        audit = self._start_audit_run(resolved_conversation_id)
        started = time.time()
        if not question:
            guarded = self._abstain(context.resolved_question, "empty_question")
        else:
            # Only the resolved question crosses this boundary. No prior user
            # text, no prior assistant text, and no prior citation is passed in.
            # The audit run is scoped here so tool executions inside the guarded
            # path are attributed to it.
            with audit_run(audit):
                guarded = self._guarded.answer(context.resolved_question)

        conversation_warnings: list[str] = []
        guarded, violation = self._enforce_turn_provenance(guarded)
        if violation:
            conversation_warnings.append(PROVENANCE_VIOLATION_WARNING)
        elif context.context_applied:
            conversation_warnings.append(CONTEXT_APPLIED_WARNING)

        result = self._persist_turn(
            conversation_id=conversation_id,
            question=question,
            context=context,
            guarded=guarded,
            conversation_warnings=conversation_warnings,
        )
        self._finish_audit_run(audit, guarded, result, started)
        return result

    # ------------------------------------------------------------------ audit

    def _start_audit_run(self, conversation_id) -> object | None:
        """Open a Phase 9 audit run. Never fails the request."""
        if self._audit is None:
            return None
        try:
            return self._audit.start_run(
                surface="api_conversation", conversation_id=conversation_id
            )
        except Exception as exc:
            logger.warning("Execution audit: run not opened (%s)", type(exc).__name__)
            return None

    def _finish_audit_run(self, audit, guarded, result, started: float) -> None:
        """Close the audit run and link the persisted conversation and turn."""
        if audit is None or self._audit is None:
            return
        try:
            self._audit.finish_run(
                audit.run_id, guarded, latency_ms=int((time.time() - started) * 1000)
            )
            self._audit.link_conversation_turn(
                audit.run_id,
                conversation_id=uuid_module.UUID(result.conversation_id),
                turn_id=uuid_module.UUID(result.turn_id),
            )
        except Exception as exc:
            logger.warning("Execution audit: run not finalized (%s)", type(exc).__name__)

    # ------------------------------------------------------------- provenance

    def _enforce_turn_provenance(
        self, guarded: GuardedAnswerResult
    ) -> tuple[GuardedAnswerResult, bool]:
        """
        Defense in depth over the Phase 7 citation validator.

        Phase 7 already rejects unsupported claims. This re-check exists
        because Phase 8 adds turns: it guarantees that nothing a previous
        turn established can leak into this turn's accepted claims. A claim
        may only cite a citation this turn actually retrieved.
        """
        if guarded.abstained:
            return guarded, False

        available = {c.citation_id for c in guarded.citations}
        for claim in guarded.claims:
            # Phase 7 owns the claim-type vocabulary; only system-level
            # limitation statements are allowed to carry no citation.
            if claim.claim_type in UNCITED_ALLOWED_CLAIM_TYPES:
                continue
            if not claim.citation_ids:
                logger.warning("Phase 8 provenance: factual claim carried no citation")
                return self._abstain(guarded.query, PROVENANCE_VIOLATION_REASON), True
            if not set(claim.citation_ids).issubset(available):
                logger.warning("Phase 8 provenance: claim cited evidence outside this turn")
                return self._abstain(guarded.query, PROVENANCE_VIOLATION_REASON), True
        return guarded, False

    @staticmethod
    def _abstain(question: str, reason: str) -> GuardedAnswerResult:
        """Build a Phase 7-shaped abstention without reaching into Phase 7 internals."""
        return GuardedAnswerResult(
            query=question,
            answer=ABSTENTION_ANSWER,
            claims=[],
            citations=[],
            warnings=[],
            confidence=ConfidenceResult(
                score=0.0, level="low", reasons=["insufficient or unsafe evidence"]
            ),
            abstained=True,
            abstention_reason=reason,
            synthesis_mode="abstention",
            provider="",
            retrieval_summary=RetrievalSummary(),
            validation=None,
            trace=GuardedTrace(
                original_provider="",
                provider_available=False,
                fallback_used=False,
                validation_outcome="abstention_required",
                repaired_claim_count=0,
                rejected_claim_count=0,
                abstention_reason=reason,
            ),
        )

    # ------------------------------------------------------------- persistence

    def _persist_turn(
        self,
        *,
        conversation_id: str,
        question: str,
        context: ResolvedContext,
        guarded: GuardedAnswerResult,
        conversation_warnings: list[str],
    ) -> ConversationTurnResult:
        max_citations = int(self._settings.PHASE8_MAX_STORED_CITATIONS_PER_TURN)
        answer_limit = int(self._settings.PHASE8_MAX_STORED_ANSWER_CHARS)
        cited_ids = {cid for claim in guarded.claims for cid in claim.citation_ids}

        with self._unit_of_work() as session:
            repo = ConversationRepository(session)
            conversation = repo.get_conversation(conversation_id)
            if conversation is None:
                # Deleted while this turn was being answered. Retention wins.
                raise ConversationNotFoundError(conversation_id)

            turn_index = repo.next_turn_index(conversation.id)
            record_keys = [c.source_record_key for c in guarded.citations[:max_citations]]
            first_seen = repo.prior_citation_turn_index(conversation.id, record_keys)

            user_message = repo.add_message(
                conversation.id, "user", question, MAX_QUESTION_CHARS
            )
            assistant_message = repo.add_message(
                conversation.id, "assistant", guarded.answer, answer_limit
            )
            turn = repo.add_turn(
                conversation_id=conversation.id,
                turn_index=turn_index,
                user_message_id=user_message.id,
                assistant_message_id=assistant_message.id,
                question=question,
                resolved_question=context.resolved_question,
                context_applied=context.context_applied,
                entities=context.entities,
                synthesis_mode=guarded.synthesis_mode,
                provider=guarded.provider,
                abstained=guarded.abstained,
                abstention_reason=guarded.abstention_reason,
                confidence_score=guarded.confidence.score,
                confidence_level=guarded.confidence.level,
                claim_count=len(guarded.claims),
                citation_count=len(guarded.citations),
                warnings=list(guarded.warnings),
            )
            repo.add_turn_citations(
                conversation_id=conversation.id,
                turn_id=turn.id,
                turn_index=turn_index,
                citations=guarded.citations,
                cited_ids=cited_ids,
                limit=max_citations,
            )
            repo.touch(conversation)

            provenance = [
                TurnCitationProvenance(
                    citation_id=citation.citation_id,
                    source_type=citation.source_type,
                    source_record_key=citation.source_record_key,
                    first_seen_turn_index=first_seen.get(citation.source_record_key, turn_index),
                    reused_from_prior_turn=citation.source_record_key in first_seen,
                    cited_by_claim=citation.citation_id in cited_ids,
                )
                for citation in guarded.citations[:max_citations]
            ]

            return ConversationTurnResult(
                conversation_id=str(conversation.id),
                turn_id=str(turn.id),
                turn_index=turn_index,
                question=question,
                context=context,
                guarded=guarded,
                provenance=provenance,
                conversation_warnings=conversation_warnings,
            )

    # ------------------------------------------------------------------- units

    class _UnitOfWork:
        def __init__(self, session: Session):
            self._session = session

        def __enter__(self) -> Session:
            return self._session

        def __exit__(self, exc_type, exc, tb) -> bool:
            try:
                if exc_type is None:
                    self._session.commit()
                else:
                    self._session.rollback()
            finally:
                self._session.close()
            return False

    def _unit_of_work(self) -> ConversationService._UnitOfWork:
        return self._UnitOfWork(self._session_factory())


def resolve_conversation_entities(question: str) -> ConversationEntities:
    """Public helper for evaluation and diagnostics. No persistence, no network."""
    return resolve_context(question, []).entities
