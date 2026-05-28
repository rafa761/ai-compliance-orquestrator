from httpx import ASGITransport, AsyncClient

from orchestrator.main import create_app
from orchestrator.settings import Settings


async def test_healthz_returns_service_status() -> None:
    app = create_app(
        Settings(
            service_name="compliant-outreach-orchestrator",
            database_url="sqlite+aiosqlite:///./test.db",
        )
    )
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "service": "compliant-outreach-orchestrator",
        "status": "ok",
    }
