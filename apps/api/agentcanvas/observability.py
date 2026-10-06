"""Metrics and traces (M8-T3).

- **Prometheus** at `GET /metrics` (`AGENTCANVAS_METRICS_ENABLED`, on by default): runs started and finished by
  status, run duration, tokens and cost, active runs, open event streams, HTTP requests by route and status, and
  online evaluation (mean score per deployment and evaluator, open drift alerts).
  The web image's nginx does not proxy it; scrape the API pods directly.
- **OpenTelemetry** traces for FastAPI, SQLAlchemy and httpx when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (the
  standard OTel environment variables configure the exporter). Each request span carries the request id, and log
  lines inside a span carry `trace_id` / `span_id`, so logs, traces and metrics join up.
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator, MutableMapping
from typing import TYPE_CHECKING, Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from agentcanvas.log import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

    from agentcanvas.runtime.runner import RunHandle

log = get_logger(__name__)

RUNS_STARTED = Counter("agentcanvas_runs_started_total", "Runs started", ["origin"])
RUNS_FINISHED = Counter("agentcanvas_runs_finished_total", "Runs finished", ["origin", "status"])
RUN_DURATION = Histogram(
    "agentcanvas_run_duration_seconds",
    "Run duration until it finishes or pauses",
    ["status"],
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 900),
)
RUNS_ACTIVE = Gauge("agentcanvas_runs_active", "Runs in progress in this process")
TOKENS = Counter("agentcanvas_tokens_total", "Model tokens", ["direction"])
COST = Counter("agentcanvas_cost_usd_total", "Model cost in US dollars (priced models only)")
SSE_CLIENTS = Gauge("agentcanvas_sse_clients", "Open run event streams")
HTTP_REQUESTS = Counter("agentcanvas_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_DURATION = Histogram(
    "agentcanvas_http_request_duration_seconds",
    "HTTP request duration (until the response starts, for streams)",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

# online evaluation (S13 FR-EVAL-06): per deployment and evaluator, so bounded by the number of deployments
ONLINE_SCORE = Gauge(
    "agentcanvas_online_score_mean", "Mean online score over the evaluator's alert window", ["deployment", "evaluator"]
)
EVAL_ALERT_OPEN = Gauge(
    "agentcanvas_eval_alert_open",
    "1 while an online evaluator's drift rule is broken",
    ["deployment", "evaluator", "kind"],
)
EVAL_ALERTS = Counter("agentcanvas_eval_alerts_total", "Drift alerts opened by online evaluators", ["kind"])

_started: dict[str, float] = {}


def run_started(h: RunHandle) -> None:
    _started[h.run_id] = time.monotonic()
    RUNS_STARTED.labels(origin=h.origin).inc()
    RUNS_ACTIVE.inc()


async def run_finished(h: RunHandle, status: str, _error: str | None, _interrupts: list[dict[str, Any]]) -> None:
    """Runner finish hook."""
    RUNS_ACTIVE.dec()
    RUNS_FINISHED.labels(origin=h.origin, status=status).inc()
    if (began := _started.pop(h.run_id, None)) is not None:
        RUN_DURATION.labels(status=status).observe(time.monotonic() - began)
    TOKENS.labels(direction="input").inc(h.usage.get("input_tokens", 0))
    TOKENS.labels(direction="output").inc(h.usage.get("output_tokens", 0))
    COST.inc(h.usage.get("cost_usd", 0.0))


async def counted_stream(stream: AsyncIterator[str]) -> AsyncIterator[str]:
    """An SSE body that counts itself in `agentcanvas_sse_clients` while it is open."""
    SSE_CLIENTS.inc()
    try:
        async for chunk in stream:
            yield chunk
    finally:
        SSE_CLIENTS.dec()


def route_label(scope: MutableMapping[str, Any]) -> str:
    """The request's route as a template ("/v1/workflows/{wf_id}"): bounded label values, never raw ids.
    Built from the path and its parameters because nested routers keep only their own part of the path."""
    if scope.get("route") is None:
        return "unmatched"
    path = scope.get("path", "")
    for name, value in (scope.get("path_params") or {}).items():
        path = path.replace(f"/{value}", f"/{{{name}}}")
    return path


def observe_request(method: str, route: str, status: int, seconds: float) -> None:
    HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
    HTTP_DURATION.labels(method=method, route=route).observe(seconds)


def metrics_response() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


# ---------------------------------------------------------------------------------------------- tracing ---


def tracing_enabled() -> bool:
    return bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"))


_tracing = {"ready": False}  # one tracer provider per process (tests build several apps)


def setup_tracing(app: FastAPI, exporter: Any = None) -> bool:
    """Install the tracer provider (once per process) and instrument the app and httpx; True when tracing is on.
    Call before the app starts; the control database engine is instrumented by `trace_engine` once it exists.
    `exporter` replaces the OTLP exporter (tests pass an in-memory one); it also turns tracing on."""
    if exporter is None and not tracing_enabled():
        return False
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    if not _tracing["ready"]:
        resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "agentcanvas-api")})
        provider = TracerProvider(resource=resource)
        from agentcanvas.runtime.masking import MaskingExporter

        provider.add_span_processor(  # secret values never leave in a span (S12)
            SimpleSpanProcessor(MaskingExporter(exporter))
            if exporter
            else BatchSpanProcessor(MaskingExporter(OTLPSpanExporter()))
        )
        trace.set_tracer_provider(provider)
        HTTPXClientInstrumentor().instrument()
        _tracing["ready"] = True
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
    log.info("tracing.enabled", endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"))
    return True


def trace_engine(engine: Any) -> None:
    """Spans for the control database's queries (an async SQLAlchemy engine); a no-op without tracing."""
    if not _tracing["ready"]:
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, skip_dep_check=True)  # SQLAlchemy 2.1 works


def tag_current_span(**attributes: Any) -> None:
    """Add attributes (e.g. the request id) to the active span; a no-op without tracing."""
    if not _tracing["ready"]:
        return
    from opentelemetry import trace

    span = trace.get_current_span()
    for key, value in attributes.items():
        span.set_attribute(key, value)


def add_trace_ids(_logger: Any, _method: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """structlog processor: `trace_id` / `span_id` of the active span, so log lines link to their trace."""
    if not _tracing["ready"]:
        return event
    from opentelemetry import trace

    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event.setdefault("trace_id", format(ctx.trace_id, "032x"))
        event.setdefault("span_id", format(ctx.span_id, "016x"))
    return event
