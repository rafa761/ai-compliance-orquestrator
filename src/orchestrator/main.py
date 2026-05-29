from fastapi import FastAPI

from orchestrator.api.accounts import router as accounts_router
from orchestrator.api.audit import router as audit_router
from orchestrator.api.correlation import correlation_id_middleware
from orchestrator.api.events import router as events_router
from orchestrator.api.health import router as health_router
from orchestrator.api.tasks import router as tasks_router
from orchestrator.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app with injectable settings for tests and demos.

    Settings are stored on app.state so lightweight handlers such as healthz can
    report the active service identity without re-reading environment variables.
    """

    app_settings = settings or get_settings()

    app = FastAPI(
        title="Compliant Outreach Orchestrator",
        summary="Demo service for compliance-aware multi-channel outreach orchestration.",
        version="0.1.0",
    )
    app.state.settings = app_settings
    app.middleware("http")(correlation_id_middleware)
    app.include_router(health_router)
    app.include_router(events_router)
    app.include_router(audit_router)
    app.include_router(tasks_router)
    app.include_router(accounts_router)
    return app


app = create_app()


def run() -> None:
    """Local development entry point; runs uvicorn with reload enabled."""

    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "orchestrator.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    run()
