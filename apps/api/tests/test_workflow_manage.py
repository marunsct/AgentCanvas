"""Home's workflow list: rename and duplicate (delete existed); the copy has the draft and layout, not the history."""

from __future__ import annotations

from test_publish import make_greeter

P = "/v1/projects/prj_default"


async def test_rename_and_duplicate_a_workflow(api_client):
    wf = await make_greeter(api_client)
    await api_client.put(f"/v1/workflows/{wf}/layout", json={"positions": {"n_g": {"x": 10, "y": 20}}})
    await api_client.post(f"/v1/workflows/{wf}/versions", json={"message": "first"})

    r = await api_client.patch(f"/v1/workflows/{wf}", json={"name": "  Welcome  "})
    assert r.status_code == 200 and r.json()["name"] == "Welcome" and r.json()["ir"]["name"] == "Welcome"
    assert (await api_client.patch(f"/v1/workflows/{wf}", json={"name": "   "})).status_code == 422
    names = [w["name"] for w in (await api_client.get(f"{P}/workflows")).json()["items"]]
    assert "Welcome" in names

    copy = await api_client.post(f"/v1/workflows/{wf}/duplicate", json={})
    assert copy.status_code == 201, copy.text
    c = copy.json()
    assert c["id"] != wf and c["name"] == "Welcome (copy)" and c["ir"]["id"] == c["id"]
    assert [n["id"] for n in c["ir"]["nodes"]] == ["n_g"] and c["layout"]["n_g"] == {"x": 10, "y": 20}
    assert (await api_client.get(f"/v1/workflows/{c['id']}/versions")).json() == []  # not the original's history
    named = (await api_client.post(f"/v1/workflows/{wf}/duplicate", json={"name": "Hello again"})).json()
    assert named["name"] == "Hello again"
    run = await api_client.post(f"/v1/workflows/{c['id']}/runs/stream", json={"input": {"who": "Ada"}})
    assert run.status_code == 200 and "Hello Ada" in run.text  # the copy runs on its own


async def test_times_are_sent_in_utc_with_their_timezone(api_client):
    """SQLite keeps no timezone, and a time without one reads as local time in a browser: a workflow changed a
    minute ago showed as "9 hours ago" in Tokyo."""
    from datetime import UTC, datetime

    made = (await api_client.post("/v1/projects/prj_default/workflows", json={"name": "Clock"})).json()
    listed = (await api_client.get("/v1/projects/prj_default/workflows")).json()["items"]
    for stamp in (
        made["created_at"],
        made["updated_at"],
        next(w for w in listed if w["id"] == made["id"])["updated_at"],
    ):
        when = datetime.fromisoformat(stamp)
        assert when.tzinfo is not None, stamp
        assert abs((datetime.now(UTC) - when).total_seconds()) < 120
