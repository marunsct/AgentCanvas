"""Backwards-compatible entry point: `uvicorn agentcanvas.api.app:app` (prefer `agentcanvas.api.main:app`)."""

from agentcanvas.api.legacy import S
from agentcanvas.api.main import app

__all__ = ["S", "app"]
