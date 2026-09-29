"""Structured logging (structlog). Bind request_id / workflow_id / thread_id / run_id as context."""

from __future__ import annotations

import logging

import structlog


def configure_logging(json: bool = False, level: int = logging.INFO) -> None:
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    processors.append(structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer())
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
