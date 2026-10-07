"""Smoke-check the curated MCP connector gallery.

Usage (from apps/api):
    uv run python scripts/smoke_mcp_gallery.py            # package checks + start keyless stdio servers
    uv run python scripts/smoke_mcp_gallery.py --live     # also send an MCP initialize to remote servers
    uv run python scripts/smoke_mcp_gallery.py --only time,fetch --no-start

For every connector it checks that the pinned source exists (npm / PyPI JSON API,
not deprecated or yanked; remote URLs must be https). Stdio connectors with
auth "none" and no secrets are started with fastmcp and their tools listed.
Exits 1 if any required check failed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GALLERY = Path(__file__).resolve().parents[1] / "agentcanvas" / "registry" / "mcp_gallery.json"
START_TIMEOUT = 120.0
HTTP_TIMEOUT = 30.0

VALID_CATEGORIES = {"Developer", "Productivity", "Data", "Web", "Files", "Utilities", "Payments", "Knowledge"}
VALID_PUBLISHERS = {"official", "vendor", "community"}
VALID_AUTH = {"none", "key", "oauth"}


class CheckError(Exception):
    pass


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "agentcanvas-smoke"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.load(resp)


def _secret_names(value) -> list[str]:
    """Secret names referenced in a headers/env mapping."""
    out = []
    for v in (value or {}).values():
        if isinstance(v, dict) and "$secret" in v:
            out.append(v["$secret"])
    return out


def check_shape(entry: dict) -> None:
    for key in (
        "id",
        "name",
        "category",
        "description",
        "publisher",
        "config",
        "secrets",
        "auth",
        "docs",
        "source",
        "verified",
    ):
        if key not in entry:
            raise CheckError(f"missing field {key!r}")
    if entry["category"] not in VALID_CATEGORIES:
        raise CheckError(f"bad category {entry['category']!r}")
    if entry["publisher"] not in VALID_PUBLISHERS:
        raise CheckError(f"bad publisher {entry['publisher']!r}")
    if entry["auth"] not in VALID_AUTH:
        raise CheckError(f"bad auth {entry['auth']!r}")
    cfg = entry["config"]
    declared = {s["name"] for s in entry["secrets"]}
    if cfg.get("transport") == "http":
        if not str(cfg.get("url", "")).startswith("https://"):
            raise CheckError("remote url is not https")
        used = _secret_names(cfg.get("headers"))
    elif cfg.get("transport") == "stdio":
        if not cfg.get("command") or not isinstance(cfg.get("args"), list):
            raise CheckError("stdio config needs command and args")
        used = _secret_names(cfg.get("env"))
    else:
        raise CheckError(f"bad transport {cfg.get('transport')!r}")
    missing = set(used) - declared
    if missing:
        raise CheckError(f"secrets used but not listed: {sorted(missing)}")


def check_source(entry: dict) -> str:
    source = entry["source"]
    kind, _, rest = source.partition(":")
    cfg = entry["config"]
    if kind == "npm":
        pkg, _, ver = rest.rpartition("@")
        if not pkg or not ver:
            raise CheckError(f"npm source must be pkg@version: {source}")
        if f"{pkg}@{ver}" not in cfg.get("args", []):
            raise CheckError("args do not pin the same npm version as source")
        url = "https://registry.npmjs.org/" + urllib.parse.quote(pkg, safe="@") + "/" + urllib.parse.quote(ver)
        try:
            meta = _get_json(url)
        except urllib.error.HTTPError as exc:
            raise CheckError(f"npm {pkg}@{ver}: HTTP {exc.code}") from exc
        if meta.get("version") != ver:
            raise CheckError(f"npm {pkg}: version {ver} not found")
        if meta.get("deprecated"):
            raise CheckError(f"npm {pkg}@{ver} is deprecated: {meta['deprecated'][:80]}")
        return f"npm {pkg}@{ver} ok"
    if kind == "pypi":
        pkg, _, ver = rest.partition("==")
        if not pkg or not ver:
            raise CheckError(f"pypi source must be pkg==version: {source}")
        if f"{pkg}=={ver}" not in cfg.get("args", []):
            raise CheckError("args do not pin the same PyPI version as source")
        try:
            meta = _get_json(f"https://pypi.org/pypi/{urllib.parse.quote(pkg)}/{urllib.parse.quote(ver)}/json")
        except urllib.error.HTTPError as exc:
            raise CheckError(f"pypi {pkg}=={ver}: HTTP {exc.code}") from exc
        files = meta.get("urls") or []
        if not files:
            raise CheckError(f"pypi {pkg}=={ver} has no files")
        if all(f.get("yanked") for f in files):
            raise CheckError(f"pypi {pkg}=={ver} is yanked")
        return f"pypi {pkg}=={ver} ok"
    if kind == "remote":
        if not rest.startswith("https://"):
            raise CheckError("remote source is not https")
        if cfg.get("transport") != "http" or cfg.get("url") != rest:
            raise CheckError("remote source does not match config url")
        return "remote https"
    raise CheckError(f"unknown source kind {kind!r}")


def can_start(entry: dict) -> bool:
    cfg = entry["config"]
    return cfg.get("transport") == "stdio" and entry["auth"] == "none" and not entry["secrets"]


async def start_and_list(entry: dict) -> int:
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    cfg = entry["config"]
    env = dict(os.environ)  # keep PATH, proxy and CA settings so npx/uvx can download
    for k, v in (cfg.get("env") or {}).items():
        if isinstance(v, str):
            env[k] = v
    # Server stderr goes to a temp file so the report stays one line per connector;
    # its tail is shown only when the start fails.
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as log:
        transport = StdioTransport(command=cfg["command"], args=list(cfg["args"]), env=env, log_file=log)
        client = Client(transport, timeout=START_TIMEOUT, init_timeout=START_TIMEOUT)

        async def run() -> int:
            async with client:
                tools = await client.list_tools()
                return len(tools)

        try:
            return await asyncio.wait_for(run(), timeout=START_TIMEOUT)
        except Exception as exc:
            log.flush()
            log.seek(0)
            tail = " | ".join(line.strip() for line in log.read().splitlines()[-3:] if line.strip())
            stderr = f" [stderr: {tail[:200]}]" if tail else ""
            raise CheckError(f"start failed: {type(exc).__name__}: {str(exc)[:120]}{stderr}") from exc


def live_probe(entry: dict) -> str:
    """POST an MCP initialize. 2xx = alive; 401/403 = alive, needs a key."""
    url = entry["config"]["url"]
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "agentcanvas-smoke", "version": "1"},
            },
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "agentcanvas-smoke",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return f"live {resp.status}"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return f"live {exc.code} (alive, needs a key)"
        raise CheckError(f"live: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CheckError(f"live: unreachable ({getattr(exc, 'reason', exc)})") from exc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="also send an MCP initialize to remote servers")
    ap.add_argument("--no-start", action="store_true", help="skip starting stdio servers")
    ap.add_argument("--only", default="", help="comma-separated connector ids")
    args = ap.parse_args()

    data = json.loads(GALLERY.read_text())
    if data.get("version") != 1:
        print(f"✗ gallery version is {data.get('version')!r}, expected 1")
        return 1
    entries = data["connectors"]
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    if only:
        entries = [e for e in entries if e.get("id") in only]

    have = {cmd: shutil.which(cmd) is not None for cmd in ("npx", "uvx")}
    tools = " ".join(f"{cmd}={'yes' if ok else 'no'}" for cmd, ok in have.items())
    print(f"gallery: {len(data['connectors'])} connectors; {tools}")

    failed = 0
    seen: set[str] = set()
    for entry in entries:
        eid = entry.get("id", "?")
        notes: list[str] = []
        ok = True
        try:
            if eid in seen:
                raise CheckError("duplicate id")
            seen.add(eid)
            check_shape(entry)
            notes.append(check_source(entry))
            cfg = entry["config"]
            if cfg["transport"] == "stdio":
                if args.no_start:
                    notes.append("start skipped (--no-start)")
                elif not can_start(entry):
                    notes.append("start skipped: needs a secret")
                elif not have.get(cfg["command"], shutil.which(cfg["command"]) is not None):
                    notes.append(f"skipped: {cfg['command']} not installed")
                else:
                    try:
                        count = asyncio.run(start_and_list(entry))
                    except CheckError:
                        raise
                    except Exception as exc:  # report any start failure
                        raise CheckError(f"start failed: {type(exc).__name__}: {str(exc)[:160]}") from exc
                    if count < 1:
                        raise CheckError("server started but listed 0 tools")
                    notes.append(f"started, {count} tools")
            elif args.live:
                notes.append(live_probe(entry))
            if entry["auth"] == "oauth":
                notes.append("oauth sign-in")
        except CheckError as exc:
            ok = False
            notes.append(str(exc))
        except Exception as exc:  # noqa: BLE001
            ok = False
            notes.append(f"{type(exc).__name__}: {str(exc)[:160]}")
        if not ok:
            failed += 1
        print(f"{'✓' if ok else '✗'} {eid:<20} {'; '.join(notes)}", flush=True)

    print(f"{len(entries) - failed}/{len(entries)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
