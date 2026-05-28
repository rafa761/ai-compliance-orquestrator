from fastapi import FastAPI, Request

from orchestrator.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()

    app = FastAPI(
        title="Compliant Outreach Orchestrator",
        summary="Demo service for compliance-aware multi-channel outreach orchestration.",
        version="0.1.0",
    )
    app.state.settings = app_settings

    @app.get("/healthz", tags=["health"])
    async def healthz(request: Request) -> dict[str, str]:
        current_settings: Settings = request.app.state.settings
        return {"service": current_settings.service_name, "status": "ok"}

    return app


app = create_app()


def run() -> None:
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
