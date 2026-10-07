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
