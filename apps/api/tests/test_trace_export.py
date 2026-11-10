"""S15: runs leave as OpenTelemetry traces for Langfuse, Phoenix, Datadog or any OTLP endpoint."""

from __future__ import annotations

import base64
import json

from test_versions import HIGH_CASE, new_triage

P = "/v1/projects/prj_default"


class Captured:
    """Stands in for the OTLP pipeline: the spans a run would send, through the real span building."""

    def __init__(self) -> None:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        self.memory = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.memory))
        self.sent: list[tuple[str, dict]] = []

    def send(self, project_id, url, headers, records):
        from agentcanvas.services.trace_export import emit

        self.sent.append((url, headers))
        emit(self.provider, records)

    def forget(self, project_id) -> None:
        pass

    def shutdown(self) -> None:
        self.provider.shutdown()


async def test_a_run_is_exported_as_a_trace(app, api_client):
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    captured = Captured()
    app.state.trace_export.exporter = captured
    for name, value in (("LF_PUBLIC", "pk-lf-123"), ("LF_SECRET", "sk-lf-456789")):
        r = await api_client.put(f"{P}/secrets/{name}", json={"value": value})
        assert r.status_code in (200, 201, 204), r.text
    body = {"kind": "langfuse", "public_key": {"$secret": "LF_PUBLIC"}, "secret_key": {"$secret": "LF_SECRET"}}
    saved = (await api_client.put(f"{P}/trace-export", json=body)).json()
    assert saved["config"]["kind"] == "langfuse" and saved["missing_secrets"] == []
    wf = await new_triage(api_client)
    r = await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": {"ticket": "Site down"}})
    assert r.status_code == 200, r.text
    thread_id = next(json.loads(line[6:])["threadId"] for line in r.text.splitlines() if '"RUN_STARTED"' in line)
    url, headers = captured.sent[-1]
    assert url == "https://cloud.langfuse.com/api/public/otel/v1/traces"
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"pk-lf-123:sk-lf-456789").decode()
    spans = {s.name: s for s in captured.memory.get_finished_spans()}
    root = next(s for s in spans.values() if s.name.startswith("invoke_agent"))
    assert root.parent is None and root.attributes["gen_ai.conversation.id"] == thread_id
    assert "Site down" in root.attributes["input.value"]
    steps = [s for s in spans.values() if s.attributes.get("openinference.span.kind") == "CHAIN"]
    assert {"classify", "escalate"} <= {s.name for s in steps}
    assert all(s.parent.span_id == root.context.span_id for s in steps)
    llm = [s for s in spans.values() if s.attributes.get("openinference.span.kind") == "LLM"]
    assert llm and all(s.attributes["gen_ai.operation.name"] == "chat" for s in llm)
    assert any(s.attributes.get("output.value") for s in llm)  # the model's reply


async def test_trace_export_settings_are_checked_and_admin_only(app, api_client):
    assert (await api_client.put(f"{P}/trace-export", json={"kind": "langfuse"})).status_code == 422
    assert (await api_client.put(f"{P}/trace-export", json={"kind": "otlp"})).status_code == 422
    bad = {"kind": "phoenix", "endpoint": "ftp://x"}
    assert (await api_client.put(f"{P}/trace-export", json=bad)).status_code == 422
    body = {"kind": "otlp", "endpoint": "http://127.0.0.1:9/v1/traces", "headers": {"x-key": {"$secret": "NOPE"}},
            "include_content": False}  # fmt: skip
    saved = (await api_client.put(f"{P}/trace-export", json=body)).json()
    assert saved["missing_secrets"] == ["NOPE"]
    test = (await api_client.post(f"{P}/trace-export/test")).json()
    assert test["ok"] is False and "NOPE" in test["detail"]
    from agentcanvas.api.deps import required_role

    assert required_role("PUT", "/v1/projects/{project_id}/trace-export") == "admin"
    assert (await api_client.delete(f"{P}/trace-export")).json()["config"] is None


def test_spans_redact_and_can_leave_content_out():
    from agentcanvas.runtime.runner import RunHandle
    from agentcanvas.services.trace_export import spans

    h = RunHandle(run_id="run_1", thread_id="th_1", workflow_id="wf_1", workflow_name="W", trace_redact=["ssn"])
    h.input = {"q": "hi", "ssn": "123-45-6789"}
    h.events = [
        {"type": "RUN_STARTED"},
        {"type": "STEP_STARTED", "stepName": "ask", "canvas_node_id": "n_a", "task_id": "t1"},
        {
            "type": "CUSTOM",
            "name": "ac.usage",
            "value": {"canvas_node_id": "n_a", "model": "openai:gpt-5", "input_tokens": 10, "output_tokens": 2},
        },
        {"type": "CUSTOM", "name": "ac.model_reply", "value": {"canvas_node_id": "n_a", "reply": "your 123-45-6789"}},
        {"type": "STEP_FINISHED", "stepName": "ask", "canvas_node_id": "n_a", "task_id": "t1", "status": "ok"},
        {"type": "RUN_FINISHED", "output": {"a": 1}},
    ]
    h.event_times = [1_000, 2_000, 3_000, 4_000, 5_000, 6_000]
    records = spans(h, "success", None, content=True)
    root, step, llm = records
    assert "123-45-6789" not in root["attrs"]["input.value"] and "[redacted]" in root["attrs"]["input.value"]
    assert "123-45-6789" not in llm["attrs"]["output.value"]
    assert (step["start"], step["end"], llm["parent"]) == (2_000, 5_000, 1)
    assert llm["attrs"]["gen_ai.request.model"] == "gpt-5" and llm["attrs"]["gen_ai.usage.input_tokens"] == 10
    bare = spans(h, "success", None, content=False)
    assert not any("input.value" in r["attrs"] or "output.value" in r["attrs"] for r in bare)


