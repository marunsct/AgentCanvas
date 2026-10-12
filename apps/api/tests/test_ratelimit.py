"""Rate limits on the public API per deployment key, webhook and project (S12, S30)."""

from __future__ import annotations

import json

import pytest
from test_publish import bearer, make_greeter
from test_triggers import signed

from agentcanvas.errors import TooManyRequests
from agentcanvas.services.ratelimit import RateLimiter


@pytest.fixture
def limits(app, monkeypatch):
    """Lower limits, on a clock that stays inside one minute (a slow test crossing a minute would reset them)."""
    from types import SimpleNamespace

    from agentcanvas.services import ratelimit

    monkeypatch.setattr(ratelimit, "time", SimpleNamespace(time=lambda: 1_800_000_030.0))
    s = app.state.settings
    saved = (s.rate_limit_per_deployment, s.rate_limit_per_webhook, s.rate_limit_per_project)
    saved_share = s.rate_limit_per_share_client
    yield s
    s.rate_limit_per_deployment, s.rate_limit_per_webhook, s.rate_limit_per_project = saved
    s.rate_limit_per_share_client = saved_share
    s.rate_limit_by_environment = {}


async def test_deployment_keys_are_limited_per_minute(app, api_client, limits):
    limits.rate_limit_per_deployment, limits.rate_limit_per_project = 2, 0
    wf = await make_greeter(api_client)
    pub = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()
    call, key = f"/v1/deployments/{pub['deployment']['id']}/runs/wait", bearer(pub["api_key"])
    for _ in range(2):
        assert (await api_client.post(call, json={"input": {"who": "Ada"}}, headers=key)).status_code == 200
    refused = await api_client.post(call, json={"input": {"who": "Ada"}}, headers=key)
    assert refused.status_code == 429 and 1 <= int(refused.headers["retry-after"]) <= 61
    assert refused.json()["limit"].startswith("deployment:")
    # nobody without the key can use it up: unauthenticated calls stop at 401 before they are counted
    assert (await api_client.post(call, json={"input": {}})).status_code == 401

    limits.rate_limit_by_environment = {"prod": 0}  # an environment can lift (or lower) the limit
    assert (await api_client.post(call, json={"input": {"who": "Ada"}}, headers=key)).status_code == 200


async def test_webhooks_and_projects_are_limited(app, api_client, limits):
    limits.rate_limit_per_webhook, limits.rate_limit_per_project = 1, 2
    wf = await make_greeter(api_client)
    hooks = [
        (await api_client.post(f"/v1/workflows/{wf}/triggers", json={"kind": "webhook", "config": {}})).json()
        for _ in range(2)
    ]
    body = json.dumps({}).encode()

    def deliver(t):
        return api_client.post(t["url"], content=body, headers=signed(t["secret"], body))

    assert (await deliver(hooks[0])).status_code == 202
    assert (await deliver(hooks[0])).status_code == 429  # this webhook's limit
    assert (await deliver(hooks[1])).status_code == 202  # the refused call did not count for the project
    over = await deliver(hooks[1])
    assert over.status_code == 429 and over.json()["limit"].startswith(("hook:", "project:"))
    assert (await api_client.post(hooks[0]["url"], content=body)).status_code == 401  # signature first


async def test_windows_reset_and_old_ones_are_dropped(app):
    limiter = RateLimiter(app.state.db)
    await limiter.hit(("k", 1), now=0)
    with pytest.raises(TooManyRequests) as exc:
        await limiter.hit(("k", 1), now=59.5)
    assert exc.value.extra["retry_after"] == 1
    await limiter.hit(("k", 1), now=60)  # a new minute
    await limiter.hit(("k", 0), ("other", -1), now=61)  # no limit set: not counted

    from sqlalchemy import select

    from agentcanvas.db.models import RateWindow

    async with app.state.db.sessionmaker() as s:
        assert (await s.execute(select(RateWindow.key, RateWindow.slot))).all() == [("k", 1)]
