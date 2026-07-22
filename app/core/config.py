from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "development"
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/autosafety"
    DATABASE_URL_SYNC: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/autosafety"
    REDIS_URL: str = "redis://localhost:6379/0"
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "neo4j"
    LOG_LEVEL: str = "INFO"
    AUTH_ENABLED: bool = False
    GRAPHRAG_EMBEDDING_PROVIDER: str = "deterministic"
    GRAPHRAG_EMBEDDING_MODEL: str = "deterministic-test-v1"
    GRAPHRAG_EMBEDDING_DIMENSION: int = 384

@lru_cache
def get_settings() -> Settings:
    return Settings()
