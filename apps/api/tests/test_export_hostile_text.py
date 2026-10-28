"""Free text from a workflow (its name, description, step labels) ends up in the files of every export: code, TOML,
YAML, JSON, Dockerfiles, shell scripts and Helm charts. Whatever it contains, an export must stay well-formed and the
text must stay text: it never becomes a key, an instruction, a command or a path of its own.

Each hostile string carries the marker `PWNED`. After exporting to every deploy target and every framework the check is:
every file still parses with the parser of its type, the marker is never a key in structured data, never the start of
a line outside Markdown, and never leaves the project folder.
"""

from __future__ import annotations

import ast
import io
import json
import subprocess
import tomllib
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentcanvas.adapters import get_adapter
from agentcanvas.adapters.langchain.deploykit import TARGETS
from agentcanvas.errors import WorkflowHasErrors
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import default_registry

TEMPLATES = Path(__file__).resolve().parents[1] / "agentcanvas/registry/templates"
MARK = "PWNED"
HOSTILE = {
    "double quote + newline": 'a"\nPWNED: 1\n"b',
    "single quote + newline": "a'\nPWNED: 1\n'b",
    "crlf command": "a\r\nPWNED --x\r\n",
    "shell expansion": "$(PWNED)`PWNED`${PWNED}; PWNED",
    "template syntax": "{{ PWNED }}{% PWNED %}{{ .Values.PWNED }}",
    "yaml flow": "a: b # PWNED\n- PWNED: [{",
    "path traversal": "../../PWNED/..\\..\\PWNED",
    "unicode line breaks": "a\u2028PWNED: 1\u2029PWNED\x85PWNED",
    "quotes and backslashes": '""" \'\'\' \\ \\n \\" PWNED',
    "nul": "a\x00PWNED",
    "emoji and rtl": "😀 ünï日本 ‮PWNED",
    "very long": "PWNED" * 80,
}
FRAMEWORKS = (
    "openai_agents", "crewai", "google_adk", "pydantic_ai", "agno", "microsoft_agent_framework", "strands",
    "llamaindex", "mastra",
)  # fmt: skip


def _hostile(template: str, text: str, framework: str | None = None) -> Workflow:
    ir = json.loads((TEMPLATES / f"{template}.ir.json").read_text())
    ir["name"], ir["description"] = f"Flow {text}", f"About {text}"
    for node in ir["nodes"]:
        node["label"] = text
    if framework:
        ir["framework"] = framework
    return Workflow.model_validate(ir)


def _keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        return {str(k) for k in node} | {k for v in node.values() for k in _keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in _keys(v)}
    return set()


def _check(files: dict[str, bytes], where: str, tmp: Path) -> list[str]:
    bad = []
    for name, raw in files.items():
        if name.startswith("/") or ".." in Path(name).parts:
            bad.append(f"{where}: {name!r} leaves the project folder")
        if name.endswith(".md") or (b"\x00" in raw and name.endswith((".png", ".ico", ".db", ".zip"))):
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        base = Path(name).name
        in_chart_templates = "/templates/" in name and "chart" in name  # Helm templates are not YAML until rendered
        try:
            keys: set[str] = set()
            if name.endswith(".py"):
                ast.parse(text)
            elif name.endswith(".toml"):
                keys = _keys(tomllib.loads(text))
            elif name.endswith(".json"):
                keys = _keys(json.loads(text))
            elif name.endswith((".yaml", ".yml")) and not in_chart_templates:
                keys = _keys(list(yaml.safe_load_all(text)))
            elif name.endswith(".sh") or (base in ("deploy.sh", "Makefile") and name.endswith(".sh")):
                target = tmp / "script.sh"
                target.write_text(text)
                proc = subprocess.run(["bash", "-n", str(target)], capture_output=True, text=True, check=False)
                if proc.returncode:
                    bad.append(f"{where}: {name} is not valid shell: {proc.stderr.strip()[:120]}")
        except Exception as exc:  # noqa: BLE001 - the parse error is the finding
            bad.append(f"{where}: {name} does not parse: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        if any(MARK in k for k in keys):
            bad.append(f"{where}: {name} has a key made from free text: {sorted(k for k in keys if MARK in k)[:3]}")
        if not name.endswith(".lock") and any(line.strip().startswith(MARK) for line in text.split("\n")):
            bad.append(f"{where}: {name} has a line that starts with the free text")
    return bad


def _files(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist() if not n.endswith("/")}


@pytest.mark.parametrize("template", ["chat_assistant", "classify_and_route"])
def test_hostile_text_stays_text_in_every_deploy_target(template, tmp_path):
    bad: list[str] = []
    for label, text in HOSTILE.items():
        wf = _hostile(template, text)
        for target in TARGETS:
            try:
                _, data = get_adapter(wf.framework).package(wf, default_registry(), target=target, version="1.0.0")
            except WorkflowHasErrors:
                continue
            bad += _check(_files(data), f"{template}/{target}/{label}", tmp_path)
    assert not bad, f"{len(bad)} problems:\n" + "\n".join(sorted(set(bad))[:30])


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_hostile_text_stays_text_in_every_framework_export(framework, tmp_path):
    bad: list[str] = []
    exported = 0
    for label, text in HOSTILE.items():
        wf = _hostile("chat_assistant", text, framework)
        try:
            _, data = get_adapter(framework).package(wf, default_registry(), version="1.0.0")
        except WorkflowHasErrors:
            continue
        exported += 1
        bad += _check(_files(data), f"{framework}/{label}", tmp_path)
    assert exported, f"{framework} exported nothing"
    assert not bad, f"{len(bad)} problems:\n" + "\n".join(sorted(set(bad))[:30])


def test_toml_string_is_valid_toml_for_any_text():
    from agentcanvas.tomltext import toml_string

    for text in [
        "",
        "plain",
        "😀 emoji",
        'q"uote',
        "back\\slash",
        "line\nbreak",
        "\x00\x1f\x7f",
        "\u2028\x85",
        "\ud800 lone",
    ]:
        assert tomllib.loads(f"x = {toml_string(text)}")["x"] == text.encode("utf-8", "replace").decode()
