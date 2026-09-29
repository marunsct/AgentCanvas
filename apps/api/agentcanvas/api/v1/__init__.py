"""Versioned public API (/v1)."""

from fastapi import APIRouter

from agentcanvas.api.v1 import (
    catalog,
    deployments,
    events,
    memory,
    projects,
    runs,
    secrets,
    tools,
    triggers,
    versions,
    workflows,
    workspace,
)

router = APIRouter(prefix="/v1")
router.include_router(catalog.router)
router.include_router(projects.router)
router.include_router(workflows.router)
router.include_router(runs.router)
router.include_router(tools.router)
router.include_router(memory.router)
router.include_router(workspace.router)
router.include_router(secrets.router)
router.include_router(events.router)
router.include_router(versions.router)
router.include_router(deployments.router)
router.include_router(triggers.router)
