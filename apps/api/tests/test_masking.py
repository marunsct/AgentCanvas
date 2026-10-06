"""Trace masking (S12 item 8): secrets never reach LangSmith or OTel; trace_redact fields never reach a trace."""

from __future__ import annotations

import langsmith
from test_publish import greeter

from agentcanvas.adapters.langchain.nodes import node_options
from agentcanvas.adapters.langchain.plan import lower
from agentcanvas.ir.models import Workflow
from agentcanvas.runtime import masking

SECRET = "s3cret-token-value"


def test_mask_hides_known_secret_values(monkeypatch):
    monkeypatch.setattr(masking, "provider", lambda: [SECRET, "short"])  # under 6 characters: too common to mask
    assert masking.mask({"a": [f"Bearer {SECRET}"], "b": "short"}) == {"a": ["Bearer ***"], "b": "short"}
    assert masking.mask(f"x{SECRET}") == "x***"
    assert masking.mask({"n": 1}) == {"n": 1}
    # escaped in JSON text (quotes, backslashes, non-ASCII) or digits inside a longer number: still masked, never broken
    odd = ['pa"ss\\wörd', "123456"]
    monkeypatch.setattr(masking, "provider", lambda: odd)
    masked = masking.mask({"k": f"x{odd[0]}y", "n": 91234567, "s": "91234567"})
    assert masked == {"k": "x***y", "n": 91234567, "s": "9***7"}


def test_langsmith_runs_are_masked(monkeypatch):
    monkeypatch.setattr(masking, "provider", lambda: [SECRET])
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    assert not masking.install_langsmith()  # tracing off: nothing to install
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test")
    try:
        assert masking.install_langsmith()
        from langchain_core.tracers.langchain import get_client  # what LangChain's tracer uses

        client = get_client()
        assert client._hide_run_inputs({"messages": [f"key is {SECRET}"]}) == {"messages": ["key is ***"]}
        assert client._hide_run_outputs({"out": SECRET}) == {"out": "***"}
        assert client._hide_run_error(f"HTTPError: Bearer {SECRET}") == "HTTPError: Bearer ***"
    finally:
        langsmith.configure(client=None)


def test_trace_redact_fields_on_every_step():
    policy = node_options(None, redact=["password"])["trace_policy"]
    assert policy.process_inputs({"password": "hunter22", "user": {"password": 1, "name": "ada"}}) == {
        "password": "[redacted]", "user": {"password": "[redacted]", "name": "ada"},
    }  # fmt: skip
    summarized = node_options({"trace": "summarize"}, redact=["password"])["trace_policy"]
    assert summarized.process_outputs({"password": "x", "n": "abc"}) == {"password": "[redacted]", "n": "str(3)"}
    assert "trace_policy" not in node_options(None)

    ir = greeter("wf_x", "Hello")
    ir["settings"] = {"trace_redact": ["who"]}
    [step] = lower(Workflow.model_validate(ir)).nodes
    assert step.options["redact"] == ["who"]


async def test_a_redacting_workflow_runs(app, api_client):
    """The redacting trace policy compiles in the interpreter and does not change what the run computes."""
    import asyncio

    from test_publish import make_greeter

    wf_id = await make_greeter(api_client)
    ir = (await api_client.get(f"/v1/workflows/{wf_id}")).json()["ir"]
    ir["settings"] = {**ir.get("settings", {}), "trace_redact": ["who"]}
    wf = Workflow.model_validate(ir)
    h = await app.state.runner.start(wf, input={"who": "Ada"})
    await asyncio.wait_for(h.done.wait(), 10)
    assert next(e for e in h.events if e["type"] == "RUN_FINISHED")["output"]["out"] == "Hello Ada"


def test_span_status_descriptions_are_masked(monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    monkeypatch.setattr(masking, "provider", lambda: [SECRET])
    inner = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(masking.MaskingExporter(inner)))
    try:
        with tracer_provider.get_tracer("t").start_as_current_span("call"):
            raise RuntimeError(f"refused key {SECRET}")
    except RuntimeError:
        pass
    [span] = inner.get_finished_spans()
    assert span.status.description == "RuntimeError: refused key ***"
    assert SECRET not in str([e.attributes for e in span.events])


def test_trace_redact_inside_a_run_context():
    """Outside a run nothing is redacted; inside, fields go at any depth and their values wherever they reappear."""
    assert masking.mask({"ssn": "123-45-6789"}) == {"ssn": "123-45-6789"}
    with masking.redacting(["ssn"]):
        assert masking.mask({"input": {"ssn": "123-45-6789", "n": 1}}) == {"input": {"ssn": "[redacted]", "n": 1}}
        prompt = {"messages": [[{"content": "Check 123-45-6789 please"}]]}
        assert masking.mask(prompt) == {"messages": [[{"content": "Check [redacted] please"}]]}
    assert masking.mask(prompt) == prompt


async def test_a_traced_run_redacts_the_root_and_model_runs(app, api_client, monkeypatch):
    """With LangSmith tracing on, nothing a `trace_redact` field held reaches any run the client uploads."""
    import json

    from test_runs_v1 import demo_triage, linear, make

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test")
    demo_triage(app)
    llm = {
        "id": "n_llm",
        "type": "core.llm",
        "name": "ask",
        "config": {"prompt": "About {{ state.ssn }}", "output": "out"},
    }
    ir = linear(llm, channels=("ssn", "out"), settings={"trace_redact": ["ssn"]})
    ir["resources"] = [{"id": "r_m", "type": "model.chat", "name": "m", "config": {"model": "fake:scripted"}}]
    ir["edges"].append(
        {"id": "w", "kind": "wiring", "source": {"node": "r_m"}, "target": {"node": "n_llm", "port": "model"}}
    )
    wf = await make(api_client, ir)
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:9")  # nothing listens: uploads fail fast
    sent: list = []
    anonymize = masking.mask

    def record(payload):
        out = anonymize(payload)
        sent.append(out)
        return out

    monkeypatch.setattr(masking, "mask", record)  # what the client installed below hands to LangSmith
    from langsmith import utils

    utils.get_env_var.cache_clear()  # langsmith caches the environment it read
    try:
        assert masking.install_langsmith()
        r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"ssn": "123-45-6789"}})
        assert "RUN_FINISHED" in r.text
    finally:
        langsmith.configure(client=None)
        utils.get_env_var.cache_clear()
    uploaded = json.dumps(sent, default=str)
    assert sent and "[redacted]" in uploaded
    assert "123-45-6789" not in uploaded
    assert "About [redacted]" in uploaded  # the model run's prompt went through the anonymizer too
