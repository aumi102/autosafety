"""
Graph Evidence adapter — Phase 7B.

Wraps Phase 3-5 graph services through a structured tool interface.
Uses predefined Cypher. No raw Cypher. No mutation. Max 20 paths.
"""

from __future__ import annotations

from typing import Any, Callable

from app.services.answer_synthesis.tools.base import (
    ToolDefinition,
    ToolInputField,
    ToolInputSchema,
    ToolCallResult,
)


SUPPORTED_OPERATIONS = [
    "vehicle_neighborhood",
    "recall_paths_by_vehicle",
    "component_evidence_by_vehicle",
    "shared_component_recall_paths",
]


GRAPH_EVIDENCE_DEFINITION = ToolDefinition(
    name="graph_evidence_tool",
    description=(
        "Retrieve structured graph evidence from Neo4j for vehicle safety data. "
        "Returns neighborhood graphs, recall paths, and component-level links. "
        "Uses predefined Cypher — no arbitrary queries. No mutations."
    ),
    input_schema=ToolInputSchema(fields={
        "operation": ToolInputField(
            type="enum",
            description="Graph evidence operation",
            required=True,
            enum_values=SUPPORTED_OPERATIONS,
        ),
        "vehicle_id": ToolInputField(
            type="string",
            description="PostgreSQL vehicle UUID",
            required=True,
            max_length=40,
        ),
        "max_paths": ToolInputField(
            type="integer",
            description="Maximum graph paths to return (default 20, max 20)",
            required=False,
            default=20,
            min_value=1,
            max_value=20,
        ),
    }),
    read_only=True,
    max_result_items=20,
    timeout_seconds=15,
)


def build_graph_evidence_adapter() -> Callable[..., ToolCallResult]:
    """
    Build a graph evidence tool adapter.

    No direct session — reuses Phase 3-5 service functions that own
    their own session lifecycle internally.
    """
    def adapter(*, call_id: str, arguments: dict[str, Any]) -> ToolCallResult:
        operation = arguments.get("operation")
        vehicle_id = arguments.get("vehicle_id")
        max_paths = arguments.get("max_paths", 20)

        try:
            from app.services.graph import (
                get_vehicle_neighborhood,
                get_vehicle_recall_paths,
                get_vehicle_component_evidence,
                get_vehicle_shared_component_recalls,
            )
            from app.services.graph.graph_models import (
                VehicleNeighborhood,
                RecallPathResult,
                ComponentEvidence,
            )

            warnings: list[str] = []
            result_data: dict[str, Any] = {}

            if operation == "vehicle_neighborhood":
                result = get_vehicle_neighborhood(vehicle_id)
                if result is None:
                    return ToolCallResult.error(
                        call_id, "graph_evidence_tool",
                        "not_found",
                        f"Vehicle {vehicle_id} not found in graph",
                    )
                result_data = _neighborhood_to_dict(result, max_paths)
                warnings.append(
                    "Graph neighborhood links are potential associations. "
                    "Recall → AFFECTS → ModelYear is the only official linkage."
                )

            elif operation == "recall_paths_by_vehicle":
                result = get_vehicle_recall_paths(vehicle_id)
                if result is None:
                    return ToolCallResult.error(
                        call_id, "graph_evidence_tool",
                        "not_found",
                        f"Vehicle {vehicle_id} not found in graph",
                    )
                result_data = _recall_paths_to_dict(result, max_paths)
                warnings.append(
                    "Recall links are potentially related — not official causality. "
                    "Only Recall → AFFECTS → ModelYear is official when explicitly in campaign records."
                )

            elif operation == "component_evidence_by_vehicle":
                result = get_vehicle_component_evidence(vehicle_id)
                if result is None:
                    return ToolCallResult.error(
                        call_id, "graph_evidence_tool",
                        "not_found",
                        f"Vehicle {vehicle_id} not found in graph",
                    )
                result_data = _component_evidence_to_dict(result, max_paths)
                warnings.append(
                    "Complaint component links are potential associations, not causality. "
                    "Shared-component links may be zero if NHTSA recall records lack component data."
                )

            elif operation == "shared_component_recall_paths":
                result = get_vehicle_shared_component_recalls(vehicle_id)
                if result is None:
                    return ToolCallResult.error(
                        call_id, "graph_evidence_tool",
                        "not_found",
                        f"Vehicle {vehicle_id} not found in graph",
                    )
                result_data = _component_evidence_to_dict(result, max_paths)
                warnings.append(
                    "Shared-component associations are potential, not causal or official."
                )

            else:
                return ToolCallResult.validation_error(
                    call_id, "graph_evidence_tool",
                    f"Unknown operation: '{operation}'",
                )

            return ToolCallResult.ok(
                call_id, "graph_evidence_tool",
                result_data, warnings, truncated=False,
            )

        except ImportError as e:
            return ToolCallResult.error(
                call_id, "graph_evidence_tool",
                "import_error",
                f"Graph service unavailable: {type(e).__name__}",
            )
        except Exception as e:
            return ToolCallResult.error(
                call_id, "graph_evidence_tool",
                "adapter_error",
                f"Graph evidence adapter error: {type(e).__name__}",
            )

    return adapter


