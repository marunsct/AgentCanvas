"""Framework versions: which pins are behind, and moving one pin everywhere it is written.

    uv run python scripts/framework_versions.py              # every pinned framework package: pinned, latest, changelog
    uv run python scripts/framework_versions.py --json       # the same, for a script or a CI job
    uv run python scripts/framework_versions.py bump crewai 1.15.39   # rewrite every place `crewai` is pinned
    uv run python scripts/framework_versions.py requirements          # rewrite infra/frameworks/requirements.txt

A framework's version is written in several places (exports, CI, the image, docs, code generators, manifests); see
tests/test_framework_pins.py, which fails when they disagree. `bump` rewrites them all and then that test proves it.
Upgrading is more than the pin: docs/agent-canvas-spec/build-guide/16-framework-upgrades.md is the checklist.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
API = ROOT / "apps" / "api"
MASTRA_PACKAGE = API / "agentcanvas" / "adapters" / "mastra" / "package.json"
PIN = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([\w.]+)$")
# where each package says what changed (the official source to read before bumping)
CHANGELOGS = {
    "openai-agents": "https://github.com/openai/openai-agents-python/releases",
    "crewai": "https://github.com/crewAIInc/crewAI/releases",
    "google-adk": "https://github.com/google/adk-python/blob/main/CHANGELOG.md",
    "litellm": "https://github.com/BerriAI/litellm/releases",
    "pydantic-ai-slim": "https://github.com/pydantic/pydantic-ai/releases",
    "agno": "https://github.com/agno-agi/agno/releases",
    "openai": "https://github.com/openai/openai-python/blob/main/CHANGELOG.md",
    "strands-agents": "https://github.com/strands-agents/sdk-python/releases",
    "llama-index-workflows": "https://github.com/run-llama/workflows-py/releases",
    "llama-index-core": "https://github.com/run-llama/llama_index/blob/main/CHANGELOG.md",
    "llama-index-llms-openai-like": "https://github.com/run-llama/llama_index/blob/main/CHANGELOG.md",
    "agent-framework-core": "https://github.com/microsoft/agent-framework/releases",
    "agent-framework-openai": "https://github.com/microsoft/agent-framework/releases",
    "claude-agent-sdk": "https://github.com/anthropics/claude-agent-sdk-python/blob/main/CHANGELOG.md",
    "pyagentspec": "https://github.com/oracle/agent-spec/releases",
    "wayflowcore": "https://github.com/oracle/wayflow/releases",
    "@mastra/core": "https://github.com/mastra-ai/mastra/releases",
}
# files a pin is written in besides `pkg==version` (a code generator's constant, a manifest's "framework" line)
REQUIREMENTS = ROOT / "infra" / "frameworks" / "requirements.txt"
TEXT_SUFFIXES = {".py", ".json", ".yml", ".yaml", ".md", ".sh", ".toml", ".txt", ".ts", ".tmpl", ".j2"}
SKIP_DIRS = {"node_modules", ".venv", ".frameworks", ".git", "dist", "site", "test-results", ".ci-tmp"}
SKIP_FILES = {"CHANGELOG.md", "uv.lock", "pnpm-lock.yaml", "package-lock.json", "decisions.md"}  # history and locks


@dataclass
class Row:
    package: str
    pinned: str
    latest: str | None
    released: str | None
    requires_python: str | None
    behind: bool
    changelog: str


def pins(spec: str) -> dict[str, str]:
    out = {}
    for token in spec.split():
        m = PIN.match(token)
        if m:
            out[m.group(1).lower().replace("_", "-")] = m.group(2)
    return out


def pinned() -> dict[str, str]:
    """Every framework package exports install, at its one version (the source tests/test_framework_pins.py checks
    every other place against), and Mastra's npm package."""
    sys.path.insert(0, str(API))
    from agentcanvas import bridges
    from agentcanvas.designs import AGENT_FRAMEWORKS

    out: dict[str, str] = {}
    for spec in [*(p for f in AGENT_FRAMEWORKS.values() for p in f.packages),
                 *(p for t in bridges.TARGETS.values() for p in t.packages)]:  # fmt: skip
        out.update(pins(spec))
    out["@mastra/core"] = json.loads(MASTRA_PACKAGE.read_text())["dependencies"]["@mastra/core"]
    return out


def _stable(version: str) -> bool:
    return not re.search(r"(a|b|rc|dev|alpha|beta|pre|next)", version, re.I)


