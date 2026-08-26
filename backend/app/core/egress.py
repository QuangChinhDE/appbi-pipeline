"""Where a connector is allowed to send traffic.

The Connector Builder lets a user point a connector at any URL they type. That
is the whole point of it, and it is also a server-side request forgery primitive:
the request leaves from inside our network, so it can reach cloud metadata
endpoints, internal services, and anything else the host can route to.

Two layers defend this, and only together:

* **Network** — connector containers run on a separate Docker network that our
  API and Redis are not attached to. This is the layer that actually holds,
  because it does not care what the connector resolves a hostname to at request
  time.
* **This module** — refuses obviously hostile targets so the failure is a clear
  message in the editor instead of a silent timeout. It works in two steps:
  `check_url_syntax` on save (no DNS, so a draft can be saved while the API is
  down or the URL is half-typed), and `check_url` before traffic actually
  leaves. Even the resolving check can be defeated by a name that resolves
  differently afterwards, which is why the network layer above is the one that
  holds.

Operators who genuinely need an internal API can allow it explicitly rather than
turning the whole policy off.

What the network layer does and does not cover, measured rather than assumed.
From the `connectors` network a container can reach:

    product API      blocked
    Redis            blocked
    the Docker host  blocked
    Postgres         reachable, deliberately — it holds the databases a
                     postgres source and destination are supposed to read and
                     write. Access is per-database and per-role, not open.

It is a normal bridge network, so a connector can still reach the public
internet and anything the host itself routes to. That is required: a SaaS
connector has to call out. Restricting *which* external addresses may be
reached is a host firewall or forward-proxy decision, not something a Compose
file can express, and it is listed as an open item in
docs/PRODUCTION_READINESS_REVIEW.md rather than quietly assumed to be handled here.

In AIRBYTE_API deployments the same split applies, because Airbyte's worker is
told to start connector containers on that network (`DOCKER_NETWORK` in
docker-compose.airbyte.yml). Nothing in this module runs there — Airbyte makes
those requests, not us — which is precisely why the network layer has to be the
one that holds.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.core.config import settings
from app.core.errors import ValidationError

# Schemes a connector may speak. Anything else (file://, gopher://, ftp://) has
# no business being reachable from a builder field.
ALLOWED_SCHEMES = {"http", "https"}


def _allowlisted_hosts() -> set[str]:
    return {
        host.strip().lower()
        for host in (settings.egress_allowlist or "").split(",")
        if host.strip()
    }


def _allowlisted_networks() -> list[ipaddress._BaseNetwork]:
    networks: list[ipaddress._BaseNetwork] = []
    for entry in (settings.egress_allowlist or "").split(","):
        candidate = entry.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            # Not a CIDR — it is a hostname, handled by _allowlisted_hosts.
            continue
    return networks


def _is_public(address: ipaddress._BaseAddress) -> bool:
    """Everything the internet cannot route to is off limits by default.

    `is_global` already covers loopback, link-local (169.254.0.0/16 — the cloud
    metadata range), private ranges, multicast and reserved blocks, so this
    stays correct as new special-use ranges are assigned.
    """
    return address.is_global


def resolve_targets(host: str) -> list[ipaddress._BaseAddress]:
    """Every address the hostname currently answers with.

    All of them are checked: a name that returns one public and one private
    address would otherwise pass on the public one and be used for the private.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValidationError(
            f"Không phân giải được tên miền '{host}'.",
            code="EGRESS_DNS_FAILED",
            details={"host": host, "reason": str(exc)[:200]},
        ) from None
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def check_url_syntax(url: str, *, field: str = "base_url") -> None:
    """The cheap half: what the URL says, without asking the network.

    Saving a draft must not depend on the target being reachable — a user types
    a URL character by character, an API can be down, and split-horizon DNS is
    normal. So the save path checks only what is knowable from the string: the
    scheme, that a host is present, and a literal address in a range we refuse.
    """
    parsed = urlparse((url or "").strip())

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValidationError(
            "Chỉ hỗ trợ http:// và https://.",
            code="EGRESS_SCHEME_BLOCKED",
            details={"field": field, "scheme": parsed.scheme,
                     "allowed": sorted(ALLOWED_SCHEMES)},
        )

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValidationError(
            "URL thiếu tên miền.", code="EGRESS_HOST_MISSING",
            details={"field": field},
        )

    if settings.egress_allow_private or host in _allowlisted_hosts():
        return

    # `localhost` never needs a lookup to be recognised.
    if host in {"localhost", "localhost.localdomain"}:
        raise ValidationError(
            "Không thể trỏ connector vào chính máy chủ.",
            code="EGRESS_PRIVATE_ADDRESS",
            details={"field": field, "host": host},
        )

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        return          # a name: only resolution can judge it, and that is later

    if any(literal in network for network in _allowlisted_networks()):
        return
    if not _is_public(literal):
        raise ValidationError(
            f"Địa chỉ '{host}' nằm trong mạng nội bộ. Nếu đây là API nội bộ, "
            "hãy nhờ quản trị viên thêm vào danh sách cho phép.",
            code="EGRESS_PRIVATE_ADDRESS",
            details={"field": field, "host": host, "resolved": str(literal)},
        )


def check_url(url: str, *, field: str = "base_url") -> None:
    """The authoritative check, run at the moment we are about to send traffic.

    This resolves the hostname, so it belongs on the test-read and publish paths
    rather than on save. A name that resolves differently afterwards (DNS
    rebinding) still slips past — the connector network is what stops that.
    """
    check_url_syntax(url, field=field)

    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").strip().lower()

    if settings.egress_allow_private or host in _allowlisted_hosts():
        return

    allow_networks = _allowlisted_networks()
    for address in resolve_targets(host):
        if any(address in network for network in allow_networks):
            continue
        if not _is_public(address):
            raise ValidationError(
                f"Địa chỉ '{host}' trỏ vào mạng nội bộ ({address}). "
                "Nếu đây là API nội bộ, hãy nhờ quản trị viên thêm vào danh sách "
                "cho phép.",
                code="EGRESS_PRIVATE_ADDRESS",
                details={"field": field, "host": host, "resolved": str(address)},
            )
