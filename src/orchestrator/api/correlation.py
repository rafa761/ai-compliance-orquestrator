from uuid import UUID, uuid4

from fastapi import Request
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response


def correlation_id_from_header(value: str | None) -> UUID:
    """Parse caller trace IDs without letting malformed headers block requests.

    Bad correlation IDs are replaced with fresh UUIDs because tracing metadata
    should never decide whether regulated business work is accepted.
    """

    if value is None:
        return uuid4()
    try:
        return UUID(value)
    except ValueError:
        return uuid4()


async def correlation_id_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Attach one correlation ID to request state, response headers, and audit rows."""

    correlation_id = correlation_id_from_header(request.headers.get("X-Correlation-ID"))
    request.state.correlation_id = correlation_id
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = str(correlation_id)
    return response
