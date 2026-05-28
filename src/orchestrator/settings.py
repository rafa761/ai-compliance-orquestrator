from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "compliant-outreach-orchestrator"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    log_level: str = "info"

    database_url: str = Field(
        default="postgresql+asyncpg://orchestrator:orchestrator@localhost:5432/outreach_orchestrator"
    )
    alembic_database_url: str | None = None
    redis_url: str = "redis://127.0.0.1:6379/0"

    def migration_database_url(self, fallback_url: str | None = None) -> str:
        return self.alembic_database_url or fallback_url or self.database_url


def _build_settings() -> Settings:
    return Settings()


get_settings = lru_cache(maxsize=1)(_build_settings)
