"""EU AI Act Art. 50(2): what a published deployment answers is machine-readably marked as AI-generated (the
`X-AI-Generated` header, and `generated_by` on API results and the final stream event)."""

from __future__ import annotations

from test_gateway import _events, _llm_ir, _make
from test_protocols import rpc
from test_publish import bearer, make_greeter

from agentcanvas.services.disclosure import generated_by, marked

MINI = "openai:gpt-5-mini"


async def _publish(api, wf: str) -> tuple[str, str]:
    pub = (await api.post(f"/v1/workflows/{wf}/publish", json={})).json()
    return pub["deployment"]["id"], pub["api_key"]


def _usage(model: str | None) -> dict:
    return {"type": "CUSTOM", "name": "ac.usage", "value": {"model": model, "input_tokens": 1, "output_tokens": 1}}


async def test_the_models_that_answered_are_named_once_in_order():
    events = [_usage(MINI), {"type": "TEXT_MESSAGE_CONTENT"}, _usage("anthropic:x"), _usage(MINI), _usage(None)]
    assert generated_by(events).model_dump() == {"kind": "ai", "models": [MINI, "anthropic:x"]}

    async def stream():
        for ev in [*events, {"type": "RUN_FINISHED", "status": "success"}]:
            yield ev

    finished = {"type": "RUN_FINISHED", "status": "success"}
    out = [ev async for ev in marked(stream())]
    assert out[-1] == {**finished, "generated_by": {"kind": "ai", "models": [MINI, "anthropic:x"]}}
    assert out[:-1] == events  # every other event passes through as it was


async def test_deployment_api_results_and_streams_are_marked_with_the_models(app, api_client):
    from agentcanvas.testing import DemoResources

    demo = DemoResources()
    app.state.resources_factory = lambda thread_id, wf: demo
    put = await api_client.put("/v1/projects/prj_default/secrets/OPENAI_API_KEY", json={"value": "sk-test-key-123"})
    assert put.status_code == 204
    dep, key = await _publish(api_client, await _make(api_client, _llm_ir({"model": MINI})))

    r = await api_client.post(f"/v1/deployments/{dep}/runs/wait", json={"input": {"q": "hi"}}, headers=bearer(key))
    assert r.status_code == 200 and r.json()["status"] == "success", r.text
    assert r.headers["x-ai-generated"] == "true"
    assert r.json()["generated_by"] == {"kind": "ai", "models": [MINI]}

    r = await api_client.post(f"/v1/deployments/{dep}/runs/stream", json={"input": {"q": "hi"}}, headers=bearer(key))
    assert r.headers["x-ai-generated"] == "true" and r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)
    assert events[-1]["type"] == "RUN_FINISHED" and events[-1]["generated_by"] == {"kind": "ai", "models": [MINI]}


async def test_every_way_a_deployment_answers_says_it_is_ai_generated(api_client):
    """A workflow that called no model still answers as the published AI system: marked, with no models named."""
    dep, key = await _publish(api_client, await make_greeter(api_client))
    auth = bearer(key)
    r = await api_client.post(f"/v1/deployments/{dep}/runs/wait", json={"input": {"who": "Ada"}}, headers=auth)
    assert r.headers["x-ai-generated"] == "true" and r.json()["generated_by"] == {"kind": "ai", "models": []}

    mcp = f"/v1/deployments/{dep}/mcp"
    call = await api_client.post(
        mcp, json=rpc("tools/call", {"name": "greeter", "arguments": {"who": "Ada"}}), headers=auth
    )
    assert call.json()["result"]["isError"] is False and call.headers["x-ai-generated"] == "true"
    listed = await api_client.post(mcp, json=rpc("tools/list"), headers=auth)
    assert listed.status_code == 200 and "x-ai-generated" not in listed.headers  # nothing generated

    msg = {"message": {"role": "user", "messageId": "m1", "parts": [{"kind": "data", "data": {"who": "Grace"}}]}}
    sent = await api_client.post(f"/v1/deployments/{dep}/a2a", json=rpc("message/send", msg), headers=auth)
    assert sent.json()["result"]["status"]["state"] == "completed" and sent.headers["x-ai-generated"] == "true"

    share = (await api_client.put(f"/v1/deployments/{dep}/share", json={"enabled": True})).json()
    body = {"message": "Ada", "visitor": "visitor-ada-0123456789"}
    r = await api_client.post(f"/v1/share/{share['token']}/runs/stream", json=body)
    assert r.headers["x-ai-generated"] == "true"
    assert _events(r.text)[-1]["generated_by"] == {"kind": "ai"}  # visitors are not told which models run inside
