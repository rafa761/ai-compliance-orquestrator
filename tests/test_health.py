from httpx import ASGITransport, AsyncClient

from orchestrator.main import app


async def test_healthz_returns_service_status() -> None:
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "service": "compliant-outreach-orchestrator",
        "status": "ok",
    }
