"""Workspace storage (M5-T1): one directory per project, a git repository, a file API that cannot escape it."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 1_000_000
CODE_DIRS = ("scripts", "tools", "nodes", "events")
HIDDEN = {".git", "__pycache__", ".venv", ".agentcanvas", ".ruff_cache"}
TEXT_SUFFIXES = {".py", ".json", ".md", ".txt", ".toml", ".yaml", ".yml", ".jinja", ".j2", ".gitignore"}


class WorkspaceError(ValueError):
    """Invalid path or content (mapped to 422 by the API)."""


SCAFFOLD: dict[str, str] = {
    "README.md": "# Workspace\n\nCode attached to this project's canvas:\n\n"
    "- `scripts/` — Python script nodes: `def run(state: dict) -> dict` (sync or async)\n"
    "- `tools/` — `@tool` functions; they appear in the palette under *Workspace tools*\n"
    "- `nodes/` — custom widgets: `@canvas_node(...)` from `agentcanvas.sdk`\n"
    '- `events/` — event handlers: `@on("type")` from `agentcanvas.sdk`; schemas in `events/schemas/`\n',
    ".gitignore": "__pycache__/\n.venv/\n.agentcanvas/\n",
    "scripts/__init__.py": "",
    "scripts/example.py": '"""A script node: return the channels you want to update."""\n\n\n'
    "def run(state: dict) -> dict:\n"
    '    text = str(state.get("text") or "")\n'
    '    return {"text": text.strip()}\n',
    "tools/__init__.py": "",
    "nodes/__init__.py": "",
    "events/__init__.py": "",
    "events/schemas/.keep": "",
}


@dataclass(frozen=True)
class FileInfo:
    path: str
    size: int
    modified: float


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()

    # ------------------------------------------------------------------ setup ----
    def ensure(self) -> Workspace:
        """Create the directory, the conventional layout and a git repository (idempotent)."""
        fresh = not self.root.exists()
        self.root.mkdir(parents=True, exist_ok=True)
        if fresh:
            for rel, text in SCAFFOLD.items():
                p = self.root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
        if not (self.root / ".git").exists():
            self._git("init", "-q")
            self._commit("create workspace")
        return self

    # ------------------------------------------------------------------ paths ----
    def resolve(self, rel: str) -> Path:
        """An absolute path inside the workspace; anything escaping it (or touching .git) is refused."""
        rel = rel.strip().lstrip("/")
        if not rel or "\x00" in rel:
            raise WorkspaceError("a file path is required")
        target = (self.root / rel).resolve()
        if target != self.root and self.root not in target.parents:
            raise WorkspaceError(f"'{rel}' is outside the workspace")
        if any(part in HIDDEN for part in target.relative_to(self.root).parts):
            raise WorkspaceError(f"'{rel}' is not editable")
        return target

    # ------------------------------------------------------------------ files ----
    def list(self) -> list[FileInfo]:
        out = []
        for p in sorted(self.root.rglob("*")):
            rel = p.relative_to(self.root)
            if p.is_file() and not any(part in HIDDEN for part in rel.parts):
                st = p.stat()
                out.append(FileInfo(rel.as_posix(), st.st_size, st.st_mtime))
        return out

    def read(self, rel: str) -> str:
        p = self.resolve(rel)
        if not p.is_file():
            raise FileNotFoundError(rel)
        if p.stat().st_size > MAX_FILE_BYTES:
            raise WorkspaceError(f"'{rel}' is larger than {MAX_FILE_BYTES} bytes")
        return p.read_text(errors="replace")

    def write(self, rel: str, text: str) -> FileInfo:
        p = self.resolve(rel)
        if p.suffix not in TEXT_SUFFIXES and p.name not in TEXT_SUFFIXES:
            raise WorkspaceError(f"'{p.suffix or p.name}' files cannot be edited here")
        if len(text.encode()) > MAX_FILE_BYTES:
            raise WorkspaceError(f"files are limited to {MAX_FILE_BYTES} bytes")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        self._commit(f"edit {p.relative_to(self.root).as_posix()}", p)
        st = p.stat()
        return FileInfo(p.relative_to(self.root).as_posix(), st.st_size, st.st_mtime)

    def delete(self, rel: str) -> None:
        p = self.resolve(rel)
        if not p.is_file():
            raise FileNotFoundError(rel)
        p.unlink()
        self._commit(f"delete {p.relative_to(self.root).as_posix()}", p)

    def revision(self) -> str:
        """Changes whenever workspace code changes (graphs built from old code are rebuilt)."""
        h = hashlib.sha256()
        for d in CODE_DIRS:
            base = self.root / d
            if base.is_dir():
                for p in sorted(base.rglob("*")):
                    if p.suffix in (".py", ".json") and "__pycache__" not in p.parts:
                        st = p.stat()
                        h.update(f"{p}:{st.st_mtime_ns}:{st.st_size}".encode())
        return h.hexdigest()[:16]

    def lint(self, rel: str) -> list[dict[str, Any]]:
        """ruff diagnostics for a Python file (M5-T2): [{line, column, end_line, end_column, code, message}]."""
        p = self.resolve(rel)
        if p.suffix != ".py":
            return []
        proc = subprocess.run(
            ["ruff", "check", "--output-format=json", "--no-cache", "--isolated", "--select", "E,F,W,B", str(p)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        try:
            found = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError:
            return []
        return [
            {
                "line": d["location"]["row"],
                "column": d["location"]["column"],
                "end_line": d["end_location"]["row"],
                "end_column": d["end_location"]["column"],
                "code": d.get("code") or "syntax",
                "message": d["message"],
            }
            for d in found
        ]

    # -------------------------------------------------------------------- git ----
    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {"GIT_AUTHOR_NAME": "AgentCanvas", "GIT_AUTHOR_EMAIL": "workspace@agentcanvas.local"}
        env |= {"GIT_COMMITTER_NAME": "AgentCanvas", "GIT_COMMITTER_EMAIL": "workspace@agentcanvas.local"}
        import os

        return subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True, check=False, env={**os.environ, **env}
        )

    def _commit(self, message: str, path: Path | None = None) -> None:
        """Best effort: every save is a commit (history, diffs and restore come in Sprint 8)."""
        self._git("add", "-A", str(path.relative_to(self.root)) if path else ".")
        self._git("commit", "-q", "-m", message, "--allow-empty")
