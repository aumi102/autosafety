from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Literal
import uuid

router = APIRouter(tags=["chat"])

class ChatSessionCreate(BaseModel):
    title: Optional[str] = None

class ChatSession(BaseModel):
    id: str
    title: Optional[str]
    created_at: str

class ChatMessageCreate(BaseModel):
    content: str
    options: Optional[dict] = {}

class ChatMessage(BaseModel):
    message_id: str
    run_id: str
    intent: str
    answer: dict
    sql: dict
    evidence: dict
    warnings: list[str]
    confidence: dict
    phase: str = "phase_0_stub"

@router.post("/sessions", response_model=ChatSession)
def create_session(data: ChatSessionCreate):
    return ChatSession(
        id=str(uuid.uuid4()),
        title=data.title,
        created_at="2025-01-01T00:00:00Z"
    )

@router.post("/sessions/{session_id}/messages", response_model=ChatMessage)
def send_message(session_id: str, data: ChatMessageCreate):
    return ChatMessage(
        message_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        intent="safety",
        answer={
            "summary": "Phase 0: no agent yet. Stub response only.",
            "sections": [
                {
                    "title": "Phase 0 Status",
                    "content": "GraphRAG agent, Text-to-SQL engine, and NHTSA ingestion are deferred to Phase 1.",
                    "type": "text"
                }
            ]
        },
        sql={"used": False, "query": None, "validated": True},
        evidence={"citations": [], "graph_paths": []},
        warnings=["Complaint volume alone does not prove a safety defect or official causality."],
        confidence={"label": "low", "score": 0.0, "reasons": ["Phase 0 stub - no real agent"]}
    )
