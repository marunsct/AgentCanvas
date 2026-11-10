"""OAuth sign-in for a deployment's endpoints (E-11): AgentCanvas as an OAuth 2.1 resource server.

Off by default; a builder turns it on per deployment, beside its keys. Then its MCP, A2A and AG-UI endpoints and its
deployment API (`runs/wait` and the others, one resource: the deployment's address) take access tokens:

- **Discovery** (RFC 9728, MCP 2026-07-28): the endpoint's protected resource metadata is served at
  `/.well-known/oauth-protected-resource<path of the endpoint>` and names the authorization server: the issuer the
  deployment was given, else the one people sign in to AgentCanvas with (`AGENTCANVAS_OIDC_ISSUER`). A call without
  a token, or with a bad one, answers 401 with `WWW-Authenticate: Bearer resource_metadata="…"`, so a client can
  find where to sign in on its own.
- **Tokens** are JWT access tokens, verified against the issuer's published keys: signature, issuer, expiry, the
  audience (RFC 8707: the endpoint's own address, or the deployment's configured audience, so a token issued for
  another server is never accepted) and the scopes the deployment requires (403 `insufficient_scope` without them);
  reading a result of the deployment API also takes `agentcanvas:deployment:read` alone.
- **Who calls**: the token's subject becomes the run's end user, `oidc:<issuer>:<subject>`, so every person has
  their own memories, conversations and runs, and may continue, read or stop only their own.

Enterprise-managed authorization (ID-JAG) needs nothing here: the organisation's identity provider issues the
access token, and this side verifies it like any other.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import jwt
from jwt import PyJWKSet
from pydantic import BaseModel, Field

from agentcanvas.errors import DomainError, InvalidInput
from agentcanvas.security.auth import ALGORITHMS
from agentcanvas.services import oauth
from agentcanvas.settings import Settings

DEFAULT_SCOPE = "agentcanvas:deployment:run"
READ_SCOPE = "agentcanvas:deployment:read"  # enough on its own to read results (the deployment API's GETs, an export)
WELL_KNOWN = "/.well-known/oauth-protected-resource"
RETRY_FAILED_S = 30.0  # an issuer whose keys could not be loaded is asked again after this long, not on every call
REFRESH_UNKNOWN_KID_S = 30.0  # a token signed with a key not seen yet reloads the keys (rotation), at most this often


class OAuthConfig(BaseModel):
    """A deployment's sign-in settings, as kept on `deployment.oauth` (none there: off)."""

    issuer: str | None = Field(None, description="the authorization server; none: the one people sign in with")
    audience: str | None = Field(
        None, description="also accepted as the token's audience, besides the endpoint's own address"
    )
    scopes: list[str] = Field(default_factory=lambda: [DEFAULT_SCOPE], description="every one is required")


def config(raw: dict[str, Any] | None) -> OAuthConfig | None:
    return OAuthConfig.model_validate(raw) if raw else None


def issuer_of(cfg: OAuthConfig, settings: Settings) -> str | None:
    return cfg.issuer or settings.oidc_issuer


def check_issuer_url(url: str) -> str:
    """An issuer the server may fetch keys from: https (http for the machine itself), no query or fragment."""
    url = url.strip()
    oauth.secure_url(url, "the issuer")
    if urlsplit(url).query:
        raise InvalidInput(f"the issuer must not have a query: {url}")
    return url


def metadata_url(resource: str) -> str:
    """Where RFC 9728 puts the metadata of `resource`: the well-known path between the host and the path."""
    p = urlsplit(resource)
    return urlunsplit((p.scheme, p.netloc, WELL_KNOWN + p.path.rstrip("/"), "", ""))


def challenge(meta: str, scopes: list[str], error: str | None = None, description: str | None = None) -> str:
    """The `WWW-Authenticate` value of a refused call (RFC 6750 section 3, RFC 9728 section 5.1)."""
    parts = []
    if error:
        parts.append(f'error="{error}"')
    if scopes:
        parts.append(f'scope="{_quoted(" ".join(scopes))}"')
    parts.append(f'resource_metadata="{_quoted(meta)}"')
    if description:
        parts.append(f'error_description="{_quoted(description)}"')
    return "Bearer " + ", ".join(parts)


