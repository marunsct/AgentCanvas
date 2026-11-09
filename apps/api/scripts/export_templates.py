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
import tempfile
import zipfile
from pathlib import Path

from agentcanvas import knowledge_search as ks
from agentcanvas.adapters import get_adapter
from agentcanvas.adapters.langchain.deploykit import TARGETS
from agentcanvas.errors import WorkflowHasErrors
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import default_registry
from agentcanvas.services import knowledge, template_kit

TEMPLATES = Path(__file__).resolve().parents[1] / "agentcanvas/registry/templates"
SAMPLES = Path(__file__).resolve().parents[1] / "agentcanvas/registry/knowledge"


def with_knowledge(wf: Workflow, data: bytes) -> bytes:
    """What the API's export does for a workflow that searches a knowledge base: ship the base next to the code. No
    database here, so the sample handbook (one chunk per page) stands in for every base a template names."""
    used = knowledge.used_knowledge_bases(wf.model_dump(mode="json"))
    if not used:
        return data
    chunks = []
    for page in sorted((SAMPLES / "handbook").glob("*.md")):
        text = page.read_text(encoding="utf-8")
        chunks.append({"id": page.stem, "doc_id": page.stem, "title": page.stem.replace("-", " "), "source": page.name,
                       "text": text, "page": None, "heading": None})  # fmt: skip
    return knowledge.bundle(data, {name: ks.dump_jsonl(name, {}, chunks) for name in used})


def kit_workspace(wf: Workflow, root: Path):
    """A template's own workspace code and the registry that knows its blocks (services/template_kit.kit_registry)."""
    return template_kit.kit_registry(wf, default_registry(), root)


def main(out: Path) -> None:
    files = sorted(TEMPLATES.glob("*.ir.json"))
    for f in files:
        wf = Workflow.model_validate(json.loads(f.read_text()))
        with tempfile.TemporaryDirectory() as tmp:
            registry, workspace = kit_workspace(wf, Path(tmp))
            for target in TARGETS:
                _, data = get_adapter(wf.framework).package(
                    wf, registry, workspace=workspace, target=target, version="1.0.0"
                )
                zipfile.ZipFile(io.BytesIO(with_knowledge(wf, data))).extractall(
                    out / target / f.name.removesuffix(".ir.json")
                )
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
            zipfile.ZipFile(io.BytesIO(with_knowledge(wf, data))).extractall(
                out / framework / f.name.removesuffix(".ir.json")
            )
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
