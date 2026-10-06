"""Structured logging (structlog). Bind request_id / workflow_id / thread_id / run_id as context."""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any

import structlog


def configure_logging(json: bool = False, level: int = logging.INFO) -> None:
    from agentcanvas.observability import add_trace_ids

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        add_trace_ids,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if json:  # JSON lines carry the traceback as text (the console renderer formats it itself)
        processors.append(structlog.processors.format_exc_info)
    processors.append(hide_secret_values)
    processors.append(structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer())
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


def hide_secret_values(_logger: Any, _method: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Mask project secret values (6+ characters) in log fields and tracebacks (S12): an exception message can
    echo a header or a URL that holds one."""
    from agentcanvas.services import projects

    registry = projects.CURRENT
    hidden = [v for vals in (registry.secrets.values() if registry else ()) for v in vals.values() if len(v) >= 6]
    if not hidden:
        return event
    for key, value in event.items():
        if isinstance(value, str) and any(v in value for v in hidden):
            masked = value
            for v in sorted(hidden, key=len, reverse=True):
                masked = masked.replace(v, "***")
            event[key] = masked
    return event


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
