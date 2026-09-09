from fastapi import APIRouter

from app.api.v1.endpoints import (
    agent_runs,
    answer_synthesis,
    chat,
    conversations,
    graph,
    graphrag,
    health,
    hybrid,
    ingestion,
    ops,
    sql_analytics,
    vehicles,
)

router = APIRouter()
router.include_router(health.router, prefix="/health", tags=["health"])
router.include_router(vehicles.router, prefix="/vehicles", tags=["vehicles"])
router.include_router(chat.router, prefix="/chat", tags=["chat"])
router.include_router(ingestion.router, prefix="/ingestion", tags=["ingestion"])
router.include_router(sql_analytics.router, prefix="/sql-analytics", tags=["sql-analytics"])
router.include_router(graph.router, prefix="/graph", tags=["graph"])
router.include_router(hybrid.router, prefix="/hybrid", tags=["hybrid"])
router.include_router(graphrag.router, prefix="/graphrag", tags=["graphrag"])
router.include_router(answer_synthesis.router, prefix="/graphrag", tags=["graphrag-answer"])
router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
router.include_router(agent_runs.router, prefix="/agent-runs", tags=["agent-runs"])
router.include_router(ops.router, prefix="/ops", tags=["ops"])
