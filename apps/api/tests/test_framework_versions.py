"""scripts/framework_versions.py: the pin report knows every package's changelog, and `bump` rewrites a pin in every
form it is written, without touching another package or the history."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

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
        "codegen.py": 'CREWAI_VERSION = "1.15.33"\n',  # the constant, in a file that does not name the package
        "Dockerfile.api": "RUN uv pip install crewai==1.15.33 jinja2==3.1.6\n",  # no suffix: still a pin
        "requirements.txt": "crewai==1.15.33\n",
        "unrelated.py": 'LITELLM_VERSION = "1.15.33"\n',  # same number, another package: left alone
        "manifest.json": '{"framework": "crewai 1.15.33"}\n',
        "ci.yml": 'with: "crewai[tools]==1.15.33 litellm==1.105.7"\n',
        "guide.md": "| CrewAI | `crewai==1.15.33` | a Crew |\n",
        "CHANGELOG.md": "- CrewAI 1.15.33 (crewai==1.15.33)\n",  # history stays as it was
    }
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    changed = {p.name for p in fv.bump("crewai", "1.15.33", "1.15.39", root=tmp_path)}
    assert changed == {"designs.py", "codegen.py", "Dockerfile.api", "requirements.txt", "manifest.json", "ci.yml",
                       "guide.md"}  # fmt: skip
    read = {name: (tmp_path / name).read_text() for name in files}
    assert "crewai==1.15.39" in read["designs.py"] and "agno==1.15.33" in read["designs.py"]
    assert 'CREWAI_VERSION = "1.15.39"' in read["codegen.py"]
    assert "crewai==1.15.39 jinja2==3.1.6" in read["Dockerfile.api"] and read["requirements.txt"] == "crewai==1.15.39\n"
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


ENTRY = re.compile(r"^([A-Za-z0-9_.-]+)==(\S+)")


def _locked(text: str) -> dict[str, str]:
    return {m.group(1).lower().replace("_", "-"): m.group(2) for m in map(ENTRY.match, text.splitlines()) if m}


@pytest.mark.parametrize("fid", sorted(fv.ENVIRONMENTS))
def test_each_framework_environment_has_a_hashed_lock_of_its_pins(fid):
    """The image and `make framework` install a framework environment from its lock: every transitive package pinned
    and hashed. With only the top-level pins, two builds a week apart shipped different trees, and a hijacked
    transitive release went into the next image unreviewed."""
    path = fv.LOCKS / f"{fid}.txt"
    assert path.is_file(), f"no lock for {fid}: uv run python scripts/framework_versions.py lock {fid}"
    text = path.read_text()
    locked = _locked(text)
    wanted = fv.pins(" ".join(fv.environment_requirements(fid)))
    stale = {name: (version, locked.get(name)) for name, version in wanted.items() if locked.get(name) != version}
    assert not stale, f"{path.name} is stale (pin, locked): {stale}; run framework_versions.py lock {fid}"
    entries = [i for i, line in enumerate(text.splitlines()) if ENTRY.match(line)]
    lines = text.splitlines()
    assert all(i + 1 < len(lines) and "--hash=sha256:" in lines[i + 1] for i in entries), "every entry needs hashes"


def test_the_image_and_the_makefile_install_framework_environments_only_from_their_locks():
    dockerfile = re.sub(r"\s*\\\n\s*", " ", (fv.ROOT / "infra" / "Dockerfile.api").read_text())  # one line per RUN
    makefile = (fv.ROOT / "Makefile").read_text()
    assert "uv pip install" not in dockerfile and "uv pip install" not in makefile
    for fid in fv.ENVIRONMENTS:
        sync = f"uv pip sync --require-hashes --python .frameworks/{fid}/bin/python framework-locks/{fid}.txt"
        assert sync in dockerfile, f"Dockerfile.api must install {fid} with: {sync}"
    assert "framework-locks/$(FW).txt" in makefile and "framework-locks/crewai.txt" in makefile
    assert "--python 3.13 .frameworks/crewai" in dockerfile and fv.ENVIRONMENTS["crewai"] == "3.13"


def test_bump_leaves_the_locks_alone(tmp_path):
    """A lock's hashes belong to its versions: `bump` must not rewrite a pin in it (`lock` regenerates it)."""
    locks = tmp_path / "framework-locks"
    locks.mkdir()
    (locks / "crewai.txt").write_text("crewai==1.15.33 \\\n    --hash=sha256:00\n")
    assert fv.bump("crewai", "1.15.33", "1.15.39", root=tmp_path) == []
