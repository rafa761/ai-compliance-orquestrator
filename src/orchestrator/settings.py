from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration for local demo/runtime wiring.

    Defaults are developer-friendly, not production hardening. Environment
    variables and `.env` override them so tests and local Docker runs can inject
    database URLs without changing application code.
    """

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

    def migration_database_url(self, fallback_url: str | None = None) -> str:
        """Return the URL Alembic should use, preferring an explicit sync URL.

        Runtime code uses the async SQLAlchemy URL. Migrations may need a
        separate driver-compatible URL, so this method centralizes the fallback
        order instead of duplicating it in Alembic configuration.
        """

        return self.alembic_database_url or fallback_url or self.database_url


def _build_settings() -> Settings:
    """Small factory so the cached settings callable can be cleared in tests."""

    return Settings()


get_settings = lru_cache(maxsize=1)(_build_settings)
