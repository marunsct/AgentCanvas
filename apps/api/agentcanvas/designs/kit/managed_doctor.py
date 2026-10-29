"""Is this machine ready to apply and run the agent? `make doctor` (or `python3 doctor.py`) checks everything.

The `ant` CLI and its version, a way to sign in to the Claude Platform, the bundle's files, the settings the custom
tools need (from .env), and the hosts they call. Each problem names the README section that fixes it. Only the
standard library is used, so this runs before anything is installed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KIT = json.loads((ROOT / "doctor.json").read_text(encoding="utf-8"))


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    section: str = ""
    warning: bool = False  # reported, but does not fail the doctor


def load_env(path: Path = ROOT / ".env") -> None:
    """KEY=VALUE lines of .env into the environment (values already set win)."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _version(text: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(x) for x in m.groups()) if m else ()


def _run(command: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return done.returncode, (done.stdout or done.stderr).strip()


def tool_checks() -> list[Check]:
    out = [
        Check(
            "python3", sys.version_info >= (3, 11), sys.version.split()[0] + " (3.11 or later)", "1-check-this-machine"
        )
    ]
    if not shutil.which("ant"):
        out.append(Check("ant", False, "not installed: the Anthropic CLI applies the files (ant apply)",
                         "1-check-this-machine"))  # fmt: skip
    else:
        code, text = _run(["ant", "--version"])
        have, need = _version(text), _version(KIT["ant_min"])
        ok = code == 0 and bool(have) and have >= need
        out.append(Check("ant", ok, f"{text.splitlines()[0] if text else 'installed'} (needs {KIT['ant_min']} or "
                         "later)", "1-check-this-machine"))  # fmt: skip
    uv = shutil.which("uv")
    missing = "not installed: make setup, test and run use it"
    out.append(Check("uv", True, "installed" if uv else missing, "1-check-this-machine", warning=not uv))
    return out


def sign_in_checks() -> list[Check]:
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return [Check("Anthropic key", True, "ANTHROPIC_API_KEY is set")]
    if shutil.which("ant") and _run(["ant", "auth", "status"])[0] == 0:
        return [Check("Anthropic key", True, "signed in with ant auth login")]
    return [Check("Anthropic key", False, "missing: run `ant auth login` or set ANTHROPIC_API_KEY in .env",
                  "1-check-this-machine")]  # fmt: skip


def file_checks() -> list[Check]:
    out = []
    for rel in KIT["files"]:
        path = ROOT / rel
        ok = path.is_file() and (not rel.endswith(".md") or path.read_text(encoding="utf-8").startswith("---\n"))
        out.append(Check(rel, ok, "ready" if ok else "missing or without its --- frontmatter", "files"))
    lock = ROOT / "claude-lock.json"
    applied = lock.is_file() and all(
        any(k.removeprefix("./") == rel for k in (json.loads(lock.read_text()).get("resources") or {}))
        for rel in KIT["files"]
    )
    out.append(Check("claude-lock.json", True, "applied" if applied else "not applied yet: run make plan, then make "
                     "apply", "2-create-the-agent", warning=not applied))  # fmt: skip
    return out


def settings_checks() -> list[Check]:
    return [
        Check(
            name,
            bool(os.environ.get(name)),
            "set" if os.environ.get(name) else "missing: a custom tool reads it (put it in .env)",
            "settings",
        )
        for name in KIT["settings"]
    ]


def host_checks() -> list[Check]:
    out = []
    for host in ["api.anthropic.com", *KIT["hosts"]]:
        try:
            socket.create_connection((host, 443), timeout=5).close()
            problem = None
        except OSError as exc:
            problem = str(exc) or type(exc).__name__
        why = "the Claude Platform" if host == "api.anthropic.com" else "a custom tool calls it from this machine"
        out.append(Check(f"reach {host}", problem is None, problem or why, "1-check-this-machine",
                         warning=problem is not None and host != "api.anthropic.com"))  # fmt: skip
    return out


def main(argv: list[str] | None = None) -> int:
    load_env()
    checks = tool_checks() + sign_in_checks() + file_checks() + settings_checks()
    if "--offline" not in (argv if argv is not None else sys.argv[1:]):
        checks += host_checks()
    print(f"Checking the Claude Managed Agents bundle of {KIT['agent']}:")
    for c in checks:
        mark = "ok" if c.ok and not c.warning else ("!!" if c.warning else "XX")
        where = f"  (README: {c.section})" if c.section and (not c.ok or c.warning) else ""
        print(f"  [{mark}] {c.name}: {c.detail}{where}")
    failed = [c for c in checks if not c.ok]
    print("Ready." if not failed else f"{len(failed)} problem(s) to fix first.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
