"""S-5: in the hosted edition, model and MCP clients reach public addresses only, checked when they connect, so a
DNS answer that changes between a check and the connection (rebinding) cannot reach the cloud metadata service."""

from __future__ import annotations

import asyncio
import socket

import pytest

from agentcanvas.adapters.langchain import pinned


def _answers(monkeypatch, ip: str) -> list[str]:
    asked: list[str] = []

    async def getaddrinfo(host, port, *a, **k):
        asked.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)
    return asked


@pytest.mark.parametrize("make", ["model", "mcp"])
async def test_a_name_resolving_to_an_internal_address_is_refused_when_connecting(monkeypatch, make):
    _answers(monkeypatch, "169.254.169.254")
    client = pinned.public_async_client() if make == "model" else pinned.public_mcp_client_factory()
    async with client:
        with pytest.raises(Exception, match="private or internal address"):
            await client.get("http://rebinding.example/latest/meta-data/")


async def test_the_connection_goes_to_the_address_that_was_checked(monkeypatch):
    asked = _answers(monkeypatch, "93.184.215.14")
    connected: list[str] = []
    backend = pinned._backend(__import__("httpcore"))

    async def connect_tcp(host, port, **kw):
        connected.append(host)
        raise OSError("stop here")

    monkeypatch.setattr(backend.inner, "connect_tcp", connect_tcp)
    with pytest.raises(OSError, match="stop here"):
        await backend.connect_tcp("api.example.com", 443)
    assert asked == ["api.example.com"] and connected == ["93.184.215.14"]  # by address: no second lookup


def test_the_hosted_edition_wires_them_in():
    from agentcanvas.adapters.langchain.nodes import Resources, mcp_target

    transport = mcp_target({"url": "https://mcp.example.com/mcp"}, public_only=True)
    assert transport.httpx_client_factory is pinned.public_mcp_client_factory
    res = Resources(secrets={"OPENAI_COMPATIBLE_API_KEY": "k" * 8})
    res.public_only = True
    model = res.model("m", {"model": "openai_compatible:x", "base_url": "https://llm.example.com/v1",
                            "api_key": {"$secret": "OPENAI_COMPATIBLE_API_KEY"}})  # fmt: skip
    inner = model
    while hasattr(inner, "model") and not hasattr(inner, "async_client"):  # the gateway wraps the client
        inner = inner.model
    backend = inner.http_async_client._transport._pool._network_backend
    assert type(backend).__name__ == "PublicOnly"
