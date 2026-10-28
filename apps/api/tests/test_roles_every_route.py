"""Roles on every route, from the OpenAPI document (see test_auth_every_route.py for sign-in itself).

A viewer or a reviewer may do only what `ROLE_EXCEPTIONS` (api/deps.py) says their role may do; every other write is
refused with 403. A new route that forgets its role check, or an exception added by accident, fails here.
"""

from __future__ import annotations

import re

import pytest

from agentcanvas.api.deps import RANK, ROLE_EXCEPTIONS, CurrentUser, current_user


@pytest.mark.parametrize("role", ["viewer", "reviewer"])
async def test_a_role_can_do_only_what_the_exceptions_table_allows(app, api_client, role):
    wf = (await api_client.post("/v1/projects/prj_default/workflows", json={"name": "Mine"})).json()
    allowed = {key for key, needed in ROLE_EXCEPTIONS.items() if RANK[needed] <= RANK[role]}
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="usr_dev", org_id="org_default", role=role, memberships={"org_default": role}
    )
    try:
        let_through: list[str] = []
        calls = 0
        for path, item in sorted(app.openapi()["paths"].items()):
            for method in item:
                if (
                    method not in ("post", "put", "patch", "delete")
                    or (method.upper(), path.removeprefix("/v1")) in allowed
                ):
                    continue
                url = path.replace("{project_id}", "prj_default").replace("{wf_id}", wf["id"])
                r = await api_client.request(method.upper(), re.sub(r"\{\w+\}", "does_not_exist", url), json={})
                calls += 1
                if r.status_code < 400:
                    let_through.append(f"{method.upper()} {path} -> {r.status_code}")
        assert calls > 100
        assert not let_through, f"a {role} was let through:\n" + "\n".join(let_through)
    finally:
        app.dependency_overrides.pop(current_user, None)
