"""
Phase 8 conversation persistence.

PostgreSQL is the single authoritative store for conversation state. Redis
is deliberately not used here: a second copy of durable conversation state
would create a second source of truth without improving the documented
architecture.

Every read and write is filtered by `session_id`, so one conversation can
never observe another conversation's state.

Persisted fields are bounded and non-sensitive: the user question, the
application-validated answer text, allowlisted entity slots, guarded
outcome metadata, and citation lineage. Provider prompts, provider raw
responses, API keys, connection strings, raw SQL, and raw Cypher are never
written by this module.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models.app import ChatMessage, ChatSession, ChatTurn, ChatTurnCitation
from app.services.answer_synthesis.guarded_models import GuardedCitation
from app.services.conversation.models import (
    MAX_QUESTION_CHARS,
    MAX_RESOLVED_QUESTION_CHARS,
    MAX_TITLE_CHARS,
    ConversationEntities,
    ConversationSummaryView,
    ConversationTurnView,
)

MAX_STORED_TEXT_SPAN_CHARS = 500
_SCORE_SCALE = 10000


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _score_to_int(score: float) -> int:
    return int(round(max(0.0, min(1.0, float(score or 0.0))) * _SCORE_SCALE))


def _score_from_int(value: int | None) -> float:
    return round((value or 0) / _SCORE_SCALE, 4)


def parse_conversation_id(conversation_id: str) -> uuid.UUID | None:
    """Parse an untrusted conversation id. Returns None when malformed."""
    try:
        return uuid.UUID(str(conversation_id))
    except (ValueError, AttributeError, TypeError):
        return None


class ConversationRepository:
    """Session-scoped conversation persistence over a SQLAlchemy Session."""

    def __init__(self, session: Session):
        self._session = session

    # ---------------------------------------------------------------- sessions

    def create_conversation(self, title: str | None = None) -> ChatSession:
        now = datetime.now(UTC)
        conversation = ChatSession(
            id=uuid.uuid4(),
            user_id=None,
            title=(title or None) and title.strip()[:MAX_TITLE_CHARS] or None,
            created_at=now,
            last_activity_at=now,
        )
        self._session.add(conversation)
        self._session.flush()
        return conversation

    def get_conversation(self, conversation_id: str) -> ChatSession | None:
        parsed = parse_conversation_id(conversation_id)
        if parsed is None:
            return None
        return self._session.get(ChatSession, parsed)

    def count_turns(self, conversation_id: uuid.UUID) -> int:
        stmt = (
            select(func.count()).select_from(ChatTurn).where(ChatTurn.session_id == conversation_id)
        )
        return int(self._session.execute(stmt).scalar_one() or 0)

    def touch(self, conversation: ChatSession) -> None:
        conversation.last_activity_at = datetime.now(UTC)
        self._session.add(conversation)

    def summarize(self, conversation: ChatSession) -> ConversationSummaryView:
        return ConversationSummaryView(
            conversation_id=str(conversation.id),
            title=conversation.title,
            created_at=_iso(conversation.created_at),
            last_activity_at=_iso(conversation.last_activity_at),
            turn_count=self.count_turns(conversation.id),
        )

    def delete_conversation(self, conversation_id: str) -> bool:
        """Hard-delete a conversation and all dependent rows. Privacy first."""
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            return False
        cid = conversation.id
        # Explicit deletes keep behavior identical on backends that do not
        # enforce ON DELETE CASCADE (for example SQLite without pragma).
        self._session.execute(delete(ChatTurnCitation).where(ChatTurnCitation.session_id == cid))
        self._session.execute(delete(ChatTurn).where(ChatTurn.session_id == cid))
        self._session.execute(delete(ChatMessage).where(ChatMessage.session_id == cid))
        self._session.execute(delete(ChatSession).where(ChatSession.id == cid))
        self._session.flush()
        return True

    def purge_expired(self, retention_days: int) -> int:
        """Delete conversations idle beyond the configured retention window."""
        if retention_days <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        stmt = select(ChatSession.id).where(ChatSession.last_activity_at < cutoff)
        expired = [row[0] for row in self._session.execute(stmt).all()]
        for conversation_id in expired:
            self.delete_conversation(str(conversation_id))
        return len(expired)

    # ---------------------------------------------------------------- messages

    def add_message(
        self, conversation_id: uuid.UUID, role: str, content: str, limit: int
    ) -> ChatMessage:
        if role not in ("user", "assistant", "system"):
            raise ValueError("unsupported chat message role")
        message = ChatMessage(
            id=uuid.uuid4(),
            session_id=conversation_id,
            role=role,
            content=(content or "")[:limit],
            created_at=datetime.now(UTC),
        )
        self._session.add(message)
        self._session.flush()
        return message

    # ------------------------------------------------------------------- turns

    def next_turn_index(self, conversation_id: uuid.UUID) -> int:
        stmt = select(func.max(ChatTurn.turn_index)).where(ChatTurn.session_id == conversation_id)
        current = self._session.execute(stmt).scalar_one_or_none()
        return 0 if current is None else int(current) + 1

    def list_turns(self, conversation_id: uuid.UUID, limit: int = 50) -> list[ChatTurn]:
        stmt = (
            select(ChatTurn)
            .where(ChatTurn.session_id == conversation_id)
            .order_by(ChatTurn.turn_index.asc())
            .limit(max(1, limit))
        )
        return list(self._session.execute(stmt).scalars().all())

    def prior_entities(self, conversation_id: uuid.UUID, limit: int) -> list[ConversationEntities]:
        """Return allowlisted entity slots of recent turns, oldest-first."""
        if limit <= 0:
            return []
        stmt = (
            select(
                ChatTurn.entity_make,
                ChatTurn.entity_model,
                ChatTurn.entity_model_year,
                ChatTurn.entity_component,
            )
            .where(ChatTurn.session_id == conversation_id)
            .order_by(ChatTurn.turn_index.desc())
            .limit(limit)
        )
        rows = list(self._session.execute(stmt).all())
        rows.reverse()
        return [
            ConversationEntities(make=row[0], model=row[1], model_year=row[2], component=row[3])
            for row in rows
        ]

    def prior_citation_turn_index(
        self, conversation_id: uuid.UUID, source_record_keys: Sequence[str]
    ) -> dict[str, int]:
        """Map source_record_key -> earliest prior turn index in this conversation."""
        keys = [k for k in dict.fromkeys(source_record_keys) if k]
        if not keys:
            return {}
        stmt = (
            select(
                ChatTurnCitation.source_record_key,
                func.min(ChatTurnCitation.turn_index),
            )
            .where(
                ChatTurnCitation.session_id == conversation_id,
                ChatTurnCitation.source_record_key.in_(keys),
            )
            .group_by(ChatTurnCitation.source_record_key)
        )
        return {row[0]: int(row[1]) for row in self._session.execute(stmt).all()}

    def add_turn(
        self,
        *,
        conversation_id: uuid.UUID,
        turn_index: int,
        user_message_id: uuid.UUID | None,
        assistant_message_id: uuid.UUID | None,
        question: str,
        resolved_question: str,
        context_applied: bool,
        entities: ConversationEntities,
        synthesis_mode: str,
        provider: str,
        abstained: bool,
        abstention_reason: str | None,
        confidence_score: float,
        confidence_level: str,
        claim_count: int,
        citation_count: int,
        warnings: Sequence[str],
    ) -> ChatTurn:
        turn = ChatTurn(
            id=uuid.uuid4(),
            session_id=conversation_id,
            turn_index=turn_index,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            question=(question or "")[:MAX_QUESTION_CHARS],
            resolved_question=(resolved_question or "")[:MAX_RESOLVED_QUESTION_CHARS],
            context_applied=bool(context_applied),
            entity_make=(entities.make or None) and entities.make[:64],
            entity_model=(entities.model or None) and entities.model[:64],
            entity_model_year=entities.model_year,
            entity_component=(entities.component or None) and entities.component[:64],
            synthesis_mode=synthesis_mode[:20],
            provider=(provider or "")[:64],
            abstained=bool(abstained),
            abstention_reason=(abstention_reason or None) and abstention_reason[:128],
            confidence_score=_score_to_int(confidence_score),
            confidence_level=(confidence_level or "low")[:10],
            claim_count=int(claim_count),
            citation_count=int(citation_count),
            warnings=list(warnings)[:20],
            created_at=datetime.now(UTC),
        )
        self._session.add(turn)
        self._session.flush()
        return turn

    def add_turn_citations(
        self,
        *,
        conversation_id: uuid.UUID,
        turn_id: uuid.UUID,
        turn_index: int,
        citations: Sequence[GuardedCitation],
        cited_ids: set[str],
        limit: int,
    ) -> int:
        stored = 0
        for citation in list(citations)[: max(0, limit)]:
            self._session.add(
                ChatTurnCitation(
                    id=uuid.uuid4(),
                    turn_id=turn_id,
                    session_id=conversation_id,
                    turn_index=turn_index,
                    citation_id=(citation.citation_id or "")[:64],
                    source_type=(citation.source_type or "")[:32],
                    source_record_key=(citation.source_record_key or "")[:128],
                    source_entity_id=(citation.source_entity_id or None)
                    and str(citation.source_entity_id)[:64],
                    title=citation.title,
                    source_url=citation.source_url,
                    text_span=(citation.text_span or "")[:MAX_STORED_TEXT_SPAN_CHARS],
                    retrieval_score=_score_to_int(citation.retrieval_score),
                    relation_basis=(citation.relation_basis or None)
                    and str(citation.relation_basis)[:64],
                    tool_name=(citation.tool_name or None) and str(citation.tool_name)[:64],
                    cited_by_claim=citation.citation_id in cited_ids,
                )
            )
            stored += 1
        self._session.flush()
        return stored

    # -------------------------------------------------------------------- views

    @staticmethod
    def turn_view(turn: ChatTurn, answer: str) -> ConversationTurnView:
        return ConversationTurnView(
            turn_id=str(turn.id),
            turn_index=turn.turn_index,
            question=turn.question,
            resolved_question=turn.resolved_question,
            context_applied=bool(turn.context_applied),
            entities=ConversationEntities(
                make=turn.entity_make,
                model=turn.entity_model,
                model_year=turn.entity_model_year,
                component=turn.entity_component,
            ),
            answer=answer,
            synthesis_mode=turn.synthesis_mode,
            provider=turn.provider,
            abstained=bool(turn.abstained),
            abstention_reason=turn.abstention_reason,
            confidence_score=_score_from_int(turn.confidence_score),
            confidence_level=turn.confidence_level,
            claim_count=turn.claim_count,
            citation_count=turn.citation_count,
            warnings=list(turn.warnings or []),
            created_at=_iso(turn.created_at),
        )

    def assistant_message_text(self, message_id: uuid.UUID | None) -> str:
        if message_id is None:
            return ""
        message = self._session.get(ChatMessage, message_id)
        return message.content if message else ""
