"""docs/user-guide/server-settings.md lists every server setting with its comment from settings.py: adding a setting
without regenerating the page (scripts/export_settings_reference.py) fails here."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import export_settings_reference as ref


def test_the_settings_page_is_current():
    assert ref.PAGE.read_text() == ref.page(), "run: uv run python scripts/export_settings_reference.py"


def test_every_setting_is_described():
    assert [v for v, _, text in ref.rows() if not text] == []


def test_a_setting_s_variable_and_comment_are_read_from_the_source():
    rows = {v: (d, t) for v, d, t in ref.rows()}
    assert rows["DATABASE_URL"][1].startswith("SQLAlchemy URL")  # its own alias, not AGENTCANVAS_DATABASE_URL
    assert rows["AGENTCANVAS_AUDIT_RETENTION_DAYS"] == ("400", rows["AGENTCANVAS_AUDIT_RETENTION_DAYS"][1])
    assert "(change it)" in rows["AGENTCANVAS_SECRET_KEY"][0]
