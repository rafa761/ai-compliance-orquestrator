from httpx import ASGITransport, AsyncClient

from orchestrator.main import create_app
from orchestrator.settings import Settings


def test_settings_loads_values_from_env_file(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "APP_ENV=test",
                "APP_HOST=0.0.0.0",
                "APP_PORT=9000",
                "LOG_LEVEL=debug",
                "DATABASE_URL=sqlite+aiosqlite:///./test.db",
            ]
        )
    )

    settings = Settings(_env_file=env_file)

    assert settings.app_env == "test"
    assert settings.app_host == "0.0.0.0"
    assert settings.app_port == 9000
    assert settings.log_level == "debug"
    assert settings.database_url == "sqlite+aiosqlite:///./test.db"


def test_settings_resolves_migration_database_url_with_explicit_override() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://app-db",
        alembic_database_url="postgresql+asyncpg://migration-db",
    )

    assert settings.migration_database_url("postgresql+asyncpg://fallback-db") == (
        "postgresql+asyncpg://migration-db"
    )


def test_settings_resolves_migration_database_url_with_config_fallback() -> None:
    settings = Settings(database_url="postgresql+asyncpg://app-db")

    assert settings.migration_database_url("postgresql+asyncpg://fallback-db") == (
        "postgresql+asyncpg://fallback-db"
    )


async def test_create_app_uses_settings_for_app_state_and_health_response() -> None:
    settings = Settings(
        app_env="test",
        service_name="demo-service",
        database_url="sqlite+aiosqlite:///./test.db",
    )
    app = create_app(settings)

    assert app.state.settings is settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"service": "demo-service", "status": "ok"}
