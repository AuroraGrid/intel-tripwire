"""SSRF-safe webhook URL validation for registration and delivery."""
from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable


BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
}


def _is_blocked_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def resolve_public_https_url(
    value: str,
    *,
    resolver: Callable[[str, int], list[tuple]] | None = None,
    allow_ports: set[int] | None = None,
) -> str:
    """Validate an HTTPS URL and ensure every resolved address is public.

    Returns the normalized URL string. Raises ValueError on policy violation.
    """
    url = str(value or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("webhook URL must use https")
    if parsed.username or parsed.password:
        raise ValueError("webhook URL must not include credentials")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("webhook URL host is required")
    if host in BLOCKED_HOSTNAMES or host.endswith(".local") or host.endswith(".internal"):
        raise ValueError("local or metadata webhook destinations are not allowed")
    port = parsed.port or 443
    allowed = allow_ports or {443}
    if port not in allowed:
        raise ValueError(f"webhook URL port {port} is not allowed")

    # Literal IP in hostname.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_blocked_ip(literal):
            raise ValueError("private webhook destinations are not allowed")
        return url

    resolve = resolver or socket.getaddrinfo
    try:
        results = resolve(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"webhook host could not be resolved: {host}") from exc
    if not results:
        raise ValueError(f"webhook host resolved to no addresses: {host}")

    seen: set[str] = set()
    for item in results:
        sockaddr = item[4]
        ip_text = sockaddr[0]
        if ip_text in seen:
            continue
        seen.add(ip_text)
        try:
            address = ipaddress.ip_address(ip_text)
        except ValueError as exc:
            raise ValueError(f"webhook host resolved to invalid address: {ip_text}") from exc
        if _is_blocked_ip(address):
            raise ValueError("private webhook destinations are not allowed")
    return url


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, "redirects are not allowed for webhooks", headers, fp)


def safe_urlopen(request: urllib.request.Request, timeout: float = 8, opener=None):
    """Open a webhook URL without following redirects, after re-validating the target."""
    resolve_public_https_url(request.full_url)
    if opener is not None:
        return opener(request, timeout=timeout)
    built = urllib.request.build_opener(NoRedirectHandler())
    return built.open(request, timeout=timeout)