def latest(package: str) -> tuple[str | None, str | None, str | None]:
    """(newest stable version, its release date, its requires-python) from PyPI or npm; Nones if unreachable."""
    try:
        if package.startswith("@"):
            with urllib.request.urlopen(f"https://registry.npmjs.org/{package}", timeout=15) as r:
                data = json.load(r)
            version = data["dist-tags"]["latest"]
            return version, data.get("time", {}).get(version, "")[:10], None
        with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/json", timeout=15) as r:
            data = json.load(r)
        stable = [v for v, files in data["releases"].items() if files and _stable(v)]
        version = max(stable, key=_key) if stable else data["info"]["version"]
        files = data["releases"].get(version) or [{}]
        return version, (files[0].get("upload_time") or "")[:10], data["info"].get("requires_python")
    except Exception:  # noqa: BLE001 - offline: say so in the table, do not fail
        return None, None, None


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.+-]", version))


def report() -> list[Row]:
    rows = []
    for package, version in sorted(pinned().items()):
        newest, released, python = latest(package)
        behind = newest is not None and _key(newest) > _key(version)
        rows.append(Row(package, version, newest, released, python, behind, CHANGELOGS.get(package, "")))
    return rows


def _files(root: Path) -> list[Path]:
    out = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in TEXT_SUFFIXES and path.name not in SKIP_FILES:
            if not SKIP_DIRS.intersection(path.relative_to(root).parts):
                out.append(path)
    return out


def bump(package: str, old: str, new: str, root: Path = ROOT) -> list[Path]:
    """Rewrite every place `package` is pinned at `old` to `new`: `pkg==old` (with or without extras), a manifest's
    "framework": "pkg old", a code generator's `*_VERSION = "old"` next to the package's name, and Mastra's
    package.json. Returns the files changed. History (CHANGELOG, decisions) and lockfiles are left alone."""
    name = re.escape(package)
    rules = [
        (re.compile(rf"(?i)(\b{name}(?:\[[^\]]*\])?==){re.escape(old)}\b"), rf"\g<1>{new}"),
        (re.compile(rf'("framework":\s*"{name} ){re.escape(old)}"'), "\\g<1>" + new + '"'),
        (re.compile(rf'("{name}":\s*"){re.escape(old)}"'), "\\g<1>" + new + '"'),
        (re.compile(rf"(`{name}(?:\[[^\]]*\])?==){re.escape(old)}`"), rf"\g<1>{new}`"),
    ]
    constant = re.compile(rf'^([A-Z_]*VERSION\s*=\s*"){re.escape(old)}"', re.M)
    changed = []
    for path in _files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        out = text
        for rule, repl in rules:
            out = rule.sub(repl, out)
        # a constant holding just the version: only in a file that names this package
        if constant.search(out) and re.search(rf"\b{name}\b", out, re.I):
            out = constant.sub("\\g<1>" + new + '"', out)
        if out != text:
            path.write_text(out, encoding="utf-8")
            changed.append(path)
    return changed


def requirements_text() -> str:
    """infra/frameworks/requirements.txt: every Python framework pin in one plain file, so Dependabot (which cannot
    read pins written in our code) opens a pull request when one has a new release. That pull request fails
    tests/test_framework_versions.py until `bump` moves the pin everywhere, which is the point: it is the reminder."""
    lines = [
        "# Every Python framework package AgentCanvas exports and tests, at its one pinned version.",
        "# Generated by apps/api/scripts/framework_versions.py requirements; do not edit by hand.",
        "# Dependabot watches this file. To take one of its updates: framework_versions.py bump <package> <version>,",
        "# then follow docs/agent-canvas-spec/build-guide/16-framework-upgrades.md.",
    ]
    lines += [f"{p}=={v}" for p, v in sorted(pinned().items()) if not p.startswith("@")]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    sub = parser.add_subparsers(dest="cmd")
    b = sub.add_parser("bump", help="rewrite every place a package is pinned")
    b.add_argument("package")
    b.add_argument("version")
    sub.add_parser("requirements", help="rewrite infra/frameworks/requirements.txt from the pins")
    args = parser.parse_args(argv)
    if args.cmd == "requirements":
        REQUIREMENTS.parent.mkdir(parents=True, exist_ok=True)
        REQUIREMENTS.write_text(requirements_text())
        print(REQUIREMENTS.relative_to(ROOT))
        return 0
    if args.cmd == "bump":
        current = pinned().get(args.package.lower())
        if current is None:
            print(f"{args.package} is not a pinned framework package", file=sys.stderr)
            return 2
        changed = bump(args.package.lower(), current, args.version)
        for path in changed:
            print(path.relative_to(ROOT))
        REQUIREMENTS.write_text(requirements_text())
        print(f"{args.package} {current} -> {args.version} in {len(changed)} files; now run the checklist in "
              "docs/agent-canvas-spec/build-guide/16-framework-upgrades.md")  # fmt: skip
        return 0
    rows = report()
    if args.json:
        print(json.dumps([asdict(r) for r in rows], indent=2))
        return 0
    print(f"{'package':32} {'pinned':12} {'latest':12} {'released':11} changelog")
    for r in rows:
        mark = "  <- behind" if r.behind else ""
        print(f"{r.package:32} {r.pinned:12} {r.latest or '?':12} {r.released or '':11} {r.changelog}{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
