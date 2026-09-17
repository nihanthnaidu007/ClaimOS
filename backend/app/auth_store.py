"""Persistence for users and refresh-token sessions.

Refresh sessions implement rotation with reuse detection: every refresh issues
a new token in the same `family_id` and revokes the presented one. Presenting
a token that is already revoked means either a replayed stolen token or a
desynchronized client — in both cases the whole family is revoked and the
attacker (and the legitimate client) must re-authenticate.
"""

import secrets
from datetime import datetime, timedelta, timezone

import structlog
from pymongo.errors import DuplicateKeyError

import database
from app.config import settings
from app.schemas import PublicUser, UserRecord
from app.security import generate_csrf_token, generate_refresh_token, hash_password, sha256_hex

logger = structlog.get_logger(__name__)


class EmailAlreadyRegistered(Exception):
    """Raised on a unique-index violation for a duplicate signup email."""


def _public_user(user: UserRecord) -> PublicUser:
    return PublicUser(
        id=user.id, email=user.email, role=user.role, createdAt=user.created_at
    )


async def get_user_by_email(email: str) -> UserRecord | None:
    doc = await database.users_col.find_one({"email": email.strip().lower()}, {"_id": 0})
    return UserRecord(**doc) if doc else None


async def get_user_by_id(user_id: str) -> UserRecord | None:
    doc = await database.users_col.find_one({"id": user_id}, {"_id": 0})
    return UserRecord(**doc) if doc else None


async def create_user(email: str, password: str, role: str) -> PublicUser:
    email = email.strip().lower()
    user = UserRecord(
        id=f"usr_{secrets.token_hex(8)}",
        email=email,
        password_hash=hash_password(password),
        role=role,  # type: ignore[arg-type]  # validated by the route's schema
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        await database.users_col.insert_one(user.model_dump())
    except DuplicateKeyError as exc:
        raise EmailAlreadyRegistered(email) from exc
    return _public_user(user)


# ---- Refresh-token sessions ----


class RefreshSession:
    """Inflated from a refresh_tokens document; `token_hash` and `csrf_hash`
    are SHA-256 digests — raw token values never round-trip through the DB."""

    def __init__(self, doc: dict) -> None:
        self.token_hash: str = doc["token_hash"]
        self.family_id: str = doc["family_id"]
        self.user_id: str = doc["user_id"]
        self.csrf_hash: str = doc["csrf_hash"]
        self.expires_at: datetime = datetime.fromisoformat(doc["expires_at"])
        self.revoked: bool = doc.get("revoked", False)


async def create_refresh_session(user_id: str) -> tuple[str, str]:
    """Create a new refresh session; returns (raw_refresh_token, raw_csrf_token)."""
    family_id = f"fam_{secrets.token_hex(8)}"
    return await _insert_session(user_id, family_id)


async def _insert_session(user_id: str, family_id: str) -> tuple[str, str]:
    raw_token = generate_refresh_token()
    raw_csrf = generate_csrf_token()
    now = datetime.now(timezone.utc)
    await database.refresh_tokens_col.insert_one(
        {
            "token_hash": sha256_hex(raw_token),
            "family_id": family_id,
            "user_id": user_id,
            "csrf_hash": sha256_hex(raw_csrf),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=settings.refresh_token_ttl_days)).isoformat(),
            "revoked": False,
        }
    )
    return raw_token, raw_csrf


async def find_refresh_session(raw_token: str) -> RefreshSession | None:
    doc = await database.refresh_tokens_col.find_one({"token_hash": sha256_hex(raw_token)})
    return RefreshSession(doc) if doc else None


async def rotate_refresh_session(session: RefreshSession) -> tuple[str, str]:
    """Revoke the presented token and issue a fresh one in the same family."""
    await database.refresh_tokens_col.update_one(
        {"token_hash": session.token_hash}, {"$set": {"revoked": True}}
    )
    return await _insert_session(session.user_id, session.family_id)


async def revoke_family(family_id: str) -> None:
    result = await database.refresh_tokens_col.update_many(
        {"family_id": family_id, "revoked": False}, {"$set": {"revoked": True}}
    )
    logger.info(
        "refresh_family_revoked", family_id=family_id, revoked_count=result.modified_count
    )
