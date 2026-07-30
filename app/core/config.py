from pydantic import SecretStr
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

    # Phase 7C — Answer Synthesis Provider
    PHASE7_SYNTHESIS_PROVIDER: str = "deterministic"  # deterministic | fake | openai_compatible
    PHASE7_SYNTHESIS_MODEL: str = ""
    PHASE7_SYNTHESIS_ALLOW_EXTERNAL: bool = False
    PHASE7_PROVIDER_TIMEOUT_SECONDS: int = 30
    PHASE7_MAX_TOOL_ROUNDS: int = 2
    PHASE7_MAX_TOOL_CALLS: int = 4
    PHASE7_MAX_EVIDENCE_ITEMS: int = 20
    PHASE7_MAX_EVIDENCE_CHARS: int = 16000
    PHASE7_MAX_OUTPUT_CHARS: int = 8000
    PHASE7_MAX_CLAIMS: int = 8
    # OpenAI-compatible provider settings (used when provider=openai_compatible)
    PHASE7_PROVIDER_API_KEY: SecretStr = SecretStr("")
    PHASE7_PROVIDER_BASE_URL: str = "https://api.openai.com/v1"

@lru_cache
def get_settings() -> Settings:
    return Settings()
