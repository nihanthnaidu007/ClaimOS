"""Claim assignment (spec F10) — round-robin over active adjuster accounts.

Auto-assignment runs at claim creation: the claim lands on the active adjuster
with the oldest last-assignment time, so rotation is stable by
least-recently-assigned. There is no assignment-counter collection — the
rotation derives from stored claim state (assignee_id/assigned_at), so Mongo
supplies the rows and Python picks; the same determinism rule the analytics
module follows. Manual reassignment is the workbench's audited write
(workbench_routes.py) and shares these claim fields.
"""

from datetime import datetime, timezone

import database
from app.config import settings
from app.schemas import UserRecord

# The claim doc stamps who made the current assignment: "auto" for the
# round-robin pick, the acting adjuster's email for a manual reassign.
AUTO_ASSIGNER = "auto"


def last_assignment_by_assignee(claims: list[dict]) -> dict[str, str]:
    """assignee_id -> the newest assigned_at among that assignee's claims."""
    latest: dict[str, str] = {}
    for claim in claims:
        assignee_id = claim.get("assignee_id")
        assigned_at = str(claim.get("assigned_at") or "")
        if assignee_id and assigned_at > latest.get(assignee_id, ""):
            latest[assignee_id] = assigned_at
    return latest


def pick_next_assignee_id(users: list[UserRecord], claims: list[dict]) -> str | None:
    """The active adjuster least-recently-assigned, or None with no adjusters.

    Stable ordering (spec F10): never-assigned adjusters first, then ascending
    last-assignment time (ISO strings compare chronologically); ties break on
    user id so identical data always picks the same adjuster.
    """
    active = [user for user in users if user.role == "adjuster" and user.active]
    if not active:
        return None
    latest = last_assignment_by_assignee(claims)
    active.sort(key=lambda user: (latest.get(user.id, ""), user.id))
    return active[0].id


async def choose_auto_assignee() -> str | None:
    """Round-robin pick for a new claim, honoring AUTO_ASSIGN (spec F10.1).

    AUTO_ASSIGN=none (or an adjuster-less deployment) leaves the claim
    unassigned — a submission is never blocked on assignment. Collections are
    read through the `database` module at call time (not imported at module
    load) so test patching and multi-process deployments bind correctly.
    """
    if settings.auto_assign != "round_robin":
        return None
    users = await database.users_col.find(
        {"role": "adjuster", "active": {"$ne": False}}, {"_id": 0}
    ).to_list(500)
    claims = await database.claims_col.find(
        {}, {"_id": 0, "assignee_id": 1, "assigned_at": 1}
    ).to_list(None)
    return pick_next_assignee_id([UserRecord(**doc) for doc in users], claims)


def assignment_fields(assignee_id: str | None, at: datetime | None = None) -> dict:
    """The claim-document fields recording the current assignment.

    Unassigned claims store explicit Nones so the queue's "unassigned" filter
    and analytics read one shape for both never-assigned and legacy claims.
    """
    return {
        "assignee_id": assignee_id,
        "assigned_at": (at or datetime.now(timezone.utc)).isoformat() if assignee_id else None,
        "assigned_by": AUTO_ASSIGNER if assignee_id else None,
    }
