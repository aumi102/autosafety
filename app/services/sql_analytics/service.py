"""
SQL Analytics service — orchestration layer.

Coordinates: question parsing → template selection → SQL validation → execution → answer contract.
"""

from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy.orm import Session

from app.services.answer_contract import (
    Answer,
    AnswerResponse,
    AnswerSection,
    Confidence,
    Evidence,
    SqlResult,
)
from app.services.ingestion.normalization import normalize_make, normalize_model
from app.services.sql_analytics.executor import ExecutionResult, execute_readonly_sql
from app.services.sql_analytics.question_parser import (
    ParsedQuestion,
    build_clarification_response,
    parse_question,
)
from app.services.sql_analytics.templates import (
    TemplateId,
    get_template,
)

logger = logging.getLogger(__name__)


# Safety caveats for Phase 2
CAVEAT_COMPLAINT_VOLUME = (
    "Complaint volume alone does not prove a safety defect. "
    "Complaint records are user-submitted public reports and may be noisy."
)
CAVEAT_RECALL_CAUSALITY = (
    "Recall links shown are official only when campaign records explicitly apply to this vehicle."
)
CAVEAT_SEMANTIC_MATCH = (
    "Component matches via semantic similarity are potential, not official causality."
)

# Default LIMIT for queries
DEFAULT_LIMIT = 10


