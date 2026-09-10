from functools import lru_cache
from typing import Any

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Phase 8 — Conversation state and bounded multi-turn context
    PHASE8_MAX_CONTEXT_TURNS: int = 5
    PHASE8_MAX_TURNS_PER_CONVERSATION: int = 100
    PHASE8_MAX_CONTEXT_CHARS: int = 300
    PHASE8_MAX_STORED_ANSWER_CHARS: int = 8000
    PHASE8_MAX_STORED_CITATIONS_PER_TURN: int = 20
    PHASE8_CONVERSATION_RETENTION_DAYS: int = 30
    # Phase 9 — Maintenance/admin route protection (fail-closed).
    # Mutation routes require a matching X-Admin-Token header. When no usable
    # token is configured they return 503 rather than becoming public.
    # Must be at least 16 characters and not a placeholder value.
    ADMIN_API_TOKEN: SecretStr = SecretStr("")
    # Deprecated Phase 8 alias, still honoured so existing deployments keep working.
    PHASE8_ADMIN_TOKEN: SecretStr = SecretStr("")

    # Phase 9 — execution audit (agent_runs / tool_calls).
    # Observability only. Audit failures never fail a user request.
    PHASE9_AUDIT_ENABLED: bool = True

@lru_cache
def get_settings() -> Settings:
    return Settings()


def isolated_settings(**overrides: Any) -> Settings:
    """Build settings that ignore the operator `.env`.

    Tests and evaluations must not inherit whatever an operator happens to have
    configured locally -- a real provider key or admin token would silently
    change what they exercise. `_env_file` is a pydantic-settings runtime
    keyword that its generated `__init__` signature does not declare, so the
    single suppression it needs lives here instead of at every call site.
    """
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]
