from fastapi import APIRouter
from app.api.v1.endpoints import health, vehicles, chat, ingestion, sql_analytics

router = APIRouter()
router.include_router(health.router, prefix="/health", tags=["health"])
router.include_router(vehicles.router, prefix="/vehicles", tags=["vehicles"])
router.include_router(chat.router, prefix="/chat", tags=["chat"])
router.include_router(ingestion.router, prefix="/ingestion", tags=["ingestion"])
router.include_router(sql_analytics.router, prefix="/sql-analytics", tags=["sql-analytics"])
