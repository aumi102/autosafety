"""
Answer contract model.

All chat responses must conform to the shape defined in:
docs/contracts/answer_contract.md

Supports: sql, graph_rag, hybrid, safety, clarification intent types.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class CitationItem:
    """A single citation from source data."""
    source_type: str  # "complaint" | "recall" | "investigation" | "manufacturer_communication"
    source_id: str
    source_key: str | None = None
    field_name: str | None = None
    text_span: str | None = None
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_key": self.source_key,
            "field_name": self.field_name,
            "text_span": self.text_span,
            "confidence": self.confidence,
        }


@dataclass
class GraphPath:
    """A graph traversal path for evidence."""
    path_text: str
    relation_source: Literal["source_record", "normalized_join", "semantic_similarity"] = "source_record"
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {
            "path_text": self.path_text,
            "relation_source": self.relation_source,
            "confidence": self.confidence,
        }


@dataclass
class AnswerSection:
    """A section of the answer."""
    title: str
    content: str
    type: Literal["text", "table_summary", "evidence_summary", "caveat"] = "text"

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "content": self.content,
            "type": self.type,
        }


@dataclass
class Answer:
    """The answer body."""
    summary: str
    sections: list[AnswerSection] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "sections": [s.to_dict() for s in self.sections],
        }


@dataclass
class SqlResult:
    """SQL query execution result."""
    used: bool = False
    query: str | None = None
    columns: list[str] | None = None
    rows: list[dict] | None = None
    row_count: int | None = None
    execution_ms: int | None = None
    validated: bool = False

    def to_dict(self) -> dict:
        return {
            "used": self.used,
            "query": self.query,
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "execution_ms": self.execution_ms,
            "validated": self.validated,
        }


@dataclass
class Evidence:
    """Evidence including citations and graph paths."""
    citations: list[CitationItem] = field(default_factory=list)
    graph_paths: list[GraphPath] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "citations": [c.to_dict() for c in self.citations],
            "graph_paths": [p.to_dict() for p in self.graph_paths],
        }


@dataclass
class Confidence:
    """Confidence assessment."""
    label: Literal["low", "medium", "high"]
    score: float  # 0.0 to 1.0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "score": self.score,
            "reasons": self.reasons,
        }


@dataclass
class AnswerResponse:
    """
    Complete answer response conforming to answer_contract.md.

    This is the primary response shape for chat messages.
    """
    run_id: str
    intent: Literal["sql", "graph_rag", "hybrid", "safety", "clarification"]
    answer: Answer
    sql: SqlResult
    evidence: Evidence
    confidence: Confidence
    warnings: list[str] = field(default_factory=list)
    tool_call_count: int = 0
    latency_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "intent": self.intent,
            "answer": self.answer.to_dict(),
            "sql": self.sql.to_dict(),
            "evidence": self.evidence.to_dict(),
            "warnings": self.warnings,
            "confidence": self.confidence.to_dict(),
            "debug": {
                "tool_call_count": self.tool_call_count,
                "latency_ms": self.latency_ms,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> AnswerResponse:
        """Deserialize from a dict (e.g., from JSON)."""
        return cls(
            run_id=data["run_id"],
            intent=data["intent"],
            answer=Answer(
                summary=data["answer"]["summary"],
                sections=[
                    AnswerSection(
                        title=s["title"],
                        content=s["content"],
                        type=s.get("type", "text"),
                    )
                    for s in data["answer"].get("sections", [])
                ],
            ),
            sql=SqlResult(
                used=data["sql"]["used"],
                query=data["sql"].get("query"),
                columns=data["sql"].get("columns"),
                rows=data["sql"].get("rows"),
                row_count=data["sql"].get("row_count"),
                execution_ms=data["sql"].get("execution_ms"),
                validated=data["sql"].get("validated", False),
            ),
            evidence=Evidence(
                citations=[
                    CitationItem(
                        source_type=c["source_type"],
                        source_id=c["source_id"],
                        source_key=c.get("source_key"),
                        field_name=c.get("field_name"),
                        text_span=c.get("text_span"),
                        confidence=c.get("confidence", 1.0),
                    )
                    for c in data["evidence"].get("citations", [])
                ],
                graph_paths=[
                    GraphPath(
                        path_text=p["path_text"],
                        relation_source=p.get("relation_source", "source_record"),
                        confidence=p.get("confidence", 1.0),
                    )
                    for p in data["evidence"].get("graph_paths", [])
                ],
            ),
            warnings=data.get("warnings", []),
            confidence=Confidence(
                label=data["confidence"]["label"],
                score=data["confidence"]["score"],
                reasons=data["confidence"].get("reasons", []),
            ),
            tool_call_count=data.get("debug", {}).get("tool_call_count", 0),
            latency_ms=data.get("debug", {}).get("latency_ms", 0),
        )


# Convenience factory functions

def make_safety_response(
    summary: str,
    warnings: list[str] | None = None,
    run_id: str | None = None,
) -> AnswerResponse:
    """Create a safety refusal response."""
    return AnswerResponse(
        run_id=run_id or str(uuid.uuid4()),
        intent="safety",
        answer=Answer(
            summary=summary,
            sections=[],
        ),
        sql=SqlResult(used=False),
        evidence=Evidence(),
        warnings=warnings or [],
        confidence=Confidence(label="low", score=0.0, reasons=["safety refusal"]),
    )


def make_stub_response(
    phase_note: str = "Phase 0: no agent yet",
    run_id: str | None = None,
) -> AnswerResponse:
    """Create a Phase 0 stub response."""
    return AnswerResponse(
        run_id=run_id or str(uuid.uuid4()),
        intent="safety",
        answer=Answer(
            summary=f"Phase 0: {phase_note}",
            sections=[
                AnswerSection(
                    title="Phase 0 Status",
                    content="GraphRAG agent, Text-to-SQL engine, and NHTSA ingestion deferred to Phase 1.",
                    type="text",
                )
            ],
        ),
        sql=SqlResult(used=False),
        evidence=Evidence(),
        warnings=["Complaint volume alone does not prove a safety defect or official causality."],
        confidence=Confidence(label="low", score=0.0, reasons=["Phase 0 stub - no real agent"]),
    )