def _quoted(value: str) -> str:
    """A header parameter's value: printable ASCII without quotes or backslashes (RFC 6750 allows no more)."""
    return "".join(c for c in value if 0x20 <= ord(c) < 0x7F and c not in '"\\')[:300]


def end_user_id(issuer: str, subject: str) -> str:
    """The `user_id` a signed-in person has on the deployment (namespaced by issuer: two issuers' subjects never
    meet)."""
    return f"oidc:{issuer}:{subject}"


def granted(claims: dict[str, Any]) -> set[str]:
    """The token's scopes: `scope` (RFC 9068, space-separated) or `scp` (a string or a list, as Entra and Okta send)."""
    out: set[str] = set()
    for name in ("scope", "scp"):
        value = claims.get(name)
        if isinstance(value, str):
            out |= set(value.split())
        elif isinstance(value, list):
            out |= {v for v in value if isinstance(v, str)}
    return out


def _same(a: str, b: str) -> bool:
    """Two resource identifiers are the same, ignoring the case of an address's scheme and host and a trailing slash
    (MCP: servers should accept both forms)."""

    def norm(u: str) -> str:
        p = urlsplit(u.strip())
        if p.scheme.lower() in ("http", "https") and p.netloc:
            return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), p.query, ""))
        return u.strip()

    return norm(a) == norm(b)


class InvalidToken(DomainError):
    """The token is not one this endpoint accepts (401 invalid_token)."""

    status, title, type_ = 401, "Unauthorized", "https://agentcanvas.dev/errors/unauthorized"


class InsufficientScope(DomainError):
    """A valid token without every required scope (403 insufficient_scope)."""

    status, title, type_ = 403, "Forbidden", "https://agentcanvas.dev/errors/forbidden"


@dataclass
class SignedIn:
    """A verified access token's caller."""

    issuer: str
    subject: str
    claims: dict[str, Any]

    @property
    def user_id(self) -> str:
        return end_user_id(self.issuer, self.subject)


def _default_client(public_only: bool) -> httpx.AsyncClient:
    if public_only:  # the hosted edition: an issuer's documents are fetched from public addresses only
        from agentcanvas.adapters.langchain.pinned import public_async_client

        return public_async_client(timeout=10, follow_redirects=False)
    return httpx.AsyncClient(timeout=10, follow_redirects=False)


# the HTTP client for issuers' discovery documents and keys; tests substitute one that reaches a fake issuer
client_factory: Callable[[bool], httpx.AsyncClient] = _default_client


@dataclass
class _Keys:
    keys: PyJWKSet | None
    loaded_at: float
    error: str | None = None


