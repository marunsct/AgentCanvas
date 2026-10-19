"""OIDC bearer-token authentication (M7-T1).

The SPA signs in with authorization code + PKCE and sends the ID provider's access token as
`Authorization: Bearer <jwt>`. The API checks signature (JWKS, cached), issuer, audience and expiry,
then maps the token's subject to an `app_user` row, creating it on first sign-in.

`auth_mode = "dev"` keeps the seeded development admin for local work and tests; production
environments refuse to start in that mode (see `check_auth_settings`).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient, PyJWKSet

from agentcanvas.errors import Unauthorized
from agentcanvas.settings import Settings

ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]


@dataclass
class Identity:
    """Who the token says the caller is (claims already verified)."""

    subject: str
    email: str
    name: str | None
    email_verified: bool
    claims: dict[str, Any] = field(default_factory=dict, repr=False)  # all of them (Matura reads `tid`, `perms`)


DEV_SECRET_KEY = "dev-only-change-me"


def check_auth_settings(settings: Settings) -> None:
    """Fail fast on configurations that would leave the API open or unable to verify tokens."""
    if settings.auth_mode == "dev" and (settings.env in ("staging", "prod") or settings.edition == "hosted"):
        raise RuntimeError(
            "AGENTCANVAS_AUTH_MODE=dev gives every request admin rights; set AGENTCANVAS_AUTH_MODE=oidc "
            "with AGENTCANVAS_OIDC_ISSUER and AGENTCANVAS_OIDC_AUDIENCE for staging, prod or the hosted edition"
        )
    if settings.auth_mode in ("oidc", "matura") and not (settings.oidc_issuer and settings.oidc_audience):
        raise RuntimeError(
            f"AGENTCANVAS_AUTH_MODE={settings.auth_mode} needs AGENTCANVAS_OIDC_ISSUER and AGENTCANVAS_OIDC_AUDIENCE"
        )
    if settings.auth_mode == "matura" and settings.matura_url and not settings.matura_client_secret:
        raise RuntimeError("AGENTCANVAS_MATURA_URL needs AGENTCANVAS_MATURA_CLIENT_SECRET to read the change feed")
    production = settings.env in ("staging", "prod") or settings.edition == "hosted"
    if production and (settings.secret_key == DEV_SECRET_KEY or len(settings.secret_key) < 32):
        raise RuntimeError(
            "AGENTCANVAS_SECRET_KEY is the development default or shorter than 32 characters; set 64+ random "
            "characters for staging, prod or the hosted edition"
        )
    if settings.edition == "hosted" and settings.script_execution not in ("docker", "langsmith", "remote"):
        raise RuntimeError(
            "the hosted edition runs workspace scripts in a sandbox: set AGENTCANVAS_SCRIPT_EXECUTION=remote "
            "(the sandbox service), docker or langsmith"
        )
    if settings.script_execution == "remote" and not (settings.sandbox_url and settings.sandbox_token):
        raise RuntimeError(
            "AGENTCANVAS_SCRIPT_EXECUTION=remote needs AGENTCANVAS_SANDBOX_URL and AGENTCANVAS_SANDBOX_TOKEN"
        )


class Authenticator:
    """Verifies bearer JWTs against the issuer's signing keys.

    Keys come from `oidc_jwks_url`, or from the issuer's discovery document when that is unset.
    Tests pass `jwks` (a JWK Set dict) to skip the network.
    """

    def __init__(self, settings: Settings, jwks: dict[str, Any] | None = None):
        self.settings = settings
        self._static = PyJWKSet.from_dict(jwks) if jwks is not None else None
        self._client: PyJWKClient | None = None
        self._lock = asyncio.Lock()

    async def _jwks_client(self) -> PyJWKClient:
        async with self._lock:
            if self._client is None:
                url = self.settings.oidc_jwks_url or await self._discover_jwks_url()
                # only the JWK Set is cached, and it expires: a key the issuer drops stops verifying after the lifespan
                self._client = PyJWKClient(
                    url, cache_jwk_set=True, cache_keys=False, lifespan=self.settings.oidc_jwks_cache_s
                )
            return self._client

    async def _discover_jwks_url(self) -> str:
        issuer = (self.settings.oidc_issuer or "").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{issuer}/.well-known/openid-configuration")
                r.raise_for_status()
                return r.json()["jwks_uri"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise Unauthorized(f"cannot load the identity provider's signing keys: {exc}") from None

    async def _key(self, token: str) -> Any:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError:
            raise Unauthorized("malformed bearer token") from None
        if self._static is not None:
            for key in self._static.keys:
                if kid is None or key.key_id == kid:
                    return key.key
            raise Unauthorized("token signed with an unknown key")
        client = await self._jwks_client()
        try:  # PyJWKClient fetches with urllib: keep it off the event loop
            return (await asyncio.to_thread(client.get_signing_key_from_jwt, token)).key
        except jwt.PyJWTError as exc:
            raise Unauthorized(f"cannot find the token's signing key: {exc}") from None

    async def verify(self, token: str) -> Identity:
        key = await self._key(token)
        s = self.settings
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                audience=s.oidc_audience,
                issuer=s.oidc_issuer,
                leeway=s.oidc_leeway_s,
                options={"require": ["exp", "iss", "sub"]},
            )
        except jwt.ExpiredSignatureError:
            raise Unauthorized("token expired") from None
        except jwt.PyJWTError as exc:
            raise Unauthorized(f"invalid token: {exc}") from None
        email = claims.get(s.oidc_email_claim) or claims.get("email") or ""
        return Identity(
            subject=str(claims["sub"]),
            email=str(email),
            name=claims.get("name") or claims.get("preferred_username"),
            email_verified=_verified(claims.get("email_verified"), trust_absent=s.oidc_trust_email),
            claims=claims,
        )


def _verified(claim: Any, *, trust_absent: bool) -> bool:
    """`email_verified` must be boolean true; with `oidc_trust_email`, a missing claim counts as verified
    (never an explicit false or a string)."""
    if claim is None:
        return trust_absent
    return claim is True


def bearer(authorization: str | None) -> str:
    """The token of an `Authorization: Bearer …` header (401 when missing or of another scheme)."""
    if not authorization:
        raise Unauthorized("sign in: send Authorization: Bearer <token>")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthorized("expected Authorization: Bearer <token>")
    return token.strip()
