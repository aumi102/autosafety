"""
Tool base contracts — Phase 7B.

Deterministic Pydantic models for tool definitions, calls, and results.
No secrets, no callable objects, no tracebacks.
"""

from __future__ import annotations

import uuid
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum


class ToolName(str, Enum):
    SQL_ANALYTICS = "sql_analytics_tool"
    GRAPH_EVIDENCE = "graph_evidence_tool"
    GRAPHRAG_RETRIEVAL = "graphrag_retrieval_tool"
    VEHICLE_RESOLUTION = "vehicle_resolution_tool"


@dataclass
class ToolInputField:
    """Schema definition for a single input field."""
    type: str  # "string" | "integer" | "boolean" | "enum"
    description: str
    required: bool = True
    default: Any = None
    max_length: Optional[int] = None  # for strings
    min_value: Optional[int] = None   # for integers
    max_value: Optional[int] = None    # for integers
    enum_values: Optional[list[str]] = None  # for enum type


@dataclass
class ToolInputSchema:
    """Full input schema for a tool."""
    fields: dict[str, ToolInputField]

    def to_dict(self) -> dict:
        return {
            name: {
                "type": f.type,
                "description": f.description,
                "required": f.required,
                "default": f.default,
                "max_length": f.max_length,
                "min_value": f.min_value,
                "max_value": f.max_value,
                "enum_values": f.enum_values,
            }
            for name, f in self.fields.items()
        }


@dataclass
class ToolDefinition:
    """
    Immutable tool definition for LLM consumption.

    Contains only schema metadata — no callable objects,
    no credentials, no internal state.
    """
    name: str
    description: str
    input_schema: ToolInputSchema
    read_only: bool = True
    max_result_items: int = 20
    timeout_seconds: int = 15

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema.to_dict(),
            "read_only": self.read_only,
            "max_result_items": self.max_result_items,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class ToolCallRequest:
    """A structured tool call from an LLM or orchestrator."""
    call_id: str
    tool_name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
        }

    @classmethod
    def make(cls, tool_name: str, arguments: dict[str, Any]) -> ToolCallRequest:
        return cls(call_id=f"call-{uuid.uuid4().hex[:12]}", tool_name=tool_name, arguments=arguments)


@dataclass
class ToolCallResult:
    """
    Result of a tool execution.

    Error codes are stable strings. No raw tracebacks.
    """
    call_id: str
    tool_name: str
    success: bool
    data: Optional[dict[str, Any]] = None
    warnings: list[str] = field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: int = 0
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "success": self.success,
            "data": self.data,
            "warnings": self.warnings,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "duration_ms": self.duration_ms,
            "truncated": self.truncated,
        }

    @classmethod
    def ok(cls, call_id: str, tool_name: str, data: dict[str, Any],
          warnings: list[str] | None = None, truncated: bool = False) -> ToolCallResult:
        return cls(
            call_id=call_id, tool_name=tool_name, success=True,
            data=data, warnings=warnings or [], truncated=truncated, duration_ms=0,
        )

    @classmethod
    def error(cls, call_id: str, tool_name: str, error_code: str,
              error_message: str) -> ToolCallResult:
        return cls(
            call_id=call_id, tool_name=tool_name, success=False,
            error_code=error_code, error_message=error_message,
        )

    @classmethod
    def validation_error(cls, call_id: str, tool_name: str,
                         error_message: str) -> ToolCallResult:
        return cls.error(call_id, tool_name, "validation_error", error_message)


@dataclass
class ToolExecutionPolicy:
    """Safety and resource bounds for tool execution."""
    max_total_calls: int = 4
    max_result_items: int = 20
    max_string_length: int = 2000
    max_total_output_chars: int = 16000
    timeout_seconds: int = 30

    def to_dict(self) -> dict:
        return {
            "max_total_calls": self.max_total_calls,
            "max_result_items": self.max_result_items,
            "max_string_length": self.max_string_length,
            "max_total_output_chars": self.max_total_output_chars,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class EvidenceItem:
    """
    A single piece of evidence from a tool execution.

    Deterministic ID based on source_record_key.
    """
    evidence_id: str
    tool_name: str
    evidence_type: str  # "complaint" | "recall" | "sql_result" | "graph_path" | "graph_neighborhood"
    source_record_key: str
    source_entity_id: Optional[str] = None
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    relation_basis: Optional[str] = None  # e.g. "official_recall_affects_vehicle"
    score: float = 1.0
    citation_label: Optional[str] = None
    citation_id: Optional[str] = None  # stable: cite-{source_type}-{source_key}
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "tool_name": self.tool_name,
            "evidence_type": self.evidence_type,
            "source_record_key": self.source_record_key,
            "source_entity_id": self.source_entity_id,
            "text": self.text,
            "metadata": self.metadata,
            "relation_basis": self.relation_basis,
            "score": self.score,
            "citation_label": self.citation_label,
            "citation_id": self.citation_id,
            "truncated": self.truncated,
        }

    @classmethod
    def make_id(cls, source_type: str, source_key: str) -> str:
        """Deterministic evidence ID."""
        return f"ev-{source_type}-{source_key}"

    @classmethod
    def make_citation_id(cls, source_type: str, source_key: str) -> str:
        """Deterministic citation ID."""
        return f"cite-{source_type}-{source_key}"


@dataclass
class EvidenceBundle:
    """
    Bounded collection of evidence items for LLM synthesis.

    Built by EvidenceBundleBuilder — sanitized and size-bounded.
    """
    items: list[EvidenceItem] = field(default_factory=list)
    tool_calls: list[ToolCallResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total_characters: int = 0
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "items": [i.to_dict() for i in self.items],
            "tool_call_count": len(self.tool_calls),
            "warnings": self.warnings,
            "total_characters": self.total_characters,
            "truncated": self.truncated,
        }

    def citation_table(self) -> list[dict]:
        """Build a citation table for the LLM from evidence items with citation_ids."""
        table = []
        for item in self.items:
            if item.citation_id:
                table.append({
                    "citation_id": item.citation_id,
                    "source_type": item.evidence_type,
                    "source_key": item.source_record_key,
                    "label": item.citation_label or f"{item.evidence_type} {item.source_record_key}",
                    "text_span": item.text[:500] if item.text else None,
                    "score": item.score,
                })
        # Deduplicate by citation_id
        seen: dict[str, dict] = {}
        for row in table:
            seen[row["citation_id"]] = row
        return list(seen.values())
