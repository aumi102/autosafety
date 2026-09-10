"""
Synthesis data models — Phase 7C.

Deterministic, bounded models for provider requests, responses, and orchestration.
No credentials, no raw tracebacks, no database clients in serialized output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.answer_synthesis.tools.base import EvidenceBundle


@dataclass
class SynthesisConfig:
    """Configuration for the synthesis orchestrator."""

    provider: str = "deterministic"  # deterministic | fake | openai_compatible
    model: str = ""
    allow_external: bool = False
    api_key: str = ""  # never serialized raw — see to_dict()
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: int = 30
    max_tool_rounds: int = 2
    max_tool_calls: int = 4
    max_evidence_items: int = 20
    max_evidence_chars: int = 16000
    max_output_chars: int = 8000
    max_claims: int = 8

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "allow_external": self.allow_external,
            "api_key_configured": bool(self.api_key),
            "base_url": self.base_url,
            "timeout_seconds": self.timeout_seconds,
            "max_tool_rounds": self.max_tool_rounds,
            "max_tool_calls": self.max_tool_calls,
            "max_evidence_items": self.max_evidence_items,
            "max_evidence_chars": self.max_evidence_chars,
            "max_output_chars": self.max_output_chars,
            "max_claims": self.max_claims,
        }


@dataclass
class ProviderToolCall:
    """A structured tool call request from a provider."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
        }


@dataclass
class ProviderClaim:
    """A factual claim extracted from synthesis output."""

    text: str
    claim_type: (
        str  # complaint_observation | official_recall | shared_component_association | sql_fact
    )
    citation_ids: list[str] = field(default_factory=list)
    unsupported: bool = False
    warning: str | None = None

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "claim_type": self.claim_type,
            "citation_ids": self.citation_ids,
            "unsupported": self.unsupported,
            "warning": self.warning,
        }


@dataclass
class ProviderSynthesisRequest:
    """Request sent to a synthesis provider."""

    question: str
    evidence_bundle_text: str
    citation_table: list[dict[str, Any]]
    available_tools: list[dict[str, Any]]
    safety_rules: str
    remaining_tool_budget: int

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "evidence_bundle_text": self.evidence_bundle_text,
            "citation_table": self.citation_table,
            "available_tools": self.available_tools,
            "safety_rules": "[hidden — see safety_rules field]",
            "remaining_tool_budget": self.remaining_tool_budget,
        }


@dataclass
class ProviderSynthesisResult:
    """Structured output from a synthesis provider."""

    answer: str = ""
    claims: list[ProviderClaim] = field(default_factory=list)
    requested_tool_calls: list[ProviderToolCall] = field(default_factory=list)
    abstain: bool = False
    abstention_reason: str | None = None
    provider: str = ""
    model: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    request_id: str | None = None
    warnings: list[str] = field(default_factory=list)
    # Stable error codes — not raw tracebacks
    error_code: str | None = None
    error_message: str | None = None

    @property
    def success(self) -> bool:
        return self.error_code is None and not self.abstain

    def to_dict(self) -> dict:
        return {
            "answer": self.answer[:2000] if self.answer else "",  # bound output
            "claims": [c.to_dict() for c in self.claims[:8]],  # PHASE7_MAX_CLAIMS default bound
            "requested_tool_calls": [t.to_dict() for t in self.requested_tool_calls],
            "abstain": self.abstain,
            "abstention_reason": self.abstention_reason,
            "provider": self.provider,
            "model": self.model,
            "finish_reason": self.finish_reason,
            "request_id": self.request_id,
            "warnings": self.warnings,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass
class OrchestrationTrace:
    """Execution trace for debugging and audit."""

    provider: str
    model: str
    tool_rounds: int = 0
    tool_calls_requested: int = 0
    tool_calls_executed: int = 0
    tool_calls_rejected: int = 0
    provider_attempts: int = 0
    fallback_used: bool = False
    timings: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Each entry: {tool_name, arguments, executed, rejected_reason}
    tool_call_log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "tool_rounds": self.tool_rounds,
            "tool_calls_requested": self.tool_calls_requested,
            "tool_calls_executed": self.tool_calls_executed,
            "tool_calls_rejected": self.tool_calls_rejected,
            "provider_attempts": self.provider_attempts,
            "fallback_used": self.fallback_used,
            "timings_ms": {k: int(v * 1000) for k, v in self.timings.items()},
            "warnings": self.warnings,
            "tool_call_log": self.tool_call_log,
        }


@dataclass
class OrchestrationResult:
    """
    Final result of orchestration.

    Phase = phase_7c until Phase 7D semantic validation completes.
    """

    question: str
    provider_result: ProviderSynthesisResult
    trace: OrchestrationTrace
    phase: str = "phase_7c"
    # Internal only — not serialized by to_dict(). Phase 7D consumes this
    # directly to adapt evidence/citations; it is never exposed as prompt
    # or provider-facing output.
    evidence_bundle: EvidenceBundle | None = None

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "provider_result": self.provider_result.to_dict(),
            "trace": self.trace.to_dict(),
            "phase": self.phase,
            "note": "Structurally parsed. Semantic claim safety pending Phase 7D validation.",
        }
