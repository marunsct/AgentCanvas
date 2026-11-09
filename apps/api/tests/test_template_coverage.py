"""Every product feature has a template that shows it: every block in the palette appears in at least one, and so
does every feature that lives outside the canvas (event handlers, workspace code, custom blocks, evaluation, saved
tests, schedules). A new block or feature needs a template, or this fails."""

from __future__ import annotations

import json

from agentcanvas.registry import default_registry
from agentcanvas.services.template_kit import kit_of
from agentcanvas.services.workflows import TEMPLATES, block_types, template_gallery

DOCS = {p.name.removesuffix(".ir.json"): json.loads(p.read_text()) for p in sorted(TEMPLATES.glob("*.ir.json"))}


def test_every_block_in_the_palette_is_in_a_template():
    used = set().union(*(block_types(d) for d in DOCS.values()))
    missing = sorted(m.type for m in default_registry().all() if m.type not in used)
    assert not missing, f"blocks no template shows: {missing}"


def test_every_feature_outside_the_canvas_is_in_a_template():
    kits = {name: kit_of(doc) for name, doc in DOCS.items()}

    def python_tools_from_kit(name: str) -> bool:
        """A Python tool block whose function is in one of the kit's tools/ files."""
        modules = {p.removesuffix(".py").replace("/", ".") for p in kits[name].files if p.startswith("tools/")}
        tools = [r for r in DOCS[name].get("resources", []) if r.get("type") == "tool.python"]
        return any(str(r["config"].get("import_path", "")).partition(":")[0] in modules for r in tools)

    def having(test) -> list[str]:
        return [name for name, kit in kits.items() if test(kit)]

    features = {
        "event handlers": having(lambda k: any(p.startswith("events/") and p.endswith(".py") for p in k.files)),
        "workspace scripts": having(lambda k: any(p.startswith("scripts/") for p in k.files)),
        "custom blocks": having(lambda k: any(p.startswith("nodes/") for p in k.files)),
        "Python tools": [name for name in DOCS if python_tools_from_kit(name)],
        "evaluation datasets": having(lambda k: k.dataset is not None and bool(k.evaluators)),
        "saved tests": having(lambda k: bool(k.tests)),
        "schedules": having(lambda k: bool(k.triggers)),
    }
    assert not [f for f, names in features.items() if not names], features


def test_every_level_has_templates():
    levels = [template_gallery(d).get("difficulty") for d in DOCS.values() if template_gallery(d)]
    assert levels.count("beginner") >= 10 and levels.count("intermediate") >= 10 and levels.count("advanced") >= 5


async def test_the_hosted_service_lists_only_templates_it_can_run(app, api_client, monkeypatch):
    monkeypatch.setattr(app.state.settings, "edition", "hosted")
    hosted = {t["id"] for t in (await api_client.get("/v1/templates")).json()}
    assert "agent_network" not in hosted  # its agent from another framework runs on the host (E041)
    assert {"lead_scoring_pro", "event_support_inbox", "faq_bot"} <= hosted
