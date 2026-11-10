"""S15: the one-click MCP connector gallery: every entry is a valid App connector block."""

from __future__ import annotations

import jsonschema

from agentcanvas.registry import default_registry, load_gallery


def _refs(value) -> set[str]:
    if isinstance(value, dict):
        if isinstance(value.get("$secret"), str):
            return {value["$secret"]}
        return set().union(set(), *(_refs(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(set(), *(_refs(v) for v in value))
    return set()


def test_every_connector_is_a_valid_app_connector():
    schema = default_registry().get("mcp.server").config_schema
    connectors = load_gallery()["connectors"]
    assert len(connectors) >= 25
    assert len({c["id"] for c in connectors}) == len(connectors)
    for c in connectors:
        jsonschema.validate(c["config"], schema)
        transport = c["config"].get("transport", "http")
        if transport == "http":
            assert c["config"]["url"].startswith("https://"), c["id"]
        else:
            assert c["config"]["command"] in ("npx", "uvx"), c["id"]
        assert _refs(c["config"]) <= {s["name"] for s in c["secrets"]}, c["id"]
        assert c["description"] and c["verified"] and c["source"], c["id"]


async def test_the_gallery_is_served(api_client):
    out = (await api_client.get("/v1/mcp-connectors")).json()
    by_id = {c["id"]: c for c in out}
    assert by_id["github"]["local"] is False and by_id["filesystem"]["local"] is True


def test_sign_in_only_connectors_are_set_up_to_sign_in():
    """G-7: a remote app that accepts only an interactive sign-in carries the `oauth` setting (the Connect button works
    from it) and needs no secret; no other connector has it."""
    connectors = load_gallery()["connectors"]
    assert {"linear", "atlassian"} <= {c["id"] for c in connectors if c["auth"] == "oauth"}
    for c in connectors:
        if c["auth"] == "oauth" and c["config"].get("transport", "http") == "http":
            assert c["config"]["oauth"] == {"identity": "starter"} and c["secrets"] == [], c["id"]
            assert "cannot do yet" not in c["setup"], c["id"]
        else:
            assert "oauth" not in c["config"], c["id"]


def test_connectors_marked_for_the_sandbox_are_pinned_local_programs():
    from agentcanvas.registry import sandboxable_connector

    marked = [c for c in load_gallery()["connectors"] if c.get("sandboxable")]
    assert {"playwright"} <= {c["id"] for c in marked}
    for c in marked:
        assert c["config"]["transport"] == "stdio" and c["source"].startswith("npm:"), c["id"]
        assert sandboxable_connector(c["config"]) is c, c["id"]


def test_a_connector_runs_in_the_sandbox_only_as_the_gallery_starts_it():
    """The command, its flags and the package (exact version) must be the gallery's; arguments after are free."""
    from agentcanvas.registry import sandboxable_connector

    base = {"transport": "stdio", "command": "npx", "args": ["-y", "@playwright/mcp@0.0.83", "--headless"]}
    assert (sandboxable_connector(base) or {}).get("id") == "playwright"
    more = {**base, "args": ["-y", "@playwright/mcp@0.0.83", "--viewport-size", "1280x720"]}
    assert (sandboxable_connector(more) or {}).get("id") == "playwright"
    for args in (
        ["-y", "@playwright/mcp@0.0.84"],
        ["-y", "-p", "evil", "@playwright/mcp@0.0.83"],
        ["@playwright/mcp@0.0.83"],
    ):
        assert sandboxable_connector({**base, "args": args}) is None, args
    assert sandboxable_connector({**base, "command": "node"}) is None
    slack = next(c for c in load_gallery()["connectors"] if c["id"] == "slack")
    assert sandboxable_connector(slack["config"]) is None  # in the gallery, but not in the sandbox image


def test_a_local_connector_is_found_in_the_gallery_by_its_package_at_any_version():
    from agentcanvas.registry import gallery_connector

    for args in (["-y", "@playwright/mcp@latest", "--isolated"], ["@playwright/mcp"], ["-y", "@playwright/mcp@0.0.83"]):
        assert (gallery_connector({"command": "npx", "args": args}) or {}).get("id") == "playwright", args
    assert (gallery_connector({"command": "uvx", "args": ["mcp-server-git"]}) or {}).get("id") == "git"
    assert gallery_connector({"command": "npx", "args": ["-y", "@playwright/mcp-evil@1"]}) is None
    assert gallery_connector({"command": "uvx", "args": ["@playwright/mcp@0.0.83"]}) is None  # another command
    assert gallery_connector({"command": "npx", "args": ["-y"]}) is None


def test_the_tools_a_connector_acts_with_are_its_own():
    """`acts` (W028) names tools of a local connector of the gallery."""
    for c in load_gallery()["connectors"]:
        acts = c.get("acts", [])
        assert isinstance(acts, list) and all(isinstance(a, str) and a for a in acts), c["id"]
        assert not acts or c["config"]["transport"] == "stdio", c["id"]


async def test_the_gallery_says_which_connectors_run_in_the_sandbox(api_client):
    by_id = {c["id"]: c for c in (await api_client.get("/v1/mcp-connectors")).json()}
    assert by_id["playwright"]["sandboxable"] is True and by_id["slack"]["sandboxable"] is False


def test_the_browser_image_installs_every_connector_marked_for_the_sandbox():
    """What the hosted edition allows (sandboxable) is what the sandbox's browser image comes with, at the gallery's
    exact version, and the image installs it from the lockfile (infra/Dockerfile.api, WITH_BROWSER)."""
    import json
    from pathlib import Path

    folder = Path(__file__).parents[1] / "agentcanvas" / "sandbox" / "connectors"
    wanted = json.loads((folder / "package.json").read_text())["dependencies"]
    locked = json.loads((folder / "package-lock.json").read_text())["packages"]
    marked = [c for c in load_gallery()["connectors"] if c.get("sandboxable")]
    for c in marked:
        name, _, version = c["source"].removeprefix("npm:").rpartition("@")
        assert wanted.get(name) == version, c["id"]
        assert locked[f"node_modules/{name}"]["version"] == version, c["id"]
        assert f"{name}@{version}" in c["config"]["args"], c["id"]  # the gallery starts what the image installed
    assert len(wanted) == len(marked)  # nothing installed that the gallery does not offer
    dockerfile = (Path(__file__).parents[3] / "infra" / "Dockerfile.api").read_text()
    assert "ARG WITH_BROWSER=false" in dockerfile and "agentcanvas/sandbox/connectors/package-lock.json" in dockerfile
    assert "npm ci" in dockerfile and "install --no-shell chromium" in dockerfile and "AC_MCP_SERVERS=" in dockerfile