def _neighborhood_to_dict(result, max_paths: int) -> dict:
    """Serialize VehicleNeighborhood safely."""
    return {
        "operation": "vehicle_neighborhood",
        "make": result.make,
        "model": result.model,
        "year": result.year,
        "vehicle_id": result.vehicle_id,
        "nodes": _serialize_nodes(result.nodes),
        "edges": _serialize_edges(result.edges),
        "complaint_count": result.complaint_count,
        "recall_count": result.recall_count,
        "components": result.components[:max_paths],
        "path_type": "vehicle_neighborhood",
        "relation_basis": "graph_neighborhood",
    }


def _recall_paths_to_dict(result, max_paths: int) -> dict:
    """Serialize RecallPathResult safely."""
    recalls = []
    for r in result.recalls[:max_paths]:
        recalls.append({
            "campaign_number": r.campaign_number,
            "report_received_date": r.report_received_date,
            "summary": (r.summary or "")[:500],
            "component": r.component,
            "remedy": (r.remedy or "")[:500],
            "units_affected": r.units_affected,
        })
    return {
        "operation": "recall_paths_by_vehicle",
        "make": result.make,
        "model": result.model,
        "year": result.year,
        "vehicle_id": result.vehicle_id,
        "recalls": recalls,
        "recall_count": len(recalls),
        "path_type": result.path_type,
        "relation_basis": "official_recall_affects_vehicle",
    }


def _component_evidence_to_dict(result, max_paths: int) -> dict:
    """Serialize ComponentEvidence safely."""
    shared_recalls = []
    for r in (result.shared_recalls or [])[:max_paths]:
        shared_recalls.append({
            "campaign_number": r.get("campaign_number", ""),
            "component": r.get("component", ""),
            "summary": (r.get("summary", "") or "")[:500],
        })
    return {
        "operation": result.path_type,
        "make": result.make,
        "model": result.model,
        "year": result.year,
        "vehicle_id": result.vehicle_id,
        "complaint_count": result.complaint_count,
        "complaint_components": result.complaint_components[:max_paths],
        "components": result.components[:max_paths],
        "shared_recalls": shared_recalls,
        "recall_count": len(shared_recalls),
        "path_type": result.path_type,
        "relation_basis": result.path_type,  # e.g. "complaint_mentions_component"
    }


def _serialize_nodes(nodes) -> list[dict]:
    """Serialize neighborhood nodes safely."""
    result = []
    for n in nodes:
        result.append({
            "id": n.id,
            "label": n.label,
            "properties": _sanitize_dict(n.properties),
        })
    return result


def _serialize_edges(edges) -> list[dict]:
    """Serialize neighborhood edges safely."""
    result = []
    for e in edges:
        result.append({
            "type": e.type,
            "source_id": e.source_id,
            "target_id": e.target_id,
            "properties": _sanitize_dict(e.properties),
        })
    return result


def _sanitize_dict(d: dict) -> dict:
    """Remove forbidden keys from a dict."""
    forbidden = {"password", "api_key", "secret", "token", "credential", "database_url", "url"}
    if not d:
        return {}
    return {k: v for k, v in d.items() if k.lower() not in forbidden}
