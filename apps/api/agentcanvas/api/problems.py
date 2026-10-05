"""RFC 9457 problem+json error responses (ADR 0009)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentcanvas.errors import DomainError

MEDIA = "application/problem+json"


def problem(status: int, title: str, detail: str, type_: str = "about:blank", **extra: Any) -> JSONResponse:
    body = {"type": type_, "title": title, "status": status, "detail": detail, **extra}
    return JSONResponse(body, status_code=status, media_type=MEDIA)


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError) -> JSONResponse:
        resp = problem(exc.status, exc.title, exc.detail, exc.type_, **exc.extra)
        if (retry := exc.extra.get("retry_after")) is not None:
            resp.headers["Retry-After"] = str(retry)
        return resp

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return problem(
            422,
            "Invalid request",
            f"{len(errors)} invalid field(s)",
            "https://agentcanvas.dev/errors/invalid",
            errors=errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "request failed"
        extra = {} if isinstance(exc.detail, str) else {"errors": exc.detail}
        return problem(exc.status_code, _TITLES.get(exc.status_code, "Error"), detail, **extra)


_TITLES = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
    422: "Invalid input",
}
