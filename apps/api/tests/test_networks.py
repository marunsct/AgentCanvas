"""E-8: an organisation's IP allow-list applies to every credential that acts in it, never locks out the admin who
saves it, and the operator can switch every list off in an emergency."""

from __future__ import annotations

import httpx
import pytest
from test_publish import bearer, make_greeter

from agentcanvas.errors import InvalidInput
from agentcanvas.services import networks

INSIDE = "127.0.0.0/8"  # the test client's own address is 127.0.0.1
OUTSIDE = ("203.0.113.7", 4321)


def outsider(app, host: tuple[str, int] = OUTSIDE) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=host), base_url="http://test")


async def allow(api, *nets: str) -> httpx.Response:
    return await api.put("/v1/org/settings", json={"security": {"allowed_networks": list(nets)}})


def test_lists_are_parsed_into_networks_and_bounded():
    assert networks.parse([" 10.1.2.3 ", "10.0.0.0/8", "10.1.2.3/32", "2001:db8::1/64"]) == [
        "10.1.2.3/32", "10.0.0.0/8", "2001:db8::/64",
    ]  # fmt: skip
    with pytest.raises(InvalidInput, match=r"'10\.0\.0\.300' is not a network"):
        networks.parse(["10.0.0.300"])
    with pytest.raises(InvalidInput, match="at most 100"):
        networks.parse([f"10.0.{i // 256}.{i % 256}" for i in range(101)])
    assert networks.allows([], None) and networks.allows(None, None)
    assert not networks.allows(["10.0.0.0/8"], None)  # an address that cannot be read is refused


async def test_an_admin_cannot_save_a_list_that_leaves_them_out(api_client):
    r = await allow(api_client, "10.0.0.0/8")
    assert r.status_code == 422 and "your own address (127.0.0.1)" in r.json()["detail"]
    assert (await api_client.get("/v1/org/settings")).json()["security"]["allowed_networks"] == []
    r = await allow(api_client, INSIDE, "10.1.2.3")
    assert r.status_code == 200, r.text
    assert r.json()["security"] | {"allowed_networks": [INSIDE, "10.1.2.3/32"], "your_address": "127.0.0.1",
                                   "networks_ignored": False} == r.json()["security"]  # fmt: skip
    assert (await allow(api_client, "nonsense")).status_code == 422
    too_many = await api_client.put("/v1/org/settings", json={"security": {"allowed_networks": ["10.0.0.1"] * 101}})
    assert too_many.status_code == 422


async def test_signed_in_requests_and_api_tokens_from_elsewhere_are_refused(app, api_client):
    token = (await api_client.post("/v1/me/tokens", json={"name": "ci", "role": "viewer"})).json()["token"]
    assert (await allow(api_client, INSIDE)).status_code == 200
    async with outsider(app) as far:
        r = await far.get("/v1/projects")
        assert r.status_code == 403
        assert r.json()["type"] == "https://agentcanvas.dev/errors/network-not-allowed"
        assert "(203.0.113.7)" in r.json()["detail"] and r.json()["code"] == "network_not_allowed"
        assert (await far.get("/v1/projects", headers=bearer(token))).status_code == 403
    assert (await api_client.get("/v1/projects", headers=bearer(token))).status_code == 200
    # IPv4 seen through an IPv6 socket counts as itself
    async with outsider(app, ("::ffff:127.0.0.1", 1)) as mapped:
        assert (await mapped.get("/v1/projects")).status_code == 200
    # an empty list lets everyone in again
    assert (await allow(api_client)).status_code == 200
    async with outsider(app) as far:
        assert (await far.get("/v1/projects")).status_code == 200


async def test_deployment_keys_and_share_links_follow_the_list(app, api_client):
    wf = await make_greeter(api_client)
    pub = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()
    dep, key = pub["deployment"]["id"], pub["api_key"]
    share = (await api_client.put(f"/v1/deployments/{dep}/share", json={"enabled": True})).json()["token"]
    assert (await allow(api_client, INSIDE)).status_code == 200
    call = f"/v1/deployments/{dep}/runs/wait"
    async with outsider(app) as far:
        assert (await far.post(call, json={"input": {}}, headers=bearer(key))).status_code == 403
        # refused before the key is looked at: a wrong key gets the same answer, so keys cannot be tried from outside
        assert (await far.post(call, json={"input": {}}, headers=bearer("ac_wrong"))).status_code == 403
        assert (await far.post(f"/v1/deployments/{dep}/mcp", json={}, headers=bearer(key))).status_code == 403
        assert (await far.get(f"/v1/share/{share}")).status_code == 403
        assert (await far.post(call.replace(dep, "dep_unknown"), json={"input": {}})).status_code == 401
    assert (await api_client.post(call, json={"input": {"who": "A"}}, headers=bearer(key))).status_code == 200
    assert (await api_client.get(f"/v1/share/{share}")).status_code == 200


async def test_saving_limits_keeps_the_list(api_client):
    assert (await allow(api_client, INSIDE)).status_code == 200
    r = await api_client.put("/v1/org/settings", json={"limits": {"max_concurrent_runs": 5}})
    assert r.status_code == 200 and r.json()["security"]["allowed_networks"] == [INSIDE]
    r = await allow(api_client, INSIDE, "10.0.0.0/8")
    assert r.json()["limits"]["own"]["max_concurrent_runs"] == 5


async def test_the_operator_can_switch_every_list_off(app, api_client):
    assert (await allow(api_client, INSIDE)).status_code == 200
    app.state.settings = app.state.settings.model_copy(update={"ignore_org_networks": True})
    async with outsider(app) as far:
        r = await far.get("/v1/org/settings")
        assert r.status_code == 200 and r.json()["security"]["networks_ignored"] is True
        # the locked-out admin fixes the list from where they are
        assert (await allow(far, "203.0.113.0/24")).status_code == 200
    app.state.settings = app.state.settings.model_copy(update={"ignore_org_networks": False})
    async with outsider(app) as far:
        assert (await far.get("/v1/projects")).status_code == 200
    assert (await api_client.get("/v1/projects")).status_code == 403
