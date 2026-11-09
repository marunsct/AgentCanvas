"""Settings > Server: the effective server settings for admins, read-only, with the variable that sets each; never
a secret's value."""

from __future__ import annotations

from test_roles import act_as

from agentcanvas.api.deps import current_user


async def test_admins_see_the_server_settings_and_no_secret(app, api_client):
    app.state.settings.oidc_issuer = None
    rows = (await api_client.get("/v1/server/settings")).json()
    by = {r["label"]: r for r in rows}
    assert by["Where it runs"]["env"] == "AGENTCANVAS_SCRIPT_EXECUTION" and by["Environment"]["value"] == "test"
    assert by["Database"]["value"].startswith(("SQLite", "PostgreSQL"))
    assert by["Mail server for approval e-mails"]["value"] == "none"
    app.state.settings.smtp_host, app.state.settings.smtp_from = "mail.example.com", "ac@example.com"
    app.state.settings.smtp_password = "smtp-password-not-shown"
    rows = (await api_client.get("/v1/server/settings")).json()
    by = {r["label"]: r for r in rows}
    assert by["Mail server for approval e-mails"]["value"] == "mail.example.com:587 (starttls)"
    text = str(rows)
    for secret in (app.state.settings.secret_key, app.state.settings.database_url, "smtp-password-not-shown"):
        assert secret not in text
    try:
        for role in ("viewer", "reviewer", "builder"):
            act_as(app, role)
            assert (await api_client.get("/v1/server/settings")).status_code == 403, role
    finally:
        app.dependency_overrides.pop(current_user, None)
