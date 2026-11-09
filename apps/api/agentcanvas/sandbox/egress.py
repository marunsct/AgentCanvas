"""The sandbox's way out: an HTTP proxy that lets workspace code reach only the hosts its project's `sandbox.json`
allows (FR-X-08). Standard library only, so it runs from the API image or as a single file mounted into any Python
image (the Docker mode does that).

Sandboxed code has no route to the network except this proxy (the `docker` mode puts its containers on an internal
network; the `remote` mode's service sits on one). Each call carries its own allow-list to the proxy, signed by the
API, in the proxy credentials of `HTTPS_PROXY`/`HTTP_PROXY`, so the proxy keeps no state and code cannot widen its
list: `user` is the base64url JSON `{"allow": [...], "exp": <unix seconds>}`, `password` its HMAC-SHA256 under
`AGENTCANVAS_EGRESS_SECRET`. The proxy answers `CONNECT host:port` (HTTPS and anything else over TLS) and plain
`http://` requests; it refuses with 407 without valid credentials and 403 for a host not on the list or a name that
resolves to a private, loopback or link-local address (cloud metadata, the database, the API), unless that address
is itself what the list names.

Allow-list entries: `host` (ports 443 and 80), `host:port`, `*.domain` (any subdomain, not the domain itself).

    AGENTCANVAS_EGRESS_SECRET=... python -m agentcanvas.sandbox.egress --port 3128
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import sys
import time
from collections.abc import Awaitable, Callable
from urllib.parse import quote, urlsplit

from agentcanvas.security.addresses import effective, is_public

HEAD_LIMIT = 16 * 1024
DEFAULT_PORTS = (443, 80)
Resolver = Callable[[str, int], Awaitable[list[str]]]


# ------------------------------------------------------------------ tokens (the API signs, the proxy checks) ----
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(allow: list[str], secret: str, ttl_s: float) -> tuple[str, str]:
    """Proxy credentials (user, password) carrying `allow`, valid for `ttl_s` seconds (one call's time: S-7), with
    a call id the proxy logs with every connection it makes for it."""
    claims = {"allow": allow, "exp": int(time.time() + ttl_s), "call": secrets.token_hex(6)}
    user = _b64(json.dumps(claims, separators=(",", ":")).encode())
    return user, hmac.new(secret.encode(), user.encode(), hashlib.sha256).hexdigest()


def proxy_url(address: str, allow: list[str], secret: str, ttl_s: float) -> str:
    """`http://user:password@address` for HTTPS_PROXY/HTTP_PROXY."""
    user, password = sign(allow, secret, ttl_s)
    return f"http://{quote(user, safe='')}:{password}@{address}"


def verify(header: str | None, secret: str, now: float | None = None) -> list[str] | None:
    """The allow-list in a `Proxy-Authorization: Basic ...` header, or None when it is missing, forged or expired."""
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        user, _, password = base64.b64decode(header[6:].strip(), validate=True).decode().partition(":")
        expected = hmac.new(secret.encode(), user.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(password.encode(), expected.encode()):
            return None
        claims = json.loads(_unb64(user))
    except ValueError:
        return None
    if not isinstance(claims, dict) or not isinstance(claims.get("allow"), list):
        return None
    if not isinstance(claims.get("exp"), int) or claims["exp"] < (now if now is not None else time.time()):
        return None
    return [str(e) for e in claims["allow"]]


def _call(header: str | None) -> str:
    """The call id of a credential `verify` already accepted (for the log line of each connection)."""
    try:
        user = base64.b64decode((header or "")[6:].strip()).decode().partition(":")[0]
        return str(json.loads(_unb64(user)).get("call", "-"))[:16]
    except ValueError:
        return "-"


# ------------------------------------------------------------------ the allow-list ----
HOP_BY_HOP = ("proxy-", "connection:", "keep-alive:", "te:", "trailer:", "upgrade:", "host:")


def _split(entry: str) -> tuple[str, int | None]:
    host, sep, port = entry.strip().lower().rpartition(":")
    if sep and port.isdigit() and "]" not in port:
        return host.strip("[]"), int(port)
    return entry.strip().lower().strip("[]"), None


def allowed(host: str, port: int, allow: list[str]) -> str | None:
    """The entry that admits `host:port`, or None."""
    host = host.lower().rstrip(".").strip("[]")
    for entry in allow:
        pattern, want = _split(entry)
        if (want is None and port not in DEFAULT_PORTS) or (want is not None and want != port):
            continue
        if pattern == host or (pattern.startswith("*.") and host.endswith(pattern[1:]) and host != pattern[2:]):
            return entry
    return None


def _ip(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """The address a connection reaches: the IPv4 one inside a mapped, NAT64, 6to4 … address (RT-15)."""
    return effective(address)


def _private(address: str) -> bool:
    return not is_public(address)


METADATA = (ipaddress.ip_network("fd00:ec2::254/128"),)  # AWS's IPv6 metadata address (IPv4's is link-local)


def _never(address: str) -> bool:
    """Link-local (cloud metadata services: 169.254.169.254), unspecified and the metadata addresses: no sandbox
    reaches these, whatever the list says or the proxy allows."""
    ip = _ip(address)
    return ip.is_link_local or ip.is_unspecified or any(ip in net for net in METADATA)


async def _resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


# ------------------------------------------------------------------ the proxy ----
class EgressProxy:
    def __init__(
        self, secret: str, *, resolve: Resolver = _resolve, allow_private: bool = False, timeout_s: float = 15.0
    ):
        if len(secret) < 16:
            raise ValueError("the egress secret needs 16+ characters")
        self.secret, self.resolve, self.allow_private, self.timeout_s = secret, resolve, allow_private, timeout_s
        self.server: asyncio.Server | None = None

    async def start(self, host: str = "0.0.0.0", port: int = 3128) -> int:
        self.server = await asyncio.start_server(self._client, host, port, limit=HEAD_LIMIT)
        return self.server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.timeout_s)
            await self._handle(head, reader, writer)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def _handle(self, head: bytes, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        lines = head.decode("latin-1").split("\r\n")
        method, _, rest = lines[0].partition(" ")
        target = rest.rpartition(" ")[0]
        headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ln)}
        allow = verify(headers.get("proxy-authorization"), self.secret)
        if allow is None:
            return await _refuse(writer, 407, "sandbox egress needs the credentials the sandbox was given", auth=True)
        if method == "CONNECT":
            host, port = _split(target)
            if port is None:
                return await _refuse(writer, 400, "CONNECT needs host:port")
        else:
            url = urlsplit(target)
            if url.scheme != "http" or not url.hostname:
                return await _refuse(writer, 400, "send absolute http:// URLs, or CONNECT for https")
            if "content-length" in headers and "transfer-encoding" in headers:  # two framings: request smuggling
                return await _refuse(writer, 400, "a request may not have both Content-Length and Transfer-Encoding")
            if headers.get("transfer-encoding", "chunked").lower() != "chunked" or not (
                headers.get("content-length", "0").isdigit()
            ):
                return await _refuse(writer, 400, "a request body is framed by a Content-Length or chunked")
            host, port = url.hostname, url.port or 80
        if allowed(host, port, allow) is None:
            return await _refuse(writer, 403, f"{host}:{port} is not in this project's sandbox.json egress list")
        try:
            addresses = await asyncio.wait_for(self.resolve(host, port), self.timeout_s)
        except (OSError, TimeoutError):
            return await _refuse(writer, 502, f"{host} does not resolve")
        addresses = [a for a in addresses if not _never(a)]
        if not addresses:
            return await _refuse(writer, 403, f"{host} is a link-local or metadata address, which no sandbox reaches")
        if not self.allow_private:  # the list is the project's to write: only the proxy's operator opens the inside
            addresses = [a for a in addresses if not _private(a)]
            if not addresses:
                return await _refuse(
                    writer, 403,
                    f"{host} is a private address; the proxy reaches one only with AGENTCANVAS_EGRESS_ALLOW_PRIVATE=1",
                )  # fmt: skip
        upstream = await self._connect(addresses, port)
        if upstream is None:
            return await _refuse(writer, 502, f"{host}:{port} is not reachable")
        up_reader, up_writer = upstream
        print(f"egress call={_call(headers.get('proxy-authorization'))} {method} {host}:{port}", file=sys.stderr,
              flush=True)  # fmt: skip
        if method == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        else:  # origin-form request line, without the proxy's own or hop-by-hop headers, for the checked host
            path = urlsplit(target).path or "/"
            query = urlsplit(target).query
            first = f"{method} {path}{'?' + query if query else ''} {rest.rpartition(' ')[2]}"
            kept = [ln for ln in lines[1:] if ln and not ln.lower().startswith(HOP_BY_HOP)]
            host_line = f"Host: {host}" + ("" if port == 80 else f":{port}")  # never another virtual host
            up_writer.write(("\r\n".join([first, host_line, *kept, "Connection: close", "", ""])).encode("latin-1"))
        await writer.drain()
        if method != "CONNECT":
            # one request per connection: its own body goes up, and nothing after it, so a second request pipelined
            # behind it can never reach the upstream unchecked (RT-16); then the answer comes back until it ends
            try:
                await _copy_body(reader, up_writer, headers)
                await _pipe(up_reader, writer)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
                pass
            finally:
                up_writer.close()
            return
        try:
            # when one side is done, the other gets a moment to finish its last bytes, then both are closed
            tasks = [asyncio.create_task(_pipe(reader, up_writer)), asyncio.create_task(_pipe(up_reader, writer))]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if pending:
                await asyncio.wait(pending, timeout=self.timeout_s)
            for t in pending:
                t.cancel()
        finally:
            up_writer.close()

    async def _connect(self, addresses: list[str], port: int):
        for address in addresses:
            try:
                return await asyncio.wait_for(asyncio.open_connection(address, port), self.timeout_s)
            except (OSError, TimeoutError):
                continue
        return None


async def _refuse(writer: asyncio.StreamWriter, status: int, reason: str, auth: bool = False) -> None:
    text = {400: "Bad Request", 403: "Forbidden", 407: "Proxy Authentication Required", 502: "Bad Gateway"}[status]
    body = (reason + "\n").encode()
    extra = 'Proxy-Authenticate: Basic realm="agentcanvas-sandbox"\r\n' if auth else ""
    writer.write(
        f"HTTP/1.1 {status} {text}\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\n{extra}"
        f"X-AgentCanvas-Egress: refused\r\nConnection: close\r\n\r\n".encode()
        + body
    )
    await writer.drain()


async def _copy_body(src: asyncio.StreamReader, dst: asyncio.StreamWriter, headers: dict[str, str]) -> None:
    """Forward exactly one request body: `Content-Length` bytes, or chunks up to the last one and its trailers."""
    if headers.get("transfer-encoding"):
        while True:
            line = await src.readuntil(b"\r\n")
            dst.write(line)
            size = int(line.split(b";")[0].strip(), 16)
            if size == 0:
                while (trailer := await src.readuntil(b"\r\n")) != b"\r\n":
                    dst.write(trailer)
                dst.write(b"\r\n")
                break
            await _copy_exactly(src, dst, size + 2)  # the chunk and its CRLF
    else:
        await _copy_exactly(src, dst, int(headers.get("content-length", "0")))
    await dst.drain()


async def _copy_exactly(src: asyncio.StreamReader, dst: asyncio.StreamWriter, n: int) -> None:
    while n > 0:
        data = await src.readexactly(min(n, 65536))
        dst.write(data)
        await dst.drain()
        n -= len(data)


async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    try:
        while data := await src.read(65536):
            dst.write(data)
            await dst.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        if dst.can_write_eof():
            try:
                dst.write_eof()
            except OSError:
                pass


def main() -> None:  # pragma: no cover - the process entry point
    parser = argparse.ArgumentParser(description="AgentCanvas sandbox egress proxy")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3128)
    args = parser.parse_args()
    secret = os.environ.get("AGENTCANVAS_EGRESS_SECRET", "")
    proxy = EgressProxy(secret, allow_private=os.environ.get("AGENTCANVAS_EGRESS_ALLOW_PRIVATE") == "1")

    async def serve() -> None:
        port = await proxy.start(args.host, args.port)
        print(f"egress proxy on {args.host}:{port}", flush=True)
        assert proxy.server is not None
        await proxy.server.serve_forever()

    asyncio.run(serve())


if __name__ == "__main__":  # pragma: no cover
    main()
