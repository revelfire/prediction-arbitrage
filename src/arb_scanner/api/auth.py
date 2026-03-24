"""Bearer token authentication middleware."""

from __future__ import annotations

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="api.auth")

_EXEMPT_PREFIXES = ("/api/health",)


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Require a bearer token for API and dashboard access.

    When ``token`` is None or empty, all requests pass through
    (auth disabled for local development).
    """

    def __init__(self, app: object, token: str | None = None) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._token = token or ""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Check bearer token on every request except exempt paths."""
        if not self._token:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        query_token = request.query_params.get("token", "")

        if auth_header == f"Bearer {self._token}" or query_token == self._token:
            return await call_next(request)

        logger.warning("auth.rejected", path=path, method=request.method)
        return JSONResponse(
            status_code=401,
            content={"error": "Unauthorized"},
        )
