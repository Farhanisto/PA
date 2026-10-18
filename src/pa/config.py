"""Application configuration loaded via Pydantic Settings.

Secrets are never logged. Required provider keys raise clear errors only
when remote inference is requested (see models/nebius.py), not at import.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_ENV: str = "development"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    APP_CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    DATABASE_URL: str = "postgresql+psycopg://pa:pa@localhost:5432/pa"

    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    NEBIUS_API_KEY: str = ""
    NEBIUS_BASE_URL: str = "https://api.tokenfactory.nebius.com/v1/"
    NEBIUS_MODEL: str = ""
    NEBIUS_TIMEOUT: float = 60.0
    NEBIUS_MAX_TOKENS: int = 2048

    GROQ_API_KEY: str = ""
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    GROQ_TIMEOUT: float = 60.0
    GROQ_MAX_TOKENS: int = 2048

    COMPOSIO_API_KEY: str = ""
    COMPOSIO_CALLBACK_URL: str = ""

    LANGGRAPH_CHECKPOINT_DB: str = "postgresql+psycopg://pa:pa@localhost:5432/pa"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
