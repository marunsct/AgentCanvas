"""Domain exceptions. The API layer maps them to RFC 9457 problem+json (ADR 0009)."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    status = 400
    title = "Bad request"
    type_ = "about:blank"

    def __init__(self, detail: str, **extra: Any):
        super().__init__(detail)
        self.detail = detail
        self.extra = extra


class NotFound(DomainError):
    status, title, type_ = 404, "Not found", "https://agentcanvas.dev/errors/not-found"


class Conflict(DomainError):
    status, title, type_ = 409, "Conflict", "https://agentcanvas.dev/errors/conflict"


class PreconditionRequired(DomainError):
    status, title, type_ = 428, "Precondition required", "https://agentcanvas.dev/errors/precondition-required"


class InvalidInput(DomainError):
    status, title, type_ = 422, "Invalid input", "https://agentcanvas.dev/errors/invalid"


class WorkflowHasErrors(DomainError):
    status, title, type_ = 422, "Workflow has errors", "https://agentcanvas.dev/errors/validation"


class Forbidden(DomainError):
    status, title, type_ = 403, "Forbidden", "https://agentcanvas.dev/errors/forbidden"


class UpstreamError(DomainError):
    status, title, type_ = 502, "Upstream error", "https://agentcanvas.dev/errors/upstream"
