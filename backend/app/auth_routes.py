"""Auth endpoints: invite-only register, login, refresh rotation, logout.

Token flow (spec security section): login/refresh return a short-TTL JWT
access token in the response body — the client holds it in memory — while the
rotating refresh token travels only as an httpOnly, SameSite=Lax cookie scoped
to /api/auth. Refresh rotation revokes the presented token; presenting an
already-revoked token revokes the whole family (reuse detection).
"""

import secrets as secrets_module
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response

from app import auth_store, security
from app.auth_store import EmailAlreadyRegistered
from app.config import settings
from app.deps import CSRF_COOKIE, REFRESH_COOKIE, require_csrf_header
from app.rate_limit import limiter
from app.schemas import (
    AuthTokensResponse,
    LoginRequest,
    PublicUser,
    RegisterRequest,
    UserRecord,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _cookie_flags() -> dict:
    return {
        "secure": settings.refresh_cookie_secure,
        "samesite": "lax",
        "path": "/api/auth",
        "max_age": settings.refresh_token_ttl_days * 24 * 60 * 60,
    }


def _set_cookies(response: Response, refresh_token: str, csrf_token: str) -> None:
    flags = _cookie_flags()
    response.set_cookie(REFRESH_COOKIE, refresh_token, httponly=True, **flags)
    # Readable by the SPA (double-submit); never attached to cross-site requests.
    # document.cookie filters by the current document path, and the SPA lives at
    # /, /new-claim, /claims/:id — so this cookie cannot be path-scoped to
    # /api/auth or silent session restore can never read it.
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, **{**flags, "path": "/"})


def _clear_cookies(response: Response) -> None:
    for name, httponly in ((REFRESH_COOKIE, True), (CSRF_COOKIE, False)):
        response.delete_cookie(
            name,
            path="/api/auth",
            secure=settings.refresh_cookie_secure,
            samesite="lax",
            httponly=httponly,
        )


def _tokens_response(response: Response, user: UserRecord) -> AuthTokensResponse:
    public_user = PublicUser(
        id=user.id, email=user.email, role=user.role, createdAt=user.created_at
    )
    access_token = security.create_access_token(
        user_id=user.id, email=user.email, role=user.role
    )
    return AuthTokensResponse(
        accessToken=access_token,
        expiresInSeconds=settings.access_token_ttl_minutes * 60,
        user=public_user,
    )


@router.post("/register", status_code=201, response_model=PublicUser)
async def register(payload: RegisterRequest):
    """Invite-only signup. An unset INVITE_CODE disables registration entirely,
    so the production default is a closed system."""
    if not settings.invite_code:
        raise HTTPException(status_code=403, detail="Registration is disabled")
    if not secrets_module.compare_digest(payload.inviteCode, settings.invite_code):
        # Constant-time comparison: invite codes are bearer credentials.
        raise HTTPException(status_code=403, detail="Invalid invite code")
    try:
        return await auth_store.create_user(payload.email, payload.password, payload.role)
    except EmailAlreadyRegistered:
        raise HTTPException(status_code=409, detail="Email already registered") from None


@router.post("/login", response_model=AuthTokensResponse)
@limiter.limit(settings.login_rate_limit)
async def login(request: Request, payload: LoginRequest, response: Response):
    user = await auth_store.get_user_by_email(payload.email)
    # One generic message for unknown email and wrong password (no enumeration).
    if not user or not security.verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    refresh_token, csrf_token = await auth_store.create_refresh_session(user.id)
    _set_cookies(response, refresh_token, csrf_token)
    return _tokens_response(response, user)


@router.post("/refresh", response_model=AuthTokensResponse)
async def refresh(request: Request, response: Response):
    refresh_cookie = request.cookies.get(REFRESH_COOKIE)
    if not refresh_cookie:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    session = await auth_store.find_refresh_session(refresh_cookie)
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if session.revoked:
        # Reuse of a rotated/revoked token: assume the family is compromised.
        await auth_store.revoke_family(session.family_id)
        raise HTTPException(
            status_code=401, detail="Refresh token reuse detected; session revoked"
        )
    if session.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Refresh token expired")
    require_csrf_header(request, session)
    new_refresh, new_csrf = await auth_store.rotate_refresh_session(session)
    _set_cookies(response, new_refresh, new_csrf)
    user = await auth_store.get_user_by_id(session.user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return _tokens_response(response, user)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response):
    refresh_cookie = request.cookies.get(REFRESH_COOKIE)
    if refresh_cookie:
        session = await auth_store.find_refresh_session(refresh_cookie)
        if session and not session.revoked:
            require_csrf_header(request, session)
            await auth_store.revoke_family(session.family_id)
    _clear_cookies(response)