class SqlAnalyticsService:
    """Main service for answering SQL analytics questions."""

    def __init__(self, session: Session):
        self.session = session

    def answer(self, question: str) -> AnswerResponse:
        """
        Answer a natural language analytics question.

        Returns AnswerResponse conforming to answer_contract.md.
        """
        start_time = time.time()
        run_id = str(uuid.uuid4())
        tool_calls = 0

        # Step 1: Parse question
        tool_calls += 1
        parsed = parse_question(question)

        # Step 2: Handle unsupported/clarification-needed
        if parsed.intent == "unknown":
            return self._build_unknown_response(run_id, question, start_time)

        if parsed.intent == "clarification_needed":
            return self._build_clarification_response(
                run_id, parsed, start_time, tool_calls
            )

        # Step 3: Select and build SQL template
        tool_calls += 1
        sql, params, template_id = self._build_template_sql(parsed)

        if sql is None:
            return self._build_clarification_response(
                run_id, parsed, start_time, tool_calls
            )

        # Validate the generated SQL
        tool_calls += 1
        execution = execute_readonly_sql(self.session, sql, params, max_rows=parsed.limit or DEFAULT_LIMIT)

        # Step 4: Build answer
        return self._build_answer_response(
            run_id=run_id,
            question=question,
            parsed=parsed,
            sql=sql,
            execution=execution,
            template_id=template_id,
            start_time=start_time,
            tool_calls=tool_calls,
        )

    def _build_template_sql(
        self, parsed: ParsedQuestion
    ) -> tuple[str | None, dict, str | None]:
        """Select and parameterize the appropriate SQL template."""
        intent = parsed.intent
        vehicle = parsed.vehicle
        limit = parsed.limit or DEFAULT_LIMIT

        # Ensure model is normalized
        if vehicle:
            norm_make = normalize_make(vehicle.make)
            norm_model = normalize_model(vehicle.model)
        else:
            norm_make = None
            norm_model = None

        params: dict = {}

        if intent == "top_complaint_components_by_vehicle":
            t = get_template(TemplateId.TOP_COMPLAINT_COMPONENTS_BY_VEHICLE)
            params = {
                "make": norm_make,
                "model": norm_model,
                "model_year": vehicle.model_year,
                "limit": limit,
            }
            return t.sql.strip(), params, intent

        elif intent == "complaint_count_by_vehicle":
            t = get_template(TemplateId.COMPLAINT_COUNT_BY_VEHICLE)
            params = {
                "make": norm_make,
                "model": norm_model,
                "model_year": vehicle.model_year,
            }
            return t.sql.strip(), params, intent

        elif intent == "recalls_by_vehicle":
            t = get_template(TemplateId.RECALLS_BY_VEHICLE)
            params = {
                "make": norm_make,
                "model": norm_model,
                "model_year": vehicle.model_year,
                "limit": limit,
            }
            return t.sql.strip(), params, intent

        elif intent == "recall_count_by_vehicle":
            t = get_template(TemplateId.RECALL_COUNT_BY_VEHICLE)
            params = {
                "make": norm_make,
                "model": norm_model,
                "model_year": vehicle.model_year,
            }
            return t.sql.strip(), params, intent

        elif intent == "vehicles_by_complaint_count":
            t = get_template(TemplateId.VEHICLES_BY_COMPLAINT_COUNT)
            params = {
                "make": parsed.make_filter,
                "limit": limit,
            }
            return t.sql.strip(), params, intent

        elif intent == "complaint_count_by_component_for_vehicle":
            t = get_template(TemplateId.COMPLAINT_COUNT_BY_COMPONENT_FOR_VEHICLE)
            params = {
                "make": norm_make,
                "model": norm_model,
                "model_year": vehicle.model_year,
                "limit": limit,
            }
            return t.sql.strip(), params, intent

        return None, {}, None

    def _build_answer_response(
        self,
        run_id: str,
        question: str,
        parsed: ParsedQuestion,
        sql: str,
        execution: ExecutionResult,
        template_id: str | None,
        start_time: float,
        tool_calls: int,
    ) -> AnswerResponse:
        """Build the final AnswerResponse from execution results."""
        latency_ms = int((time.time() - start_time) * 1000)

        # Build summary
        summary, sections, warnings, confidence_score, confidence_label = \
            self._summarize_result(question, parsed, execution)

        # Determine confidence
        if execution.row_count == 0:
            confidence_label = "low"
            confidence_score = 0.3
        elif execution.row_count > 0 and execution.validated:
            confidence_label = "medium"
            confidence_score = 0.7

        confidence_reasons = [
            "Deterministic template-based SQL",
            f"{execution.row_count} result rows",
        ]
        if not execution.validated:
            confidence_reasons.append("Validation warning")

        return AnswerResponse(
            run_id=run_id,
            intent="sql",
            answer=Answer(
                summary=summary,
                sections=sections,
            ),
            sql=SqlResult(
                used=True,
                query=sql,
                columns=execution.columns,
                rows=execution.rows,
                row_count=execution.row_count,
                execution_ms=execution.execution_ms,
                validated=execution.validated,
            ),
            evidence=Evidence(),
            warnings=warnings,
            confidence=Confidence(
                label=confidence_label,
                score=confidence_score,
                reasons=confidence_reasons,
            ),
            tool_call_count=tool_calls,
            latency_ms=latency_ms,
        )

    def _summarize_result(
        self, question: str, parsed: ParsedQuestion, execution: ExecutionResult
    ) -> tuple[str, list[AnswerSection], list[str], float, str]:
        """Generate natural-language summary from SQL result."""
        vehicle_desc = ""
        if parsed.vehicle:
            year_str = f" {parsed.vehicle.model_year}" if parsed.vehicle.model_year else ""
            vehicle_desc = f"{parsed.vehicle.make} {parsed.vehicle.model}{year_str}"

        rows = execution.rows or []
        n = len(rows)
        sections: list[AnswerSection] = []
        warnings: list[str] = []
        confidence_score = 0.7
        confidence_label = "medium"

        # Build warnings
        if "complaint" in parsed.intent.lower():
            warnings.append(CAVEAT_COMPLAINT_VOLUME)

        if parsed.intent == "recalls_by_vehicle":
            warnings.append(CAVEAT_RECALL_CAUSALITY)

        intent = parsed.intent

        if intent == "top_complaint_components_by_vehicle":
            if n == 0:
                summary = f"No complaints found for {vehicle_desc} in the current database."
            else:
                top = rows[0]
                top_comp = top.get("component", "unknown")
                top_cnt = top.get("complaint_count", 0)
                summary = f"Top complaint component for {vehicle_desc} is **{top_comp}** with {top_cnt} complaints."
                sections.append(AnswerSection(
                    title="Top Components",
                    content=self._format_table(rows, ["component", "complaint_count"]),
                    type="table_summary",
                ))
            confidence_score = 0.8

        elif intent == "complaint_count_by_vehicle":
            if n == 0:
                summary = f"No complaints found for {vehicle_desc} in the current database."
            else:
                count = rows[0].get("complaint_count", 0)
                summary = f"{vehicle_desc} has **{count}** complaints in the current database."
            confidence_score = 0.9

        elif intent == "recalls_by_vehicle":
            if n == 0:
                summary = f"No recalls found for {vehicle_desc} in the current database."
            else:
                summary = f"Found **{n}** recall(s) for {vehicle_desc}."
                sections.append(AnswerSection(
                    title="Recalls",
                    content=self._format_table(
                        rows,
                        ["campaign_number", "component", "summary", "remedy"],
                    ),
                    type="table_summary",
                ))
            warnings.append(CAVEAT_RECALL_CAUSALITY)
            confidence_score = 0.9

        elif intent == "recall_count_by_vehicle":
            if n == 0:
                summary = f"No recalls found for {vehicle_desc} in the current database."
            else:
                count = rows[0].get("recall_count", 0)
                summary = f"{vehicle_desc} has **{count}** recall(s) on record."
            warnings.append(CAVEAT_RECALL_CAUSALITY)
            confidence_score = 0.9

        elif intent == "vehicles_by_complaint_count":
            if n == 0:
                summary = "No vehicles with complaints found in the current database."
            else:
                make_filter = f" for {parsed.make_filter}" if parsed.make_filter else ""
                summary = f"Top {min(n, 5)} vehicles{make_filter} by complaint count:"
                sections.append(AnswerSection(
                    title="Vehicle Complaint Rankings",
                    content=self._format_table(rows, ["make", "model", "model_year", "complaint_count"]),
                    type="table_summary",
                ))
            warnings.append(CAVEAT_COMPLAINT_VOLUME)

        elif intent == "complaint_count_by_component_for_vehicle":
            if n == 0:
                summary = f"No complaints found for {vehicle_desc} in the current database."
            else:
                summary = f"Complaint breakdown by component for {vehicle_desc}:"
                sections.append(AnswerSection(
                    title="Component Breakdown",
                    content=self._format_table(
                        rows,
                        ["component", "complaint_count", "crash_count", "injury_count"],
                    ),
                    type="table_summary",
                ))
            warnings.append(CAVEAT_COMPLAINT_VOLUME)
            confidence_score = 0.8

        else:
            summary = f"Query returned {n} result(s)."
            confidence_score = 0.5

        # Add caveat warning section
        if warnings:
            sections.append(AnswerSection(
                title="Important Caveats",
                content=" ".join(warnings),
                type="caveat",
            ))

        return summary, sections, warnings, confidence_score, confidence_label

    def _format_table(self, rows: list[dict], columns: list[str]) -> str:
        """Format rows as a readable text table."""
        if not rows:
            return "No results."

        # Filter columns that exist
        available = [c for c in columns if c in rows[0].keys()]
        if not available:
            available = list(rows[0].keys())

        # Build header
        header = " | ".join(available)
        sep = "-" * len(header)

        lines = [header, sep]
        for row in rows:
            vals = [str(row.get(c, "")) for c in available]
            lines.append(" | ".join(vals))

        return "\n".join(lines)

    def _build_unknown_response(
        self, run_id: str, question: str, start_time: float
    ) -> AnswerResponse:
        """Handle unknown intent."""
        latency_ms = int((time.time() - start_time) * 1000)
        return AnswerResponse(
            run_id=run_id,
            intent="clarification",
            answer=Answer(
                summary="I couldn't determine what you're asking. Please rephrase your question.",
                sections=[
                    AnswerSection(
                        title="Supported Questions",
                        content=self._get_supported_questions_help(),
                        type="text",
                    )
                ],
            ),
            sql=SqlResult(used=False),
            evidence=Evidence(),
            warnings=[CAVEAT_COMPLAINT_VOLUME],
            confidence=Confidence(label="low", score=0.1, reasons=["Unknown intent"]),
            tool_call_count=1,
            latency_ms=latency_ms,
        )

    def _build_clarification_response(
        self, run_id: str, parsed: ParsedQuestion, start_time: float, tool_calls: int
    ) -> AnswerResponse:
        """Handle clarification-needed intent."""
        latency_ms = int((time.time() - start_time) * 1000)
        clarification = build_clarification_response(parsed)
        return AnswerResponse(
            run_id=run_id,
            intent="clarification",
            answer=Answer(
                summary=clarification,
                sections=[
                    AnswerSection(
                        title="How to Ask",
                        content=self._get_supported_questions_help(),
                        type="text",
                    )
                ],
            ),
            sql=SqlResult(used=False),
            evidence=Evidence(),
            warnings=[CAVEAT_COMPLAINT_VOLUME],
            confidence=Confidence(label="low", score=0.2, reasons=["Missing required entities"]),
            tool_call_count=tool_calls,
            latency_ms=latency_ms,
        )

    def _get_supported_questions_help(self) -> str:
        return """Supported question types:
• "Top complaint components for Ford F-150 2020"
• "How many complaints does Honda Accord 2021 have?"
• "List recalls for Ford F-150 2020"
• "Which vehicles have the most complaints?"
• "Complaint count by component for Toyota Camry 2022"

Please include make, model, and year for specific vehicle queries."""


def answer_sql_analytics_question(session: Session, question: str) -> AnswerResponse:
    """
    Convenience function: answer a SQL analytics question.

    Args:
        session: SQLAlchemy session
        question: Natural language question

    Returns:
        AnswerResponse conforming to answer_contract.md
    """
    service = SqlAnalyticsService(session)
    return service.answer(question)
