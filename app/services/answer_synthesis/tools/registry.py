"""
Tool registry — Phase 7B.

Application-owned tool execution with allowlist enforcement.
No eval, no exec, no dynamic imports from LLM input.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from app.services.answer_synthesis.tools.argument_validator import (
    validate_tool_arguments,
)
from app.services.answer_synthesis.tools.base import (
    ToolCallRequest,
    ToolCallResult,
    ToolDefinition,
    ToolExecutionPolicy,
)
from app.services.observability.context import notify_tool_call

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    Centralized allowlist of safe, read-only tools.

    Execution flow:
      1. Validate tool name against allowlist
      2. Validate arguments against schema
      3. Execute registered adapter (never LLM input)
      4. Return sanitized ToolCallResult
      5. No eval, no exec, no dynamic imports
    """

    def __init__(self) -> None:
        # name -> (ToolDefinition, adapter callable)
        self._tools: dict[str, tuple[ToolDefinition, Callable[..., ToolCallResult]]] = {}
        self._policy = ToolExecutionPolicy()

    def register(
        self,
        definition: ToolDefinition,
        adapter: Callable[..., ToolCallResult],
    ) -> None:
        """Register a tool. Raises ValueError on duplicate name."""
        if definition.name in self._tools:
            raise ValueError(f"Tool already registered: {definition.name}")
        self._tools[definition.name] = (definition, adapter)

    def list_definitions(self) -> list[ToolDefinition]:
        """Return sorted list of tool definitions for LLM consumption."""
        return sorted((defn for defn, _ in self._tools.values()), key=lambda x: x.name)

    def is_registered(self, tool_name: str) -> bool:
        return tool_name in self._tools

    def get_definition(self, tool_name: str) -> ToolDefinition | None:
        """Return definition for a registered tool, or None."""
        entry = self._tools.get(tool_name)
        return entry[0] if entry else None

    def execute(self, request: ToolCallRequest) -> ToolCallResult:
        """
        Execute a validated tool call.

        1. Check name is registered
        2. Validate arguments against schema
        3. Execute adapter
        4. Return safe result
        """
        start = time.time()

        # 1. Check registration
        if request.tool_name not in self._tools:
            return self._audited(
                request,
                ToolCallResult.validation_error(
                    request.call_id,
                    request.tool_name,
                    f"Unknown tool: '{request.tool_name}'. Available: {self._available_names()}",
                ),
            )

        definition, adapter = self._tools[request.tool_name]

        # 2. Validate arguments
        validation = validate_tool_arguments(definition, request.arguments)
        if not validation.valid:
            return self._audited(
                request,
                ToolCallResult.validation_error(
                    request.call_id,
                    request.tool_name,
                    "; ".join(str(e) for e in validation.errors),
                ),
            )

        # 3. Execute adapter with timeout awareness
        try:
            result = adapter(call_id=request.call_id, arguments=request.arguments)
            elapsed_ms = int((time.time() - start) * 1000)
            if result.duration_ms == 0:
                result.duration_ms = elapsed_ms
            return self._audited(request, result)
        except Exception as e:
            logger.warning(f"Tool adapter error for {request.tool_name}: {e}")
            return self._audited(
                request,
                ToolCallResult.error(
                    request.call_id,
                    request.tool_name,
                    "adapter_error",
                    f"Tool execution failed: {type(e).__name__}",
                ),
            )

    @staticmethod
    def _audited(request: ToolCallRequest, result: ToolCallResult) -> ToolCallResult:
        """Report the execution to the Phase 9 audit side channel and return it.

        A no-op when no audit run is in scope. Audit never alters the result and
        never fails the call.
        """
        notify_tool_call(request, result)
        return result

    def execute_batch(
        self,
        requests: list[ToolCallRequest],
    ) -> list[ToolCallResult]:
        """Execute multiple tool calls. Returns results in same order."""
        return [self.execute(r) for r in requests]

    def set_policy(self, policy: ToolExecutionPolicy) -> None:
        self._policy = policy

    def _available_names(self) -> str:
        return ", ".join(sorted(self._tools.keys()))


def build_default_tool_registry(
    *,
    sql_session_factory: Callable[[], Any] | None = None,
    neo4j_available: bool = True,
    graphrag_retrieval_fn: Callable[..., Any] | None = None,
) -> ToolRegistry:
    """
    Build the default Phase 7B tool registry with all available adapters.

    Args:
        sql_session_factory: callable returning a SQLAlchemy session.
                            If None, sql_analytics_tool is unavailable.
        neo4j_available: whether Neo4j is reachable.
                         If False, graph_evidence_tool skips graph calls safely.
        graphrag_retrieval_fn: callable matching `retrieve_graphrag_evidence` signature.
                              If None, graphrag_retrieval_tool is unavailable.
    """
    registry = ToolRegistry()

    # sql_analytics_tool
    if sql_session_factory is not None:
        from app.services.answer_synthesis.tools.sql_adapter import (
            SQL_ANALYTICS_DEFINITION,
            build_sql_analytics_adapter,
        )

        adapter = build_sql_analytics_adapter(sql_session_factory)
        registry.register(SQL_ANALYTICS_DEFINITION, adapter)

    # graph_evidence_tool
    if neo4j_available:
        from app.services.answer_synthesis.tools.graph_adapter import (
            GRAPH_EVIDENCE_DEFINITION,
            build_graph_evidence_adapter,
        )

        adapter = build_graph_evidence_adapter()
        registry.register(GRAPH_EVIDENCE_DEFINITION, adapter)

    # graphrag_retrieval_tool
    if graphrag_retrieval_fn is not None:
        from app.services.answer_synthesis.tools.graphrag_adapter import (
            GRAPHRAG_RETRIEVAL_DEFINITION,
            build_graphrag_adapter,
        )

        adapter = build_graphrag_adapter(graphrag_retrieval_fn)
        registry.register(GRAPHRAG_RETRIEVAL_DEFINITION, adapter)

    # vehicle_resolution_tool — always available if domain models exist
    from app.services.answer_synthesis.tools.vehicle_adapter import (
        VEHICLE_RESOLUTION_DEFINITION,
        build_vehicle_resolution_adapter,
    )

    adapter = build_vehicle_resolution_adapter(sql_session_factory)
    registry.register(VEHICLE_RESOLUTION_DEFINITION, adapter)

    return registry
