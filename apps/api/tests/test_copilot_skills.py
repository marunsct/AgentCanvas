"""The Copilot's per-framework "build for" skills: one for every framework the product exports to, with facts filled
in from the adapter manifests so a skill cannot say something the validator does not."""

from __future__ import annotations

import re

import yaml

from agentcanvas import designs
from agentcanvas.copilot import skillbook
from agentcanvas.copilot.tools import ToolContext, run_tool
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import adapters as manifests
from agentcanvas.registry import default_registry
from agentcanvas.validation import validate


def test_every_framework_has_a_build_skill():
    covered = {f for family in skillbook.FAMILIES.values() for f in family}
    targets = {*manifests.all_manifests(), *designs.AGENT_FRAMEWORKS} - skillbook.NOT_TARGETS
    assert targets <= covered, f"no build-for skill for: {sorted(targets - covered)}"  # a new adapter needs one
    assert covered <= {*manifests.all_manifests(), *designs.AGENT_FRAMEWORKS}  # and a removed one drops its skill
    assert set(skillbook.FAMILIES) <= set(skillbook.skills())  # each family has its SKILL.md
    assert {s for s in skillbook.skills() if s.startswith("build-for-")} == set(skillbook.FAMILIES)  # none stray


def test_skill_files_have_the_front_matter_the_harness_reads():
    for name, text in skillbook.skills().items():
        meta = yaml.safe_load(re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S).group(1))  # type: ignore[union-attr]
        assert meta["name"] == name and 0 < len(meta["description"]) <= 1024, name
        assert skillbook.SUPPORT not in text  # the marker is always replaced


def test_a_build_skill_states_what_the_manifest_says():
    registry = default_registry()
    for skill, family in skillbook.FAMILIES.items():
        text = skillbook.skills()[skill]
        for framework in family:
            m = manifests.all_manifests().get(framework)
            if m is None:
                continue
            assert m.label in text
            for block in registry.all():
                if m.support(block.type) == "emulated":  # one line per emulated block, with the manifest's note
                    assert f"`{block.type}`): {m.notes.get(block.type, 'it behaves differently')}" in text
                if m.support(block.type) == "unsupported":
                    assert f"`{block.type}`" in next(ln for ln in text.splitlines() if "Refused" in ln)
    crew = skillbook.skills()["build-for-crewai"]
    assert "does not apply node policies" in crew and "editorial_crew" in crew  # P007; the template made for it
    openai = skillbook.skills()["build-for-openai-agents"]
    assert "P005" in openai and "`engine: openai_agents`" in openai


def test_a_changed_manifest_changes_the_skill(monkeypatch):
    before = skillbook.support("mastra")
    known = manifests.all_manifests()
    changed = known["mastra"].model_copy(update={"nodes": {"*": "native"}})
    monkeypatch.setattr(manifests, "all_manifests", lambda: {**known, "mastra": changed})
    after = skillbook.support("mastra")
    assert after != before and "Refused (P001 error; remove or replace them first): none" in after


def test_the_deep_agents_files_hold_every_skill_with_its_facts():
    from agentcanvas.adapters.langchain.copilot import DeepAgentTurn

    turn = DeepAgentTurn.__new__(DeepAgentTurn)
    turn.before = Workflow(id="wf_x", name="x")
    files = turn._files()
    for name, text in skillbook.skills().items():
        assert "\n".join(files[f"/skills/{name}/SKILL.md"]["content"]) == text.rstrip("\n")


async def test_read_skill_tool_serves_the_loop_engine():
    async def check(wf: Workflow):
        return validate(wf, default_registry())

    ctx = ToolContext(Workflow(id="wf_x", name="x"), default_registry(), {}, check)
    out = await run_tool(ctx, "read_skill", {"name": "build-for-google-adk"}, ["read_skill"])
    assert out["ok"] and "Google ADK" in out["skill"] and "ADK 2 graph workflow" in out["skill"]
    missing = await run_tool(ctx, "read_skill", {"name": "build-for-cobol"}, ["read_skill"])
    assert not missing["ok"] and "build-for-crewai" in missing["error"]
