"""Application-owned Phase 7E dependency wiring.

Builds the Phase 7B tool registry, Phase 7C provider/orchestrator, and
Phase 7D guarded service from trusted application configuration. User input
never selects providers, tools, database connections, or tool budgets.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import get_sync_engine
from app.services.answer_synthesis.models import SynthesisConfig
from app.services.answer_synthesis.orchestrator import (
    SynthesisOrchestrator,
    build_config_from_settings,
)
from app.services.answer_synthesis.providers import (
    DeterministicProvider,
    SynthesisProvider,
    build_synthesis_provider,
)
from app.services.answer_synthesis.service import GuardedAnswerService
from app.services.answer_synthesis.tools.registry import build_default_tool_registry
from app.services.graph.neo4j_client import verify_connectivity
from app.services.graphrag import retrieve_graphrag_evidence
from app.services.observability.audited import AuditedGuardedAnswerService
from app.services.observability.recorder import ExecutionAuditRecorder


@dataclass(frozen=True)
class AnswerSynthesisStatus:
    """Safe operational status. Contains no credentials or connection strings."""

    synthesis_available: bool
    configured_provider: str
    active_provider: str
    provider_available: bool
    real_llm_enabled: bool
    real_llm_configured: bool
    deterministic_fallback_available: bool
    tool_calling_enabled: bool
    max_tool_rounds: int
    max_tool_calls: int
    graphrag_base_required: bool
    guarded_validation_enabled: bool
    phase: str = "phase_7"

    def to_dict(self) -> dict:
        return {
            "synthesis_available": self.synthesis_available,
            "configured_provider": self.configured_provider,
            "active_provider": self.active_provider,
            "provider_available": self.provider_available,
            "real_llm_enabled": self.real_llm_enabled,
            "real_llm_configured": self.real_llm_configured,
            "deterministic_fallback_available": self.deterministic_fallback_available,
            "tool_calling_enabled": self.tool_calling_enabled,
            "max_tool_rounds": self.max_tool_rounds,
            "max_tool_calls": self.max_tool_calls,
            "graphrag_base_required": self.graphrag_base_required,
            "guarded_validation_enabled": self.guarded_validation_enabled,
            "phase": self.phase,
        }


def _provider_config(config: SynthesisConfig) -> dict:
    """Build internal provider configuration; never serialize or log this mapping."""
    return {
        "provider": config.provider,
        "model": config.model,
        "allow_external": config.allow_external,
        "api_key": config.api_key,
        "base_url": config.base_url,
        "timeout_seconds": config.timeout_seconds,
        "model_planning_enabled": config.model_planning_enabled,
    }


def build_guarded_answer_service(
    settings: Settings | None = None,
    *,
    sql_session_factory: Callable | None = None,
    neo4j_available: bool | None = None,
    graphrag_retrieval_fn: Callable | None = None,
    primary_provider: SynthesisProvider | None = None,
) -> GuardedAnswerService:
    """Build the complete application-owned Phase 7 service dependency.

    Optional arguments exist for deterministic tests and controlled runtime
    injection only. None is derived from an API request or CLI flag.
    """
    resolved_settings = settings or get_settings()
    config = build_config_from_settings(resolved_settings)

    if sql_session_factory is None:
        # Reuse the process-level engine from app.db.session. The adapter owns
        # individual Session lifetimes and closes each Session after use.
        sql_session_factory = sessionmaker(bind=get_sync_engine(), expire_on_commit=False)

    if neo4j_available is None:
        # Uses the repository's process-level Neo4j driver singleton. This is a
        # local dependency check only; it never calls an external LLM provider.
        neo4j_available = verify_connectivity()

    registry = build_default_tool_registry(
        sql_session_factory=sql_session_factory,
        neo4j_available=neo4j_available,
        graphrag_retrieval_fn=graphrag_retrieval_fn or retrieve_graphrag_evidence,
    )

    provider = primary_provider or build_synthesis_provider(_provider_config(config))
    orchestrator = SynthesisOrchestrator(
        registry=registry,
        primary_provider=provider,
        deterministic_fallback=DeterministicProvider(),
        config=config,
    )
    return GuardedAnswerService(orchestrator)


def build_execution_audit_recorder(
    settings: Settings | None = None,
    *,
    session_factory: Callable | None = None,
) -> ExecutionAuditRecorder:
    """Build the Phase 9 execution audit recorder from trusted configuration."""
    resolved = settings or get_settings()
    if session_factory is None:
        session_factory = sessionmaker(bind=get_sync_engine(), expire_on_commit=False)
    return ExecutionAuditRecorder(
        session_factory, enabled=bool(getattr(resolved, "PHASE9_AUDIT_ENABLED", True))
    )


@lru_cache(maxsize=1)
def get_unaudited_guarded_answer_service() -> GuardedAnswerService:
    """Return the single process-level Phase 7 service graph, without audit.

    Callers that open their own Phase 9 audit run — the conversation service
    does — must use this, so one execution never opens two nested audit runs.
    """
    return build_guarded_answer_service()


@lru_cache(maxsize=1)
def get_guarded_answer_service() -> AuditedGuardedAnswerService:
    """Return the audited process-level service for FastAPI dependency injection.

    The wrapper is transparent: it forwards the question unchanged and returns
    the guarded contract unchanged.
    """
    return AuditedGuardedAnswerService(
        get_unaudited_guarded_answer_service(),
        build_execution_audit_recorder(),
        surface="api_guarded",
    )


def get_answer_synthesis_status(
    settings: Settings | None = None,
) -> AnswerSynthesisStatus:
    """Return configuration/availability status without any network request."""
    config = build_config_from_settings(settings or get_settings())
    configured_provider = config.provider

    credentials_configured = bool(config.api_key and config.api_key != "changeme")
    real_llm_configured = bool(credentials_configured and config.model and config.base_url)

    if configured_provider == "deterministic":
        provider_available = True
        active_provider = "deterministic"
    elif configured_provider == "openai_compatible":
        provider_available = bool(config.allow_external and real_llm_configured)
        active_provider = "openai_compatible" if provider_available else "deterministic"
    elif configured_provider == "fake":
        # FakeProvider is configuration-visible for tests, never request-selectable.
        provider_available = True
        active_provider = "fake"
    else:
        provider_available = False
        active_provider = "deterministic"

    return AnswerSynthesisStatus(
        synthesis_available=True,
        configured_provider=configured_provider,
        active_provider=active_provider,
        provider_available=provider_available,
        real_llm_enabled=config.allow_external,
        real_llm_configured=real_llm_configured,
        deterministic_fallback_available=True,
        tool_calling_enabled=config.max_tool_rounds > 0 and config.max_tool_calls > 0,
        max_tool_rounds=config.max_tool_rounds,
        max_tool_calls=config.max_tool_calls,
        graphrag_base_required=True,
        guarded_validation_enabled=True,
    )
