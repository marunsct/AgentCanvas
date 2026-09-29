"""A project's sandbox policy (FR-X-08, basic): `sandbox.json` at the workspace root.

    {
      "egress": ["api.github.com:443"],
      "inject": [{"name": "github", "hosts": ["api.github.com"],
                  "headers": {"Authorization": "Bearer {{ secret.GITHUB_TOKEN }}"}}],
      "timeout_s": 30,
      "memory_mb": 512
    }

`egress` is an allow-list (default: no network). `inject` rules are applied by the LangSmith auth proxy outside the
sandbox, so secret values never enter it; their hosts are allowed on port 443 automatically. The local sandboxes
(subprocess, docker) have no network at all and only apply the limits.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

POLICY_FILE = "sandbox.json"
SECRET_REF = re.compile(r"\{\{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_HOST = re.compile(r"^(\*\.)?[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$")


class PolicyError(ValueError):
    """`sandbox.json` is not valid JSON or does not match the policy schema."""


class HeaderRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    hosts: list[str] = Field(min_length=1, description="hostnames, or `*.example.com`; no scheme, port or path")
    paths: list[str] = Field(default_factory=list, description="path prefixes to match (empty: every path)")
    headers: dict[str, str] = Field(min_length=1, description="values may use {{ secret.NAME }}")


class SandboxPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    egress: list[str] = Field(default_factory=list, description="allow-list: host, host:port, *.domain; [] = offline")
    inject: list[HeaderRule] = Field(default_factory=list)
    timeout_s: float = Field(default=30.0, gt=0, le=900)
    memory_mb: int = Field(default=512, ge=64, le=16384)

    def secret_names(self) -> set[str]:
        return {name for rule in self.inject for value in rule.headers.values() for name in SECRET_REF.findall(value)}

    def allow_list(self) -> list[str]:
        """Egress entries plus port 443 for every host an inject rule targets (they must be reachable)."""
        out = list(dict.fromkeys(self.egress))
        for rule in self.inject:
            for host in rule.hosts:
                if host not in out and f"{host}:443" not in out:
                    out.append(f"{host}:443")
        return out


DEFAULT = SandboxPolicy()


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


def check_policy(root: Path, mode: str, secrets: set[str]) -> list[dict[str, str]]:
    """Workspace problems for the policy: E082 invalid file, E083 unknown secret, W082/W074 egress it cannot get."""
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
    wants_network = bool(policy.egress or policy.inject)
    if wants_network and mode != "langsmith":
        how = "run in the API process with its network" if mode == "inprocess" else "have no network at all"
        out.append({
            "path": POLICY_FILE, "code": "W082",
            "message": f"egress and inject rules need script execution 'langsmith'; '{mode}' scripts {how}",
        })  # fmt: skip
    if any(e.split(":")[0] in ("*", "~.*", "~.+") for e in policy.egress):
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
