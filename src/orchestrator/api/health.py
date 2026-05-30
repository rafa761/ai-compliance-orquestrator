from fastapi import APIRouter, Request

from orchestrator.settings import Settings

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, str]:
    """Shallow process health check; it does not verify database reachability."""

    current_settings: Settings = request.app.state.settings
    return {"service": current_settings.service_name, "status": "ok"}
