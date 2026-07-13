"""
Answer composer — merge SQL + graph evidence into answer contract.

Phase 4: deterministic composition, no LLM.
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

from app.services.answer_contract import (
    AnswerResponse, Answer, AnswerSection,
    SqlResult, Evidence, GraphPath, Confidence,
)
from app.services.hybrid.hybrid_models import HybridAnswerResult, GraphEvidenceItem


CAVEAT_COMPLAINT_VOLUME = (
    "Complaint volume alone does not prove a safety defect. "
    "Complaint records are user-submitted public reports and may be noisy."
)
CAVEAT_POTENTIAL_RELATION = (
    "Graph relationships shown via shared vehicle/component are "
    "potential associations, not official causality. "
    "Only Recall → AFFECTS → ModelYear links are official when campaign "
    "records explicitly apply to the vehicle."
)
CAVEAT_NEO4J_UNAVAILABLE = (
    "Graph evidence unavailable — Neo4j is not connected or returned an error. "
    "SQL analytics results are still valid."
)


def compose_hybrid_answer(
    result: HybridAnswerResult,
    question: str,
    tool_calls: int = 0,
    start_time: Optional[float] = None,
) -> AnswerResponse:
    """
    Compose a full AnswerResponse from SQL + graph evidence.

    Uses the answer contract schema from docs/contracts/answer_contract.md.

    Confidence scoring:
    - high (0.8): SQL has data + graph evidence available
    - medium (0.6): SQL has data, graph unavailable/failed
    - low (0.3): SQL has no data, or graph only
    """
    start_time = start_time or time.time()
    latency_ms = int((time.time() - start_time) * 1000)
    run_id = str(uuid.uuid4())

    sql_resp = result.sql_response
    graph_items = result.graph_evidence

    # Extract SQL result info
    sql_row_count = sql_resp.get("sql", {}).get("row_count") or 0
    sql_query = sql_resp.get("sql", {}).get("query") or ""
    sql_rows = sql_resp.get("sql", {}).get("rows") or []
    sql_columns = sql_resp.get("sql", {}).get("columns") or []
    sql_execution_ms = sql_resp.get("sql", {}).get("execution_ms") or 0
    sql_validated = sql_resp.get("sql", {}).get("validated", True)
    sql_summary = sql_resp.get("answer", {}).get("summary", "")
    sql_intent = sql_resp.get("intent", "sql")

    warnings: list[str] = []

    # Complaint caveat if SQL returned complaint data
    if "complaint" in question.lower() and sql_row_count > 0:
        warnings.append(CAVEAT_COMPLAINT_VOLUME)

    # Graph caveats
    if not result.neo4j_available:
        warnings.append(CAVEAT_NEO4J_UNAVAILABLE)
    elif graph_items:
        warnings.append(CAVEAT_POTENTIAL_RELATION)

    # Confidence
    if sql_row_count > 0 and graph_items:
        confidence_label = "high"
        confidence_score = 0.8
    elif sql_row_count > 0:
        confidence_label = "medium"
        confidence_score = 0.6
    elif graph_items:
        confidence_label = "low"
        confidence_score = 0.3
    else:
        confidence_label = "low"
        confidence_score = 0.2

    confidence_reasons = [
        f"SQL result rows: {sql_row_count}",
    ]
    if graph_items:
        confidence_reasons.append(f"Graph evidence items: {len(graph_items)}")
    if result.neo4j_available:
        confidence_reasons.append("Neo4j connected")
    else:
        confidence_reasons.append("Neo4j unavailable")

    # Build answer sections
    sections: list[AnswerSection] = []

    # Summary section
    summary_text = _build_summary(question, sql_row_count, graph_items, result)
    sections.append(AnswerSection(
        title="Answer",
        content=summary_text,
        type="text",
    ))

    # SQL table section if data exists
    if sql_rows and sql_columns:
        table_content = _format_table(sql_rows, sql_columns)
        sections.append(AnswerSection(
            title="SQL Results",
            content=table_content,
            type="table_summary",
        ))

    # Graph evidence section if available
    if graph_items:
        evidence_text = _build_graph_evidence_text(graph_items)
        sections.append(AnswerSection(
            title="Graph Evidence",
            content=evidence_text,
            type="evidence_summary",
        ))

    # Caveats section
    if warnings:
        sections.append(AnswerSection(
            title="Important Caveats",
            content=" ".join(warnings),
            type="caveat",
        ))

    # Build evidence object
    citations: list = []
    graph_paths: list[GraphPath] = []

    for item in graph_items:
        # Build path text from nodes
        path_text = _build_path_text(item, sql_rows)
        graph_paths.append(GraphPath(
            path_text=path_text,
            relation_source=item.relation_basis,
            confidence=0.7 if item.relation_basis == "potentially_related_by_shared_component" else 0.9,
        ))

    # Build final response
    return AnswerResponse(
        run_id=run_id,
        intent="hybrid",
        answer=Answer(summary=summary_text, sections=sections),
        sql=SqlResult(
            used=True,
            query=sql_query,
            columns=sql_columns,
            rows=sql_rows,
            row_count=sql_row_count,
            execution_ms=sql_execution_ms,
            validated=sql_validated,
        ),
        evidence=Evidence(
            citations=citations,
            graph_paths=graph_paths,
        ),
        warnings=warnings,
        confidence=Confidence(
            label=confidence_label,
            score=confidence_score,
            reasons=confidence_reasons,
        ),
        tool_call_count=tool_calls,
        latency_ms=latency_ms,
    )


def _build_summary(
    question: str,
    sql_row_count: int,
    graph_items: list,
    result: HybridAnswerResult,
) -> str:
    """Build natural language summary."""
    parts = []

    if sql_row_count > 0:
        parts.append(f"Found {sql_row_count} SQL result row(s).")
    elif sql_row_count == 0:
        parts.append("No SQL results found for this query in the current database.")

    if graph_items:
        recall_count = sum(len(item.recall_campaigns) for item in graph_items)
        if recall_count > 0:
            parts.append(
                f"Graph evidence found {recall_count} recall(s) "
                f"potentially related by vehicle/component."
            )
        else:
            parts.append("Graph neighborhood retrieved but no recalls found.")
    elif not result.neo4j_available:
        parts.append("Graph evidence unavailable (Neo4j not connected).")

    return " ".join(parts)


def _build_graph_evidence_text(items: list[GraphEvidenceItem]) -> str:
    """Build readable graph evidence text."""
    lines = []
    for i, item in enumerate(items, 1):
        lines.append(f"{i}. {item.path_type}")
        if item.recall_campaigns:
            lines.append(f"   Recalls: {', '.join(item.recall_campaigns[:5])}")
        if item.summary:
            lines.append(f"   {item.summary}")
    return "\n".join(lines) if lines else "No graph evidence available."


def _build_path_text(item: GraphEvidenceItem, sql_rows: list[dict]) -> str:
    """Build a readable path description."""
    parts = []

    # Get vehicle info from first SQL row if available
    if sql_rows:
        row = sql_rows[0]
        make = row.get("make", "")
        model = row.get("model", "")
        year = row.get("model_year", "")
        if make:
            vehicle_str = f"{make}"
            if model:
                vehicle_str += f" {model}"
            if year:
                vehicle_str += f" {year}"
            parts.append(vehicle_str)

    # Add recall info
    if item.recall_campaigns:
        campaigns = item.recall_campaigns[:3]
        parts.append(f"→ Recall(s): {', '.join(campaigns)}")

    # Path type
    parts.append(f"[{item.path_type}]")

    return " | ".join(parts)


def _format_table(rows: list[dict], columns: list[str]) -> str:
    """Format rows as readable text table."""
    if not rows:
        return "No results."
    available = [c for c in columns if c in rows[0].keys()]
    if not available:
        available = list(rows[0].keys())
    header = " | ".join(available)
    sep = "-" * len(header)
    lines = [header, sep]
    for row in rows:
        vals = [str(row.get(c, "")) for c in available]
        lines.append(" | ".join(vals))
    return "\n".join(lines)
