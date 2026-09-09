"""Direct SQL Analytics API endpoint."""


from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.services.sql_analytics.service import SqlAnalyticsService

router = APIRouter(tags=["sql-analytics"])


def _get_sync_session():
    settings = get_settings()
    db_url = settings.DATABASE_URL_SYNC
    if "postgresql+asyncpg" in db_url:
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(db_url, echo=False)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


class SqlAnalyticsRequest(BaseModel):
    question: str


@router.post("/query")
def sql_analytics_query(data: SqlAnalyticsRequest):
    """
    Direct SQL analytics endpoint.

    Accepts a natural language question and returns structured analytics
    using template-based SQL. Conforms to answer_contract.md.

    Example questions:
    - "Top complaint components for Ford F-150 2020"
    - "How many complaints does Honda Accord 2021 have?"
    - "List recalls for Ford F-150 2020"
    - "Which vehicles have the most complaints?"
    """
    session = _get_sync_session()
    try:
        response = SqlAnalyticsService(session).answer(data.question)
        return response.to_dict()
    finally:
        session.close()
