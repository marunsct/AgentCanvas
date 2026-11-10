"""Required multi-factor sign-in (E-8): what the identity provider's token must show about how the person signed in.

Two levels, both read from the sign-in token's `acr` (how strongly the person was authenticated, a value the provider
defines) and `amr` (the methods used, RFC 8176: `mfa`, `otp`, `hwk`, ...):

- the server's requirement, `AGENTCANVAS_OIDC_REQUIRED_ACR` / `AGENTCANVAS_OIDC_REQUIRED_AMR`, for every person;
- an organisation's `require_mfa` (Settings > Organisation > Security), for its members while they act in it, met by
  what the server counts as multi-factor: `AGENTCANVAS_OIDC_MFA_ACR` / `AGENTCANVAS_OIDC_MFA_AMR` (`mfa` by default).

A token meets a requirement when its `acr` is one of the listed values or its `amr` has one of the listed methods.
One that does not gets a 401 asking for a stronger sign-in (RFC 9470: `WWW-Authenticate: Bearer
error="insufficient_user_authentication"`, with the `acr_values` to ask for), which the web app answers by signing in
again at the identity provider with `prompt=login` and those `acr_values`. Personal API tokens do not pass through the
identity provider and are not affected; they are made from a sign-in that met the requirement.
"""

from __future__ import annotations

from typing import Any

from agentcanvas.errors import Unauthorized
from agentcanvas.settings import Settings

KEY = "require_mfa"  # in `organization.settings`


class MfaRequired(Unauthorized):
    type_ = "https://agentcanvas.dev/errors/mfa-required"


def meets(claims: dict[str, Any], acr: list[str], amr: list[str]) -> bool:
    """Whether the token's `acr` is one of `acr` or its `amr` names one of `amr` (True when both lists are empty)."""
    if not acr and not amr:
        return True
    methods = claims.get("amr")
    shown = {methods} if isinstance(methods, str) else {str(m) for m in methods or [] if isinstance(m, str | int)}
    return (claims.get("acr") is not None and str(claims.get("acr")) in acr) or bool(shown & set(amr))


def _refuse(detail: str, acr: list[str]) -> MfaRequired:
    values = " ".join(acr) or None
    challenge = 'Bearer error="insufficient_user_authentication", error_description="' + detail.replace('"', "'") + '"'
    if values:
        challenge += f', acr_values="{values}"'
    return MfaRequired(detail, code="mfa_required", acr_values=values, challenge=challenge)


def check_server(settings: Settings, claims: dict[str, Any]) -> None:
    """The server's requirement, for every sign-in."""
    acr, amr = settings.oidc_required_acr, settings.oidc_required_amr
    if not meets(claims, acr, amr):
        raise _refuse("this server needs a stronger sign-in (such as a second factor): sign in again", acr)


def org_requires(org_settings: dict[str, Any] | None) -> bool:
    return bool((org_settings or {}).get(KEY))


def is_mfa(settings: Settings, claims: dict[str, Any]) -> bool:
    """Whether the sign-in counts as multi-factor on this server."""
    acr, amr = settings.oidc_mfa_acr, settings.oidc_mfa_amr
    return bool(acr or amr) and meets(claims, acr, amr)


def check_org(settings: Settings, org_settings: dict[str, Any] | None, claims: dict[str, Any]) -> None:
    """An organisation that requires multi-factor sign-in, for its members while they act in it."""
    if org_requires(org_settings) and not is_mfa(settings, claims):
        raise _refuse(
            "this organisation requires multi-factor sign-in: sign in again with a second factor", settings.oidc_mfa_acr
        )
