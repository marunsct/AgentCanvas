"""Versioned public API (/v1).

Every route requires a signed-in user (M7-T1), and every id it names must belong to the user's
organisation (M7-T2) and the user's role must allow it (M7-T3), except the deployment endpoints and their MCP and A2A
faces (deployment API keys), signed webhooks (HMAC, from triggers and Matura), share links (their token) and the
sign-in configuration.
"""

from fastapi import APIRouter, Depends

from agentcanvas.api.deps import current_user, org_scope, role_policy
from agentcanvas.api.v1 import (
    annotation,
    auth,
    bridges,
    catalog,
    copilot,
    datasets,
    deployments,
    designs,
    evaluators,
    events,
    experiments,
    gates,
    knowledge,
    matura,
    memory,
    online,
    org,
    playground,
    projects,
    protocols,
    runs,
    secrets,
    setup,
    share,
    tools,
    traces,
    triggers,
    versions,
    workflows,
    workspace,
)

signed_in = APIRouter(dependencies=[Depends(current_user), Depends(role_policy), Depends(org_scope)])
signed_in.include_router(auth.router)
signed_in.include_router(catalog.router)
signed_in.include_router(org.router)
signed_in.include_router(projects.router)
signed_in.include_router(workflows.router)
signed_in.include_router(designs.router)
signed_in.include_router(bridges.router)
signed_in.include_router(copilot.router)
signed_in.include_router(runs.router)
signed_in.include_router(tools.router)
signed_in.include_router(memory.router)
signed_in.include_router(knowledge.router)
signed_in.include_router(workspace.router)
signed_in.include_router(secrets.router)
signed_in.include_router(setup.router)
signed_in.include_router(playground.router)
signed_in.include_router(events.router)
signed_in.include_router(versions.router)
signed_in.include_router(datasets.router)
signed_in.include_router(evaluators.router)
signed_in.include_router(experiments.router)
signed_in.include_router(gates.router)
signed_in.include_router(online.router)
signed_in.include_router(traces.router)
signed_in.include_router(annotation.router)
signed_in.include_router(deployments.router)
signed_in.include_router(share.router)
signed_in.include_router(triggers.router)

router = APIRouter(prefix="/v1")
router.include_router(signed_in)
router.include_router(auth.public_router)
router.include_router(deployments.public_router)
router.include_router(protocols.router)  # MCP and A2A, deployment keys
router.include_router(triggers.public_router)
router.include_router(share.public_router)  # share links (S23): the token is the capability
router.include_router(matura.public_router)  # Matura's webhooks (TS9a): HMAC
