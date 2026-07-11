from pydantic import BaseModel
from typing import Optional, Literal

class ChatSessionCreate(BaseModel):
    title: Optional[str] = None

class ChatSessionResponse(BaseModel):
    id: str
    title: Optional[str]
    created_at: str

    class Config:
        from_attributes = True

class ChatMessageCreate(BaseModel):
    content: str
    options: Optional[dict] = {}

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
    query: Optional[str] = None
    columns: Optional[list[str]] = None
    rows: Optional[list[dict]] = None
    row_count: Optional[int] = None
    execution_ms: Optional[int] = None
    validated: bool = True

class CitationItem(BaseModel):
    source_type: str
    source_id: str
    source_key: Optional[str] = None
    field_name: Optional[str] = None
    text_span: Optional[str] = None
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
