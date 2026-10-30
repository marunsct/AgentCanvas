"""Per-user OAuth connections to MCP servers that need a sign-in (G-7).

The flow follows the MCP authorization specification and the standards it builds on, against whatever server it is
pointed at, with nothing provider-specific:

1. **Discovery.** An unauthenticated request to the MCP server answers 401 and names its protected resource
   metadata (RFC 9728, `WWW-Authenticate: Bearer resource_metadata="..."`; the well-known URLs are the fallback). That
   names the authorization server, whose metadata (RFC 8414, or OpenID Connect discovery) gives the endpoints.
2. **Client identity.** A server that supports Client ID Metadata Documents (MCP 2026-07-28) is told to fetch ours
   (`GET /api/v1/oauth/client-metadata.json` on the public address); otherwise AgentCanvas registers itself with the
   server's dynamic registration endpoint (RFC 7591). A server with neither is not supported here.
3. **Authorization code with PKCE (S256)**, the `resource` parameter (RFC 8707) naming the MCP server, a single-use
   `state` bound to the person and organisation that started it, and the issuer check of RFC 9207 when the server sends
   `iss`. The browser comes back to the web app, which hands `code` and `state` to the API as the signed-in person.
4. **Tokens** are encrypted with the organisation's data key (the same keyring as project secrets), refreshed shortly
   before they expire or when the server rejects one (refresh tokens rotate, so one replica refreshes at a time, under a
   row lock), and a connection whose refresh fails is marked `reconnect`.

A connection belongs to one person and one MCP server (its URL), or to the organisation's **service** identity, which
an admin connects for schedules, webhooks and deployments, where no person starts the run. At run time the connector
asks for the token of whoever it was set to act as (`runtime/identity.py`), at the moment of each call.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import re
import secrets
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
import httpx2
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import OAuthClient, OAuthConnection, OAuthFlow
from agentcanvas.errors import DomainError, InvalidInput, UpstreamError
from agentcanvas.ids import new_id
from agentcanvas.log import get_logger
from agentcanvas.runtime.identity import CURRENT
from agentcanvas.security.kms import Keyring

log = get_logger(__name__)
SERVICE = "service"  # the owner of an organisation's service connection (a person's id never looks like this)
FLOW_TTL = timedelta(minutes=10)
REFRESH_BEFORE = timedelta(seconds=60)
MAX_BODY = 1_000_000
CALLBACK_PATH = "/oauth/callback"
CLIENT_METADATA_PATH = "/api/v1/oauth/client-metadata.json"


class ConnectionRequired(DomainError):
    """A call needs a connection nobody has made (or one that stopped working)."""

    status, title, type_ = 409, "Connection required", "https://agentcanvas.dev/errors/connection-required"


# --- http -----------------------------------------------------------------------------------------------------------


def _default_client(public_only: bool) -> httpx.AsyncClient:
    if public_only:  # the hosted edition: every address named by a remote document must be public, checked on connect
        from agentcanvas.adapters.langchain.pinned import public_async_client

        return public_async_client(timeout=15, follow_redirects=False)
    return httpx.AsyncClient(timeout=15, follow_redirects=False)


# the HTTP client for discovery, registration and token calls; tests substitute one that reaches a fake server
client_factory: Callable[[bool], httpx.AsyncClient] = _default_client


def secure_url(url: str, what: str) -> str:
    """`url` if it is an https address (http only for the machine itself), else 422 naming `what`."""
    parts = urlsplit(url)
    local = False
    try:
        local = bool(parts.hostname) and (
            parts.hostname == "localhost" or ipaddress.ip_address(parts.hostname or "").is_loopback
        )
    except ValueError:
        local = parts.hostname == "localhost"
    if not parts.hostname or parts.username or parts.password or parts.fragment:
        raise InvalidInput(f"{what} is not a usable address: {url}")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise InvalidInput(f"{what} must be an https address (got {url})")
    return url


def canonical(server_url: str) -> str:
    """The MCP server's address as connections are keyed (and as a browser's `new URL(...).href` writes it):
    lower-case scheme and host, no fragment, trailing dot or default port."""
    parts = urlsplit(server_url.strip().split("#")[0])  # a fragment is not part of an address
    secure_url(urlunsplit(parts), "the connector's server URL")
    host = (parts.hostname or "").rstrip(".").lower()
    port = parts.port if parts.port != {"https": 443, "http": 80}.get(parts.scheme.lower()) else None
    netloc = (f"[{host}]" if ":" in host else host) + (f":{port}" if port else "")
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))


def host_of(resource: str) -> str:
    return urlsplit(resource).hostname or resource


async def _json(client: httpx.AsyncClient, method: str, url: str, **kw: Any) -> dict[str, Any]:
    what = urlsplit(url).netloc
    try:
        r = await client.request(method, url, **kw)
    except (httpx.HTTPError, OSError) as exc:
        raise UpstreamError(f"cannot reach {what}: {type(exc).__name__}: {exc}") from None
    if len(r.content) > MAX_BODY:
        raise UpstreamError(f"{what} sent an unreasonably large answer")
    try:
        body = r.json()
    except ValueError:
        body = None
    if r.status_code >= 400 or not isinstance(body, dict):
        detail = ""
        if isinstance(body, dict) and body.get("error"):
            detail = f": {str(body['error'])[:80]} {str(body.get('error_description', ''))[:200]}".rstrip()
        raise UpstreamError(f"{what} answered {r.status_code}{detail}")
    return body


# --- discovery ------------------------------------------------------------------------------------------------------


@dataclass
class ServerAuth:
    resource: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    revocation_endpoint: str | None = None
    scope: str | None = None
    cimd: bool = False
    iss_in_response: bool = False
    token_auth_methods: list[str] = field(default_factory=list)


def _challenge(header: str, name: str) -> str | None:
    m = re.search(rf'{name}="([^"]+)"', header)
    return m.group(1) if m else None


def _well_known(base: str, suffix: str, *, insert: bool) -> str:
    """`/.well-known/<suffix>` for an address with a path: put before the path (RFC 8414, 9728) or after it (OIDC)."""
    p = urlsplit(base)
    path = p.path.rstrip("/")
    well = f"/.well-known/{suffix}"
    return urlunsplit((p.scheme, p.netloc, (well + path) if insert else (path + well), "", ""))


def _same_resource(declared: str, resource: str) -> bool:
    try:
        d = canonical(declared).rstrip("/")
    except InvalidInput:
        return False
    return resource.rstrip("/") == d or resource.startswith(d + "/")


async def discover(server_url: str, *, public_only: bool = False) -> ServerAuth:
    """What the MCP server's authorization needs, found from the server itself (RFC 9728, RFC 8414)."""
    resource = canonical(server_url)
    async with client_factory(public_only) as client:
        scope: str | None = None
        metadata_url: str | None = None
        try:
            probe = await client.get(resource, headers={"Accept": "text/event-stream, application/json"})
            if probe.status_code == 401:
                challenge = probe.headers.get("www-authenticate", "")
                metadata_url = _challenge(challenge, "resource_metadata")
                scope = _challenge(challenge, "scope")
        except (httpx.HTTPError, OSError) as exc:
            raise UpstreamError(f"cannot reach {host_of(resource)}: {type(exc).__name__}: {exc}") from None
        candidates = [metadata_url] if metadata_url else []
        candidates += [_well_known(resource, "oauth-protected-resource", insert=True)]
        prm: dict[str, Any] | None = None
        for url in dict.fromkeys(candidates):
            try:
                prm = await _json(client, "GET", secure_url(url, "the protected resource metadata address"))
                break
            except (UpstreamError, InvalidInput):
                continue
        if prm is not None:
            if not _same_resource(str(prm.get("resource") or ""), resource):
                raise UpstreamError(
                    f"{host_of(resource)} published sign-in metadata for another resource ({prm.get('resource')})"
                )
            servers = [s for s in prm.get("authorization_servers") or [] if isinstance(s, str)]
            scope = scope or " ".join(s for s in prm.get("scopes_supported") or [] if isinstance(s, str)) or None
            if not servers:
                raise UpstreamError(f"{host_of(resource)} names no authorization server")
            issuer = servers[0]
        else:  # servers from before RFC 9728: the authorization server is the server's own origin
            p = urlsplit(resource)
            issuer = f"{p.scheme}://{p.netloc}"
        issuer = secure_url(issuer, "the authorization server")
        meta = await _authorization_server(client, issuer)
    methods = meta.get("code_challenge_methods_supported") or []
    if "S256" not in methods:
        raise UpstreamError(f"{host_of(issuer)} does not advertise PKCE with S256, which signing in requires")
    for key in ("authorization_endpoint", "token_endpoint"):
        if not isinstance(meta.get(key), str):
            raise UpstreamError(f"{host_of(issuer)} does not publish a {key.replace('_', ' ')}")
    return ServerAuth(
        resource=resource, issuer=str(meta["issuer"]),
        authorization_endpoint=secure_url(meta["authorization_endpoint"], "the authorization endpoint"),
        token_endpoint=secure_url(meta["token_endpoint"], "the token endpoint"),
        registration_endpoint=secure_url(meta["registration_endpoint"], "the registration endpoint")
        if isinstance(meta.get("registration_endpoint"), str) else None,
        revocation_endpoint=secure_url(meta["revocation_endpoint"], "the revocation endpoint")
        if isinstance(meta.get("revocation_endpoint"), str) else None,
        scope=scope, cimd=meta.get("client_id_metadata_document_supported") is True,
        iss_in_response=meta.get("authorization_response_iss_parameter_supported") is True,
        token_auth_methods=[m for m in meta.get("token_endpoint_auth_methods_supported") or [] if isinstance(m, str)],
    )  # fmt: skip


