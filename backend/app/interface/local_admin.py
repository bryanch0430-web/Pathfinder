"""Local admin mode: whether a request comes from the owner's own machine.

With ``LOCAL_ADMIN=true`` (honoured only in development and test, see ``Settings.local_admin_active``) a
caller whose address is loopback opens the admin routes without the admin token. The address is the ASGI
peer, ``request.client.host``. The application never reads ``X-Forwarded-For`` or ``Forwarded`` itself;
uvicorn replaces the peer with a forwarded address only for a proxy in ``--forwarded-allow-ips``.
"""

from __future__ import annotations

import ipaddress

from fastapi import Request

from app.config import Settings


def is_loopback(host: str | None) -> bool:
    """127.0.0.0/8, ::1 (also as an IPv4-mapped address) or the name ``localhost``."""
    if not host:
        return False
    if host.strip().lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host.strip().strip("[]"))
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return address.is_loopback or bool(mapped and mapped.is_loopback)


def local_admin_for(request: Request, settings: Settings) -> bool:
    """True when local admin mode is on and this caller is on the same machine."""
    return settings.local_admin_active and is_loopback(request.client.host if request.client else None)
