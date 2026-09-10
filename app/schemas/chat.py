from typing import Literal

from pydantic import BaseModel


class ChatSessionCreate(BaseModel):
    title: str | None = None


class ChatSessionResponse(BaseModel):
    id: str
    title: str | None
    created_at: str

    class Config:
        from_attributes = True


class ChatMessageCreate(BaseModel):
    content: str
    options: dict | None = {}


class ConfidenceResponse(BaseModel):
    label: Literal["low", "medium", "high"]
    score: float
    reasons: list[str]


class AnswerSection(BaseModel):
    title: str
    content: str
    type: Literal["text", "table_summary", "evidence_summary", "caveat"]


class AnswerResponse(BaseModel):
    summary: str
    sections: list[AnswerSection]


class SqlResponse(BaseModel):
    used: bool
    query: str | None = None
    columns: list[str] | None = None
    rows: list[dict] | None = None
    row_count: int | None = None
    execution_ms: int | None = None
    validated: bool = True


class CitationItem(BaseModel):
    source_type: str
    source_id: str
    source_key: str | None = None
    field_name: str | None = None
    text_span: str | None = None
    confidence: float


class GraphPath(BaseModel):
    path_text: str
    relation_source: Literal["source_record", "normalized_join", "semantic_similarity"]
    confidence: float


class EvidenceResponse(BaseModel):
    citations: list[CitationItem]
    graph_paths: list[GraphPath]


class ChatMessageResponse(BaseModel):
    message_id: str
    run_id: str
    intent: Literal["sql", "graph_rag", "hybrid", "safety", "clarification"]
    answer: AnswerResponse
    sql: SqlResponse
    evidence: EvidenceResponse
    warnings: list[str]
    confidence: ConfidenceResponse
    phase: str = "phase_0_stub"
