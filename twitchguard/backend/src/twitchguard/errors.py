"""Unified API error format: {"error": {"code", "message", "field"}} (§9)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        field: str | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field = field
        self.details = details


def error_payload(
    code: str,
    message: str,
    field: str | None = None,
    details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message, "field": field}
    if details:
        err["details"] = details
    return {"error": err}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, exc.field, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        details = [
            {
                "field": ".".join(str(p) for p in e.get("loc", []) if p != "body"),
                "message": str(e.get("msg", "invalid value")),
            }
            for e in errors
        ]
        field = details[0]["field"] if details else None
        return JSONResponse(
            status_code=422,
            content=error_payload("validation_error", "Request validation failed", field, details),
        )

    @app.exception_handler(Exception)
    async def _internal_error(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error: %s", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content=error_payload("internal_error", "Internal server error"),
        )
