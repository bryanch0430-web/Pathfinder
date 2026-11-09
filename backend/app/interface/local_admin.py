"""Local admin mode: whether a request comes from the owner's own machine, from the owner's own page.

With ``LOCAL_ADMIN=true`` (honoured only in development and test, see ``Settings.local_admin_active``) a
request opens the admin routes without the admin token only when all of these hold:

- its peer address is loopback. That is the ASGI peer, ``request.client.host``. The application never reads
  ``X-Forwarded-For`` or ``Forwarded`` itself; uvicorn replaces the peer with a forwarded address only for a
  proxy in ``--forwarded-allow-ips``, and ``Settings.check_secrets`` refuses ``FORWARDED_ALLOW_IPS=*``;
- its ``Host`` header names this machine (``localhost``, ``127.0.0.1`` or ``[::1]``, any port). Any page the
  owner's browser opens is a loopback peer, so a DNS-rebinding page (``Host: rebind.example``) is refused
  here. Behind the Vite proxy, ``changeOrigin`` rewrites the Host, and Vite's own ``allowedHosts`` check
  refuses the rebinding name;
- for a method other than GET, HEAD and OPTIONS, its ``Origin`` header is present and is a loopback origin
  (``http(s)://localhost``, ``127.0.0.1`` or ``[::1]``, any port). A cross-site form or fetch from the
  owner's browser carries the other site's Origin, and a write without one is refused.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from fastapi import Request

from app.config import Settings

#: Methods that change nothing, which a plain navigation or fetch sends without an Origin.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
#: Names of this machine accepted in a Host header or an Origin, as written there (IPv6 in brackets).
LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "[::1]"})


def is_loopback(host: str | None) -> bool:
    """A loopback IP address: 127.0.0.0/8 or ::1, also as an IPv4-mapped address. Never a name."""
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host.strip())
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return address.is_loopback or bool(mapped and mapped.is_loopback)


def _host_name(netloc: str) -> str:
    """The name part of ``host[:port]``, lower case; an IPv6 address keeps its brackets."""
    netloc = netloc.strip().lower()
    if netloc.startswith("["):
        end = netloc.find("]")
        return netloc[: end + 1] if end != -1 else ""
    return netloc.split(":", 1)[0]


def is_loopback_host_header(value: str | None) -> bool:
    """A Host header that names this machine, with or without a port."""
    return bool(value) and _host_name(value) in LOOPBACK_NAMES


def is_loopback_origin(value: str | None) -> bool:
    """An Origin of a page served by this machine: ``http(s)://localhost|127.0.0.1|[::1][:port]`` and nothing more."""
    if not value:
        return False
    try:
        parts = urlsplit(value.strip())
        parts.port  # noqa: B018 - raises ValueError for a port that is not a number
    except ValueError:
        return False
    if parts.scheme not in {"http", "https"} or parts.path or parts.query or parts.fragment or "@" in parts.netloc:
        return False
    return _host_name(parts.netloc) in LOOPBACK_NAMES


def local_admin_for(request: Request, settings: Settings) -> bool:
    """True when local admin mode is on and this request is the owner's own page on the owner's own machine."""
    if not settings.local_admin_active:
        return False
    if not is_loopback(request.client.host if request.client else None):
        return False
    if not is_loopback_host_header(request.headers.get("host")):
        return False
    if request.method.upper() not in SAFE_METHODS and not is_loopback_origin(request.headers.get("origin")):
        return False
    return True
