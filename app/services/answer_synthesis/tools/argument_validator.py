"""
Strict argument validation for tool calls — Phase 7B.

All validation is application-owned, not delegated to the LLM.
No eval, no exec, no dynamic imports.
"""

from __future__ import annotations

from typing import Any

from app.services.answer_synthesis.tools.base import (
    ToolDefinition,
)

# Forbidden keys that must never appear in tool arguments
FORBIDDEN_KEYS: set[str] = {
    "sql",
    "query_sql",
    "cypher",
    "raw_query",
    "raw_sql",
    "database_url",
    "password",
    "api_key",
    "secret",
    "token",
    "credential",
    "n4ey",  # neo4j password
    "auth",
    "connection",
    "driver",
    "session",
    "execute",
    "eval",
    "exec",
    "__",
}


class ValidationError:
    """Structured validation failure."""
    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


class ValidationResult:
    def __init__(self):
        self.errors: list[ValidationError] = []

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0

    def add(self, field: str, message: str) -> None:
        self.errors.append(ValidationError(field, message))

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "errors": [{"field": e.field, "message": e.message} for e in self.errors],
        }


def validate_tool_arguments(
    definition: ToolDefinition,
    arguments: dict[str, Any],
) -> ValidationResult:
    """
    Validate tool arguments against a tool definition's schema.

    Rules enforced:
    - Forbidden keys rejected
    - Unknown keys rejected
    - Required fields enforced
    - Type checks enforced (bool not accepted for integer)
    - String length limits enforced
    - Integer range limits enforced
    - Enum values enforced
    - No nested structures

    Returns ValidationResult with errors if any.
    """
    result = ValidationResult()
    schema = definition.input_schema
    provided_keys = set(arguments.keys())

    # 1. Check for forbidden keys
    for key in provided_keys:
        key_lower = key.lower()
        for forbidden in FORBIDDEN_KEYS:
            if forbidden in key_lower:
                result.add(key, f"Forbidden key pattern: '{key}'")
        # Also check the exact key
        if key in FORBIDDEN_KEYS:
            result.add(key, f"Forbidden key: '{key}'")

    # 2. Check for unknown keys
    schema_keys = set(schema.fields.keys())
    for key in provided_keys:
        if key not in schema_keys:
            result.add(key, f"Unknown argument: '{key}'")

    # 3. Validate each schema field
    for name, field_def in schema.fields.items():
        value = arguments.get(name)

        # Required check
        if field_def.required and value is None:
            result.add(name, "Required field is missing")
            continue

        # Skip optional missing fields
        if value is None:
            continue

        # Type: string
        if field_def.type == "string":
            if not isinstance(value, str):
                result.add(name, f"Expected string, got {type(value).__name__}")
            elif field_def.max_length and len(value) > field_def.max_length:
                result.add(name, f"String exceeds max length {field_def.max_length}")
            elif len(value.strip()) == 0:
                result.add(name, "String cannot be empty")
            else:
                # Strip whitespace from valid strings
                arguments[name] = value.strip()

        # Type: integer
        elif field_def.type == "integer":
            # Reject booleans explicitly (True/False are instances of int in Python)
            if isinstance(value, bool):
                result.add(name, "Expected integer, got boolean")
            elif not isinstance(value, int):
                result.add(name, f"Expected integer, got {type(value).__name__}")
            else:
                if field_def.min_value is not None and value < field_def.min_value:
                    result.add(name, f"Integer {value} below minimum {field_def.min_value}")
                if field_def.max_value is not None and value > field_def.max_value:
                    result.add(name, f"Integer {value} above maximum {field_def.max_value}")

        # Type: boolean
        elif field_def.type == "boolean":
            if not isinstance(value, bool):
                result.add(name, f"Expected boolean, got {type(value).__name__}")

        # Type: enum
        elif field_def.type == "enum":
            if not isinstance(value, str):
                result.add(name, f"Expected string for enum, got {type(value).__name__}")
            elif field_def.enum_values and value not in field_def.enum_values:
                result.add(name, f"Invalid enum value '{value}'. Allowed: {field_def.enum_values}")

        # 4. Reject nested/structured arguments beyond simple values
        if value is not None and isinstance(value, (list, dict, set)):
            result.add(name, f"Nested structures not allowed for '{name}'")

    return result
