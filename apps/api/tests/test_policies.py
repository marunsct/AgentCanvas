"""Policy-as-code lint profiles (FR-X-19): organisation rules as CEL over the IR, in the editor and at publish."""

from __future__ import annotations

import pytest
from test_blast import agent_wf
from test_publish import make_greeter

from agentcanvas.api.deps import CurrentUser, current_user
from agentcanvas.ir.models import Workflow
from agentcanvas.validation import validate
from agentcanvas.validation.policies import Policy, check_policies

WRITES_NEED_APPROVAL = Policy(
    name="writes need approval",
    description="a person approves every external write",
    require='node.effects.all(e, e.effect != "external_write" || e.gated)',
)
PROD_MODELS = Policy(
    name="approved models",
    scope="resource",
    match='resource.type == "model.chat"',
    require='resource.config.model in ["anthropic:claude-sonnet-5-5"]',
    environments=["prod"],
)


def test_every_external_write_requires_approval():
    """FR-X-19 AC: "every external_write requires approval" as a policy."""
    [d] = check_policies(Workflow.model_validate(agent_wf()), [WRITES_NEED_APPROVAL])
    assert (d.code, d.severity, d.node_id) == ("E095", "error", "n_ag")
    assert "'assistant' breaks policy 'writes need approval': a person approves" in d.message
    assert not check_policies(Workflow.model_validate(agent_wf(approve=["update_crm"])), [WRITES_NEED_APPROVAL])
    assert not check_policies(Workflow.model_validate(agent_wf(gate=True)), [WRITES_NEED_APPROVAL])


def test_environment_policies_warn_in_the_editor_and_block_their_environment():
    """FR-X-19 AC: "no model outside the allow-list in prod"."""
    wf = Workflow.model_validate(agent_wf())
    [editor] = check_policies(wf, [PROD_MODELS])
    assert (editor.code, editor.severity, editor.node_id) == ("W095", "warning", "r_m")
    assert "enforced when publishing to prod" in editor.message
    [prod] = check_policies(wf, [PROD_MODELS], environment="prod")
    assert (prod.code, prod.severity) == ("E095", "error")
    assert check_policies(wf, [PROD_MODELS], environment="staging") == []
    assert check_policies(wf, [PROD_MODELS.model_copy(update={"enabled": False})], environment="prod") == []


def test_a_policy_that_cannot_be_evaluated_fails_closed():
    broken = Policy(
        name="needs a timeout", scope="resource", match='resource.id == "r_m"', require="resource.config.timeout > 0"
    )
    [d] = check_policies(Workflow.model_validate(agent_wf()), [broken])  # the model has no timeout: missing field
    assert (d.code, d.severity) == ("E096", "error")
    not_bool = Policy(name="counts", scope="workflow", require="size(workflow.nodes)")
    assert check_policies(Workflow.model_validate(agent_wf()), [not_bool])[0].code == "E096"


def test_policies_run_with_the_validator():
    diags = validate(Workflow.model_validate(agent_wf()), policies=[WRITES_NEED_APPROVAL])
    assert any(d.code == "E095" for d in diags)


NEEDS_A_PERSON = {
    "name": "a person in the loop",
    "scope": "workflow",
    "require": 'workflow.node_types.exists(t, t == "core.approval")',
    "environments": ["prod"],
}


async def test_publish_enforces_the_organisations_policies(api_client):
    wf = await make_greeter(api_client)
    r = await api_client.post("/v1/org/policies", json=NEEDS_A_PERSON)
    assert r.status_code == 201, r.text
    # the editor shows it early, as a warning
    diags = (await api_client.get(f"/v1/workflows/{wf}")).json()["diagnostics"]
    assert [d["code"] for d in diags if d["code"].endswith("095")] == ["W095"]
    refused = await api_client.post(f"/v1/workflows/{wf}/publish", json={"environment": "prod"})
    assert refused.status_code == 422
    checks = {c["name"]: c for c in refused.json()["checklist"]}
    assert checks["policies"]["ok"] is False and checks["validation"]["ok"] is True
    assert "a person in the loop" in checks["policies"]["items"][0]
    staging = await api_client.post(f"/v1/workflows/{wf}/publish", json={"environment": "staging"})
    assert staging.status_code == 200, staging.text
    # switching the policy off lifts it
    pid = r.json()["id"]
    off = await api_client.put(f"/v1/org/policies/{pid}", json={**NEEDS_A_PERSON, "enabled": False})
    assert off.status_code == 200 and off.json()["enabled"] is False
    assert (await api_client.post(f"/v1/workflows/{wf}/publish", json={"environment": "prod"})).status_code == 200


async def test_policy_crud_checks_expressions_and_is_audited(api_client):
    bad = await api_client.post("/v1/org/policies", json={"name": "bad", "require": "node.type =="})
    assert bad.status_code == 422 and "invalid condition" in bad.json()["detail"]
    r = await api_client.post("/v1/org/policies", json=NEEDS_A_PERSON)
    assert (await api_client.post("/v1/org/policies", json=NEEDS_A_PERSON)).status_code == 409
    listed = (await api_client.get("/v1/org/policies")).json()
    assert [p["name"] for p in listed] == ["a person in the loop"]
    pid = r.json()["id"]
    assert (await api_client.delete(f"/v1/org/policies/{pid}")).status_code == 204
    assert (await api_client.delete(f"/v1/org/policies/{pid}")).status_code == 404
    log = (await api_client.get("/v1/org/audit")).json()
    assert [e["action"] for e in log[:2]] == ["delete_policy", "create_policy"]
    assert log[1]["target"] == f"policy_id={pid}"


@pytest.mark.parametrize("role", ["builder", "viewer"])
async def test_only_admins_change_policies(app, api_client, role):
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="usr_dev", org_id="org_default", role=role)
    try:
        assert (await api_client.get("/v1/org/policies")).status_code == 200
        assert (await api_client.post("/v1/org/policies", json=NEEDS_A_PERSON)).status_code == 403
    finally:
        app.dependency_overrides.pop(current_user)


async def test_policies_belong_to_one_organisation(app, api_client):
    pid = (await api_client.post("/v1/org/policies", json=NEEDS_A_PERSON)).json()["id"]
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="usr_b", org_id="org_b", role="admin")
    try:
        assert (await api_client.get("/v1/org/policies")).json() == []
        assert (await api_client.delete(f"/v1/org/policies/{pid}")).status_code == 404
        assert (await api_client.put(f"/v1/org/policies/{pid}", json=NEEDS_A_PERSON)).status_code == 404
    finally:
        app.dependency_overrides.pop(current_user)
