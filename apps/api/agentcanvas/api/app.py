"""Backwards-compatible entry point: `uvicorn agentcanvas.api.app:app` (prefer `agentcanvas.api.main:app`)."""

from agentcanvas.api.main import app

__all__ = ["app"]
