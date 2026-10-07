"""Sprint 7 secrets (M5-T6): values never appear in API responses, run events, generated code or the database."""

from __future__ import annotations

import json

from sqlalchemy import select

from agentcanvas.db.models import Secret

P = "/v1/projects/prj_default"
VALUE = "tok-3f9a1c77e2"


def events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


async def script_wf(api, name: str, body: str) -> str:
    await api.put(f"{P}/files/scripts/{name}.py", json={"content": body})
    ir = {
        "name": name,
        "state": {"channels": [{"name": "out"}]},
        "nodes": [{"id": "n_s", "type": "core.script", "name": name, "config": {"import_path": f"scripts.{name}:run"}}],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_s"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_s"}, "target": {"node": "END"}},
        ],
    }
    wf = (await api.post(f"{P}/workflows", json={"name": name})).json()
    r = await api.put(f"/v1/workflows/{wf['id']}", json={"ir": {**ir, "id": wf["id"]}}, headers={"If-Match": "1"})
    assert r.status_code == 200, r.text
    return wf["id"]


async def test_secrets_are_write_only_and_redacted_everywhere(app, api_client):
    assert (await api_client.put(f"{P}/secrets/API_TOKEN", json={"value": VALUE})).status_code == 204
    assert (await api_client.put(f"{P}/secrets/bad-name", json={"value": "x"})).status_code == 422
    listed = await api_client.get(f"{P}/secrets")
    assert [s["name"] for s in listed.json()] == ["API_TOKEN"] and VALUE not in listed.text

    async with app.state.db.sessionmaker() as s:  # encrypted at rest
        row = await s.scalar(select(Secret).where(Secret.name == "API_TOKEN"))
        assert row is not None and VALUE.encode() not in row.ciphertext

    uses = await script_wf(
        api_client,
        "uses_secret",
        "from agentcanvas.sdk import secret\n\n\ndef run(state):\n"
        "    return {'out': f'len={len(secret(\"API_TOKEN\"))}'}\n",
    )
    fin = events((await api_client.post(f"/v1/workflows/{uses}/runs/stream", json={})).text)[-1]
    assert fin["output"]["out"] == f"len={len(VALUE)}"  # available to in-process code

    leaks = await script_wf(
        api_client,
        "leaks_secret",
        "from agentcanvas.sdk import secret\n\n\ndef run(state):\n    return {'out': 'value ' + secret('API_TOKEN')}\n",
    )
    stream = (await api_client.post(f"/v1/workflows/{leaks}/runs/stream", json={"thread_id": "th_leak"})).text
    assert VALUE not in stream and events(stream)[-1]["output"]["out"] == "value ***"  # masked in every event
    for read in ("", "/history?include_values=true", "/explain"):  # S12: the state the run wrote holds it
        r = await api_client.get(f"/v1/threads/th_leak{read}")
        assert r.status_code == 200 and VALUE not in r.text and "value ***" in r.text, read
    from agentcanvas.log import hide_secret_values

    line = hide_secret_values(None, "error", {"event": "call failed", "exception": f"401 for token={VALUE}"})
    assert line["exception"] == "401 for token=***"  # and in log lines

    ir = (await api_client.get(f"/v1/workflows/{uses}")).json()["ir"]
    ir["resources"] = [
        {
            "id": "r_m",
            "type": "model.chat",
            "name": "m",
            "config": {"model": "openai:gpt-5-mini", "api_key": {"$secret": "API_TOKEN"}},
        }
    ]
    await api_client.put(f"/v1/workflows/{uses}", json={"ir": ir}, headers={"If-Match": "2"})
    code = (await api_client.get(f"/v1/workflows/{uses}/code")).text
    assert '"$secret": "API_TOKEN"' in code and VALUE not in code  # exported code carries references only

    assert (await api_client.delete(f"{P}/secrets/API_TOKEN")).status_code == 204
    fin = events((await api_client.post(f"/v1/workflows/{uses}/runs/stream", json={})).text)[-1]
    assert fin["type"] == "RUN_ERROR" and "API_TOKEN" in fin["message"] and VALUE not in fin["message"]
    assert (await api_client.delete(f"{P}/secrets/API_TOKEN")).status_code == 404


async def test_secrets_json_escapes_are_masked_too(app, api_client):
    """A value with a quote, backslash or newline is masked in run events (the interrupt included) and thread reads,
    though its JSON text differs from the value."""
    from agentcanvas.services.secrets import hide_secrets

    odd = 'pa"ss\\wo\nrd-77'
    assert hide_secrets({"a": [f"x {odd} y"], odd: 1}, {"K": odd}) == {"a": ["x *** y"], "***": 1}
    assert hide_secrets({"a": "clean"}, {"K": odd}) == {"a": "clean"}
    assert (await api_client.put(f"{P}/secrets/ODD", json={"value": odd})).status_code == 204
    await api_client.put(
        f"{P}/files/scripts/leak.py",
        json={
            "content": "from agentcanvas.sdk import secret\n\n\ndef run(state):\n    return {'out': secret('ODD')}\n"
        },
    )
    ask = {"id": "n_a", "type": "core.approval", "name": "sign", "config": {"show": "out", "output": "ok"}}
    leak = {"id": "n_s", "type": "core.script", "name": "leak", "config": {"import_path": "scripts.leak:run"}}
    ir = {
        "name": "odd",
        "state": {"channels": [{"name": "out"}, {"name": "ok"}]},
        "nodes": [leak, ask],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_s"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_s"}, "target": {"node": "n_a"}},
            {"id": "e3", "kind": "flow", "source": {"node": "n_a"}, "target": {"node": "END"}},
        ],
    }
    wf = (await api_client.post(f"{P}/workflows", json={"name": "odd"})).json()
    r = await api_client.put(
        f"/v1/workflows/{wf['id']}", json={"ir": {**ir, "id": wf["id"]}}, headers={"If-Match": "1"}
    )
    assert r.status_code == 200, r.text
    stream = (await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"thread_id": "th_odd"})).text
    evs = events(stream)
    assert evs[-1]["status"] == "interrupted" and any(e.get("name") == "ac.interrupt" for e in evs)
    assert odd not in json.dumps(evs) and json.dumps(odd)[1:-1] not in stream
    assert "***" in json.dumps([e for e in evs if e.get("name") == "ac.interrupt"])
    thread = await api_client.get("/v1/threads/th_odd")
    assert json.dumps(odd)[1:-1] not in thread.text and thread.json()["values"]["out"] == "***"


async def test_secrets_survive_a_restart(settings, api_client):
    from agentcanvas.api.main import create_app

    await api_client.put(f"{P}/secrets/DB_PASSWORD", json={"value": "hunter2-long"})
    fresh = create_app(settings)
    async with fresh.router.lifespan_context(fresh):
        assert fresh.state.projects.secrets["prj_default"]["DB_PASSWORD"] == "hunter2-long"
