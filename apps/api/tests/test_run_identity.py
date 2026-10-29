"""The runner tells app connectors who a run acts for (G-7): the person who started it, or nobody."""

from test_publish import make_greeter

from agentcanvas.runtime.identity import CURRENT, RunIdentity


async def test_the_runner_tells_connectors_who_the_run_acts_for(app, api_client, monkeypatch):
    """A run started by a person acts for them; one nobody started (a deployment call) acts for nobody."""
    wf = await make_greeter(api_client)
    adapter = app.state.runner.adapter
    seen: list[RunIdentity | None] = []
    original = adapter.execute

    async def spy(spec, resources):
        seen.append(CURRENT.get())
        async for event in original(spec, resources):
            yield event

    monkeypatch.setattr(adapter, "execute", spy)
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"who": "Ada"}})
    assert r.status_code == 200, r.text
    assert seen == [RunIdentity("org_default", "usr_dev")]
    pub = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()
    headers = {"Authorization": f"Bearer {pub['api_key']}"}
    r = await api_client.post(
        f"/v1/deployments/{pub['deployment']['id']}/runs/wait", json={"input": {"who": "Ada"}}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert seen[-1] == RunIdentity("org_default", None) and len(seen) == 2
