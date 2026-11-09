"""Framework versions are written in several places; they must not drift apart (see HARDENING.md).

`designs.AGENT_FRAMEWORKS` is the source for what an export installs; the others are checked against it.
"""

import json
import re
from pathlib import Path

import pytest

from agentcanvas import bridges
from agentcanvas.adapters import flowpackager
from agentcanvas.adapters.agno.codegen import AGNO_VERSION
from agentcanvas.adapters.agno.codegen import OPENAI_VERSION as AGNO_OPENAI_VERSION
from agentcanvas.adapters.crewai.codegen import CREWAI_VERSION
from agentcanvas.adapters.google_adk.codegen import ADK_VERSION, LITELLM_VERSION
from agentcanvas.adapters.langchain.interop import FRAMEWORKS as INTEROP
from agentcanvas.adapters.langchain.packager import package_name
from agentcanvas.adapters.llamaindex.codegen import LLAMA_INDEX_CORE_VERSION, OPENAI_LIKE_VERSION, WORKFLOWS_VERSION
from agentcanvas.adapters.mastra.codegen import MASTRA_VERSION
from agentcanvas.adapters.microsoft_agent_framework.codegen import (
    AGENT_FRAMEWORK_OPENAI_VERSION,
    AGENT_FRAMEWORK_VERSION,
)
from agentcanvas.adapters.openai_agents.codegen import SDK_VERSION
from agentcanvas.adapters.pydantic_ai.codegen import PYDANTIC_AI_VERSION
from agentcanvas.adapters.strands.codegen import STRANDS_VERSION
from agentcanvas.designs import AGENT_FRAMEWORKS

ROOT = Path(__file__).parents[3]
MASTRA_PACKAGE = ROOT / "apps/api/agentcanvas/adapters/mastra/package.json"
PIN = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([\w.]+)$")


def pins(spec: str) -> dict[str, str]:
    out = {}
    for token in spec.split():
        m = PIN.match(token)
        if m:
            out[m.group(1).lower().replace("_", "-")] = m.group(2)
    return out


def ci_matrix() -> dict[str, dict[str, str]]:
    text = (ROOT / ".github/workflows/ci.yml").read_text()
    rows = re.findall(r'\{ check: (\w+), with: "([^"]*)" \}', text)
    return {check: pins(spec) for check, spec in rows}


@pytest.mark.parametrize("fid", sorted(AGENT_FRAMEWORKS))
def test_ci_matrix_installs_what_exports_install(fid):
    wanted = {k: v for p in AGENT_FRAMEWORKS[fid].packages for k, v in pins(p).items()}
    have = ci_matrix()[fid]
    assert {k: have.get(k) for k in wanted} == wanted


@pytest.mark.parametrize("fid", sorted(set(INTEROP) & set(ci_matrix())))
def test_interop_versions_match_ci_matrix(fid):
    wanted = pins(INTEROP[fid].package)
    have = ci_matrix()[fid]
    assert {k: have.get(k) for k in wanted} == wanted


def test_crewai_version_is_one_number():
    assert CREWAI_VERSION == pins(AGENT_FRAMEWORKS["crewai"].packages[0])["crewai"]
    manifest = json.loads((ROOT / "apps/api/agentcanvas/registry/adapters/crewai.json").read_text())
    assert manifest["framework"] == f"crewai {CREWAI_VERSION}"


def canonical() -> dict[str, str]:
    """Every pinned package and its one version: what exports install (designs, and the Agent Spec bridges)."""
    out: dict[str, str] = {}
    for spec in [*(p for f in AGENT_FRAMEWORKS.values() for p in f.packages),
                 *(p for t in bridges.TARGETS.values() for p in t.packages)]:  # fmt: skip
        for name, version in pins(spec).items():
            assert out.setdefault(name, version) == version, f"{name} is pinned to {out[name]} and {version}"
    return out


@pytest.mark.parametrize("fid", sorted(AGENT_FRAMEWORKS))
def test_exports_install_what_ci_tests(fid):
    """The other way round: a package CI pins for a framework (llama-index-workflows) is pinned in exports too."""
    wanted = ci_matrix()[fid]
    have = {k: v for p in AGENT_FRAMEWORKS[fid].packages for k, v in pins(p).items()}
    assert {k: have.get(k) for k in wanted} == wanted


@pytest.mark.parametrize("target", sorted(bridges.TARGETS))
def test_bridge_exports_install_what_ci_tests(target):
    wanted = {k: v for p in bridges.TARGETS[target].packages for k, v in pins(p).items()}
    assert ci_matrix()[f"bridge_{target}"] == wanted


