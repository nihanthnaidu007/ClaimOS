"""In-app notification center endpoints (authenticated).

Scope rule: a customer sees only notifications addressed to their own email —
the fan-out stamps `recipient_email` (claim contact, else policy holder) and
both routes filter on `current_user.email`. Adjusters see their own too; there
is no cross-customer surface here. CSRF for the unsafe method is enforced by
the app-wide `verify_csrf` dependency.
"""

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends

from app.deps import require_authenticated
from app.schemas import (
    MarkReadRequest,
    MarkReadResponse,
    NotificationListResponse,
)
import database

router = APIRouter(prefix="/notifications", tags=["notifications"])

logger = structlog.get_logger("claimos.notifications")

_LIST_LIMIT = 50


@router.get("", response_model=NotificationListResponse)
async def list_notifications(current_user=Depends(require_authenticated)):
    email = current_user.email.strip().lower()
    docs = (
        await database.notifications_col.find({"recipient_email": email}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(_LIST_LIMIT)
    )
    items = [
        {
            "id": d["id"],
            "claimId": d["claim_id"],
            "milestone": d["milestone"],
            "title": d["title"],
            "body": d["body"],
            "read": d.get("read", False),
            "createdAt": d["created_at"],
        }
        for d in docs
    ]
    unread = sum(1 for i in items if not i["read"])
    return {"notifications": items, "unreadCount": unread}


@router.post("/mark-read", response_model=MarkReadResponse)
async def mark_notifications_read(
    payload: MarkReadRequest,
    current_user=Depends(require_authenticated),
):
    # Scoped by recipient: IDs belonging to someone else simply don't match.
    email = current_user.email.strip().lower()
    result = await database.notifications_col.update_many(
        {"id": {"$in": payload.ids}, "recipient_email": email, "read": False},
        {"$set": {"read": True, "read_at": datetime.now(timezone.utc).isoformat()}},
    )
    logger.info("notifications_marked_read", count=result.modified_count, user=email)
    return {"markedRead": result.modified_count}