def test_new_credentials_replace_the_old_pipeline():
    from agentcanvas.services.trace_export import Exporter

    ex = Exporter()
    first = ex._provider("prj_a", "https://x.example/v1/traces", {"Authorization": "Basic old"})
    assert ex._provider("prj_a", "https://x.example/v1/traces", {"Authorization": "Basic old"}) is first
    second = ex._provider("prj_a", "https://x.example/v1/traces", {"Authorization": "Basic new"})
    assert second is not first and len(ex._providers) == 1  # the old key is not kept
    ex.forget("prj_a")
    assert ex._providers == {}
    ex.shutdown()


def test_pipelines_of_idle_projects_are_shut_down_and_their_number_is_capped():
    """Every project that exported traces kept its pipeline (a background thread and a queue) until the server
    stopped: the number of threads grew with the projects ever seen."""
    import threading

    from agentcanvas.services.trace_export import Exporter

    now = [0.0]
    ex = Exporter(max_pipelines=2, idle_s=900, clock=lambda: now[0])
    stopped: dict[str, threading.Event] = {}

    def pipeline(project_id: str):
        provider = ex._provider(project_id, f"https://{project_id}.example/v1/traces", {})
        if project_id not in stopped:
            stopped[project_id] = threading.Event()
            provider.shutdown = stopped[project_id].set  # type: ignore[method-assign]
        return provider

    a = pipeline("prj_a")
    pipeline("prj_b")
    now[0] = 10
    assert pipeline("prj_a") is a  # used again: now the most recent
    pipeline("prj_c")  # one too many: the least recently used goes
    assert sorted(ex._providers) == ["prj_a", "prj_c"] and stopped["prj_b"].wait(5)

    now[0] = 10 + 901  # prj_a and prj_c sat idle for more than 15 minutes
    pipeline("prj_d")
    assert sorted(ex._providers) == ["prj_d"]
    assert stopped["prj_a"].wait(5) and stopped["prj_c"].wait(5) and not stopped["prj_d"].is_set()
    ex.shutdown()
    assert stopped["prj_d"].is_set()


async def test_the_hook_forgets_the_settings_of_projects_it_has_not_seen_for_a_while():
    from agentcanvas.services.trace_export import TraceExportHook

    class Db:
        def sessionmaker(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, _model, _id):
            return None

    hook = TraceExportHook(Db(), projects=None, ttl_s=0)
    for i in range(300):
        await hook.config(f"prj_{i}")
    assert len(hook._config) <= 256


async def test_the_hosted_edition_sends_traces_to_public_addresses_only(app, api_client, monkeypatch):
    """S-5: a tenant admin must not make the API server post to the cluster network or the cloud metadata address,
    whether the address is saved, tested, or reached later through a name that moved."""
    import socket

    from agentcanvas.services import trace_export

    monkeypatch.setattr(app.state.settings, "edition", "hosted")
    for body in ({"kind": "otlp", "endpoint": "http://127.0.0.1:9/v1/traces"},
                 {"kind": "otlp", "endpoint": "http://169.254.169.254/latest"},
                 {"kind": "datadog"},  # the defaults are this server's own localhost
                 {"kind": "phoenix"}):  # fmt: skip
        r = await api_client.put(f"{P}/trace-export", json=body)
        assert r.status_code == 422 and "public addresses only" in r.text, (body, r.text)

    answer = {"collector.example.com": "93.184.215.14"}

    async def getaddrinfo(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answer[host], port))]

    import asyncio

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)
    sent: list[tuple] = []
    monkeypatch.setattr(trace_export, "test_send", lambda *a: sent.append(a))
    body = {"kind": "otlp", "endpoint": "https://collector.example.com/v1/traces"}
    assert (await api_client.put(f"{P}/trace-export", json=body)).status_code == 200
    answer["collector.example.com"] = "10.0.0.5"  # the name moved after it was saved
    test = (await api_client.post(f"{P}/trace-export/test")).json()
    assert test["ok"] is False and "private or internal" in test["detail"] and sent == []
    assert app.state.trace_export.exporter.public_only is False  # this app was made in the self-hosted edition


def test_the_hosted_exporter_pins_every_connection(monkeypatch):
    """Every run's trace and the test span: in the hosted edition neither reaches this server's own loopback."""
    import http.server
    import threading

    from agentcanvas.services.trace_export import Exporter, test_send

    hits: list[str] = []

    class Collector(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append(self.path)
            self.rfile.read(int(self.headers.get("content-length") or 0))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "1")  # a refused connection is retried until then
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/v1/traces"
    try:
        for public_only in (True, False):
            hits.clear()
            problem = test_send(url, {}, public_only, timeout=1)
            ex = Exporter(public_only=public_only)
            provider = ex._provider("prj_a", url, {})
            provider.get_tracer("t").start_span("run").end()
            provider.force_flush()
            ex.shutdown()
            assert (problem is None, len(hits)) == ((False, 0) if public_only else (True, 2)), (public_only, problem)
    finally:
        server.shutdown()
