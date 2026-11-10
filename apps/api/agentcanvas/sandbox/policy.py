"""A project's sandbox policy (FR-X-08, basic): `sandbox.json` at the workspace root.

    {
      "egress": ["api.github.com:443"],
      "inject": [{"name": "github", "hosts": ["api.github.com"],
                  "headers": {"Authorization": "Bearer {{ secret.GITHUB_TOKEN }}"}}],
      "timeout_s": 30,
      "memory_mb": 512,
      "mcp": {"memory_mb": 1024, "idle_s": 300, "lifetime_s": 1800}
    }

`egress` is an allow-list (default: no network). The `docker` and `remote` modes enforce it through the egress proxy
(`sandbox.egress`), LangSmith through its own; `inprocess` and `subprocess` cannot (an error in staging and
production, a warning in development). `inject` rules are applied by the LangSmith auth proxy outside the sandbox, so
secret values never enter it; their hosts are allowed on port 443 automatically. Elsewhere they are not applied.
`mcp` limits the local (stdio) app connectors the sandbox runs (`sandbox.mcp`): each gets the same egress list.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentcanvas.sandbox.egress import _never

POLICY_FILE = "sandbox.json"
SECRET_REF = re.compile(r"\{\{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
# host, host:port, *.domain, an IPv4 or [IPv6] address with or without a port; "*" and LangSmith's "~regex" pass
_EGRESS = re.compile(r"^(\*|~.+|(\*\.)?[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?|\[[0-9A-Fa-f:.]+\])(:\d{1,5})?$")
_HOST = re.compile(r"^(\*\.)?[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$")


class PolicyError(ValueError):
    """`sandbox.json` is not valid JSON or does not match the policy schema."""


class HeaderRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    hosts: list[str] = Field(min_length=1, description="hostnames, or `*.example.com`; no scheme, port or path")
    paths: list[str] = Field(default_factory=list, description="path prefixes to match (empty: every path)")
    headers: dict[str, str] = Field(min_length=1, description="values may use {{ secret.NAME }}")


class McpPolicy(BaseModel):
    """Limits of a local (stdio) app connector's sandbox: it lives for a run, or a conversation, within these."""

    model_config = ConfigDict(extra="forbid")

    memory_mb: int = Field(default=1024, ge=128, le=16384, description="memory of the connector's processes")
    idle_s: float = Field(default=300, gt=0, le=3600, description="stopped after this long without a tool call")
    lifetime_s: float = Field(default=1800, gt=0, le=4 * 3600, description="stopped after this long in any case")


class SandboxPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    egress: list[str] = Field(
        default_factory=list, max_length=100, description="allow-list: host, host:port, *.domain; [] = offline"
    )
    inject: list[HeaderRule] = Field(default_factory=list)
    timeout_s: float = Field(default=30.0, gt=0, le=900)
    memory_mb: int = Field(default=512, ge=64, le=16384)
    mcp: McpPolicy = Field(default_factory=McpPolicy)

    def secret_names(self) -> set[str]:
        return {name for rule in self.inject for value in rule.headers.values() for name in SECRET_REF.findall(value)}

    def langsmith_only(self) -> list[str]:
        """Entries only LangSmith's proxy understands ("*" and "~regex"): the egress proxy of the docker and remote
        modes matches hosts and *.domains, so with it they would silently allow nothing (RT-17)."""
        return [e for e in self.egress if e.strip().startswith(("*:", "~")) or e.strip() == "*"]

    def allow_list(self) -> list[str]:
        """Egress entries plus port 443 for every host an inject rule targets (they must be reachable)."""
        out = list(dict.fromkeys(self.egress))
        for rule in self.inject:
            for host in rule.hosts:
                if host not in out and f"{host}:443" not in out:
                    out.append(f"{host}:443")
        return out


DEFAULT = SandboxPolicy()


def _metadata(entry: str) -> bool:
    """An entry naming a link-local (169.254.x, fe80::) or cloud metadata address: the egress proxy never connects
    there, so the list is refused rather than failing at the first call."""
    host = entry.strip()
    if host.startswith("["):  # [IPv6] or [IPv6]:port
        host = host[1:].partition("]")[0]
    elif host.count(":") == 1:  # IPv4:port or name:port
        host = host.partition(":")[0]
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return _never(host)


