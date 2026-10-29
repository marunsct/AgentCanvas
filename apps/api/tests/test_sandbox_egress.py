"""The sandbox egress proxy (sandbox/egress.py): only what `sandbox.json` allows gets out, with credentials the API
signed; private addresses stay out of reach unless named; plain HTTP and CONNECT both work for allowed hosts."""

from __future__ import annotations

import asyncio
import base64
import time

import httpx
import pytest

from agentcanvas.sandbox.egress import EgressProxy, allowed, proxy_url, sign, verify

SECRET = "egress-test-secret-0123456789"


def test_allow_list_entries():
    allow = ["api.github.com", "files.example.com:8443", "*.googleapis.com", "10.0.0.5:5432"]
    assert allowed("api.github.com", 443, allow) and allowed("API.GitHub.com.", 80, allow)
    assert not allowed("api.github.com", 22, allow)  # a bare host means 443 and 80
    assert allowed("files.example.com", 8443, allow) and not allowed("files.example.com", 443, allow)
    assert allowed("storage.googleapis.com", 443, allow) and not allowed("googleapis.com", 443, allow)
    assert not allowed("evilgoogleapis.com", 443, allow) and not allowed("api.github.com.evil.io", 443, allow)
    assert allowed("10.0.0.5", 5432, allow) and not allowed("10.0.0.6", 5432, allow)
    assert not allowed("anything", 443, [])


def test_credentials_are_signed_and_expire():
    user, password = sign(["a.example"], SECRET, ttl_s=60)
    header = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    assert verify(header, SECRET) == ["a.example"]
    assert verify(header, "another-secret-0123456789") is None
    assert verify(header, SECRET, now=time.time() + 120) is None  # expired
    widened_user, _ = sign(["*.example", "evil.io"], "attacker-guess-0123456789", ttl_s=60)
    forged = "Basic " + base64.b64encode(f"{widened_user}:{password}".encode()).decode()
    assert verify(forged, SECRET) is None  # a wider list with the old signature
    for bad in (None, "", "Bearer x", "Basic !!!", "Basic " + base64.b64encode(b"nocolon").decode()):
        assert verify(bad, SECRET) is None


@pytest.fixture
async def upstream():
    """A plain TCP server that answers any HTTP request with 'hello', on 127.0.0.1."""

    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(answer, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()


async def _proxy(resolve_to: str = "127.0.0.1", allow_private: bool = True) -> tuple[EgressProxy, int]:
    async def resolve(host: str, port: int) -> list[str]:
        return [resolve_to]

    proxy = EgressProxy(SECRET, resolve=resolve, allow_private=allow_private, timeout_s=5)
    return proxy, await proxy.start("127.0.0.1", 0)


async def _get(proxy_port: int, url: str, allow: list[str] | None, secret: str = SECRET) -> httpx.Response:
    proxy = (
        proxy_url(f"127.0.0.1:{proxy_port}", allow, secret, 60)
        if allow is not None
        else f"http://127.0.0.1:{proxy_port}"
    )
    async with httpx.AsyncClient(proxy=proxy, timeout=5) as client:
        return await client.get(url)


async def test_plain_http_to_an_allowed_host_goes_through_and_others_are_refused(upstream):
    proxy, port = await _proxy()
    try:
        ok = await _get(port, f"http://api.example.com:{upstream}/v1/x?q=1", [f"api.example.com:{upstream}"])
        assert ok.status_code == 200 and ok.text == "hello"
        refused = await _get(port, f"http://other.example.com:{upstream}/", [f"api.example.com:{upstream}"])
        assert refused.status_code == 403 and "not in this project's sandbox.json" in refused.text
        assert refused.headers["x-agentcanvas-egress"] == "refused"
        assert (await _get(port, f"http://api.example.com:{upstream}/", None)).status_code == 407
        forged = await _get(port, f"http://api.example.com:{upstream}/", [f"api.example.com:{upstream}"], "x" * 20)
        assert forged.status_code == 407
    finally:
        await proxy.close()


async def test_connect_tunnels_only_to_allowed_hosts(upstream):
    proxy, port = await _proxy()
    try:
        user, password = sign([f"tunnel.example.com:{upstream}"], SECRET, 60)
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        for target, expect in ((f"tunnel.example.com:{upstream}", b"200 Connection established"),
                               (f"elsewhere.example.com:{upstream}", b"403 Forbidden")):  # fmt: skip
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(
                f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\nProxy-Authorization: Basic {auth}\r\n\r\n".encode()
            )
            await writer.drain()
            status = await reader.readuntil(b"\r\n\r\n")
            assert expect in status, status
            if expect.startswith(b"200"):  # the tunnel carries a request to the upstream and its answer back
                writer.write(b"GET / HTTP/1.1\r\nHost: tunnel\r\n\r\n")
                await writer.drain()
                assert (await reader.read()).endswith(b"hello")
            writer.close()
    finally:
        await proxy.close()


async def test_names_that_resolve_to_private_addresses_are_refused(upstream):
    for address in ("127.0.0.1", "169.254.169.254", "10.1.2.3", "::1"):
        proxy, port = await _proxy(resolve_to=address, allow_private=False)
        try:
            r = await _get(port, f"http://metadata.example.com:{upstream}/", [f"metadata.example.com:{upstream}"])
            assert r.status_code == 403 and "address" in r.text, address
        finally:
            await proxy.close()


async def test_a_private_address_named_in_the_list_is_still_refused(upstream):
    """The list is written by the project (sandbox.json), so naming an address there opens nothing: a project could
    otherwise list the cloud metadata service or the database. Only the proxy's operator opens private addresses."""
    for address in ("127.0.0.1", "10.0.0.5", "172.18.0.2"):
        proxy, port = await _proxy(resolve_to=address, allow_private=False)
        try:
            r = await _get(port, f"http://{address}:{upstream}/", [f"{address}:{upstream}"])
            assert r.status_code == 403 and "AGENTCANVAS_EGRESS_ALLOW_PRIVATE" in r.text, address
        finally:
            await proxy.close()
    proxy, port = await _proxy(resolve_to="127.0.0.1", allow_private=True)  # the operator's choice
    try:
        r = await _get(port, f"http://127.0.0.1:{upstream}/", [f"127.0.0.1:{upstream}"])
        assert r.status_code == 200 and r.text == "hello"
    finally:
        await proxy.close()


async def test_metadata_addresses_are_refused_even_when_private_ones_are_allowed(upstream):
    for address in ("169.254.169.254", "fe80::1", "fd00:ec2::254", "0.0.0.0"):
        proxy, port = await _proxy(resolve_to=address, allow_private=True)
        try:
            r = await _get(port, f"http://metadata.example.com:{upstream}/", [f"metadata.example.com:{upstream}"])
            assert r.status_code == 403 and "metadata" in r.text, address
        finally:
            await proxy.close()


async def test_garbage_does_not_take_the_proxy_down(upstream):
    proxy, port = await _proxy()
    try:
        for junk in (b"\x00\xff" * 50 + b"\r\n\r\n", b"GET\r\n\r\n", b"CONNECT nope HTTP/1.1\r\n\r\n", b"A" * 20000):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(junk)
            await writer.drain()
            await asyncio.wait_for(reader.read(), 5)
            writer.close()
        ok = await _get(port, f"http://api.example.com:{upstream}/", [f"api.example.com:{upstream}"])
        assert ok.status_code == 200
    finally:
        await proxy.close()
