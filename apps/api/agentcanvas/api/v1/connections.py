"""Per-user OAuth connections to app connectors (G-7): Connect in the editor, and the organisation's service connection.

`POST /connections/authorize` finds how the MCP server wants people to sign in and returns the address to send the
browser to; the browser comes back to the web app (`/oauth/callback`), which posts the `code` and `state` to
`POST /connections/callback` as the signed-in person. See `services/oauth.py`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.audit import note
from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.models import OAuthConnection
from agentcanvas.errors import Forbidden, NotFound
from agentcanvas.services import oauth

router = APIRouter(tags=["connections"])
public_router = APIRouter(tags=["connections"])


class AuthorizeIn(BaseModel):
    server_url: str = Field(max_length=2000, description="the app connector's server URL")
    service: bool = Field(False, description="connect the organisation's service identity (admins) instead of you")
    return_to: str = Field("/", max_length=500, description="the page of this app to come back to")


class AuthorizeOut(BaseModel):
    authorization_url: str = Field(description="send the browser here")
    resource: str = Field(description="the MCP server the connection is for (tokens are sent there)")
    host: str = Field(description="the MCP server's host")
    issuer: str = Field(description="the sign-in server the browser goes to")
    issuer_host: str = Field(description="the sign-in server's host: show it when it differs from `host`")


class CallbackIn(BaseModel):
    code: str = Field(min_length=1, max_length=4000)
    state: str = Field(min_length=1, max_length=500)
    iss: str | None = Field(None, max_length=2000, description="the server's issuer, when it sent one")


class ConnectionOut(BaseModel):
    id: str
    resource: str = Field(description="the MCP server's address")
    host: str
    owner: str = Field(description="'you', 'service', or (for admins) another person's id")
    status: str = Field(description="'active', or 'reconnect' when the server no longer accepts it")
    scope: str | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CallbackOut(BaseModel):
    connection: ConnectionOut
    return_to: str


def _out(c: OAuthConnection, user: CurrentUser) -> ConnectionOut:
    owner = "you" if c.owner == user.id else c.owner
    return ConnectionOut(
        id=c.id, resource=c.resource, host=oauth.host_of(c.resource), owner=owner, status=c.status, scope=c.scope,
        expires_at=c.expires_at, created_at=c.created_at, updated_at=c.updated_at,
    )  # fmt: skip


def _connections(request: Request) -> oauth.Connections:
    return request.app.state.oauth


@router.get("/connections", response_model=list[ConnectionOut])
async def list_connections(
    user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[ConnectionOut]:
    """Your connections and the organisation's service connections (admins also see everyone's)."""
    q = select(OAuthConnection).where(OAuthConnection.org_id == user.org_id)
    if user.role != "admin":
        q = q.where(OAuthConnection.owner.in_([user.id, oauth.SERVICE]))
    rows = (await session.scalars(q.order_by(OAuthConnection.resource, OAuthConnection.owner))).all()
    return [_out(c, user) for c in rows]


class ClientIn(BaseModel):
    server_url: str = Field(max_length=2000, description="the app connector's server URL; its sign-in server is found")
    client_id: str = Field(min_length=1, max_length=500)
    client_secret: str | None = Field(
        None, max_length=2000,
        description="for a confidential client; kept encrypted. Left out with the same client id: the saved one stays",
    )  # fmt: skip


class ClientOut(BaseModel):
    issuer: str = Field(description="the sign-in server these credentials are for")
    host: str
    client_id: str
    has_secret: bool
    resources: list[str] | None = Field(
        description="the MCP servers entered credentials sign in to (no other server may use them); null: "
        "AgentCanvas registered itself"
    )
    created_at: datetime


class ClientsOut(BaseModel):
    redirect_uri: str = Field(description="register this as the redirect (callback) address at the provider")
    clients: list[ClientOut]


def _client_out(c: Any) -> ClientOut:
    return ClientOut(
        issuer=c.issuer, host=oauth.host_of(c.issuer), client_id=c.client_id, has_secret=bool(c.client_secret_enc),
        resources=c.resources, created_at=c.created_at,
    )  # fmt: skip


def _admin(user: CurrentUser) -> None:
    if user.role != "admin":
        raise Forbidden("only an admin manages the sign-in credentials of app connectors")


@router.get("/connections/clients", response_model=ClientsOut)
async def list_clients(
    request: Request, user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> ClientsOut:
    """The client credentials this organisation uses to sign in to app connectors' servers (admins)."""
    _admin(user)
    conns = _connections(request)
    return ClientsOut(
        redirect_uri=conns.redirect_uri, clients=[_client_out(c) for c in await conns.clients(session, user.org_id)]
    )