class AccessTokens:
    """Verifies deployments' access tokens, with each issuer's keys cached for `oidc_jwks_cache_s` (per server: every
    replica fetches its own, and a key the issuer drops stops verifying when the cache expires). `jwks` (issuer ->
    JWK Set) skips the network, for tests."""

    def __init__(self, settings: Settings, jwks: dict[str, dict[str, Any]] | None = None):
        self.settings = settings
        self._static = {iss: PyJWKSet.from_dict(s) for iss, s in (jwks or {}).items()}
        self._cache: dict[str, _Keys] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def check(self, issuer: str) -> None:
        """Load the issuer's keys now (when a builder names it), so a wrong address is told at once (502)."""
        found = await self._keys(issuer, fresh=True)
        if found.keys is None:
            raise oauth.UpstreamError(f"cannot load the signing keys of {issuer}: {found.error}")

    async def _keys(self, issuer: str, *, fresh: bool = False, at_least: float = 0.0) -> _Keys:
        if issuer in self._static:
            return _Keys(self._static[issuer], time.monotonic())
        if self._static:  # tests: an issuer they did not set up has no keys
            return _Keys(None, time.monotonic(), "unknown issuer")
        lock = self._locks.setdefault(issuer, asyncio.Lock())
        async with lock:  # one fetch per issuer at a time; the others wait for its answer
            now = time.monotonic()
            cached = self._cache.get(issuer)
            if cached is not None and not fresh and cached.loaded_at >= at_least:
                ttl = self.settings.oidc_jwks_cache_s if cached.keys is not None else RETRY_FAILED_S
                if now - cached.loaded_at < ttl:
                    return cached
            try:
                found = _Keys(await self._fetch(issuer), now)
            except DomainError as exc:
                found = _Keys(None, now, exc.detail)
            self._cache[issuer] = found
            return found

    async def _fetch(self, issuer: str) -> PyJWKSet:
        s = self.settings
        async with client_factory(s.edition == "hosted") as client:
            if issuer == s.oidc_issuer and s.oidc_jwks_url:
                url = s.oidc_jwks_url
            else:
                meta = await oauth._authorization_server(client, issuer)  # checks it is this issuer's
                url = oauth.secure_url(str(meta.get("jwks_uri") or ""), "the issuer's keys address (jwks_uri)")
            body = await oauth._json(client, "GET", url)
        try:
            return PyJWKSet.from_dict(body)
        except jwt.PyJWTError as exc:
            raise oauth.UpstreamError(f"{oauth.host_of(url)} published no usable signing keys: {exc}") from None

    async def _key(self, token: str, issuer: str) -> Any:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise InvalidToken("the access token is not a JWT") from None
        kid = header.get("kid")
        found = await self._keys(issuer)
        for attempt in range(2):
            if found.keys is None:
                raise InvalidToken(f"cannot load the issuer's signing keys: {found.error}")
            for key in found.keys.keys:
                if kid is None or key.key_id == kid:
                    return key.key
            if attempt == 0 and time.monotonic() - found.loaded_at >= REFRESH_UNKNOWN_KID_S:
                found = await self._keys(issuer, at_least=found.loaded_at + 1e-6)  # the issuer may have rotated
            else:
                break
        raise InvalidToken("the access token is signed with a key the issuer does not publish")

    async def verify(
        self, token: str, *, issuer: str, audiences: list[str], scopes: list[str], enough: str | None = None
    ) -> SignedIn:
        """The token's caller, once its signature, issuer, expiry, audience (one of `audiences`) and scopes (all of
        `scopes`, or else `enough` alone) hold."""
        key = await self._key(token, issuer)
        try:
            claims = jwt.decode(
                token, key, algorithms=ALGORITHMS, issuer=issuer, leeway=self.settings.oidc_leeway_s,
                options={"require": ["exp", "iss", "sub"], "verify_aud": False},  # checked below, per RFC 8707
            )  # fmt: skip
        except jwt.ExpiredSignatureError:
            raise InvalidToken("the access token expired") from None
        except jwt.InvalidIssuerError:
            raise InvalidToken(f"the access token was not issued by {issuer}") from None
        except jwt.PyJWTError as exc:
            raise InvalidToken(f"invalid access token: {exc}") from None
        aud = claims.get("aud")
        got = [aud] if isinstance(aud, str) else [a for a in aud if isinstance(a, str)] if isinstance(aud, list) else []
        if not any(_same(a, want) for a in got for want in audiences):
            raise InvalidToken(f"the access token is for another resource: its audience must be {audiences[0]}")
        subject = str(claims["sub"]).strip()
        if not subject:  # people are told apart by subject: a blank one would be everybody's
            raise InvalidToken("the access token's subject is empty")
        have = granted(claims)
        missing = [s for s in scopes if s not in have]
        if missing and not (enough and enough in have):
            either = f" (or {enough})" if enough else ""
            raise InsufficientScope(f"the access token lacks the scope {' '.join(missing)}{either}")
        return SignedIn(issuer=issuer, subject=subject, claims=claims)
