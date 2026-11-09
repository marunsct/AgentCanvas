"""G-5: file fields in exports. A LangGraph export carries them; the other targets say they cannot."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import zipfile

from test_multimodal import PNG, SEES, make_pdf, wf_with_files

from agentcanvas.adapters.langchain.codegen import generate
from agentcanvas.adapters.langchain.packager import canvas_only, gaps_md, package
from agentcanvas.validation import validate

SCRIPT = """
import asyncio, base64, json, sys
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from agentcanvas.adapters.langchain.nodes import Resources
from agentcanvas.adapters.langchain.testing import ToolFakeModel
from pydantic import Field
from looks.graph import build_graph

class Rec(ToolFakeModel):
    seen: list = Field(default_factory=list)
    def _generate(self, messages, stop=None, run_manager=None, **kw):
        self.seen.append(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

model = Rec(messages=iter([]), disable_streaming=True)
graph = build_graph(Resources(overrides={"vis": model}), checkpointer=InMemorySaver())
png = base64.b64encode(open("photo.png", "rb").read()).decode()
pdf = base64.b64encode(open("doc.pdf", "rb").read()).decode()
state = {
    "question": "what is this?",
    "photo": {"kind": "file", "name": "photo.png", "media_type": "image/png", "data": png},
    "contract": {"kind": "file", "name": "doc.pdf", "media_type": "application/pdf", "data": pdf},
}
asyncio.run(graph.ainvoke(state, {"configurable": {"thread_id": "t"}}))
print("blocks", [b["type"] for b in model.seen[0][0].content])
"""


def test_a_langgraph_export_carries_file_fields_and_what_reads_them(tmp_path):
    wf = wf_with_files(SEES).model_copy(update={"name": "looks"})
    code = generate(wf)
    assert 'files=["photo", "contract"]' in code and "read_file=resources.files" in code
    _, data = package(wf, code)
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    assert "looks/src/agentcanvas/filecontent.py" in names
    assert "looks/src/agentcanvas/registry/models.json" in names  # which models see
    pyproject = z.read("looks/pyproject.toml").decode()
    assert '"pypdf' in pyproject and '"python-docx' in pyproject
    readme = z.read("looks/README.md").decode()
    assert "## Files and images" in readme and "`photo`, `contract`" in readme and '"data": "<base64>"' in readme

    z.extractall(tmp_path)
    root = tmp_path / "looks"
    (root / "photo.png").write_bytes(PNG)
    (root / "doc.pdf").write_bytes(make_pdf("Clause 7"))
    (root / "run_files.py").write_text(SCRIPT)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTCANVAS_", "PYTEST"))}
    env["PYTHONPATH"] = os.pathsep.join([str(root / "src"), str(root)])
    proc = subprocess.run(
        [sys.executable, "run_files.py"], cwd=root, env=env, capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    assert proc.returncode == 0 and "blocks ['text', 'image', 'file']" in proc.stdout, proc.stdout + proc.stderr[-3000:]


def test_an_export_of_a_workflow_without_files_adds_nothing_for_them():
    wf = wf_with_files(SEES)
    wf.state.channels = [c for c in wf.state.channels if c.name not in ("photo", "contract")]
    code = generate(wf)
    assert "files=" not in code and "read_file" not in code
    _, data = package(wf, code)
    z = zipfile.ZipFile(io.BytesIO(data))
    assert b"pypdf" not in z.read(f"{z.namelist()[0].split('/')[0]}/pyproject.toml")
    assert "Files and images" not in z.read(f"{z.namelist()[0].split('/')[0]}/README.md").decode()


def test_the_other_targets_say_they_cannot_carry_files():
    wf = wf_with_files(SEES)
    [line] = canvas_only(wf, "a CrewAI flow")
    assert "photo, contract" in line and "only a LangGraph export shows them to a model" in line
    assert "never sees it" in gaps_md(wf, "a Mastra flow")
    plain = wf_with_files(SEES)
    plain.state.channels = [c for c in plain.state.channels if c.name not in ("photo", "contract")]
    assert canvas_only(plain, "a CrewAI flow") == []


def test_a_workflow_on_another_framework_is_warned_that_models_will_not_see_its_files():
    wf = wf_with_files(SEES)
    assert [d for d in validate(wf) if d.code == "P008"] == []  # LangChain shows them
    wf.framework = "crewai"
    [warn] = [d for d in validate(wf) if d.code == "P008"]
    assert warn.severity == "warning" and "photo, contract" in warn.message and "plain data" in warn.message
