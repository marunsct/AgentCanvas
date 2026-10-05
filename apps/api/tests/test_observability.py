"""Metrics and traces (S11 M8-T3)."""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from prometheus_client.parser import text_string_to_metric_families
from test_publish import make_greeter

from agentcanvas import observability


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
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/hello")).status_code == 200
    [server] = [s for s in exporter.get_finished_spans() if s.kind.name == "SERVER"]
    assert server.attributes["request.id"] == "req_t"
    assert server.attributes["http.route"] == "/hello"
    assert seen["trace_id"] == format(server.context.trace_id, "032x")


def test_route_labels_hide_ids():
    scope = {
        "route": object(),
        "path": "/v1/threads/th_1/runs/run_2",
        "path_params": {"thread_id": "th_1", "run_id": "run_2"},
    }
    assert observability.route_label(scope) == "/v1/threads/{thread_id}/runs/{run_id}"
    assert observability.route_label({"path": "/v1/nope/123"}) == "unmatched"