async def _authorization_server(client: httpx.AsyncClient, issuer: str) -> dict[str, Any]:
    urls = [
        _well_known(issuer, "oauth-authorization-server", insert=True),
        _well_known(issuer, "openid-configuration", insert=True),
        _well_known(issuer, "openid-configuration", insert=False),
    ]
    last: Exception | None = None
    for url in dict.fromkeys(urls):
        try:
            meta = await _json(client, "GET", url)
        except UpstreamError as exc:
            last = exc
            continue
        if str(meta.get("issuer", "")).rstrip("/") != issuer.rstrip("/"):  # RFC 8414 section 3.3: it must be the same
            raise UpstreamError(f"{host_of(issuer)} published metadata for another issuer ({meta.get('issuer')})")
        return meta
    raise UpstreamError(f"{host_of(issuer)} publishes no authorization server metadata ({last})")


# --- starting and finishing a sign-in --------------------------------------------------------------------------------


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def hash_state(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def client_metadata(public_url: str) -> dict[str, Any]:
    """AgentCanvas's Client ID Metadata Document: what a server that supports them fetches from our client_id URL."""
    base = public_url.rstrip("/")
    return {
        "client_id": base + CLIENT_METADATA_PATH, "client_name": "AgentCanvas", "client_uri": base,
        "redirect_uris": [base + CALLBACK_PATH], "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "none",
    }  # fmt: skip


def safe_return(path: str | None) -> str:
    """A page of this app to come back to: a path, never an address (no open redirect)."""
    path = path or "/"
    return path if path.startswith("/") and not path.startswith("//") and "\\" not in path else "/"


@dataclass
class Begun:
    authorization_url: str
    issuer: str


class Connections:
    """Making, listing and removing connections, for the API."""

    def __init__(self, keyring: Keyring, public_url: str, public_only: bool = False):
        self.keyring, self.public_url, self.public_only = keyring, public_url.rstrip("/"), public_only

    @property
    def redirect_uri(self) -> str:
        return self.public_url + CALLBACK_PATH

    async def _client(self, s: AsyncSession, org_id: str, meta: ServerAuth) -> tuple[str, str | None]:
        """The client id (and secret) to use at this authorization server: ours by metadata document if it takes
        those, else a registration made once per organisation and server."""
        found = await s.get(OAuthClient, (org_id, meta.issuer, self.redirect_uri))  # entered by an admin, or registered
        fernet = await self.keyring.fernet(s, org_id)
        if found is not None:
            secret = fernet.decrypt(found.client_secret_enc).decode() if found.client_secret_enc else None
            return found.client_id, secret
        if meta.cimd and self.public_url.startswith("https://"):
            return self.public_url + CLIENT_METADATA_PATH, None
        if not meta.registration_endpoint:
            raise InvalidInput(
                f"{host_of(meta.issuer)} supports neither client ID metadata documents nor dynamic client "
                "registration, so AgentCanvas cannot sign in to it yet"
            )
        body = {
            "client_name": "AgentCanvas", "redirect_uris": [self.redirect_uri], "response_types": ["code"],
            "grant_types": ["authorization_code", "refresh_token"], "token_endpoint_auth_method": "none",
        }  # fmt: skip
        async with client_factory(self.public_only) as client:
            reg = await _json(client, "POST", meta.registration_endpoint, json=body)
        client_id = reg.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise UpstreamError(f"{host_of(meta.issuer)} registered no client")
        secret = reg.get("client_secret") if isinstance(reg.get("client_secret"), str) else None
        s.add(OAuthClient(
            org_id=org_id, issuer=meta.issuer, redirect_uri=self.redirect_uri, client_id=client_id,
            client_secret_enc=fernet.encrypt(secret.encode()) if secret else None,
        ))  # fmt: skip
        await s.commit()
        return client_id, secret

    def _need_public_url(self) -> None:
        if not self.public_url:
            raise InvalidInput(
                "signing in to app connectors needs the address people reach this app at: set AGENTCANVAS_PUBLIC_URL"
            )

    async def clients(self, s: AsyncSession, org_id: str) -> list[OAuthClient]:
        """The client registrations of this organisation at this address of ours."""
        q = select(OAuthClient).where(OAuthClient.org_id == org_id, OAuthClient.redirect_uri == self.redirect_uri)
        return list((await s.scalars(q.order_by(OAuthClient.issuer))).all())

    async def set_client(
        self, s: AsyncSession, *, org_id: str, server_url: str, client_id: str, client_secret: str | None
    ) -> OAuthClient:
        """Use the client id (and secret) an admin made at the provider, for the authorization server of this MCP
        server: for providers that take neither client ID metadata documents nor dynamic registration (N-5). Replaces
        a registration made earlier."""
        self._need_public_url()
        meta = await discover(secure_url(server_url, "the server"), public_only=self.public_only)
        fernet = await self.keyring.fernet(s, org_id)
        enc = fernet.encrypt(client_secret.encode()) if client_secret else None
        row = await s.get(OAuthClient, (org_id, meta.issuer, self.redirect_uri))
        if row is None:
            row = OAuthClient(org_id=org_id, issuer=meta.issuer, redirect_uri=self.redirect_uri)
            s.add(row)
        row.client_id, row.client_secret_enc = client_id, enc
        await s.commit()
        return row

    async def remove_client(self, s: AsyncSession, org_id: str, issuer: str) -> bool:
        row = await s.get(OAuthClient, (org_id, issuer, self.redirect_uri))
        if row is None:
            return False
        await s.delete(row)
        await s.commit()
        return True

    async def begin(
        self, s: AsyncSession, *, org_id: str, user_id: str, server_url: str, service: bool = False,
        return_to: str | None = None,
    ) -> Begun:  # fmt: skip
        self._need_public_url()
        meta = await discover(server_url, public_only=self.public_only)
        client_id, client_secret = await self._client(s, org_id, meta)
        verifier, challenge = pkce()
        state = secrets.token_urlsafe(32)
        fernet = await self.keyring.fernet(s, org_id)
        now = datetime.now(UTC)
        await s.execute(delete(OAuthFlow).where(OAuthFlow.expires_at < now))  # stale attempts
        s.add(OAuthFlow(
            state_hash=hash_state(state), org_id=org_id, user_id=user_id, owner=SERVICE if service else user_id,
            resource=meta.resource, issuer=meta.issuer, token_endpoint=meta.token_endpoint,
            revocation_endpoint=meta.revocation_endpoint, client_id=client_id, iss_in_response=meta.iss_in_response,
            client_secret_enc=fernet.encrypt(client_secret.encode()) if client_secret else None,
            verifier_enc=fernet.encrypt(verifier.encode()), return_to=safe_return(return_to),
            expires_at=now + FLOW_TTL,
        ))  # fmt: skip
        await s.commit()
        params = {
            "response_type": "code", "client_id": client_id, "redirect_uri": self.redirect_uri, "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256", "resource": meta.resource,
        }  # fmt: skip
        if meta.scope:
            params["scope"] = meta.scope
        base = meta.authorization_endpoint
        sep = "&" if urlsplit(base).query else "?"
        return Begun(f"{base}{sep}{urlencode(params)}", meta.issuer)

    async def complete(
        self, s: AsyncSession, *, org_id: str, user_id: str, code: str, state: str, iss: str | None = None
    ) -> tuple[OAuthConnection, str]:  # fmt: skip
        """Exchange the code the browser brought back; returns the connection and the page to return to."""
        flow = await s.get(OAuthFlow, hash_state(state))
        now = datetime.now(UTC)
        if flow is not None:  # single use, whatever happens next
            await s.delete(flow)
            await s.commit()
        if flow is None or flow.org_id != org_id or flow.user_id != user_id or flow.expires_at < now:
            raise InvalidInput("this sign-in link is unknown, expired or was started by someone else; start again")
        if iss is not None and iss.rstrip("/") != flow.issuer.rstrip("/"):  # RFC 9207: a code from another server
            raise InvalidInput("the sign-in came back from a different authorization server than it started with")
        if iss is None and flow.iss_in_response:
            raise InvalidInput("the authorization server did not say who it is (iss), which it promised to")
        fernet = await self.keyring.fernet(s, org_id)
        form = {
            "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri,
            "client_id": flow.client_id, "code_verifier": fernet.decrypt(flow.verifier_enc).decode(),
            "resource": flow.resource,
        }  # fmt: skip
        secret = fernet.decrypt(flow.client_secret_enc).decode() if flow.client_secret_enc else None
        tokens = await _token_call(flow.token_endpoint, form, secret, self.public_only)
        row = await s.scalar(
            select(OAuthConnection).where(
                OAuthConnection.org_id == org_id, OAuthConnection.owner == flow.owner,
                OAuthConnection.resource == flow.resource,
            )
        )  # fmt: skip
        if row is None:
            row = OAuthConnection(id=new_id("oac"), org_id=org_id, owner=flow.owner, resource=flow.resource,
                                  created_by=user_id)  # fmt: skip
            s.add(row)
        _store(row, tokens, fernet, flow.token_endpoint, flow.issuer, flow.client_id, flow.client_secret_enc)
        row.revocation_endpoint = flow.revocation_endpoint
        await s.commit()
        await s.refresh(row)  # `updated_at` is set by the database
        return row, flow.return_to

    async def remove(self, s: AsyncSession, row: OAuthConnection) -> None:
        """Delete a connection; the server is asked to revoke its tokens, and not waited for to be reachable."""
        if row.revocation_endpoint:
            try:
                fernet = await self.keyring.fernet(s, row.org_id)
                secret = fernet.decrypt(row.client_secret_enc).decode() if row.client_secret_enc else None
                for kind, blob in (("refresh_token", row.refresh_token_enc), ("access_token", row.access_token_enc)):
                    if blob:
                        form = {"token": fernet.decrypt(blob).decode(), "token_type_hint": kind,
                                "client_id": row.client_id}  # fmt: skip
                        await _post_form(row.revocation_endpoint, form, secret, self.public_only, raw=True)
            except Exception as exc:  # noqa: BLE001 - best effort: removing it here is what matters
                log.warning("oauth.revoke_failed", resource=row.resource, error=repr(exc))
        await s.delete(row)
        await s.commit()


async def _post_form(url: str, form: dict[str, str], secret: str | None, public_only: bool, raw: bool = False) -> Any:
    if secret:
        form = {**form, "client_secret": secret}
    try:
        async with client_factory(public_only) as client:
            r = await client.post(url, data=form, headers={"Accept": "application/json"})
    except (httpx.HTTPError, OSError) as exc:
        raise UpstreamError(f"cannot reach {urlsplit(url).netloc}: {type(exc).__name__}: {exc}") from None
    if raw:
        return r
    body: Any = None
    if len(r.content) <= MAX_BODY:
        try:
            body = r.json()
        except ValueError:
            body = None
    if not isinstance(body, dict):
        raise UpstreamError(f"{urlsplit(url).netloc} answered {r.status_code} with no token")
    if r.status_code >= 400 or "error" in body:
        err = str(body.get("error", r.status_code))[:80]
        raise TokenRefused(err, f"{urlsplit(url).netloc} refused: {err} {str(body.get('error_description', ''))[:200]}")
    return body


class TokenRefused(UpstreamError):
    def __init__(self, error: str, detail: str):
        super().__init__(detail)
        self.error = error


async def _token_call(url: str, form: dict[str, str], secret: str | None, public_only: bool) -> dict[str, Any]:
    body = await _post_form(url, form, secret, public_only)
    token = body.get("access_token")
    if not isinstance(token, str) or not token:
        raise UpstreamError(f"{urlsplit(url).netloc} sent no access token")
    if str(body.get("token_type", "Bearer")).lower() != "bearer":
        raise UpstreamError(f"{urlsplit(url).netloc} sent a {body.get('token_type')} token; only Bearer is supported")
    return body


def _store(
    row: OAuthConnection, tokens: dict[str, Any], fernet: Any, token_endpoint: str, issuer: str, client_id: str,
    client_secret_enc: bytes | None,
) -> None:  # fmt: skip
    row.token_endpoint, row.issuer, row.client_id, row.client_secret_enc = (
        token_endpoint, issuer, client_id, client_secret_enc,
    )  # fmt: skip
    row.access_token_enc = fernet.encrypt(tokens["access_token"].encode())
    refresh = tokens.get("refresh_token")
    if isinstance(refresh, str) and refresh:  # a response without one keeps the one we have
        row.refresh_token_enc = fernet.encrypt(refresh.encode())
    try:
        row.expires_at = (
            datetime.now(UTC) + timedelta(seconds=int(tokens["expires_in"])) if "expires_in" in tokens else None
        )
    except (TypeError, ValueError):
        row.expires_at = None
    row.scope = tokens.get("scope") if isinstance(tokens.get("scope"), str) else row.scope
    row.status = "active"


# --- tokens at run time -------------------------------------------------------------------------------------------


class ConnectionTokens:
    """The access token for a connection, refreshed when it has to be. One per server process."""

    def __init__(self, db: Any, keyring: Keyring, public_only: bool = False):
        self.db, self.keyring, self.public_only = db, keyring, public_only
        # one refresh at a time per connection in this process (across replicas the row lock does it; SQLite has none)
        self._locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    async def access_token(self, org_id: str, owner: str, resource: str, *, rejected: str | None = None) -> str:
        """The token to send. `rejected`: the token the server just refused; it is replaced (once, across replicas)."""
        lock = self._locks.setdefault((org_id, owner, resource), asyncio.Lock())
        async with lock:
            return await self._token(org_id, owner, resource, rejected)

    async def _token(self, org_id: str, owner: str, resource: str, rejected: str | None) -> str:
        async with self.db.sessionmaker() as s:
            row = await s.scalar(
                select(OAuthConnection)
                .where(OAuthConnection.org_id == org_id, OAuthConnection.owner == owner,
                       OAuthConnection.resource == resource)
                .with_for_update()
            )  # fmt: skip
            where = "the service connection" if owner == SERVICE else "your connection"
            if row is None:
                raise ConnectionRequired(
                    f"{where.capitalize()} to {host_of(resource)} is missing: connect it from the connector's "
                    "Connect button in the editor"
                )
            if row.status != "active":
                raise ConnectionRequired(f"{where.capitalize()} to {host_of(resource)} has expired: connect it again")
            fernet = await self.keyring.fernet(s, org_id)
            token = fernet.decrypt(row.access_token_enc).decode()
            due = row.expires_at is not None and row.expires_at <= datetime.now(UTC) + REFRESH_BEFORE
            if token != rejected and not due:
                return token
            if not row.refresh_token_enc:
                row.status = "reconnect"
                await s.commit()
                raise ConnectionRequired(
                    f"{where.capitalize()} to {host_of(resource)} expired and cannot be renewed: connect it again"
                )
            form = {"grant_type": "refresh_token", "refresh_token": fernet.decrypt(row.refresh_token_enc).decode(),
                    "client_id": row.client_id, "resource": row.resource}  # fmt: skip
            secret = fernet.decrypt(row.client_secret_enc).decode() if row.client_secret_enc else None
            try:
                tokens = await _token_call(row.token_endpoint, form, secret, self.public_only)
            except TokenRefused as exc:
                if exc.error in ("invalid_grant", "invalid_token", "unauthorized_client", "invalid_client"):
                    row.status = "reconnect"
                    await s.commit()
                    raise ConnectionRequired(
                        f"{where.capitalize()} to {host_of(resource)} was revoked or expired: connect it again"
                    ) from None
                raise
            _store(row, tokens, fernet, row.token_endpoint, row.issuer, row.client_id, row.client_secret_enc)
            await s.commit()
            return str(tokens["access_token"])


def whom() -> tuple[str, str | None]:
    """(organisation, person) the current call acts for; the person is None when none started the run."""
    ident = CURRENT.get()
    if ident is None or ident.org_id is None:
        raise ConnectionRequired("this call has no organisation to find a connection in")
    return ident.org_id, ident.user_id


def owner_for(identity: str, resource: str) -> tuple[str, str]:
    """(organisation, connection owner) for a connector set to act as `starter` or `service`."""
    org_id, user_id = whom()
    if identity == "service":
        return org_id, SERVICE
    if not user_id:
        raise ConnectionRequired(
            f"No person started this run, so it has no connection to {host_of(resource)} of its own. Set the connector "
            "to use the service connection, or start the run yourself."
        )
    return org_id, user_id


class ConnectionAuth(httpx2.Auth):
    """Signs the MCP client's requests with the connection of whoever the current run acts for, looked up at each
    call, and renews it once if the server refuses it."""

    requires_response_body = False

    def __init__(self, tokens: ConnectionTokens, resource: str, identity: str):
        self.tokens, self.resource, self.identity = tokens, canonical(resource), identity

    async def async_auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        org_id, owner = owner_for(self.identity, self.resource)
        token = await self.tokens.access_token(org_id, owner, self.resource)
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request
        if response.status_code == 401:
            fresh = await self.tokens.access_token(org_id, owner, self.resource, rejected=token)
            request.headers["Authorization"] = f"Bearer {fresh}"
            yield request


# --- the connector's hook -----------------------------------------------------------------------------------------


def install(tokens: ConnectionTokens | None) -> None:
    """Let `mcp.server` blocks with an `oauth` setting sign in with these tokens (None: they cannot)."""
    from agentcanvas.adapters.langchain import nodes

    nodes.OAUTH_AUTH = (lambda url, identity: ConnectionAuth(tokens, url, identity)) if tokens else None
