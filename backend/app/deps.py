"""FastAPI auth dependencies and CSRF defense.

Auth model (spec security section):
- Access token: short-TTL JWT, sent as `Authorization: Bearer` from client
  memory. Cross-site attackers cannot set this header, which is the token
  binding half of the CSRF defense for every bearer-authenticated route.
- Refresh token: opaque, httpOnly cookie scoped to /api/auth. Its routes
  additionally require a session-bound CSRF token (double-submit header).

CSRF defense on unsafe methods: when a browser sends Origin/Referer (it does
on every cross-site unsafe request), the origin must be in the CORS allowlist.
"""

import secrets as secrets_module
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request
from jwt import InvalidTokenError

from app import auth_store, security
from app.config import settings
from app.schemas import UserRecord

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

REFRESH_COOKIE = "claimos_refresh"
CSRF_COOKIE = "claimos_csrf"

_NOT_AUTHENTICATED = {"WWW-Authenticate": "Bearer"}


def _origin_of(request: Request) -> str | None:
    """Origin header value, else the origin embedded in Referer, else None."""
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/")
    referer = request.headers.get("referer")
    if referer:
        parts = urlparse(referer)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    return None


async def verify_csrf(request: Request) -> None:
    """Reject unsafe-method requests whose Origin/Referer points outside the
    CORS allowlist. No Origin/Referer means a non-browser client — auth is
    still enforced per-route, so this does not open anything by itself."""
    if request.method not in UNSAFE_METHODS:
        return
    origin = _origin_of(request)
    if origin is None or "*" in settings.cors_origins:
        return
    if origin not in [allowed.rstrip("/") for allowed in settings.cors_origins]:
        raise HTTPException(status_code=403, detail="Cross-site request rejected")


async def get_current_user(request: Request) -> UserRecord:
    """Resolve the caller from the Bearer access token; 401 when absent,
    invalid, expired, or the user no longer exists."""
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="Not authenticated", headers=_NOT_AUTHENTICATED
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = security.decode_access_token(token)
    except InvalidTokenError:
        raise HTTPException(
            status_code=401, detail="Invalid or expired token", headers=_NOT_AUTHENTICATED
        ) from None
    user = await auth_store.get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(
            status_code=401, detail="User no longer exists", headers=_NOT_AUTHENTICATED
        )
    return user


async def require_authenticated(user: UserRecord = Depends(get_current_user)) -> UserRecord:
    """Any authenticated role — for routes both adjusters and customers use."""
    return user


async def require_adjuster(user: UserRecord = Depends(get_current_user)) -> UserRecord:
    if user.role != "adjuster":
        raise HTTPException(status_code=403, detail="Adjuster role required")
    return user


async def require_customer(user: UserRecord = Depends(get_current_user)) -> UserRecord:
    if user.role != "customer":
        raise HTTPException(status_code=403, detail="Customer role required")
    return user


def require_csrf_header(request: Request, session: auth_store.RefreshSession) -> None:
    """Session-bound CSRF check for cookie-authenticated unsafe routes: the
    X-CSRF-Token header must equal the non-httpOnly CSRF cookie AND the token
    bound to this refresh session server-side."""
    header_token = request.headers.get("x-csrf-token", "")
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    mismatched = not header_token or not secrets_module.compare_digest(
        header_token, cookie_token
    )
    if mismatched:
        raise HTTPException(status_code=403, detail="CSRF token missing or mismatched")
    if not secrets_module.compare_digest(security.sha256_hex(header_token), session.csrf_hash):
        raise HTTPException(status_code=403, detail="CSRF token does not match session")
