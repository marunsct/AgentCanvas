"""`allow_private_network` lets a step reach the company's own network, never the cloud metadata service: from the API
process that address hands out the node's cloud credentials. Link-local, unspecified and metadata addresses are
refused in every edition, as the sandbox's egress proxy refuses them (S7, RT-15)."""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
from langchain_core.tools import ToolException

from agentcanvas.adapters.langchain import interop, nodes
from agentcanvas.security.addresses import is_metadata

METADATA = "http://169.254.169.254/latest/meta-data/iam/security-credentials/"


@pytest.fixture
async def dns(monkeypatch):
    """Names resolve as the test says; httpx records what it would send instead of sending it."""
    answers: dict[str, str] = {}
    sent: list[httpx.Request] = []

    async def getaddrinfo(host, port, *a, **k):
        ip = answers.get(host, host)
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, port))]

    async def send(self, request, **kw):
        sent.append(request)
        return httpx.Response(200, text="ok", request=request)

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return answers, sent


@pytest.mark.parametrize("address", ["169.254.169.254", "fd00:ec2::254", "0.0.0.0", "::ffff:169.254.169.254",
                                     "64:ff9b::a9fe:a9fe", "fe80::1"])  # fmt: skip
def test_metadata_and_link_local_addresses_are_recognised(address):
    assert is_metadata(address)


@pytest.mark.parametrize("address", ["10.0.0.5", "192.168.1.10", "127.0.0.1", "8.8.8.8", "fd12::1"])
def test_ordinary_private_and_public_addresses_are_not_metadata(address):
    assert not is_metadata(address)


async def test_an_http_tool_with_private_network_on_still_cannot_reach_the_metadata_service(dns):
    answers, sent = dns
    answers["meta.internal"] = "169.254.169.254"  # a name that resolves there is refused too
    for url in (METADATA, "http://meta.internal/latest/"):
        tool = nodes.http_tool(name="t", config={"url": url, "allow_private_network": True,
                                                 "allowed_domains": ["169.254.169.254", "meta.internal"]})  # fmt: skip
        with pytest.raises(ToolException, match="metadata"):
            await tool.coroutine()  # type: ignore[misc]
    assert sent == []
    answers["wiki.corp"] = "10.0.0.5"  # the company's own network: still reachable, at the address checked
    tool = nodes.http_tool(name="t", config={"url": "http://wiki.corp/page", "allow_private_network": True,
                                             "allowed_domains": ["wiki.corp"]})  # fmt: skip
    assert await tool.coroutine() == "ok"  # type: ignore[misc]
    assert sent[-1].url.host == "10.0.0.5" and sent[-1].headers["host"] == "wiki.corp"


async def test_an_a2a_step_with_private_network_on_cannot_reach_it_either(dns):
    with pytest.raises(Exception, match="metadata"):
        await interop._pinned(METADATA, {}, private_ok=True)
    pinned = await interop._pinned("http://10.0.0.5/a2a", {}, private_ok=True)
    assert pinned.url == "http://10.0.0.5:80/a2a" and pinned.headers["Host"] == "10.0.0.5"


async def test_an_mcp_server_at_the_metadata_address_is_refused(dns):
    res = nodes.Resources()
    with pytest.raises(Exception, match="metadata"):
        await res.load_mcp("m", {"transport": "http", "url": "http://169.254.169.254/mcp"})
