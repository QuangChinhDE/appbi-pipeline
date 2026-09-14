"""Proving that a Google ID token really came from Google.

The browser hands us a string and says it identifies somebody. That claim is
worth nothing until it is checked against Google's signing keys, and checked
completely -- a signature that verifies against the wrong audience belongs to a
different application, and a token whose email is unverified belongs to somebody
who typed an address rather than proved it.

No new dependency. `google-auth` does this in one call and brings a transport
stack with it; PyJWT and httpx are already here and the work is a JWKS fetch and
a decode with the right options turned on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from app.core.config import settings
from app.core.errors import ForbiddenError, UnauthorizedError

#: Google publishes its public keys here and rotates them without notice.
CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"

#: Both spellings are current; Google has issued each at different times and a
#: token carrying the one we did not list is rejected for no real reason.
ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})

#: How long a fetched key set is trusted. Google's own Cache-Control is hours;
#: this is shorter so a rotation is picked up without a restart, and long
#: enough that a sign-in does not fetch keys every time.
_CACHE_SECONDS = 3600

_keys: dict[str, object] = {}
_fetched_at: float = 0.0


@dataclass(frozen=True)
class GoogleIdentity:
    """Who Google says this is."""

    #: Stable for the life of the Google account, unlike the email. This is what
    #: an account is matched on once linked, so that a workspace administrator
    #: renaming somebody's email does not hand their seat to a stranger who
    #: later claims the old address.
    subject: str
    email: str
    full_name: str
    picture: str | None


async def _signing_keys(*, force: bool = False) -> dict[str, object]:
    global _fetched_at
    fresh = _keys and (time.time() - _fetched_at) < _CACHE_SECONDS
    if fresh and not force:
        return _keys

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(CERTS_URL)
            response.raise_for_status()
            document = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Keys we already hold are better than no sign-in at all: Google being
        # briefly unreachable should not log everybody out of the product.
        if _keys:
            return _keys
        raise UnauthorizedError(
            "Could not reach Google to verify the sign-in.",
            code="GOOGLE_KEYS_UNREACHABLE",
        ) from exc

    parsed: dict[str, object] = {}
    for key in document.get("keys", []):
        kid = key.get("kid")
        if kid:
            parsed[str(kid)] = RSAAlgorithm.from_jwk(key)
    if parsed:
        _keys.clear()
        _keys.update(parsed)
        _fetched_at = time.time()
    return _keys


async def verify(credential: str) -> GoogleIdentity:
    """Check a Google ID token from end to end, or refuse it.

    Every failure here is 401 or 403 with a code the interface can translate.
    None of them say which check failed in a way that would help somebody
    probing: a wrong audience and an expired token read the same to the caller.
    """
    if not settings.google_login_ready:
        raise ForbiddenError(
            "Google sign-in is not configured for this deployment.",
            code="GOOGLE_LOGIN_UNAVAILABLE",
        )

    try:
        header = jwt.get_unverified_header(credential)
    except jwt.PyJWTError as exc:
        raise UnauthorizedError(
            "That Google sign-in could not be verified.", code="GOOGLE_TOKEN_INVALID",
        ) from exc

    kid = str(header.get("kid") or "")
    keys = await _signing_keys()
    key = keys.get(kid)
    if key is None:
        # A key id we have not seen is the normal shape of a rotation, so try
        # once with a fresh fetch before calling the token bad.
        keys = await _signing_keys(force=True)
        key = keys.get(kid)
    if key is None:
        raise UnauthorizedError(
            "That Google sign-in could not be verified.", code="GOOGLE_TOKEN_INVALID",
        )

    try:
        claims = jwt.decode(
            credential,
            key=key,
            algorithms=["RS256"],
            audience=settings.auth_google_client_id.strip(),
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise UnauthorizedError(
            "That Google sign-in could not be verified.", code="GOOGLE_TOKEN_INVALID",
        ) from exc

    if str(claims.get("iss")) not in ISSUERS:
        raise UnauthorizedError(
            "That Google sign-in could not be verified.", code="GOOGLE_TOKEN_INVALID",
        )

    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise ForbiddenError(
            "That Google account has no email address to match.",
            code="GOOGLE_EMAIL_MISSING",
        )
    # Google sets this false for accounts that claimed an address without
    # proving it. Matching one to a user by email would let somebody take a
    # colleague's seat by typing their address into a Google profile.
    if claims.get("email_verified") is not True:
        raise ForbiddenError(
            "That Google account's email address is not verified.",
            code="GOOGLE_EMAIL_UNVERIFIED",
        )

    allowed = settings.google_domains
    if allowed and email.split("@")[-1] not in allowed:
        raise ForbiddenError(
            "That Google account is not from a domain this workspace accepts.",
            code="GOOGLE_DOMAIN_NOT_ALLOWED",
            details={"domains": ", ".join(allowed)},
        )

    picture = claims.get("picture")
    return GoogleIdentity(
        subject=str(claims["sub"]),
        email=email,
        full_name=str(claims.get("name") or email.split("@")[0]),
        picture=str(picture) if isinstance(picture, str) and picture.strip() else None,
    )
