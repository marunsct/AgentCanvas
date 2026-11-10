"""AgentCanvas's license: one text, carried by the package and by every export that contains AgentCanvas code."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from agentcanvas import licensing

ROOT = Path(__file__).resolve().parents[3]


def test_the_package_carries_the_repositorys_license():
    assert licensing.text() == (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert licensing.text().startswith("Business Source License 1.1")


def _names(data: bytes) -> list[str]:
    return zipfile.ZipFile(io.BytesIO(data)).namelist()


@pytest.mark.parametrize("framework", ["langchain", "crewai", "google_adk", "pydantic_ai", "openai_agents", "mastra"])
def test_every_framework_export_carries_the_license(framework):
    """Exports copy AgentCanvas's runtime modules and generated code: the License must be displayed on each copy."""
    from test_bridges import template

    from agentcanvas.adapters import get_adapter
    from agentcanvas.registry import default_registry

    wf = template("classify_and_route").model_copy(update={"framework": framework})
    _, data = get_adapter(framework).package(wf, default_registry(), target="local", version="1.0.0")
    names = _names(data)
    assert any(n.endswith("/" + licensing.EXPORT_NAME) for n in names), names[:20]
    assert not any("Apache-2.0" in zipfile.ZipFile(io.BytesIO(data)).read(n).decode(errors="ignore")
                   for n in names if n.endswith("NOTICE.md"))  # fmt: skip
