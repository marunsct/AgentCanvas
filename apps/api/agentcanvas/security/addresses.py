"""Which IP addresses count as public: the one check every outbound connection uses (RT-15).

`ipaddress`'s `is_global` alone is not enough. Several IPv6 forms carry an IPv4 address that the network delivers
to: NAT64 (64:ff9b::/96 is "global", yet 64:ff9b::a9fe:a9fe reaches 169.254.169.254 through a NAT64 gateway),
IPv4-compatible (::a.b.c.d), IPv4-mapped (::ffff:a.b.c.d), 6to4 (2002::/16) and Teredo (2001::/32). An address is
public only when it and the IPv4 address inside it, if any, both are. Shipped with exports: standard library only.
"""

from __future__ import annotations

import ipaddress

IP = ipaddress.IPv4Address | ipaddress.IPv6Address
NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
_COMPATIBLE = ipaddress.ip_network("::/96")


def parse(address: str) -> IP:
    """An address as a resolver or a URL gives it: brackets and an IPv6 zone (`%eth0`) are dropped."""
    return ipaddress.ip_address(address.strip().strip("[]").split("%")[0])


def embedded_ipv4(ip: IP) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 address carries (mapped, compatible, NAT64, 6to4 or Teredo), or None."""
    if isinstance(ip, ipaddress.IPv4Address):
        return None
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]  # the client's address
    low32 = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if any(ip in net for net in NAT64):
        return low32
    if ip in _COMPATIBLE and int(ip) > 1:  # not :: or ::1, which have their own meaning
        return low32
    return None


def effective(address: str | IP) -> IP:
    """The address a connection really reaches: the embedded IPv4 address when there is one."""
    ip = parse(address) if isinstance(address, str) else address
    return embedded_ipv4(ip) or ip


def is_public(address: str | IP) -> bool:
    """A globally reachable unicast address, also counting the IPv4 address inside an IPv6 one."""
    ip = parse(address) if isinstance(address, str) else address
    inner = embedded_ipv4(ip)
    for candidate in (ip, inner) if inner is not None else (ip,):
        if not candidate.is_global or candidate.is_multicast:
            return False
    return True
