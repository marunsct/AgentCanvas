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
