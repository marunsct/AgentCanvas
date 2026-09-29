"""Versioned public API (/v1)."""

from fastapi import APIRouter

from agentcanvas.api.v1 import catalog, projects, workflows

router = APIRouter(prefix="/v1")
router.include_router(catalog.router)
router.include_router(projects.router)
router.include_router(workflows.router)
