"""Versioned public API (/v1).

Every route requires a signed-in user (M7-T1), and every id it names must belong to the user's
organisation (M7-T2) and the user's role must allow it (M7-T3), except the deployment endpoints (deployment API keys),
signed webhooks (HMAC) and the sign-in configuration.
"""

from fastapi import APIRouter, Depends

from agentcanvas.api.deps import current_user, org_scope, role_policy
from agentcanvas.api.v1 import (
    annotation,
    auth,
    catalog,
    copilot,
    datasets,
    deployments,
    evaluators,
    events,
    experiments,
    gates,
    memory,
    online,
    org,
    projects,
    runs,
    secrets,
    setup,
    tools,
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
signed_in.include_router(copilot.router)
signed_in.include_router(runs.router)
signed_in.include_router(tools.router)
signed_in.include_router(memory.router)
signed_in.include_router(workspace.router)
signed_in.include_router(secrets.router)
signed_in.include_router(setup.router)
signed_in.include_router(events.router)
signed_in.include_router(versions.router)
signed_in.include_router(datasets.router)
signed_in.include_router(evaluators.router)
signed_in.include_router(experiments.router)
signed_in.include_router(gates.router)
signed_in.include_router(online.router)
signed_in.include_router(annotation.router)
signed_in.include_router(deployments.router)
signed_in.include_router(triggers.router)

router = APIRouter(prefix="/v1")
router.include_router(signed_in)
router.include_router(auth.public_router)
router.include_router(deployments.public_router)
router.include_router(triggers.public_router)
