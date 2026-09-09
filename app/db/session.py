from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

async_engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

sync_engine = create_engine(settings.DATABASE_URL_SYNC, echo=False)

async def get_async_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session

def get_sync_engine():
    return sync_engine
