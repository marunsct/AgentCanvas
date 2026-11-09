"""Statements in the user guide that the code can check: counts, audit action names, what the local setup starts and
what the product builds. Each one was wrong once."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import export_settings_reference as ref

from agentcanvas.services.audit_sink import SECURITY_ACTIONS

ROOT = Path(__file__).resolve().parents[3]
GUIDE = ROOT / "docs" / "user-guide"
TEMPLATES = ROOT / "apps" / "api" / "agentcanvas" / "registry" / "templates"
# ticket ids of the developer plans (M7-T1, TS9a, E041, S12, G-5, FR-X-10...), which mean nothing to an operator
TICKET = re.compile(r"\b(?:[A-Z]{1,3}\d+[a-z]?(?:-T\d+)?|[A-Z]{1,2}-\d+|FR-X-\d+)\b")


def _runbook(name: str) -> str:
    return next(line for line in (GUIDE / "operations.md").read_text().splitlines() if line.startswith(f"| {name} |"))


def test_every_template_count_matches_the_gallery_and_the_gallery_page_lists_each_one():
    import json

    names = [json.loads(p.read_text())["name"] for p in sorted(TEMPLATES.glob("*.ir.json"))]
    stated = {
        (page.name, int(n))
        for page in GUIDE.glob("*.md")
        for n in re.findall(r"\b(\d+) (?:templates|ready-to-run workflows)\b", page.read_text())
    }
    assert stated and all(n == len(names) for _, n in stated), (len(names), stated)
    listed = set(re.findall(r"^\| \*\*(.+?)\*\* \|", (GUIDE / "templates.md").read_text(), re.M))
    assert set(names) == listed, set(names) ^ listed


def test_the_leaked_secret_runbook_names_audit_actions_that_exist():
    """Reads are never audited and actions are named after routes: an operator who searched for `secret.read` found
    nothing and could conclude the secret was never read."""
    for page in GUIDE.glob("*.md"):
        assert "secret.read" not in page.read_text(), page.name
    row = _runbook("Leaked secret")
    named = set(re.findall(r"`([a-z_]+)`", row)) - {"audit_log"}
    assert {"put_secret", "delete_secret", "rotate_key"} <= named <= SECURITY_ACTIONS, named - SECURITY_ACTIONS
    assert "status banner" not in _runbook("Model provider outage")  # no such feature


def test_the_local_setup_is_described_as_it_is():
    compose = yaml.safe_load((ROOT / "infra" / "docker-compose.yml").read_text())
    assert not any("redis" in name for name in compose["services"])
    assert "Redis" not in next(r for r in (GUIDE / "operations.md").read_text().splitlines() if r.startswith("| `dev`"))
    local = (GUIDE / "run-locally.md").read_text()
    assert "starts Redis" not in local
    # CI runs on the developer's machine, and on GitHub only by hand (.github/workflows/ci.yml)
    assert "workflow_dispatch" in (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "on every change" not in local


def test_the_guide_and_the_site_name_every_framework_the_product_builds_for():
    intro = (GUIDE / "index.md").read_text().split("\n## ", 1)[0]
    site = yaml.safe_load((ROOT / "mkdocs.yml").read_text().split("\ntheme:", 1)[0])["site_description"]
    for text in (intro, site):
        for framework in ("LangGraph", "OpenAI Agents SDK", "CrewAI", "Google ADK", "Mastra"):
            assert framework in text, (framework, text)


def test_the_server_settings_page_speaks_plainly():
    """server-settings.md is made from the comments in settings.py: they carry no ticket ids."""
    found = {v: TICKET.findall(text) for v, _, text in ref.rows() if TICKET.search(text)}
    assert found == {}
    upload = next(text for v, _, text in ref.rows() if v == "AGENTCANVAS_MAX_UPLOAD_MB")
    assert "in MB the web server" not in upload


def test_vulnerabilities_have_a_private_channel_and_customers_a_security_page():
    """Without SECURITY.md a finder's only channel was a public issue; without a security page a customer's
    questionnaire (data flows, retention, isolation, compliance) had nothing to point to."""
    policy = (ROOT / ".github" / "SECURITY.md").read_text()
    assert "security/advisories/new" in policy  # GitHub's private vulnerability reporting
    for part in ("Supported versions", "Response targets"):
        assert part in policy, part
    page = (GUIDE / "security.md").read_text()
    nav = yaml.safe_load((ROOT / "mkdocs.yml").read_text().replace("!!python", "#"))["nav"]
    assert "user-guide/security.md" in str(nav)
    for topic in ("Where your data is stored", "Encryption", "Organisations", "sandbox", "Audit", "SCIM",
                  "Retention", "Backups", "Third parties", "Compliance", "Reporting a vulnerability"):  # fmt: skip
        assert topic in page, topic
    for link, anchor in re.findall(r"\]\(([a-z-]+\.md)#([a-z0-9-]+)\)", page):  # every anchor it links to exists
        headings = re.findall(r"^#+ (.+)$", (GUIDE / link).read_text(), re.M)
        slugs = {re.sub(r"[^a-z0-9 -]", "", h.lower()).strip().replace(" ", "-") for h in headings}
        assert anchor in slugs, (link, anchor)
