"""Bearer-token authentication middleware."""

from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Authorization header doesn't match the token.

    Public paths (e.g. /health, /meetgeek/webhook) are allow-listed and use
    their own auth.
    """

    def __init__(self, app, token: str, public_paths: tuple[str, ...] = ()) -> None:
        super().__init__(app)
        self.token = token
        self.public_paths = public_paths

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in self.public_paths):
            return await call_next(request)

        if not self.token:
            return JSONResponse({"error": "server_misconfigured"}, status_code=500)

        header = request.headers.get("authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        provided = header[len(prefix):].strip()
        if not secrets.compare_digest(provided, self.token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        return await call_next(request)


def constant_time_equals(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
