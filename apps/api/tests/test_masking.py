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
    same = {"n": 1}
    assert masking.mask(same) is same


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
