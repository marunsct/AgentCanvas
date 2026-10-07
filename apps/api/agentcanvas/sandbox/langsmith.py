"""Workspace code in a LangSmith Sandbox (spike SP-2, `script_execution = "langsmith"`).

One sandbox per project, created on first use from `settings.langsmith_snapshot` (the default Ubuntu snapshot when
unset). Its proxy config comes from the project's sandbox policy: an egress allow-list and header rules that the
auth proxy applies outside the sandbox, so secret values never enter it. The workspace is uploaded as one archive
whenever its files change; every call then runs the same stdlib child protocol as the local sandboxes:

    cd /ws && python3 -I /sandbox/child.py IMPORT < /sandbox/in-<id>.json

A policy or secret change patches the sandbox's proxy config in place (a memory change replaces it). Idle
sandboxes stop after `langsmith_idle_s` and are deleted soon after, so a crashed API process does not leak them.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import math
import shlex
import tarfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentcanvas.sandbox.policy import SandboxPolicy, fingerprint, proxy_config
from agentcanvas.settings import Settings
from agentcanvas.workspace.store import HIDDEN, MAX_FILE_BYTES

CHILD = Path(__file__).with_name("child.py")


def _files(root: Path) -> list[Path]:
    """The workspace files a sandbox gets: no .git, caches or oversized files."""
    return [
        p
        for p in sorted(root.rglob("*"))
        if p.is_file()
        and not any(part in HIDDEN for part in p.relative_to(root).parts)
        and p.stat().st_size <= MAX_FILE_BYTES
    ]


def files_key(root: Path) -> str:
    """Changes whenever a file the sandbox gets changes."""
    h = hashlib.sha256()
    for p in _files(root):
        st = p.stat()
        h.update(f"{p.relative_to(root).as_posix()}:{st.st_mtime_ns}:{st.st_size}\n".encode())
    return h.hexdigest()[:16]


def workspace_archive(root: Path) -> bytes:
    """The workspace files as a gzip'd tar."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for p in _files(root):
            tar.add(p, arcname=p.relative_to(root).as_posix(), recursive=False)
    return buf.getvalue()


def _client(settings: Settings) -> Any:
    from langsmith.sandbox import AsyncSandboxClient

    # the API key comes from LANGSMITH_API_KEY, the endpoint from LANGSMITH_ENDPOINT (the SDK's defaults)
    return AsyncSandboxClient(api_endpoint=settings.langsmith_sandbox_endpoint)


class LangSmithBox:
    def __init__(
        self,
        settings: Settings,
        workspace: Path,
        secrets: dict[str, str],
        client_factory: Callable[[Settings], Any] | None = None,
    ):
        self.settings, self.workspace, self.secrets = settings, workspace, secrets
        self._client_factory = client_factory or _client
        self._client: Any = None
        self._box: Any = None
        self._proxy_key: str | None = None
        self._files_key: str | None = None
        self._memory_mb: int | None = None
        self._lock = asyncio.Lock()

    async def _ensure(self, policy: SandboxPolicy) -> Any:
        config = proxy_config(policy, self.secrets)
        proxy_key, files = fingerprint(config), files_key(self.workspace)
        async with self._lock:
            if self._client is None:
                self._client = self._client_factory(self.settings)
            if self._box is not None and self._memory_mb != policy.memory_mb:
                await self._delete()  # the size of a sandbox is fixed when it is created
            if self._box is None:
                self._box = await self._client.create_sandbox(
                    snapshot=self.settings.langsmith_snapshot,
                    name=f"agentcanvas-{self.workspace.name.lower().replace('_', '-')}-{uuid.uuid4().hex[:8]}",
                    idle_ttl_seconds=self.settings.langsmith_idle_s,
                    delete_after_stop_seconds=600,
                    mem_bytes=policy.memory_mb * 1024 * 1024,
                    proxy_config=config,
                )
                self._proxy_key, self._files_key, self._memory_mb = proxy_key, None, policy.memory_mb
                await self._box.write("/sandbox/child.py", CHILD.read_bytes())
            elif self._proxy_key != proxy_key:  # egress or a secret changed: patch the proxy, keep the sandbox warm
                await self._client.update_sandbox(self._box.name, proxy_config=config)
                self._proxy_key = proxy_key
            if self._files_key != files:
                await self._box.write("/sandbox/ws.tgz", workspace_archive(self.workspace))
                res = await self._box.run("rm -rf /ws && mkdir -p /ws && tar -xzf /sandbox/ws.tgz -C /ws", timeout=60)
                if res.exit_code != 0:
                    raise RuntimeError(f"uploading the workspace failed: {res.stderr[-300:]}")
                self._files_key = files
            return self._box

    async def exchange(self, import_path: str, payload: bytes, policy: SandboxPolicy) -> tuple[str, str, int]:
        """Run the child for one call; returns (stdout, stderr, exit code)."""
        from langsmith.sandbox import CommandTimeoutError, ResourceNotFoundError, SandboxNotReadyError

        for retry in (False, True):
            box = self._box
            try:
                box = await self._ensure(policy)
                inp = f"/sandbox/in-{uuid.uuid4().hex}.json"
                await box.write(inp, payload)
                cmd = f"python3 -I /sandbox/child.py {shlex.quote(import_path)} < {inp}; rc=$?; rm -f {inp}; exit $rc"
                res = await box.run(cmd, timeout=math.ceil(policy.timeout_s), cwd="/ws")
                return res.stdout, res.stderr, res.exit_code
            except CommandTimeoutError:
                raise TimeoutError(f"{import_path} ran longer than {policy.timeout_s:g} s") from None
            except (ResourceNotFoundError, SandboxNotReadyError):
                if retry:
                    raise
                async with self._lock:  # stopped after its idle TTL, or deleted: start a fresh one, once
                    if self._box is box:  # unless another call already replaced it
                        self._box, self._files_key = None, None
        raise AssertionError("unreachable")

    async def _delete(self) -> None:
        box, self._box = self._box, None
        try:
            await self._client.delete_sandbox(box.name)
        except Exception:  # noqa: BLE001 - already gone (TTL) is fine
            pass

    async def aclose(self) -> None:
        async with self._lock:
            if self._box is not None:
                await self._delete()
            if self._client is not None:
                await self._client.aclose()
                self._client = None
