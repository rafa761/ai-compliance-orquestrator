from uuid import UUID, uuid4

from fastapi import Request
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response


def correlation_id_from_header(value: str | None) -> UUID:
    if value is None:
        return uuid4()
    try:
        return UUID(value)
    except ValueError:
        return uuid4()


async def correlation_id_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    correlation_id = correlation_id_from_header(request.headers.get("X-Correlation-ID"))
    request.state.correlation_id = correlation_id
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = str(correlation_id)
    return response
