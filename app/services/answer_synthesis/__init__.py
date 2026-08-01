"""
Answer synthesis package — Phase 7.

Subpackages:
- tools/  — controlled tool layer (Phase 7B)
- models.py — synthesis request/result/tracing models (Phase 7C)
- providers.py — provider abstraction (Phase 7C)
- prompt_builder.py — bounded prompt construction (Phase 7C)
- orchestrator.py — bounded tool-call orchestration (Phase 7C)
- guarded_models.py — final validated claim/citation/answer models (Phase 7D)
- evidence_adapter.py — EvidenceBundle -> GuardedCitation adaptation (Phase 7D)
- policy.py — sufficiency, claim-type allowlist, mandatory warnings (Phase 7D)
- citation_validator.py — claim/citation validation and safe repair (Phase 7D)
- confidence.py — deterministic confidence scoring (Phase 7D)
- composer.py — deterministic answer composition (Phase 7D)
- service.py — GuardedAnswerService, the final Phase 7D entry point (Phase 7D)
"""

from app.services.answer_synthesis.tools import (
    build_default_tool_registry,
    ToolRegistry,
    ToolDefinition,
    ToolCallRequest,
    ToolCallResult,
    EvidenceBundle,
    EvidenceItem,
    ToolExecutionPolicy,
)

from app.services.answer_synthesis.models import (
    SynthesisConfig,
    ProviderToolCall,
    ProviderClaim,
    ProviderSynthesisRequest,
    ProviderSynthesisResult,
    OrchestrationTrace,
    OrchestrationResult,
)

from app.services.answer_synthesis.providers import (
    SynthesisProvider,
    DeterministicProvider,
    FakeProvider,
    OpenAICompatibleProvider,
    build_synthesis_provider,
)

from app.services.answer_synthesis.orchestrator import (
    SynthesisOrchestrator,
    build_config_from_settings,
    _orchestrate_with_config,
)

from app.services.answer_synthesis.guarded_models import (
    GuardedClaim,
    GuardedCitation,
    CitationValidationResult,
    EvidenceSufficiencyResult,
    ConfidenceResult,
    RetrievalSummary,
    GuardedTrace,
    GuardedAnswerResult,
)

from app.services.answer_synthesis.service import GuardedAnswerService

__all__ = [
    # Phase 7B
    "build_default_tool_registry",
    "ToolRegistry",
    "ToolDefinition",
    "ToolCallRequest",
    "ToolCallResult",
    "EvidenceBundle",
    "EvidenceItem",
    "ToolExecutionPolicy",
    # Phase 7C
    "SynthesisConfig",
    "ProviderToolCall",
    "ProviderClaim",
    "ProviderSynthesisRequest",
    "ProviderSynthesisResult",
    "OrchestrationTrace",
    "OrchestrationResult",
    "SynthesisProvider",
    "DeterministicProvider",
    "FakeProvider",
    "OpenAICompatibleProvider",
    "build_synthesis_provider",
    "SynthesisOrchestrator",
    "build_config_from_settings",
    "_orchestrate_with_config",
    # Phase 7D
    "GuardedClaim",
    "GuardedCitation",
    "CitationValidationResult",
    "EvidenceSufficiencyResult",
    "ConfidenceResult",
    "RetrievalSummary",
    "GuardedTrace",
    "GuardedAnswerResult",
    "GuardedAnswerService",
]
