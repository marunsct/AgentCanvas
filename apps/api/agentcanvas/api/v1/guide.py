"""The user guide, in the app (S15): the same Markdown the Copilot searches, so its answers can link to the section
they came from. Read-only, for anyone signed in."""

from __future__ import annotations

import re

from fastapi import APIRouter
from pydantic import BaseModel

from agentcanvas.copilot import docs
from agentcanvas.errors import NotFound

router = APIRouter(prefix="/guide", tags=["guide"])
PAGE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class GuidePageRef(BaseModel):
    page: str
    title: str


class GuidePage(GuidePageRef):
    markdown: str


def _pages() -> dict[str, str]:
    """page name -> Markdown, from the guide the Copilot reads (`AGENTCANVAS_DOCS_DIR`, else the packaged copy)."""
    base = docs.docs_dir()
    return {p.stem: p.read_text() for p in sorted(base.glob("*.md"))} if base else {}


def _title(page: str, markdown: str) -> str:
    first = next((ln for ln in markdown.splitlines() if ln.startswith("# ")), None)
    return first[2:].strip() if first else page.replace("-", " ").capitalize()


@router.get("", response_model=list[GuidePageRef])
async def list_guide() -> list[GuidePageRef]:
    """The guide's pages, the introduction first."""
    pages = _pages()
    order = sorted(pages, key=lambda p: (p != "index", _title(p, pages[p]).lower()))
    return [GuidePageRef(page=p, title=_title(p, pages[p])) for p in order]


@router.get("/{page}", response_model=GuidePage)
async def guide_page(page: str) -> GuidePage:
    pages = _pages()
    if not PAGE.match(page) or page not in pages:  # names come from the folder, never a path
        raise NotFound(f"no guide page '{page}'")
    return GuidePage(page=page, title=_title(page, pages[page]), markdown=pages[page])
