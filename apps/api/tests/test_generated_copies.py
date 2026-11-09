"""Copies the build makes of a source, compared with the source in the default suite: `make check` regenerated them
in the working tree but did not diff two of them, so a guide edit committed without `make schema` passed every
default gate and shipped a Copilot that answers from the old guide."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GUIDE = ROOT / "docs" / "user-guide"
COPILOT_GUIDE = ROOT / "apps" / "api" / "agentcanvas" / "copilot" / "knowledge" / "user-guide"
FIX = "regenerate with `make schema` (scripts/export_ir_schema.py) and commit the copies"


def test_the_copilots_user_guide_is_the_user_guide():
    pages = {p.name for p in GUIDE.glob("*.md")}
    copies = {p.name for p in COPILOT_GUIDE.glob("*.md")}
    assert pages == copies, f"pages missing from or extra in the Copilot's copy: {sorted(pages ^ copies)}; {FIX}"
    stale = sorted(name for name in pages if (GUIDE / name).read_bytes() != (COPILOT_GUIDE / name).read_bytes())
    assert not stale, f"the Copilot's copy differs from docs/user-guide: {stale}; {FIX}"


def test_the_web_apps_diagnostics_are_the_servers():
    source = (ROOT / "apps" / "api" / "agentcanvas" / "validation" / "help.json").read_bytes()
    assert (ROOT / "packages" / "ir-schema" / "diagnostics.json").read_bytes() == source, FIX
    web = ROOT / "apps" / "web" / "src" / "help" / "diagnostics.generated.json"
    assert web.read_bytes() == source, "regenerate with `make schema` then `cd apps/web && pnpm gen`, and commit"
