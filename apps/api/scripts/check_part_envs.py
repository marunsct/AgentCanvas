"""Check which agent frameworks need an environment of their own in a mixed-flow export (S28 CI).

`python scripts/check_part_envs.py` resolves each framework's pinned packages together with the LangGraph host's
(with `uv pip compile`, nothing is installed) and fails when `designs.AGENT_FRAMEWORKS[...].own_env` disagrees: a
framework marked as sharing the host must resolve with it, and one marked `own_env` must not (else it could share).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

from agentcanvas import designs
from agentcanvas.adapters.langchain.packager import PINNED, SERVICE

HOST = [*PINNED, *SERVICE, "langchain-openai", "langchain-anthropic", "openai", "anthropic", "pydantic"]


def resolves(packages: list[str]) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        req = Path(tmp) / "requirements.txt"
        req.write_text("\n".join(packages) + "\n")
        proc = subprocess.run(
            ["uv", "pip", "compile", "-q", "--python-version", "3.12", str(req), "-o", str(Path(tmp) / "out.txt")],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        because = [ln.strip(" ╰─▶") for ln in proc.stderr.splitlines() if "Because" in ln]
        return proc.returncode == 0, because[0] if because else ""


def main() -> int:
    host = [f"{d}=={metadata.version(d)}" for d in HOST]
    wrong = []
    for fw in designs.AGENT_FRAMEWORKS.values():
        ok, why = resolves([*host, *fw.packages])
        verdict = "shares the host" if ok else "needs its own environment"
        print(f"{fw.id:28} {verdict}" + (f"  ({why})" if why else ""))
        if ok == fw.own_env:
            wrong.append(fw.id)
    if wrong:
        print(f"own_env is wrong for: {', '.join(wrong)} (agentcanvas/designs/__init__.py)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
