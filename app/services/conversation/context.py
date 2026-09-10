"""
Phase 8 deterministic conversation context selection.

The only thing that crosses a turn boundary is a small set of allowlisted,
deterministically extracted entity slots (make, model, model year,
component). Prior conversation text — user or assistant — is never
forwarded to retrieval, to the provider, or to `GuardedAnswerService`.

That is the structural prompt-injection defense: an instruction planted in
turn 1 cannot survive into turn 2 because no turn-1 text is carried, only
recognized entity values drawn from a closed vocabulary.

Prior assistant output is never treated as evidence. Context only widens
what the current turn retrieves; it never supplies a claim or a citation.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.services.conversation.models import (
    MAX_RESOLVED_QUESTION_CHARS,
    ConversationEntities,
    ResolvedContext,
)
from app.services.sql_analytics.question_parser import extract_vehicle_slots

# Closed component vocabulary. Values are drawn from NHTSA component naming
# and are matched by keyword, never taken verbatim from user text.
COMPONENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SERVICE BRAKES", ("brake", "brakes", "braking")),
    ("AIR BAGS", ("air bag", "airbag", "airbags", "air bags")),
    ("STEERING", ("steering",)),
    ("ENGINE", ("engine",)),
    ("FUEL SYSTEM", ("fuel system", "fuel pump", "fuel tank")),
    ("ELECTRICAL SYSTEM", ("electrical", "wiring", "battery")),
    ("POWER TRAIN", ("power train", "powertrain", "transmission", "drivetrain")),
    ("TIRES", ("tire", "tires")),
    ("SEAT BELTS", ("seat belt", "seatbelt", "seat belts", "seatbelts")),
    ("SUSPENSION", ("suspension",)),
    ("VISIBILITY", ("windshield", "wiper", "wipers", "visibility")),
)

# Words that steer Phase 7 mandatory base retrieval. Carried context must
# never contain them, or an inherited slot could silently change routing.
_ROUTING_WORDS = ("complaint", "recall")


def extract_component(text: str) -> str | None:
    """Return an allowlisted component name, or None. Never returns user text."""
    lowered = (text or "").lower()
    for canonical, keywords in COMPONENT_KEYWORDS:
        for keyword in keywords:
            if keyword in lowered:
                return canonical
    return None


def extract_entities(text: str) -> ConversationEntities:
    """Deterministically extract allowlisted entity slots from one question."""
    slots = extract_vehicle_slots(text or "")
    return ConversationEntities(
        make=slots.get("make"),
        model=slots.get("model"),
        model_year=slots.get("model_year"),
        component=extract_component(text or ""),
    )


def _same_vehicle(current: ConversationEntities, prior: ConversationEntities) -> bool:
    """True when the current turn does not name a different vehicle than prior."""
    if current.make and prior.make and current.make != prior.make:
        return False
    if current.model and prior.model and current.model != prior.model:
        return False
    return True


def _render_context(entities: ConversationEntities, max_chars: int) -> str:
    """Render inherited slots as a short, routing-neutral clause."""
    vehicle_parts = [
        part
        for part in (
            entities.make,
            entities.model,
            str(entities.model_year) if entities.model_year else None,
        )
        if part
    ]
    fragments = []
    if vehicle_parts:
        fragments.append(f"for {' '.join(vehicle_parts)}")
    if entities.component:
        fragments.append(f"component {entities.component}")
    if not fragments:
        return ""
    clause = f" ({'; '.join(fragments)})"
    if any(word in clause.lower() for word in _ROUTING_WORDS):
        # Defensive: the closed vocabularies above cannot produce a routing
        # word today. If one is ever added, drop context rather than let an
        # inherited slot change Phase 7 retrieval routing.
        return ""
    if len(clause) > max_chars:
        return ""
    return clause


def resolve_context(
    question: str,
    prior_entities: Sequence[ConversationEntities],
    *,
    max_context_turns: int = 5,
    max_context_chars: int = 300,
) -> ResolvedContext:
    """
    Resolve a follow-up question against bounded prior-turn entity state.

    `prior_entities` must be ordered oldest-first and already limited to one
    conversation. Only the newest `max_context_turns` entries are consulted.

    Carryover rules:
      * A slot the current question states itself is never overridden.
      * When the current question names no vehicle at all, every missing slot
        may be inherited from the most recent turn that has it.
      * When the current question does name a vehicle, only slots consistent
        with that vehicle are inherited (a new vehicle resets the context).
      * Context is dropped entirely when it would exceed the character bound.
    """
    question = (question or "").strip()
    current = extract_entities(question)

    window = list(prior_entities)[-max(max_context_turns, 0) :] if max_context_turns > 0 else []
    turns_considered = len(window)

    make = current.make
    model = current.model
    model_year = current.model_year
    component = current.component
    inherited: list[str] = []
    context_turn_index: int | None = None

    # Newest first: the most recent turn that supplies a slot wins.
    for offset, prior in enumerate(reversed(window)):
        if not _same_vehicle(current, prior):
            # A different vehicle was named; older context is not applicable.
            break
        used = False
        if make is None and prior.make:
            make, used = prior.make, True
            inherited.append("make")
        if model is None and prior.model:
            model, used = prior.model, True
            inherited.append("model")
        if model_year is None and prior.model_year:
            model_year, used = prior.model_year, True
            inherited.append("model_year")
        if component is None and prior.component:
            component, used = prior.component, True
            inherited.append("component")
        if used and context_turn_index is None:
            context_turn_index = len(window) - 1 - offset
        if make and model and model_year and component:
            break

    resolved_entities = ConversationEntities(
        make=make, model=model, model_year=model_year, component=component
    )

    if not inherited:
        return ResolvedContext(
            resolved_question=question[:MAX_RESOLVED_QUESTION_CHARS],
            entities=resolved_entities,
            context_applied=False,
            inherited_slots=[],
            context_turn_index=None,
            context_chars=0,
            turns_considered=turns_considered,
        )

    inherited_only = ConversationEntities(
        make=make if "make" in inherited else None,
        model=model if "model" in inherited else None,
        model_year=model_year if "model_year" in inherited else None,
        component=component if "component" in inherited else None,
    )
    clause = _render_context(inherited_only, max_context_chars)
    resolved_question = f"{question}{clause}"

    if not clause or len(resolved_question) > MAX_RESOLVED_QUESTION_CHARS:
        # Deterministic truncation: keep the current question intact and drop
        # inherited context rather than truncate mid-clause.
        return ResolvedContext(
            resolved_question=question[:MAX_RESOLVED_QUESTION_CHARS],
            entities=current,
            context_applied=False,
            inherited_slots=[],
            context_turn_index=None,
            context_chars=0,
            turns_considered=turns_considered,
        )

    return ResolvedContext(
        resolved_question=resolved_question,
        entities=resolved_entities,
        context_applied=True,
        inherited_slots=sorted(set(inherited)),
        context_turn_index=context_turn_index,
        context_chars=len(clause),
        turns_considered=turns_considered,
    )
