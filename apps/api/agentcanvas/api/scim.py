"""SCIM 2.0 endpoints under /scim/v2 (G-13): Users and Groups, authenticated with the organisation's SCIM bearer
token (Settings > Organisation > Provisioning), not with a person's sign-in. Behind the web container's proxy the
base URL is `https://<your AgentCanvas>/api/scim/v2`. See `services/scim.py` for how resources map to members."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from agentcanvas.api.deps import get_session
from agentcanvas.errors import DomainError
from agentcanvas.services import scim
from agentcanvas.services.scim import ScimError, ScimService

MEDIA = "application/scim+json"


def reply(body: dict[str, Any], status: int = 200, **headers: str) -> JSONResponse:
    return JSONResponse(body, status_code=status, media_type=MEDIA, headers=headers or None)


class ScimRoute(APIRoute):
    """Answers every failure in SCIM's error shape (RFC 7644 section 3.12) instead of problem+json."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def scim_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except ScimError as exc:
                extra = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
                return JSONResponse(exc.body(), status_code=exc.status, media_type=MEDIA, headers=extra)
            except DomainError as exc:
                err = scim.from_domain(exc)
                return JSONResponse(err.body(), status_code=err.status, media_type=MEDIA)
            except RequestValidationError as exc:
                err = ScimError(400, f"invalid request: {exc.errors()[0]['msg']}", "invalidSyntax")
                return JSONResponse(err.body(), status_code=400, media_type=MEDIA)

        return scim_handler


router = APIRouter(prefix="/scim/v2", tags=["scim"], route_class=ScimRoute)


async def scim_service(request: Request, session: AsyncSession = Depends(get_session)) -> ScimService:
    """The service for the organisation whose SCIM token the request carries (401 otherwise)."""
    if request.app.state.settings.auth_mode != "oidc":
        raise ScimError(
            403,
            "SCIM needs sign-in through OpenID Connect (Matura manages its own members)",
        )
    scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ScimError(401, "send Authorization: Bearer scim_...")
    org = await scim.organisation_of(session, token.strip())
    return ScimService(session, org.id)


async def _body(request: Request) -> dict[str, Any]:
    """The JSON object of a request (`application/scim+json` is JSON like any other)."""
    try:
        body = await request.json()
    except ValueError:
        raise ScimError(400, "the body is not valid JSON", "invalidSyntax") from None
    if not isinstance(body, dict):
        raise ScimError(400, "the body must be a JSON object", "invalidSyntax")
    return body


def _located(request: Request, resource: dict[str, Any]) -> dict[str, str]:
    prefix = request.headers.get("x-forwarded-prefix", "").rstrip("/")
    base = str(request.base_url).rstrip("/") + prefix
    return {"Location": f"{base}/scim/v2/{resource['meta']['resourceType']}s/{resource['id']}"}


# --- discovery ----------------------------------------------------------------------------------------------------


@router.get("/ServiceProviderConfig")
async def service_provider_config(svc: ScimService = Depends(scim_service)) -> JSONResponse:
    return reply({
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "documentationUri": "https://github.com/marunsct/AgentCanvas/blob/main/docs/user-guide/accounts.md",
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": scim.MAX_COUNT},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [{
            "type": "oauthbearertoken", "name": "Bearer token", "primary": True,
            "description": "A SCIM token made in Settings > Organisation > Provisioning",
        }],
    })  # fmt: skip


@router.get("/ResourceTypes")
async def resource_types(svc: ScimService = Depends(scim_service)) -> JSONResponse:
    types = [
        {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": name, "name": name,
         "endpoint": f"/{name}s", "schema": schema}
        for name, schema in (("User", scim.USER), ("Group", scim.GROUP))
    ]  # fmt: skip
    return reply(scim.listing(types, len(types), 1))


@router.get("/Schemas")
async def schemas(svc: ScimService = Depends(scim_service)) -> JSONResponse:
    def attr(name: str, kind: str = "string", **kw: Any) -> dict[str, Any]:
        return {"name": name, "type": kind, "multiValued": False, "required": False, "mutability": "readWrite", **kw}

    found = [
        {"id": scim.USER, "name": "User", "description": "A member of the organisation", "attributes": [
            attr("userName", required=True, uniqueness="server"), attr("externalId"), attr("displayName"),
            attr("active", "boolean"), attr("emails", "complex", multiValued=True),
            attr("groups", "complex", multiValued=True, mutability="readOnly"),
        ]},
        {"id": scim.GROUP, "name": "Group", "description": "A group of members", "attributes": [
            attr("displayName", required=True), attr("externalId"), attr("members", "complex", multiValued=True),
        ]},
    ]  # fmt: skip
    return reply(scim.listing(found, len(found), 1))


# --- users --------------------------------------------------------------------------------------------------------


@router.get("/Users")
async def list_users(
    filter: str | None = None, startIndex: int | None = None, count: int | None = None,
    svc: ScimService = Depends(scim_service),
) -> JSONResponse:  # fmt: skip
    return reply(await svc.list_users(filter, startIndex, count))


@router.post("/Users")
async def create_user(request: Request, svc: ScimService = Depends(scim_service)) -> JSONResponse:
    user = await svc.create_user(await _body(request))
    return reply(user, 201, **_located(request, user))


@router.get("/Users/{user_id}")
async def get_user(user_id: str, svc: ScimService = Depends(scim_service)) -> JSONResponse:
    return reply(await svc.get_user(user_id))


@router.put("/Users/{user_id}")
async def replace_user(user_id: str, request: Request, svc: ScimService = Depends(scim_service)) -> JSONResponse:
    return reply(await svc.replace_user(user_id, await _body(request)))


@router.patch("/Users/{user_id}")
async def patch_user(user_id: str, request: Request, svc: ScimService = Depends(scim_service)) -> JSONResponse:
    return reply(await svc.patch_user(user_id, scim.operations(await _body(request))))


@router.delete("/Users/{user_id}", status_code=204)
async def delete_user(user_id: str, svc: ScimService = Depends(scim_service)) -> Response:
    await svc.delete_user(user_id)
    return Response(status_code=204)


# --- groups -------------------------------------------------------------------------------------------------------


@router.get("/Groups")
async def list_groups(
    request: Request, filter: str | None = None, startIndex: int | None = None, count: int | None = None,
    excludedAttributes: str | None = None, svc: ScimService = Depends(scim_service),
) -> JSONResponse:  # fmt: skip
    members = "members" not in (excludedAttributes or "").lower().replace(" ", "").split(",")
    return reply(await svc.list_groups(filter, startIndex, count, members))


@router.post("/Groups")
async def create_group(request: Request, svc: ScimService = Depends(scim_service)) -> JSONResponse:
    group = await svc.create_group(await _body(request))
    return reply(group, 201, **_located(request, group))


@router.get("/Groups/{group_id}")
async def get_group(group_id: str, svc: ScimService = Depends(scim_service)) -> JSONResponse:
    return reply(await svc.get_group(group_id))


@router.put("/Groups/{group_id}")
async def replace_group(group_id: str, request: Request, svc: ScimService = Depends(scim_service)) -> JSONResponse:
    return reply(await svc.replace_group(group_id, await _body(request)))


@router.patch("/Groups/{group_id}")
async def patch_group(group_id: str, request: Request, svc: ScimService = Depends(scim_service)) -> JSONResponse:
    return reply(await svc.patch_group(group_id, scim.operations(await _body(request))))


@router.delete("/Groups/{group_id}", status_code=204)
async def delete_group(group_id: str, svc: ScimService = Depends(scim_service)) -> Response:
    await svc.delete_group(group_id)
    return Response(status_code=204)
