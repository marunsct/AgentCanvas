"""RFC 9457 problem+json error responses (ADR 0009)."""

from __future__ import annotations

from typing import Any

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError, IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException
from structlog.contextvars import get_contextvars

from agentcanvas.errors import DomainError
from agentcanvas.log import get_logger

MEDIA = "application/problem+json"
log = get_logger(__name__)


def problem(status: int, title: str, detail: str, type_: str = "about:blank", **extra: Any) -> JSONResponse:
    body = {"type": type_, "title": title, "status": status, "detail": detail, **extra}
    return JSONResponse(body, status_code=status, media_type=MEDIA)


def unexpected_error(request_id: str | None) -> JSONResponse:
    """A bug's answer: the same problem document as every other error (not a text page), with the request id to
    quote; what went wrong stays in the server's log."""
    return problem(
        500,
        "Internal error",
        "something went wrong on the server; quote the request id when you report it",
        "https://agentcanvas.dev/errors/internal",
        request_id=request_id,
    )


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

    def _out_of_range() -> JSONResponse:
        return problem(
            422,
            "Invalid input",
            "a value contains something the database cannot store (a NUL character, a number out of range)",
            "https://agentcanvas.dev/errors/invalid",
        )

    @app.exception_handler(DataError)
    @app.exception_handler(psycopg.DataError)  # LangGraph's Postgres store talks psycopg directly (the memory routes)
    async def _data(_: Request, exc: Exception) -> JSONResponse:
        # the database refused a value as such: a NUL character in a name or id (Postgres text cannot hold one), a
        # number out of range, text longer than a column. The client's input is at fault; the SQL stays in the log.
        return _out_of_range()

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        # a uniqueness or reference the database enforces, met by a request that raced another (two saves of one
        # name, a row deleted meanwhile) where the code did not expect it: a conflict to retry, not a 500. The
        # constraint stays in the log, where a real bug shows up too.
        log.warning("db.integrity_conflict", path=request.url.path, error=str(exc.orig)[:300])
        return problem(
            409,
            "Conflict",
            "this conflicts with a change saved at the same moment, or with something that already exists; "
            "reload and try again",
            "https://agentcanvas.dev/errors/conflict",
        )

    @app.exception_handler(OverflowError)
    async def _overflow(_: Request, exc: OverflowError) -> JSONResponse:
        # SQLite's way of saying DataError ("Python int too large to convert to SQLite INTEGER"): the same cause and
        # answer. Any other overflow is a bug and goes on to the 500 below. (Registered for this class, not left to the
        # `Exception` handler, which runs outside the middleware that would let the answer replace the failure.)
        if "SQLite" not in str(exc):
            raise exc
        return _out_of_range()

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        # the request middleware answers a route's crash itself (counted, logged with its id); this one is left for
        # a failure in a middleware outside it, and Starlette logs the traceback after answering
        return unexpected_error(get_contextvars().get("request_id"))

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
    500: "Internal error",
}
