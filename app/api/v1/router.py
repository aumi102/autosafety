from fastapi import APIRouter
from app.api.v1.endpoints import health, vehicles, chat, ingestion

router = APIRouter()
router.include_router(health.router, prefix="/health", tags=["health"])
router.include_router(vehicles.router, prefix="/vehicles", tags=["vehicles"])
router.include_router(chat.router, prefix="/chat", tags=["chat"])
router.include_router(ingestion.router, prefix="/ingestion", tags=["ingestion"])