@pytest.mark.parametrize(
    ("constant", "package"),
    [
        (SDK_VERSION, "openai-agents"), (CREWAI_VERSION, "crewai"), (ADK_VERSION, "google-adk"),
        (LITELLM_VERSION, "litellm"), (PYDANTIC_AI_VERSION, "pydantic-ai-slim"), (AGNO_VERSION, "agno"),
        (AGNO_OPENAI_VERSION, "openai"), (STRANDS_VERSION, "strands-agents"),
        (LLAMA_INDEX_CORE_VERSION, "llama-index-core"), (WORKFLOWS_VERSION, "llama-index-workflows"),
        (OPENAI_LIKE_VERSION, "llama-index-llms-openai-like"), (AGENT_FRAMEWORK_VERSION, "agent-framework-core"),
        (AGENT_FRAMEWORK_OPENAI_VERSION, "agent-framework-openai"),
    ],
)  # fmt: skip
def test_each_code_generators_version_is_the_exports(constant, package):
    assert constant == canonical()[package]


@pytest.mark.parametrize("fid", sorted(flowpackager.TARGETS))
def test_flow_exports_pin_what_agent_exports_pin(fid):
    have = {k: v for p in flowpackager.TARGETS[fid].pins for k, v in pins(p).items()}
    assert have == {k: v for p in AGENT_FRAMEWORKS[fid].packages for k, v in pins(p).items()}


@pytest.mark.parametrize("manifest", sorted((ROOT / "apps/api/agentcanvas/registry/adapters").glob("*.json")))
def test_adapter_manifests_name_the_pinned_version(manifest):
    name, _, version = json.loads(manifest.read_text()).get("framework", "").partition(" ")
    if name == "@mastra/core":
        assert version == MASTRA_VERSION == json.loads(MASTRA_PACKAGE.read_text())["dependencies"][name]
    elif name in canonical():
        assert version == canonical()[name]


WRITTEN = [
    ".github/workflows/ci.yml", "scripts/ci-local.sh", "Makefile", "infra/Dockerfile.api", "apps/api/pyproject.toml",
    "docs/user-guide/agents-and-connectors.md",
    "apps/api/agentcanvas/copilot/knowledge/user-guide/agents-and-connectors.md",
    "apps/api/agentcanvas/adapters/langchain/interop.py",
]  # fmt: skip


@pytest.mark.parametrize("path", WRITTEN)
def test_pins_written_by_hand_match(path):
    """Pins typed into CI, the Makefile, the image, the server's own dependencies and the docs are the exports'."""
    found = re.findall(r"([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([0-9][\w.]*)", (ROOT / path).read_text())
    wrong = sorted({(n.lower(), v) for n, v in found if canonical().get(n.lower(), v) != v})
    assert not wrong, f"{path}: {wrong} (exports pin {[canonical()[n] for n, _ in wrong]})"


def test_an_exports_package_never_hides_a_framework():
    """`CrewAI` became the package `crewai` on a server without CrewAI and hid the framework in the export."""
    assert {package_name(n) for n in ("CrewAI", "Agents", "Strands", "Workflows")} == {
        "wf_crewai", "wf_agents", "wf_strands", "wf_workflows",
    }  # fmt: skip


def test_the_mastra_lockfile_matches_its_package_json():
    """`npm ci` (make mastra, the Docker image) installs what the lockfile says; a version changed in package.json
    without `npm install --package-lock-only` would otherwise be ignored by it, or fail only in the image build."""
    import json
    from pathlib import Path

    folder = Path(__file__).parents[1] / "agentcanvas" / "adapters" / "mastra"
    manifest = json.loads((folder / "package.json").read_text())
    root = json.loads((folder / "package-lock.json").read_text())["packages"][""]
    assert root["dependencies"] == manifest["dependencies"]
    for name, version in manifest["dependencies"].items():
        assert version[0].isdigit(), f"{name} must be pinned exactly, not '{version}'"


def test_every_project_we_write_and_the_server_itself_name_an_upper_python_version():
    """`requires-python = ">=3.12"` alone lets `uv` pick the newest Python on a machine. When 3.15 arrived, an export
    would have been built on it and failed (a dependency has no 3.15 wheel and needs a C++ toolchain). Every pyproject
    we write names the newest tested Python as its ceiling, so the install picks one we know works."""
    api = Path(__file__).parents[1]
    sources = [api / "pyproject.toml", *(api / "agentcanvas").rglob("*.py")]
    bad = []
    for path in sources:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "requires-python" in line and "=" in line and '">=' in line and "<" not in line:
                bad.append(f"{path.relative_to(api)}: {line.strip()}")
    assert not bad, "\n".join(bad)
