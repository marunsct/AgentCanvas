"""An organisation's IP allow-list (E-8): the networks its requests may come from.

An admin lists networks (CIDR, or single addresses) under Settings > Organisation > Security; an empty list (the
default) allows every address. The list applies to every credential that acts in the organisation: people signed in
through the identity provider, personal API tokens, deployment keys, share links and the SCIM token. A list at the
identity provider would cover only the first of these. The client address is the one the rate limits and the audit
log use: the direct peer, or the address a trusted proxy forwarded (uvicorn `--proxy-headers` with
`FORWARDED_ALLOW_IPS`). An address that cannot be read is refused when a list is set (fails closed).

The operator's escape hatch, `AGENTCANVAS_IGNORE_ORG_NETWORKS`, switches every list off, for an admin locked out by
their own (an address that changed).
"""

from __future__ import annotations

import ipaddress
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from agentcanvas.db.models import Deployment, Organization, Project, WorkflowRow
from agentcanvas.errors import Forbidden, InvalidInput
from agentcanvas.log import get_logger

log = get_logger(__name__)
KEY = "allowed_networks"  # in `organization.settings`
MAX_NETWORKS = 100
IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class NetworkNotAllowed(Forbidden):
    type_ = "https://agentcanvas.dev/errors/network-not-allowed"


def parse(entries: list[str]) -> list[str]:
    """The list as stored: each entry a network in its normal form ("10.1.2.3" -> "10.1.2.3/32", host bits
    cleared), duplicates dropped. A malformed entry is refused with its value."""
    if len(entries) > MAX_NETWORKS:
        raise InvalidInput(f"an organisation lists at most {MAX_NETWORKS} networks")
    out: list[str] = []
    for raw in entries:
        try:
            network = str(ipaddress.ip_network(raw.strip(), strict=False))
        except ValueError:
            hint = "write an address (203.0.113.7) or CIDR (203.0.113.0/24)"
            raise InvalidInput(f"'{raw}' is not a network: {hint}") from None
        if network not in out:
            out.append(network)
    return out


def client_ip(conn: HTTPConnection) -> IpAddress | None:
    """The caller's address (behind a trusted proxy, the one it forwarded); IPv4 as itself even when the server
    listens on IPv6 (::ffff:203.0.113.7)."""
    host = conn.client.host if conn.client else None
    try:
        ip = ipaddress.ip_address(host or "")
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def allows(networks: list[str] | None, ip: IpAddress | None) -> bool:
    if not networks:
        return True
    if ip is None:
        return False
    return any(ip in ipaddress.ip_network(n, strict=False) for n in networks)


def of(org_settings: dict[str, Any] | None) -> list[str]:
    return list((org_settings or {}).get(KEY) or [])


def check(conn: HTTPConnection, org_id: str, org_settings: dict[str, Any] | None, what: str) -> None:
    """403 when the organisation has a list and the caller's address is not on it. `what` names the credential in
    the log line (sign-in, API token, deployment key, share link, SCIM)."""
    networks = of(org_settings)
    if not networks or conn.app.state.settings.ignore_org_networks:
        return
    ip = client_ip(conn)
    if allows(networks, ip):
        return
    log.warning("auth.network_refused", org_id=org_id, credential=what, ip=str(ip) if ip else None)
    raise NetworkNotAllowed(
        f"this organisation accepts requests only from its allowed networks, and your address ({ip or 'unknown'}) "
        "is not one of them: connect from an allowed network, or ask an admin of the organisation",
        code="network_not_allowed",
    )


async def check_deployment(conn: HTTPConnection, session: AsyncSession, dep_id: str, what: str) -> None:
    """The allow-list of the organisation a deployment belongs to (its keys and its share link). An unknown
    deployment passes: the credential check that follows refuses it."""
    found = (
        await session.execute(
            select(Organization.id, Organization.settings)
            .join(Project, Project.org_id == Organization.id)
            .join(WorkflowRow, WorkflowRow.project_id == Project.id)
            .join(Deployment, Deployment.workflow_id == WorkflowRow.id)
            .where(Deployment.id == dep_id)
        )
    ).first()
    if found is not None:
        check(conn, found[0], found[1], what)
