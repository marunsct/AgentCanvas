"""Starts one local (stdio) MCP server inside the sandbox and gets out of its way.

Standard library only (it runs inside the connector image and in the sandbox service):

    AC_EGRESS_UPSTREAM=http://user:password@egress:3128  python -I mcp_child.py npx -y @playwright/mcp@0.0.83

The server's stdin and stdout are this process's, so the MCP messages pass straight through. Without
`AC_EGRESS_UPSTREAM` the server gets no proxy (and the sandbox has no network). With it, the server gets a relay on
127.0.0.1 as its HTTP(S) proxy, which adds the signed credential to every request on its way to the egress proxy:
browsers (Chromium reads the proxy from the environment but sends no credential in it) and Node.js reach the hosts
of the project's egress list like any client, and the credential never enters the server's environment.
"""

from __future__ import annotations

import asyncio
import base64
import os
import signal
import subprocess
import sys
import threading
from urllib.parse import unquote, urlsplit

HEAD_LIMIT = 16 * 1024


def _upstream(url: str) -> tuple[str, int, bytes]:
    """(host, port, the Proxy-Authorization header line) of the egress proxy URL."""
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname or not parts.port:
        raise SystemExit("AC_EGRESS_UPSTREAM must be http://user:password@host:port")
    token = base64.b64encode(f"{unquote(parts.username or '')}:{unquote(parts.password or '')}".encode()).decode()
    return parts.hostname, parts.port, f"Proxy-Authorization: Basic {token}\r\n".encode()


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


def _relay(host: str, port: int, auth: bytes):
    async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        up_writer = None
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            first, _, rest = head.partition(b"\r\n")
            # whatever the client sent as credentials is dropped: the signed one is the only one
            kept = b"".join(
                line + b"\r\n" for line in rest[:-4].split(b"\r\n") if line and not line.lower().startswith(b"proxy-")
            )
            up_reader, up_writer = await asyncio.open_connection(host, port)
            up_writer.write(first + b"\r\n" + auth + kept + b"\r\n")
            await up_writer.drain()
            await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError, OSError):
            pass
        finally:
            writer.close()
            if up_writer is not None:
                up_writer.close()

    return client


def _start_relay(upstream: str) -> int:
    """The relay's port on 127.0.0.1, serving from a thread of its own for as long as this process lives."""
    host, port, auth = _upstream(upstream)
    ready: list[int] = []
    started = threading.Event()

    def serve() -> None:
        async def main() -> None:
            server = await asyncio.start_server(_relay(host, port, auth), "127.0.0.1", 0, limit=HEAD_LIMIT)
            ready.append(server.sockets[0].getsockname()[1])
            started.set()
            await server.serve_forever()

        asyncio.run(main())

    threading.Thread(target=serve, daemon=True).start()
    if not started.wait(10):
        raise SystemExit("the egress relay did not start")
    return ready[0]


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: mcp_child.py <command> [args...]", file=sys.stderr)
        return 2
    env = dict(os.environ)
    upstream = env.pop("AC_EGRESS_UPSTREAM", "")
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(name, None)
    if upstream:
        relay = f"http://127.0.0.1:{_start_relay(upstream)}"
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"):
            env[name] = relay
        env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1"
        env["NODE_USE_ENV_PROXY"] = "1"  # Node.js 24's fetch and http follow HTTPS_PROXY with it
    try:
        proc = subprocess.Popen(argv, env=env)  # stdin, stdout and stderr are this process's
    except OSError as exc:
        print(f"could not start {argv[0]}: {exc}", file=sys.stderr)
        return 127

    def forward(signum: int, _frame: object) -> None:
        proc.send_signal(signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, forward)
    return proc.wait()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
