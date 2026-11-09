"""An organisation (or the operator) may forbid approving your own run: an approval step is then a four-eyes control,
not a click the person who asked for the action makes themselves."""

from __future__ import annotations

from test_roles import act_as
from test_runs_v1 import events, triage_api  # noqa: F401 - fixture

from agentcanvas.api.deps import current_user
from agentcanvas.db.models import AppUser, InterruptTask, Membership

APPROVE = {"value": {"decisions": [{"type": "approve"}]}}


async def _second_reviewer(app) -> None:
    async with app.state.db.sessionmaker() as s:
        s.add(AppUser(id="usr_rev", oidc_subject="rev", email="rev@example.com"))
        await s.flush()
        s.add(Membership(org_id="org_default", user_id="usr_rev", role="reviewer", accepted=True))
        await s.commit()


async def test_who_started_a_run_cannot_approve_it_when_the_organisation_says_so(app, triage_api):  # noqa: F811
    api, wf = triage_api, triage_api.wf_id
    r = await api.put("/v1/org/settings", json={"limits": {"approve_own_runs": False}})
    assert r.status_code == 200 and r.json()["limits"]["effective"]["approve_own_runs"] is False, r.text
    act_as(app, "builder", "usr_dev")
    try:
        await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"ticket": "Invoice?"}, "thread_id": "th_own"})
        [item] = (await api.get("/v1/inbox", params={"workflow_id": wf})).json()
        async with app.state.db.sessionmaker() as s:
            assert (await s.get(InterruptTask, item["id"])).requested_by == "usr_dev"
        r = await api.post(f"/v1/inbox/{item['id']}/decision", json=APPROVE)
        assert r.status_code == 403 and "you started" in r.json()["detail"], r.text
        r = await api.post("/v1/threads/th_own/resume", json=APPROVE)  # the other way to answer it
        assert r.status_code == 403, r.text
        await _second_reviewer(app)
        act_as(app, "reviewer", "usr_rev")
        r = await api.post(f"/v1/inbox/{item['id']}/decision", params={"stream": "true"}, json=APPROVE)
        assert events(r.text)[-1]["outcome"] == "success", r.text
    finally:
        app.dependency_overrides.pop(current_user, None)


async def test_by_default_a_person_may_approve_their_own_run(app, triage_api):  # noqa: F811
    api, wf = triage_api, triage_api.wf_id
    await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"ticket": "Invoice?"}, "thread_id": "th_mine"})
    [item] = (await api.get("/v1/inbox", params={"workflow_id": wf})).json()
    r = await api.post(f"/v1/inbox/{item['id']}/decision", params={"stream": "true"}, json=APPROVE)
    assert events(r.text)[-1]["outcome"] == "success", r.text


async def test_the_operator_may_forbid_it_for_every_organisation(app, triage_api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app.state.settings, "approve_own_runs", False)
    api = triage_api
    r = await api.put("/v1/org/settings", json={"limits": {"approve_own_runs": True}})
    assert r.status_code == 422, r.text  # an organisation cannot allow what the server forbids
    await api.post(f"/v1/workflows/{api.wf_id}/runs/stream", json={"input": {"ticket": "x"}, "thread_id": "th_s"})
    [item] = (await api.get("/v1/inbox", params={"workflow_id": api.wf_id})).json()
    assert (await api.post(f"/v1/inbox/{item['id']}/decision", json=APPROVE)).status_code == 403
