"""Export every starter template to every deploy target (S16 CI): `python scripts/export_templates.py <dir>`.

Writes <dir>/<target>/<template>/<project>/ so CI can lint the deploy files, run each export's own tests and
deploy some of them for real; and <dir>/<framework>/<template>/<project>/ for every template the OpenAI Agents SDK
(S25), CrewAI (S27), Google ADK (S29), Pydantic AI and Agno (S32), the Microsoft
Agent Framework (S33-S34), Strands and LlamaIndex (S35) and Mastra (S36, TypeScript projects) can run.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

from agentcanvas.adapters import get_adapter
from agentcanvas.adapters.langchain.deploykit import TARGETS
from agentcanvas.errors import WorkflowHasErrors
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import default_registry

TEMPLATES = Path(__file__).resolve().parents[1] / "agentcanvas/registry/templates"


def main(out: Path) -> None:
    files = sorted(TEMPLATES.glob("*.ir.json"))
    for f in files:
        wf = Workflow.model_validate(json.loads(f.read_text()))
        for target in TARGETS:
            _, data = get_adapter(wf.framework).package(wf, default_registry(), target=target, version="1.0.0")
            zipfile.ZipFile(io.BytesIO(data)).extractall(out / target / f.name.removesuffix(".ir.json"))
    counts = {}
    frameworks = (
        "openai_agents",
        "crewai",
        "google_adk",
        "pydantic_ai",
        "agno",
        "microsoft_agent_framework",
        "strands",
        "llamaindex",
        "mastra",
    )  # S25, S27, S29, S32, S33-S34, S35, S36
    for framework in frameworks:  # each template it can run, as a project
        adapter, counts[framework] = get_adapter(framework), 0
        for f in files:
            wf = Workflow.model_validate({**json.loads(f.read_text()), "framework": framework})
            try:
                _, data = adapter.package(wf, default_registry(), version="1.0.0")
            except WorkflowHasErrors:
                continue  # blocks this framework cannot run
            zipfile.ZipFile(io.BytesIO(data)).extractall(out / framework / f.name.removesuffix(".ir.json"))
            counts[framework] += 1
    print(
        f"exported {len(files)} templates to {len(TARGETS)} targets, {counts['openai_agents']} to the OpenAI Agents "
        f"SDK, {counts['crewai']} to CrewAI, {counts['google_adk']} to Google ADK, {counts['pydantic_ai']} to Pydantic "
        f"AI, {counts['agno']} to Agno, {counts['microsoft_agent_framework']} to the Microsoft Agent Framework, "
        f"{counts['strands']} to Strands, {counts['llamaindex']} to LlamaIndex and {counts['mastra']} to Mastra, "
        f"in {out}"
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
