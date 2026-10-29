"""Metrics and traces (S11 M8-T3)."""

from __future__ import annotations

import json

import httpx
from fastapi import FastAPI
from prometheus_client.parser import text_string_to_metric_families
from test_publish import make_greeter

from agentcanvas import observability
from agentcanvas.runtime import masking


def sample(text: str, name: str, **labels: str) -> float:
    for family in text_string_to_metric_families(text):
        for s in family.samples:
            if s.name == name and all(s.labels.get(k) == v for k, v in labels.items()):
                return s.value
    return 0.0


async def scrape(api) -> str:
    r = await api.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    return r.text


async def test_metrics_count_runs_streams_and_requests(api_client):
    wf = await make_greeter(api_client)
    before = await scrape(api_client)
    run = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"who": "Ada"}})
    assert run.status_code == 200 and "RUN_FINISHED" in run.text
    after = await scrape(api_client)

    def delta(name: str, **labels: str) -> float:
        return sample(after, name, **labels) - sample(before, name, **labels)

    assert delta("agentcanvas_runs_started_total", origin="user") == 1
    assert delta("agentcanvas_runs_finished_total", origin="user", status="success") == 1
    assert delta("agentcanvas_run_duration_seconds_count", status="success") == 1
    assert sample(after, "agentcanvas_runs_active") == 0
    assert sample(after, "agentcanvas_sse_clients") == 0  # the stream closed
    route = "/v1/workflows/{wf_id}/runs/stream"  # the route template, never the id: bounded label values
    assert delta("agentcanvas_http_requests_total", method="POST", route=route, status="200") == 1


async def test_metrics_can_be_switched_off(settings):
    from agentcanvas.api.main import create_app

    app = create_app(settings.model_copy(update={"metrics_enabled": False}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/metrics")).status_code == 404


async def test_requests_are_traced_with_their_request_id_and_logs_carry_the_trace():
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    app = FastAPI()
    seen: dict = {}

    @app.middleware("http")
    async def rid(request, call_next):
        observability.tag_current_span(**{"request.id": "req_t"})
        return await call_next(request)

    @app.get("/hello")
    def hello() -> dict:
        seen.update(observability.add_trace_ids(None, "info", {}))
        return {}

    assert observability.setup_tracing(app, exporter=exporter)
    masking.provider = lambda: ["s3cret-token-value"]
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.get("/hello?token=s3cret-token-value")).status_code == 200
    finally:
        masking.provider = lambda: ()
    exported = json.dumps([dict(s.attributes or {}) for s in exporter.get_finished_spans()])
    assert "s3cret-token-value" not in exported and "token=***" in exported  # S12: secrets never leave in a span
    [server] = [s for s in exporter.get_finished_spans() if s.kind.name == "SERVER"]
    assert server.attributes["request.id"] == "req_t"
    assert server.attributes["http.route"] == "/hello"
    assert seen["trace_id"] == format(server.context.trace_id, "032x")

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    observability.trace_engine(engine)  # SQLAlchemy 2.1 is newer than the instrumentation declares
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    await engine.dispose()
    assert any(s.attributes.get("db.system") == "sqlite" for s in exporter.get_finished_spans())


def test_route_labels_hide_ids():
    scope = {
        "route": object(),
        "path": "/v1/threads/th_1/runs/run_2",
        "path_params": {"thread_id": "th_1", "run_id": "run_2"},
    }
    assert observability.route_label(scope) == "/v1/threads/{thread_id}/runs/{run_id}"
    assert observability.route_label({"path": "/v1/nope/123"}) == "unmatched"


async def test_a_quiet_stream_says_it_is_alive():
    """R-9: a long step sent nothing for minutes and a proxy with a 60 s read timeout cut the stream; a comment
    line (which SSE readers ignore) now goes out when nothing else did, and every event still arrives in order."""
    import asyncio

    from agentcanvas.observability import counted_stream

    async def slow():
        yield "data: 1\n\n"
        await asyncio.sleep(0.25)
        yield "data: 2\n\n"

    got = [chunk async for chunk in counted_stream(slow(), keepalive_s=0.05)]
    assert got[0] == "data: 1\n\n" and got[-1] == "data: 2\n\n"
    assert got.count(": keepalive\n\n") >= 3 and len(got) == got.count(": keepalive\n\n") + 2
