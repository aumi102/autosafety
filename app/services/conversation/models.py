"""
Phase 8 conversation models.

Session-scoped multi-turn contract layered strictly on top of the final
Phase 7 guarded answer contract. These models never carry provider prompts,
provider raw responses, API keys, connection strings, database or Neo4j
clients, raw SQL, or raw Cypher.

Deterministic serialization. Bounded text and list sizes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.answer_synthesis.guarded_models import GuardedAnswerResult

# Bounds. Kept independent of operator configuration so serialization is
# deterministic even when settings are misconfigured.
MAX_QUESTION_CHARS = 1000
MAX_RESOLVED_QUESTION_CHARS = 1000
MAX_TITLE_CHARS = 200
MAX_TURNS_RETURNED = 50
MAX_CITATIONS_PER_TURN = 20
MAX_WARNINGS = 20

# Deterministic entity carryover slots. Only these bounded, allowlisted
# fields ever cross a turn boundary — never free conversation text.
ENTITY_SLOTS = ("make", "model", "model_year", "component")


def _bound(text: str | None, limit: int) -> str:
    if not text:
        return ""
    return text[:limit]


@dataclass(frozen=True)
class ConversationEntities:
    """Deterministic, allowlisted entity slots resolved for one turn."""

    make: str | None = None
    model: str | None = None
    model_year: int | None = None
    component: str | None = None

    @property
    def has_vehicle(self) -> bool:
        return bool(self.make or self.model)

    @property
    def is_empty(self) -> bool:
        return not (self.make or self.model or self.model_year or self.component)

    def to_dict(self) -> dict:
        return {
            "make": self.make,
            "model": self.model,
            "model_year": self.model_year,
            "component": self.component,
        }


@dataclass(frozen=True)
class ResolvedContext:
    """Outcome of deterministic follow-up resolution for one turn."""

    resolved_question: str
    entities: ConversationEntities
    context_applied: bool = False
    inherited_slots: list[str] = field(default_factory=list)
    context_turn_index: int | None = None
    context_chars: int = 0
    turns_considered: int = 0

    def to_dict(self) -> dict:
        return {
            "resolved_question": _bound(self.resolved_question, MAX_RESOLVED_QUESTION_CHARS),
            "entities": self.entities.to_dict(),
            "context_applied": self.context_applied,
            "inherited_slots": sorted(self.inherited_slots),
            "context_turn_index": self.context_turn_index,
            "context_chars": self.context_chars,
            "turns_considered": self.turns_considered,
        }


@dataclass(frozen=True)
class TurnCitationProvenance:
    """
    Cross-turn lineage for a single current-turn citation.

    `first_seen_turn_index` records the earliest turn in this conversation
    that retrieved the same source record. It is lineage only: it never
    authorizes a claim. Every accepted claim is validated against the
    citations retrieved in its own turn.
    """

    citation_id: str
    source_type: str
    source_record_key: str
    first_seen_turn_index: int
    reused_from_prior_turn: bool
    cited_by_claim: bool

    def to_dict(self) -> dict:
        return {
            "citation_id": self.citation_id,
            "source_type": self.source_type,
            "source_record_key": self.source_record_key,
            "first_seen_turn_index": self.first_seen_turn_index,
            "reused_from_prior_turn": self.reused_from_prior_turn,
            "cited_by_claim": self.cited_by_claim,
        }


@dataclass(frozen=True)
class ConversationSummaryView:
    """Safe public view of a conversation. Exposes no internal policy."""

    conversation_id: str
    title: str | None
    created_at: str
    last_activity_at: str
    turn_count: int
    phase: str = "phase_8"

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "title": _bound(self.title, MAX_TITLE_CHARS) or None,
            "created_at": self.created_at,
            "last_activity_at": self.last_activity_at,
            "turn_count": self.turn_count,
            "phase": self.phase,
        }


@dataclass(frozen=True)
class ConversationTurnView:
    """Safe public view of one stored turn. Never exposes provider internals."""

    turn_id: str
    turn_index: int
    question: str
    resolved_question: str
    context_applied: bool
    entities: ConversationEntities
    answer: str
    synthesis_mode: str
    provider: str
    abstained: bool
    abstention_reason: str | None
    confidence_score: float
    confidence_level: str
    claim_count: int
    citation_count: int
    warnings: list[str]
    created_at: str

    def to_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "turn_index": self.turn_index,
            "question": _bound(self.question, MAX_QUESTION_CHARS),
            "resolved_question": _bound(self.resolved_question, MAX_RESOLVED_QUESTION_CHARS),
            "context_applied": self.context_applied,
            "entities": self.entities.to_dict(),
            "answer": self.answer,
            "synthesis_mode": self.synthesis_mode,
            "provider": self.provider,
            "abstained": self.abstained,
            "abstention_reason": self.abstention_reason,
            "confidence_score": round(self.confidence_score, 4),
            "confidence_level": self.confidence_level,
            "claim_count": self.claim_count,
            "citation_count": self.citation_count,
            "warnings": self.warnings[:MAX_WARNINGS],
            "created_at": self.created_at,
        }


@dataclass
class ConversationTurnResult:
    """
    Final Phase 8 conversational answer contract.

    Wraps — and never replaces — the Phase 7 `GuardedAnswerResult`. The
    guarded answer remains the sole authority on claims, citations,
    warnings, confidence, and abstention.
    """

    conversation_id: str
    turn_id: str
    turn_index: int
    question: str
    context: ResolvedContext
    guarded: GuardedAnswerResult
    provenance: list[TurnCitationProvenance] = field(default_factory=list)
    conversation_warnings: list[str] = field(default_factory=list)
    phase: str = "phase_8"

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "turn_index": self.turn_index,
            "question": _bound(self.question, MAX_QUESTION_CHARS),
            "context": self.context.to_dict(),
            "guarded_answer": self.guarded.to_dict(),
            "provenance": [p.to_dict() for p in self.provenance[:MAX_CITATIONS_PER_TURN]],
            "conversation_warnings": self.conversation_warnings[:MAX_WARNINGS],
            "phase": self.phase,
        }


class ConversationNotFoundError(LookupError):
    """Raised when a conversation id does not resolve to a live conversation."""


class ConversationLimitError(ValueError):
    """Raised when a conversation exceeds its configured bounded turn limit."""
