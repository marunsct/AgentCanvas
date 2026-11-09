"""The running build: the package version (one source, `pyproject.toml`) and the git commit the image was built from.

Support and operators need to know exactly what runs: two installs both saying "0.32" can differ by many migrations.
The commit comes from `AGENTCANVAS_GIT_SHA`, which the API image sets from its `GIT_SHA` build argument; a build
without it (a source checkout, an image built without the argument) says None.
"""

from __future__ import annotations

import os
from importlib import metadata


def _version() -> str:
    try:
        return metadata.version("agentcanvas")
    except metadata.PackageNotFoundError:  # running from a copy of the sources that was never installed
        return "0+unknown"


VERSION = _version()
COMMIT: str | None = os.getenv("AGENTCANVAS_GIT_SHA", "").strip() or None
COMMIT_LABEL = COMMIT or "unknown"  # the agentcanvas_build_info label (a label has no None)
