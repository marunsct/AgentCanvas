"""HTTP clients that reach public addresses only, checked at the moment they connect (the hosted edition, S-5).

Checking a host name and then letting the client resolve it again leaves a gap: a DNS server can answer a public
address to the check and the cloud metadata address (or the cluster's) to the connection ("DNS rebinding"). The
network backend here resolves the name itself, refuses it if any answer is not a global address, and connects to the
address it checked. TLS still verifies the certificate against the name. It plugs into httpx (model clients) and
httpx2 (the MCP client) through their connection pools' `network_backend`, and into requests (the OpenTelemetry trace
exporter) through urllib3's connection class.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any

from agentcanvas.security.addresses import is_public


class RefusedAddress(OSError):
    """The host resolves to a private, loopback, link-local (cloud metadata), reserved or multicast address."""


def _literal(host: str) -> list[str] | None:
    try:
        return [str(ipaddress.ip_address(host.strip("[]")))]
    except ValueError:
        return None


async def public_address(host: str, port: int) -> str:
    answers = _literal(host)
    if answers is None:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        answers = [str(info[4][0]) for info in infos]
    return _checked(host, answers)


def public_address_sync(host: str, port: int) -> str:
    """`public_address` for blocking clients (requests, in a worker thread)."""
    answers = _literal(host)
    if answers is None:
        answers = [str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
    return _checked(host, answers)


def _checked(host: str, answers: list[str]) -> str:
    for a in answers:
        if not is_public(a):  # NAT64 and other IPv6 forms of a private IPv4 address too (RT-15)
            raise RefusedAddress(f"'{host}' resolves to a private or internal address ({a}); refused")
    if not answers:
        raise RefusedAddress(f"'{host}' does not resolve")
    return answers[0]


def _backend(core: Any) -> Any:
    class PublicOnly(core.AsyncNetworkBackend):
        def __init__(self) -> None:
            self.inner = core.AnyIOBackend()

        async def connect_tcp(
            self, host: str, port: int, timeout: float | None = None, local_address: str | None = None,
            socket_options: Any = None,
        ) -> Any:  # fmt: skip
            ip = await public_address(host, port)
            return await self.inner.connect_tcp(ip, port, timeout=timeout, local_address=local_address,
                                                socket_options=socket_options)  # fmt: skip

        async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options: Any = None) -> Any:
            raise RefusedAddress("unix sockets are not reachable from the hosted edition")

        async def sleep(self, seconds: float) -> None:
            await self.inner.sleep(seconds)

    return PublicOnly()


def _transport(httpx_module: Any, core: Any) -> Any:
    transport = httpx_module.AsyncHTTPTransport()
    ssl_context = getattr(transport._pool, "_ssl_context", None)  # keep the transport's certificate checks
    transport._pool = core.AsyncConnectionPool(ssl_context=ssl_context, network_backend=_backend(core))
    return transport


def public_async_client(**kwargs: Any) -> Any:
    """An `httpx.AsyncClient` (OpenAI-compatible model clients) that connects to public addresses only."""
    import httpcore
    import httpx

    return httpx.AsyncClient(transport=_transport(httpx, httpcore), **kwargs)


def public_mcp_client_factory(
    headers: dict[str, str] | None = None, timeout: Any = None, auth: Any = None, **_asked: Any
) -> Any:
    """The MCP client's `httpx_client_factory`, connecting to public addresses only. It asks for `follow_redirects=True`
    among other things; a redirect could lead anywhere, so none is followed whatever it asks."""
    import httpcore2
    import httpx2

    timeout = timeout or httpx2.Timeout(30.0, read=300.0)  # the MCP client's own defaults (long-lived streams)
    return httpx2.AsyncClient(headers=headers, timeout=timeout, auth=auth, follow_redirects=False,
                              transport=_transport(httpx2, httpcore2))  # fmt: skip


def public_requests_session() -> Any:
    """A `requests.Session` (the OpenTelemetry trace exporter's) that connects to public addresses only. Every new
    connection, a redirect's too, is checked and made to the address checked; the Host header and TLS keep the name.
    Proxy variables are ignored: the proxy would be the address connected to."""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.connection import HTTPConnection, HTTPSConnection
    from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
    from urllib3.exceptions import NewConnectionError

    def pinned(conn: Any, connect: Any) -> Any:
        name = conn._dns_host
        try:
            ip = public_address_sync(name, conn.port)
        except OSError as exc:
            raise NewConnectionError(conn, str(exc)) from exc
        conn._dns_host = ip  # only for opening the socket: the request and TLS are made with the name
        try:
            return connect()
        finally:
            conn._dns_host = name

    class PinnedHTTP(HTTPConnection):
        def _new_conn(self) -> Any:
            return pinned(self, super()._new_conn)

    class PinnedHTTPS(HTTPSConnection):
        def _new_conn(self) -> Any:
            return pinned(self, super()._new_conn)

    class HTTPPool(HTTPConnectionPool):
        ConnectionCls = PinnedHTTP  # pyright: ignore[reportAssignmentType] - its stubs refuse urllib3's own class too

    class HTTPSPool(HTTPSConnectionPool):
        ConnectionCls = PinnedHTTPS  # pyright: ignore[reportAssignmentType]

    class PublicOnly(HTTPAdapter):
        def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
            super().init_poolmanager(*args, **kwargs)
            self.poolmanager.pool_classes_by_scheme = {"http": HTTPPool, "https": HTTPSPool}

    session = requests.Session()
    session.trust_env = False
    session.mount("http://", PublicOnly())
    session.mount("https://", PublicOnly())
    return session
