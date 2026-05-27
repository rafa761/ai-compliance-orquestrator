from fastapi import FastAPI

SERVICE_NAME = "compliant-outreach-orchestrator"

app = FastAPI(
    title="Compliant Outreach Orchestrator",
    summary="Demo service for compliance-aware multi-channel outreach orchestration.",
    version="0.1.0",
)


@app.get("/healthz", tags=["health"])
async def healthz() -> dict[str, str]:
    return {"service": SERVICE_NAME, "status": "ok"}


def run() -> None:
    import uvicorn

    uvicorn.run("orchestrator.main:app", host="0.0.0.0", port=8000, reload=True)


def create_app() -> FastAPI:
    return app
