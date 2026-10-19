"""Framework versions are written in several places; they must not drift apart (see HARDENING.md).

`designs.AGENT_FRAMEWORKS` is the source for what an export installs; the others are checked against it.
"""

import json
import re
from pathlib import Path

import pytest

from agentcanvas.adapters.crewai.codegen import CREWAI_VERSION
from agentcanvas.adapters.langchain.interop import FRAMEWORKS as INTEROP
from agentcanvas.designs import AGENT_FRAMEWORKS

ROOT = Path(__file__).parents[3]
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