def parse_policy(text: str) -> SandboxPolicy:
    try:
        raw = json.loads(text or "{}")
    except ValueError as exc:
        raise PolicyError(f"{POLICY_FILE} is not valid JSON: {exc}") from None
    try:
        policy = SandboxPolicy.model_validate(raw)
    except ValidationError as exc:
        err = exc.errors()[0]
        where = ".".join(str(p) for p in err["loc"]) or "(root)"
        raise PolicyError(f"{POLICY_FILE}: {where}: {err['msg']}") from None
    bad = [e for e in policy.egress if not _EGRESS.match(e.strip())]
    if bad:
        raise PolicyError(f"{POLICY_FILE}: egress: '{bad[0]}' is not a host, host:port or *.domain (no scheme or path)")
    unreachable = [e for e in policy.egress if _metadata(e)]
    if unreachable:
        raise PolicyError(
            f"{POLICY_FILE}: egress: '{unreachable[0]}' is a link-local or cloud metadata address, which no sandbox "
            "reaches"
        )
    for rule in policy.inject:
        bad = [h for h in rule.hosts if not _HOST.match(h)]
        if bad:
            raise PolicyError(f"{POLICY_FILE}: inject '{rule.name}': '{bad[0]}' is not a hostname")
    return policy


_CACHE: dict[Path, tuple[tuple[int, int], SandboxPolicy]] = {}


def load_policy(root: Path) -> SandboxPolicy:
    """The workspace's policy (the defaults when there is no `sandbox.json`); raises PolicyError when invalid."""
    path = root / POLICY_FILE
    if not path.is_file():
        return DEFAULT
    st = path.stat()
    key = (st.st_mtime_ns, st.st_size)
    cached = _CACHE.get(path)
    if cached is None or cached[0] != key:
        cached = (key, parse_policy(path.read_text()))
        _CACHE[path] = cached
    return cached[1]


ENFORCING = ("docker", "remote", "langsmith")  # the modes that give code exactly its egress list


def check_policy(root: Path, mode: str, secrets: set[str], env: str = "dev") -> list[dict[str, str]]:
    """Workspace problems for the policy: E082 invalid file, E083 unknown secret, E084/W082 an egress list this
    server cannot enforce (error in staging and production, where such code is refused), W083 inject rules it
    cannot apply, E085 "*" or "~regex" entries the egress proxy (docker, remote) cannot match, W074 egress open to
    every host (LangSmith)."""
    if not (root / POLICY_FILE).is_file():
        return []
    try:
        policy = load_policy(root)
    except PolicyError as exc:
        return [{"path": POLICY_FILE, "code": "E082", "message": str(exc)}]
    out = [
        {"path": POLICY_FILE, "code": "E083", "message": f"inject uses secret '{name}', which this project lacks"}
        for name in sorted(policy.secret_names() - secrets)
    ]
    if policy.allow_list() and mode not in ENFORCING:
        how = "run in the API process with its network" if mode == "inprocess" else "have no network at all"
        strict = env in ("staging", "prod")
        out.append({
            "path": POLICY_FILE, "code": "E084" if strict else "W082",
            "message": f"script execution '{mode}' cannot restrict network access ('{mode}' scripts {how})"
            + ("; this server refuses to run code with an egress list" if strict else "")
            + ": use docker, remote or langsmith",
        })  # fmt: skip
    if policy.inject and mode != "langsmith":
        out.append({
            "path": POLICY_FILE, "code": "W083",
            "message": f"inject rules are applied by LangSmith's proxy only; with '{mode}' requests go out without "
            "those headers",
        })  # fmt: skip
    if mode in ("docker", "remote") and (only := policy.langsmith_only()):
        out.append({
            "path": POLICY_FILE, "code": "E085",
            "message": f"egress entry '{only[0]}' is LangSmith's syntax; with '{mode}' the egress proxy matches "
            "hosts, host:port and *.domain only, so this code is refused: list the hosts needed",
        })  # fmt: skip
    elif mode == "langsmith" and any(e.split(":")[0] in ("*", "~.*", "~.+") for e in policy.egress):
        out.append({"path": POLICY_FILE, "code": "W074", "message": "egress allows every host; list the hosts needed"})
    return out


def proxy_config(policy: SandboxPolicy, secrets: dict[str, str]) -> dict[str, Any]:
    """The LangSmith sandbox `proxy_config`: header rules (secrets as write-only `opaque` values) + allow-list."""
    rules = []
    for rule in policy.inject:
        headers = []
        for name, value in rule.headers.items():
            refs = SECRET_REF.findall(value)
            missing = [r for r in refs if r not in secrets]
            if missing:
                raise PolicyError(f"inject '{rule.name}' uses secret '{missing[0]}', which this project lacks")
            resolved = SECRET_REF.sub(lambda m: secrets[m.group(1)], value)
            headers.append({"name": name, "type": "opaque" if refs else "plaintext", "value": resolved})
        rules.append({"name": rule.name, "match_hosts": rule.hosts, "match_paths": rule.paths, "headers": headers})
    return {"rules": rules, "access_control": {"allow_list": policy.allow_list()}}


def fingerprint(config: dict[str, Any]) -> str:
    """Changes when the proxy config (including secret values) changes; never reveals them."""
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]
