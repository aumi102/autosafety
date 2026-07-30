"""
Answer synthesis package — Phase 7.

Subpackages:
- tools/  — controlled tool layer (Phase 7B)
- models.py — synthesis request/result/tracing models (Phase 7C)
- providers.py — provider abstraction (Phase 7C)
- prompt_builder.py — bounded prompt construction (Phase 7C)
- orchestrator.py — bounded tool-call orchestration (Phase 7C)
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
]
