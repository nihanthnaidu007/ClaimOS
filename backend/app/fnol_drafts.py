"""FNOL draft persistence: resumable claim applications.

Draft IDs are client-generated (localStorage + server share one key), so the
shape is validated before an arbitrary string can become a collection key.
Collections are reached through the `database` module at call time — the same
pattern the workbench and auth routes use — so test patching and multi-process
deployments bind correctly.
"""

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import database
from app.deps import UserRecord, require_authenticated

router = APIRouter()

_DRAFT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{5,79}$")


class DraftIn(BaseModel):
    data: dict = Field(default_factory=dict)


class DraftOut(BaseModel):
    draftId: str
    data: dict
    updatedAt: str


class DraftDeleted(BaseModel):
    ok: bool


def _draft_col():
    return database.db["fnol_drafts"]


def _validate_id(draft_id: str) -> str:
    if not _DRAFT_ID_PATTERN.match(draft_id):
        raise HTTPException(status_code=400, detail="Invalid draft id")
    return draft_id


@router.get("/fnol/drafts/{draft_id}", response_model=DraftOut)
async def get_fnol_draft(draft_id: str, current_user: UserRecord = Depends(require_authenticated)):
    _validate_id(draft_id)
    doc = await _draft_col().find_one({"draft_id": draft_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"draftId": draft_id, "data": doc.get("data") or {}, "updatedAt": doc.get("updated_at", "")}


@router.put("/fnol/drafts/{draft_id}", response_model=DraftOut)
async def put_fnol_draft(
    draft_id: str, body: DraftIn, current_user: UserRecord = Depends(require_authenticated)
):
    _validate_id(draft_id)
    now = datetime.now(timezone.utc).isoformat()
    await _draft_col().update_one(
        {"draft_id": draft_id},
        {"$set": {"data": body.data, "updated_at": now, "updated_by": current_user.email}},
        upsert=True,
    )
    return {"draftId": draft_id, "data": body.data, "updatedAt": now}


@router.delete("/fnol/drafts/{draft_id}", response_model=DraftDeleted)
async def delete_fnol_draft(draft_id: str, current_user: UserRecord = Depends(require_authenticated)):
    _validate_id(draft_id)
    await _draft_col().delete_one({"draft_id": draft_id})
    return {"ok": True}
