"""The Mastra adapter (S36): workflows designed for Mastra, run and exported as TypeScript Mastra flows.

`codegen` writes the workflow as a TypeScript module with Mastra `Agent`s for the AI blocks and a `FLOW` that the
flow engine runs as the canvas draws it (`flow.ts`, the TypeScript twin of `supersteps`); `runtime.ts` adds the Mastra
parts (models, tools, tool approvals that suspend the agent's run, typed answers, call limits). Mastra is a TypeScript
framework, so the flows run in a Node.js worker (`worker.ts`) started from the Mastra environment
(`AGENTCANVAS_MASTRA_DIR`, or `.frameworks/mastra` next to the API: `make mastra`) and driven exactly like the Python
frameworks' workers (see `flowadapter`). Without one, Mastra workflows can still be designed, checked and exported;
runs say how to install it.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agentcanvas.adapters.crewai import API_ROOT, CrewWorker
from agentcanvas.adapters.flowadapter import FlowAdapter
from agentcanvas.adapters.mastra import codegen
from agentcanvas.ir.models import Workflow
from agentcanvas.registry.adapters import all_manifests

ENV_DIR = API_ROOT.parent / ".frameworks" / "mastra"
WORKER_FILES = ["adapters/mastra/flow.ts", "adapters/mastra/runtime.ts", "adapters/mastra/worker.ts"]
INSTALL_HINT = (
    f"Mastra is not installed on this server: run `make mastra` (Node.js {codegen.NODE_VERSION} or later and "
    f"@mastra/core@{codegen.MASTRA_VERSION} in apps/api/.frameworks/mastra) or set AGENTCANVAS_MASTRA_DIR, then "
    "restart the server"
)


def node() -> str | None:
    """A Node.js that runs TypeScript as it is (22.18 or later): AGENTCANVAS_NODE, else `node` on the PATH."""
    found = os.environ.get("AGENTCANVAS_NODE") or shutil.which("node")
    return found if found and _recent(found) else None


@functools.lru_cache(maxsize=8)
def _recent(found: str) -> bool:
    try:
        out = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    m = re.match(r"v(\d+)\.(\d+)", out.strip())
    wanted = tuple(int(x) for x in codegen.NODE_VERSION.split("."))
    return bool(m) and (int(m[1]), int(m[2])) >= wanted


def mastra_dir() -> str | None:
    """The folder whose `node_modules` has Mastra (None: not installed here, or no recent enough Node.js)."""
    found = Path(os.environ.get("AGENTCANVAS_MASTRA_DIR") or ENV_DIR)
    if not (found / "node_modules" / "@mastra" / "core" / "package.json").is_file():
        return None
    return str(found) if node() else None


class MastraWorker(CrewWorker):
    """The Node.js worker: the engine, the Mastra runtime and the worker next to the environment's `node_modules`."""

    label = "Mastra"
    package = "mastra"
    files = WORKER_FILES

    def prepare(self) -> tuple[list[str], dict[str, str]]:
        here = Path(self._dir)
        for rel in self.files:
            shutil.copyfile(API_ROOT / rel, here / Path(rel).name)
        (here / "package.json").write_text('{"type": "module"}\n')
        (here / "node_modules").symlink_to(Path(self.python) / "node_modules", target_is_directory=True)
        env = {**os.environ, "NODE_NO_WARNINGS": "1", "MASTRA_TELEMETRY_DISABLED": "true"}
        return [node() or "node", str(here / "worker.ts")], env


class MastraAdapter(FlowAdapter):
    id = "mastra"
    manifest = all_manifests()["mastra"]
    install_hint = INSTALL_HINT
    worker_class = MastraWorker
    generate_source = staticmethod(codegen.generate)

    def python(self) -> str | None:  # the Mastra environment's folder (the worker runs on Node.js)
        return mastra_dir()

    def package(
        self,
        wf: Workflow,
        registry: Any = None,
        *,
        workspace: Any = None,
        test_cases: list[dict[str, Any]] | None = None,
        version: str = "0.0.0",
        target: str = "local",
    ) -> tuple[str, bytes]:
        """A TypeScript project of its own (see `packager`)."""
        from agentcanvas.adapters.mastra.packager import package

        return package(wf, self.generate(wf, registry), workspace=workspace, test_cases=test_cases, version=version)