@router.put("/connections/clients", response_model=ClientOut)
async def put_client(
    body: ClientIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ClientOut:
    """Use a client id (and secret) made at the provider, for providers without client ID metadata documents or
    dynamic registration (admins). Replaces the registration for that sign-in server."""
    _admin(user)
    row = await _connections(request).set_client(
        session, org_id=user.org_id, server_url=body.server_url, client_id=body.client_id.strip(),
        client_secret=body.client_secret or None,
    )  # fmt: skip
    note(request, target=f"server={oauth.host_of(row.issuer)}", has_secret=bool(row.client_secret_enc))
    return _client_out(row)


@router.delete("/connections/clients", status_code=204)
async def delete_client(
    request: Request,
    issuer: str = Query(description="the sign-in server (as the list shows it)"),
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Forget the credentials for a sign-in server (admins); the next sign-in registers again if the server allows."""
    _admin(user)
    if not await _connections(request).remove_client(session, user.org_id, issuer):
        raise NotFound("no client credentials for that sign-in server")
    note(request, target=f"server={oauth.host_of(issuer)}")
    return Response(status_code=204)


@router.post("/connections/authorize", response_model=AuthorizeOut)
async def authorize(
    body: AuthorizeIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AuthorizeOut:
    """Start signing in to an app connector's server: the address to send the browser to."""
    if body.service and user.role != "admin":
        raise Forbidden("only an admin connects the organisation's service identity")
    if user.token_id:
        raise Forbidden("signing in to a connector needs a browser session, not an API token")
    begun = await _connections(request).begin(
        session, org_id=user.org_id, user_id=user.id, server_url=body.server_url, service=body.service,
        return_to=body.return_to,
    )  # fmt: skip
    note(request, target=f"server={oauth.host_of(body.server_url)}", service=body.service, issuer=begun.issuer)
    return AuthorizeOut(
        authorization_url=begun.authorization_url, resource=begun.resource, host=oauth.host_of(begun.resource),
        issuer=begun.issuer, issuer_host=oauth.host_of(begun.issuer),
    )  # fmt: skip


@router.post("/connections/callback", response_model=CallbackOut)
async def callback(
    body: CallbackIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> CallbackOut:
    """Finish signing in with what the browser brought back from the authorization server."""
    row, back = await _connections(request).complete(
        session, org_id=user.org_id, user_id=user.id, code=body.code, state=body.state, iss=body.iss
    )
    note(
        request,
        target=f"server={oauth.host_of(row.resource)}",
        owner="service" if row.owner == oauth.SERVICE else "you",
    )
    return CallbackOut(connection=_out(row, user), return_to=back)


@router.delete("/connections/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Disconnect: the server is asked to revoke the tokens, which are deleted (yours; admins also the service's)."""
    row = await session.get(OAuthConnection, connection_id)
    if row is None or row.org_id != user.org_id:
        raise NotFound(f"connection '{connection_id}' not found")
    if row.owner != user.id and user.role != "admin":
        raise NotFound(f"connection '{connection_id}' not found")  # not yours: the same answer as no such connection
    note(request, target=f"server={oauth.host_of(row.resource)}", owner=row.owner)
    await _connections(request).remove(session, row)
    return Response(status_code=204)


@public_router.get("/oauth/client-metadata.json", response_model=dict[str, Any])
async def client_metadata(request: Request) -> dict[str, Any]:
    """AgentCanvas's Client ID Metadata Document: authorization servers that support them fetch it from here (it is
    this address that is the client id). Public by design; 404 unless the server's public https address is set."""
    public = request.app.state.settings.public_url
    if not public or not public.startswith("https://"):
        raise NotFound("this server has no public https address (AGENTCANVAS_PUBLIC_URL)")
    return oauth.client_metadata(public)
