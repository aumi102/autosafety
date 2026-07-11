"""Chat API endpoints with Phase 2 SQL analytics."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, Literal
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import get_settings
from app.services.sql_analytics.service import SqlAnalyticsService

router = APIRouter(tags=["chat"])


def _get_sync_session():
    settings = get_settings()
    db_url = settings.DATABASE_URL_SYNC
    if "postgresql+asyncpg" in db_url:
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(db_url, echo=False)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


class ChatSessionCreate(BaseModel):
    title: Optional[str] = None


class ChatSession(BaseModel):
    id: str
    title: Optional[str]
    created_at: str


class ChatMessageCreate(BaseModel):
    content: str
    options: Optional[dict] = {}


class ChatMessageResponse(BaseModel):
    message_id: str
    run_id: str
    intent: str
    answer: dict
    sql: dict
    evidence: dict
    warnings: list[str]
    confidence: dict
    debug: dict
    phase: str = "phase_2"


@router.post("/sessions", response_model=ChatSession)
def create_session(data: ChatSessionCreate):
    """Create a new chat session."""
    return ChatSession(
        id=str(uuid.uuid4()),
        title=data.title,
        created_at="2025-01-01T00:00:00Z"
    )


@router.post("/sessions/{session_id}/messages", response_model=ChatMessageResponse)
def send_message(session_id: str, data: ChatMessageCreate):
    """
    Send a message in a chat session.

    Phase 2: routes SQL analytics questions through template-based SQL engine.
    Returns structured answer conforming to answer_contract.md.
    """
    session = _get_sync_session()
    try:
        # Run SQL analytics service
        response = SqlAnalyticsService(session).answer(data.content)

        return ChatMessageResponse(
            message_id=str(uuid.uuid4()),
            run_id=response.run_id,
            intent=response.intent,
            answer=response.answer.to_dict(),
            sql=response.sql.to_dict(),
            evidence=response.evidence.to_dict(),
            warnings=response.warnings,
            confidence=response.confidence.to_dict(),
            debug={
                "tool_call_count": response.tool_call_count,
                "latency_ms": response.latency_ms,
            },
            phase="phase_2",
        )
    finally:
        session.close()
