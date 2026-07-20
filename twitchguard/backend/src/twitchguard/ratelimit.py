"""Fixed-window rate limit for mutating endpoints (NFR-Sec-06)."""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .errors import error_payload

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def install_rate_limit(app: FastAPI) -> None:
    @app.middleware("http")
    async def _rate_limit(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method not in MUTATING:
            return await call_next(request)
        limit = request.app.state.config.security.rate_limit_per_minute
        if limit <= 0:
            return await call_next(request)
        cookie = request.cookies.get(request.app.state.settings.session_cookie_name)
        client = request.client.host if request.client else "unknown"
        ident = cookie.split(".")[0][:16] if cookie and "." in cookie else client
        key = f"tg:rl:{ident}:{int(time.time() // 60)}"
        redis = request.app.state.redis
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
        if count > limit:
            return JSONResponse(
                status_code=429,
                content=error_payload("rate_limited", "Too many requests, slow down"),
            )
        return await call_next(request)
