"""scripts/framework_versions.py: the pin report knows every package's changelog, and `bump` rewrites a pin in every
form it is written, without touching another package or the history."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "framework_versions.py"
spec = importlib.util.spec_from_file_location("framework_versions", SCRIPT)
assert spec and spec.loader
fv = importlib.util.module_from_spec(spec)
sys.modules["framework_versions"] = fv  # dataclasses look their module up there
spec.loader.exec_module(fv)


def test_every_pinned_package_names_its_changelog():
    missing = sorted(set(fv.pinned()) - set(fv.CHANGELOGS))
    assert not missing, f"add the official changelog of {missing} to CHANGELOGS"


def test_bump_rewrites_every_form_and_nothing_else(tmp_path):
    files = {
        "designs.py": 'AgentFramework("crewai", "CrewAI", ("crewai==1.15.33",), "crewai")\nOTHER = "agno==1.15.33"\n',
        "codegen.py": 'import crewai\nCREWAI_VERSION = "1.15.33"\n',
        "unrelated.py": 'LITELLM_VERSION = "1.15.33"\n',  # same number, another package: left alone
        "manifest.json": '{"framework": "crewai 1.15.33"}\n',
        "ci.yml": 'with: "crewai[tools]==1.15.33 litellm==1.105.7"\n',
        "guide.md": "| CrewAI | `crewai==1.15.33` | a Crew |\n",
        "CHANGELOG.md": "- CrewAI 1.15.33 (crewai==1.15.33)\n",  # history stays as it was
    }
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    changed = {p.name for p in fv.bump("crewai", "1.15.33", "1.15.39", root=tmp_path)}
    assert changed == {"designs.py", "codegen.py", "manifest.json", "ci.yml", "guide.md"}
    read = {name: (tmp_path / name).read_text() for name in files}
    assert "crewai==1.15.39" in read["designs.py"] and "agno==1.15.33" in read["designs.py"]
    assert 'CREWAI_VERSION = "1.15.39"' in read["codegen.py"]
    assert read["unrelated.py"] == files["unrelated.py"]
    assert '"crewai 1.15.39"' in read["manifest.json"]
    assert "crewai[tools]==1.15.39 litellm==1.105.7" in read["ci.yml"]
    assert "`crewai==1.15.39`" in read["guide.md"]
    assert read["CHANGELOG.md"] == files["CHANGELOG.md"]


def test_versions_compare_as_numbers_and_pre_releases_are_not_offered():
    assert fv._key("1.15.10") > fv._key("1.15.9")
    assert not fv._stable("0.24.0a6") and not fv._stable("2.0.0rc1") and fv._stable("2.14.0")


def test_the_file_dependabot_watches_lists_every_pin():
    """Dependabot cannot read pins in our code; it watches this file. Out of step means someone edited one side."""
    assert fv.REQUIREMENTS.read_text() == fv.requirements_text(), (
        "infra/frameworks/requirements.txt differs from the pins: to take a Dependabot update run "
        "`scripts/framework_versions.py bump <package> <version>`; otherwise `framework_versions.py requirements`"
    )
