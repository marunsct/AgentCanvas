"""Whether a framework's runtime is set up on this server, and what to do when it is not.

Frameworks that clash with LangGraph's packages run in an environment of their own (`make crewai`, `make framework
FW=<id>`, `make mastra`); in the Docker app that environment is built into the image instead
(`make docker-up` includes every framework by default), so a `make` on the host does not reach the server.
"""

from __future__ import annotations

import os
from typing import Any


def in_docker() -> bool:
    """The API image sets AGENTCANVAS_IN_DOCKER=1 (infra/Dockerfile.api)."""
    return os.environ.get("AGENTCANVAS_IN_DOCKER") == "1"


def setup_hint(adapter: Any) -> str | None:
    """What to do so `adapter` runs here (None: nothing, it runs in the server's own process or is set up)."""
    python = getattr(adapter, "python", None)
    if python is None or python() is not None:
        return None
    if in_docker():
        return (
            f"{adapter.manifest.label} is not in this server's Docker image (a `make` on your computer does not reach "
            f"it): rebuild the image with `make docker-up` (every framework by default) "
            f"or `make docker-up FRAMEWORKS={adapter.id}`"
        )
    return str(adapter.install_hint)
