"""The egress proxy for the `docker` script mode: an internal Docker network (`agentcanvas-sandbox`, no route out)
for sandbox containers that have an egress list, and one proxy container (`agentcanvas-egress`) on it and on the
default bridge. Containers without a list keep `--network none`.

The API starts both on first need and recreates the proxy when its secret changes. The secret is
`AGENTCANVAS_EGRESS_SECRET`, or one derived from `AGENTCANVAS_SECRET_KEY`, so every API process on the host signs
lists the same proxy accepts.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from pathlib import Path

from agentcanvas.settings import Settings

NETWORK = "agentcanvas-sandbox"
CONTAINER = "agentcanvas-egress"
PORT = 3128
PROXY_FILE = Path(__file__).with_name("egress.py")
_ready: dict[str, str] = {}  # secret fingerprint -> address, once checked in this process
_lock = asyncio.Lock()


class EgressUnavailable(RuntimeError):
    """The proxy container could not be started (Docker refused, or the image is missing)."""


def secret(settings: Settings) -> str:
    if settings.egress_secret:
        return settings.egress_secret
    return hmac.new(settings.secret_key.encode(), b"agentcanvas sandbox egress", hashlib.sha256).hexdigest()


async def _docker(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace").strip()


async def ensure(settings: Settings) -> str:
    """The proxy's address on the sandbox network (`agentcanvas-egress:3128`), starting it when needed."""
    key = secret(settings)
    fingerprint = hashlib.sha256(key.encode()).hexdigest()[:16]
    if fingerprint in _ready:
        return _ready[fingerprint]
    async with _lock:
        if fingerprint in _ready:
            return _ready[fingerprint]
        code, _ = await _docker("network", "inspect", NETWORK)
        if code != 0:
            code, out = await _docker("network", "create", "--internal", NETWORK)
            if code != 0 and "already exists" not in out:
                raise EgressUnavailable(f"could not create the Docker network {NETWORK}: {out[-300:]}")
        running = '{{.State.Running}} {{index .Config.Labels "agentcanvas.egress"}}'
        _, state = await _docker("inspect", "-f", running, CONTAINER)
        if state != f"true {fingerprint}":
            await _docker("rm", "-f", CONTAINER)
            code, out = await _docker(
                "run", "-d", "--name", CONTAINER, "--label", f"agentcanvas.egress={fingerprint}",
                "--restart", "unless-stopped", "--user", "65534:65534", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--memory", "256m", "--pids-limit", "256",
                "-e", f"AGENTCANVAS_EGRESS_SECRET={key}", "-v", f"{PROXY_FILE}:/egress.py:ro",
                settings.sandbox_image, "python", "-I", "/egress.py", "--port", str(PORT),
            )  # fmt: skip
            if code != 0:
                raise EgressUnavailable(f"could not start the egress proxy container: {out[-300:]}")
            code, out = await _docker("network", "connect", NETWORK, CONTAINER)
            if code != 0 and "already exists" not in out:
                raise EgressUnavailable(f"could not attach the egress proxy to {NETWORK}: {out[-300:]}")
        _ready[fingerprint] = f"{CONTAINER}:{PORT}"
        return _ready[fingerprint]
