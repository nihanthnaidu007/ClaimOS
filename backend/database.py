import secrets
from datetime import datetime, timezone

import structlog
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import BulkWriteError, DuplicateKeyError, PyMongoError

from app.config import settings

logger = structlog.get_logger(__name__)

client = AsyncIOMotorClient(settings.mongo_url, serverSelectionTimeoutMS=10000)
db = client[settings.db_name]

# Collections
policies_col = db.policies
claims_col = db.claims
claim_documents_col = db.claim_documents
counters_col = db.counters
events_col = db.events
claim_runs_col = db.claim_runs
seed_state_col = db.seed_state
users_col = db.users
refresh_tokens_col = db.refresh_tokens
# Adjuster workbench: append-only decision audit trail. Writers only ever
# insert; there is no update or delete path in the application.
audit_log_col = db.audit_log
notifications_col = db.notifications
# LLM usage telemetry (app/usage.py): resolved dynamically there via
# db[USAGE_COLLECTION]; the constant lives here so ensure-seeding can index it
# without a circular import.
LLM_USAGE_COLLECTION = "llm_usage"
# Document checklists (spec F3): claim-scoped asks with an independent
# lifecycle — requested → received (F4 upload) or waived.
document_requests_col = db.document_requests
# Spec F13: decision-letter templates; the default is seeded at startup.
letter_templates_col = db.letter_templates

# Saved workbench views (spec F12): one adjuster's named queue-filter presets.
# Views are private to their owner — every read is scoped by owner_id.
workbench_views_col = db.workbench_views

# Adjuster <-> customer claim message threads (F5): append-only conversation.
claim_messages_col = db.claim_messages
# F11 internal notes: adjuster-only working notes, never copied onto claims,
# agent traces, or the event stream the customer portal reads.
claim_notes_col = db.claim_notes

SEED_MARKER_ID = "seed:v1"

SEED_POLICIES = [
    {
        "id": "POL-001",
        "policy_number": "AUTO-2024-001847",
        "holder_name": "Sarah Chen",
        "holder_email": "sarah.chen@example.com",
        "holder_phone": "+1-555-0101",
        "policy_type": "auto",
        "status": "active",
        "coverage_limit": 50000.00,
        "deductible": 500.00,
        "monthly_premium": 185.00,
        "start_date": "2024-01-15",
        "end_date": "2025-01-15",
        "covered_events": ["accident", "theft", "vandalism", "weather_damage"]
    },
    {
        "id": "POL-002",
        "policy_number": "HOME-2023-009234",
        "holder_name": "Marcus Johnson",
        "holder_email": "marcus.j@example.com",
        "holder_phone": "+1-555-0102",
        "policy_type": "home",
        "status": "active",
        "coverage_limit": 250000.00,
        "deductible": 1000.00,
        "monthly_premium": 320.00,
        "start_date": "2023-06-01",
        "end_date": "2025-06-01",
        "covered_events": ["fire", "flood", "theft", "vandalism", "earthquake"]
    },
    {
        "id": "POL-003",
        "policy_number": "DEV-2024-003381",
        "holder_name": "Priya Patel",
        "holder_email": "priya.patel@example.com",
        "holder_phone": "+1-555-0103",
        "policy_type": "device",
        "status": "active",
        "coverage_limit": 2000.00,
        "deductible": 100.00,
        "monthly_premium": 12.00,
        "start_date": "2024-02-01",
        "end_date": "2025-02-01",
        "covered_events": ["accidental_damage", "theft", "malfunction"]
    },
    {
        "id": "POL-004",
        "policy_number": "AUTO-2022-007712",
        "holder_name": "David Kim",
        "holder_email": "david.kim@example.com",
        "holder_phone": "+1-555-0104",
        "policy_type": "auto",
        "status": "expired",
        "coverage_limit": 35000.00,
        "deductible": 750.00,
        "monthly_premium": 150.00,
        "start_date": "2022-08-01",
        "end_date": "2023-08-01",
        "covered_events": ["accident", "theft", "vandalism"]
    },
    {
        "id": "POL-005",
        "policy_number": "HEALTH-2024-011023",
        "holder_name": "Elena Rodriguez",
        "holder_email": "elena.r@example.com",
        "holder_phone": "+1-555-0105",
        "policy_type": "health",
        "status": "active",
        "coverage_limit": 100000.00,
        "deductible": 2500.00,
        "monthly_premium": 450.00,
        "start_date": "2024-01-01",
        "end_date": "2025-01-01",
        "covered_events": ["hospitalization", "surgery", "emergency", "specialist_visit"]
    },
    {
        "id": "POL-006",
        "policy_number": "HOME-2024-004455",
        "holder_name": "James Wilson",
        "holder_email": "james.w@example.com",
        "holder_phone": "+1-555-0106",
        "policy_type": "home",
        "status": "active",
        "coverage_limit": 180000.00,
        "deductible": 1500.00,
        "monthly_premium": 275.00,
        "start_date": "2024-03-01",
        "end_date": "2025-03-01",
        "covered_events": ["fire", "theft", "vandalism", "wind_damage"]
    },
    {
        "id": "POL-007",
        "policy_number": "AUTO-2024-008899",
        "holder_name": "Aisha Okafor",
        "holder_email": "aisha.o@example.com",
        "holder_phone": "+1-555-0107",
        "policy_type": "auto",
        "status": "active",
        "coverage_limit": 45000.00,
        "deductible": 600.00,
        "monthly_premium": 195.00,
        "start_date": "2024-01-20",
        "end_date": "2025-01-20",
        "covered_events": ["accident", "theft", "vandalism", "weather_damage", "hit_and_run"]
    },
    {
        "id": "POL-008",
        "policy_number": "DEV-2023-002210",
        "holder_name": "Tom Brennan",
        "holder_email": "tom.b@example.com",
        "holder_phone": "+1-555-0108",
        "policy_type": "device",
        "status": "suspended",
        "coverage_limit": 1500.00,
        "deductible": 150.00,
        "monthly_premium": 10.00,
        "start_date": "2023-05-15",
        "end_date": "2024-05-15",
        "covered_events": ["accidental_damage", "theft"]
    },
    {
        "id": "POL-009",
        "policy_number": "HEALTH-2023-008876",
        "holder_name": "Lisa Chang",
        "holder_email": "lisa.c@example.com",
        "holder_phone": "+1-555-0109",
        "policy_type": "health",
        "status": "active",
        "coverage_limit": 75000.00,
        "deductible": 1000.00,
        "monthly_premium": 380.00,
        "start_date": "2023-09-01",
        "end_date": "2025-09-01",
        "covered_events": ["hospitalization", "surgery", "emergency", "dental", "vision"]
    },
    {
        "id": "POL-010",
        "policy_number": "AUTO-2024-012001",
        "holder_name": "Raj Mehta",
        "holder_email": "raj.m@example.com",
        "holder_phone": "+1-555-0110",
        "policy_type": "auto",
        "status": "active",
        "coverage_limit": 60000.00,
        "deductible": 400.00,
        "monthly_premium": 210.00,
        "start_date": "2024-02-15",
        "end_date": "2025-02-15",
        "covered_events": ["accident", "theft", "vandalism", "weather_damage", "total_loss"]
    }
]

SEED_HISTORICAL_CLAIMS = [
    {"policy_number": "AUTO-2024-001847", "claim_date": "2024-06-15", "incident_date": "2024-06-10", "incident_type": "vandalism", "claimed_amount": 1200.00, "status": "approved", "risk_score": 12, "decision_reason": "Minor vandalism claim, well-documented with police report."},
    {"policy_number": "HOME-2023-009234", "claim_date": "2024-01-20", "incident_date": "2024-01-18", "incident_type": "theft", "claimed_amount": 8500.00, "status": "approved", "risk_score": 22, "decision_reason": "Home burglary with police report and inventory list provided."},
    {"policy_number": "HOME-2023-009234", "claim_date": "2024-08-05", "incident_date": "2024-08-01", "incident_type": "fire", "claimed_amount": 45000.00, "status": "under_review", "risk_score": 55, "decision_reason": "Large fire damage claim requiring additional investigation."},
    {"policy_number": "DEV-2024-003381", "claim_date": "2024-04-10", "incident_date": "2024-04-08", "incident_type": "accidental_damage", "claimed_amount": 850.00, "status": "approved", "risk_score": 15, "decision_reason": "Dropped laptop, screen replacement covered."},
    {"policy_number": "HEALTH-2024-011023", "claim_date": "2024-03-22", "incident_date": "2024-03-20", "incident_type": "emergency", "claimed_amount": 12000.00, "status": "approved", "risk_score": 18, "decision_reason": "Emergency room visit with hospital documentation."},
    {"policy_number": "HEALTH-2024-011023", "claim_date": "2024-07-15", "incident_date": "2024-07-12", "incident_type": "surgery", "claimed_amount": 35000.00, "status": "approved", "risk_score": 25, "decision_reason": "Scheduled surgery, pre-authorized."},
    {"policy_number": "AUTO-2024-008899", "claim_date": "2024-05-10", "incident_date": "2024-05-08", "incident_type": "accident", "claimed_amount": 5200.00, "status": "approved", "risk_score": 20, "decision_reason": "Rear-end collision, other driver at fault."},
    {"policy_number": "AUTO-2024-008899", "claim_date": "2024-09-01", "incident_date": "2024-08-28", "incident_type": "hit_and_run", "claimed_amount": 3800.00, "status": "approved", "risk_score": 28, "decision_reason": "Hit and run in parking lot with security footage."},
    {"policy_number": "HEALTH-2023-008876", "claim_date": "2024-02-10", "incident_date": "2024-02-08", "incident_type": "dental", "claimed_amount": 2800.00, "status": "approved", "risk_score": 10, "decision_reason": "Routine dental procedure, covered under plan."},
    {"policy_number": "HEALTH-2023-008876", "claim_date": "2024-06-20", "incident_date": "2024-06-18", "incident_type": "hospitalization", "claimed_amount": 18000.00, "status": "approved", "risk_score": 22, "decision_reason": "3-day hospitalization with full documentation."},
    {"policy_number": "AUTO-2024-012001", "claim_date": "2024-04-05", "incident_date": "2024-04-02", "incident_type": "weather_damage", "claimed_amount": 4500.00, "status": "approved", "risk_score": 14, "decision_reason": "Hail damage, documented with photos and weather report."},
    {"policy_number": "AUTO-2024-012001", "claim_date": "2024-08-20", "incident_date": "2024-08-17", "incident_type": "accident", "claimed_amount": 7800.00, "status": "approved", "risk_score": 19, "decision_reason": "Intersection collision, police report filed."},
    {"policy_number": "HOME-2024-004455", "claim_date": "2024-07-10", "incident_date": "2024-07-08", "incident_type": "wind_damage", "claimed_amount": 15000.00, "status": "approved", "risk_score": 20, "decision_reason": "Storm damage to roof, adjuster confirmed."},
    {"policy_number": "AUTO-2022-007712", "claim_date": "2023-03-15", "incident_date": "2023-03-12", "incident_type": "accident", "claimed_amount": 6500.00, "status": "approved", "risk_score": 16, "decision_reason": "Fender bender, claim processed before policy expiry."},
    {"policy_number": "DEV-2023-002210", "claim_date": "2023-11-20", "incident_date": "2023-11-18", "incident_type": "theft", "claimed_amount": 1200.00, "status": "rejected", "risk_score": 72, "decision_reason": "Policy was suspended at time of incident."},
]


def _raise_on_real_conflict(exc: BulkWriteError, collection: str) -> None:
    """Duplicate keys are benign seed races; any other write error is fatal."""
    write_errors = exc.details.get("writeErrors", [])
    if any(err.get("code") != 11000 for err in write_errors):
        raise exc
    logger.warning(
        "seed_partial_duplicates_ignored", collection=collection, duplicates=len(write_errors)
    )


async def seed_database():
    """Idempotently seed policies and historical claims.

    Startup races (multiple workers, container restarts) previously produced
    duplicate seeds: count-guards were checked before any unique index existed.
    Now indexes are created first, a marker doc is claimed before seeding, and
    duplicate writes are tolerated so concurrent seeds converge.
    """
    # Indexes first: duplicate-seed protection must exist before any insert.
    await policies_col.create_index("policy_number", unique=True)
    await claims_col.create_index("id", unique=True)
    await claims_col.create_index("policy_number")
    await claim_documents_col.create_index("claim_id")
    # document_requests: the case-view checklist queries per claim, oldest first.
    await document_requests_col.create_index("claim_id")
    # audit_log: append-only trail read newest-first per claim; the compound
    # index covers every audit read (its claim_id prefix serves the per-claim
    # equality filter). The legacy single-field claim_id/at indexes it
    # superseded are dropped from pre-existing deployments.
    await audit_log_col.create_index([("claim_id", 1), ("at", -1)])
    for legacy in ("claim_id_1", "at_1"):
        try:
            await audit_log_col.drop_index(legacy)
        except PyMongoError:
            logger.debug("legacy_audit_log_index_absent", index=legacy)
    await events_col.create_index([("claim_id", 1), ("seq", 1)], unique=True)

    await claim_messages_col.create_index([("claim_id", 1), ("created_at", 1)])
    # claim_runs: one doc per run attempt; {claim_id, attempt} unique so a
    # concurrent enqueue can never create two runs with the same attempt, and
    # {status, created_at} backs the worker's atomic queue-claim query.
    await claim_runs_col.create_index([("claim_id", 1), ("attempt", 1)], unique=True)
    await claim_runs_col.create_index([("status", 1), ("created_at", 1)])
    # notifications: the bell dropdown filters by recipient email; mark-read
    # matches the application-level id field scoped to the recipient.
    await notifications_col.create_index("recipient_email")
    await notifications_col.create_index("id")
    # One notification per claim per milestone per request: replayed events and
    # retried fan-outs must never double-notify, while per-request events
    # (portal document uploads) each deserve their own bell. Rows without a
    # request_id (customer milestone fan-out) dedupe on (claim_id, milestone)
    # exactly as before — absent fields index as null.
    try:
        await notifications_col.drop_index("claim_id_1_milestone_1")
    except PyMongoError:
        logger.debug("legacy_notification_index_absent")
    await notifications_col.create_index(
        [("claim_id", 1), ("milestone", 1), ("request_id", 1)], unique=True
    )
    # workbench_views (spec F12): a view name is unique per owner, so a
    # re-save of the same name can never fork into two presets. Reads are
    # always owner-scoped on top of this index.
    await workbench_views_col.create_index(
        [("owner_id", 1), ("name", 1)], unique=True
    )

    # claim_notes: the case view reads one claim's notes oldest-first.
    await claim_notes_col.create_index([("claim_id", 1), ("created_at", 1)])

    # users: login and per-request role lookups key on email. Created here so
    # the index exists even when demo seeding is skipped (production).
    await users_col.create_index("email", unique=True)

    # refresh_tokens: rotation validates the presented token by hash, and
    # reuse detection revokes every token in the family.
    await refresh_tokens_col.create_index("token_hash")
    await refresh_tokens_col.create_index("family_id")

    # llm_usage (app/usage.py): per-claim cost rollups match claim_id, and
    # logged_at expires telemetry after ~90 days. TTL requires a BSON Date —
    # usage.py writes a datetime, not an ISO string.
    await db[LLM_USAGE_COLLECTION].create_index("claim_id")
    await db[LLM_USAGE_COLLECTION].create_index(
        "logged_at", expireAfterSeconds=90 * 24 * 60 * 60
    )

    # Marker claim: exactly one caller proceeds to the seeding block.
    try:
        await seed_state_col.insert_one(
            {"_id": SEED_MARKER_ID, "seeded_at": datetime.now(timezone.utc).isoformat()}
        )
    except DuplicateKeyError:
        logger.info("seed_skipped", reason="marker present")
        return

    # Seeded separately: tolerated policy duplicates must not skip claims.
    try:
        await policies_col.insert_many(SEED_POLICIES, ordered=False)
        logger.info("seeded_policies", count=len(SEED_POLICIES))
    except BulkWriteError as exc:
        _raise_on_real_conflict(exc, "policies")

    historical_docs = [
        {
            "id": f"CLM-HIST-{i + 1:03d}",
            "policy_number": claim["policy_number"],
            "claim_date": claim["claim_date"],
            "incident_date": claim["incident_date"],
            "incident_type": claim["incident_type"],
            "claimed_amount": claim["claimed_amount"],
            "status": claim["status"],
            "risk_score": claim["risk_score"],
            "decision_reason": claim["decision_reason"],
            "agent_trace": {},
            "is_historical": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        for i, claim in enumerate(SEED_HISTORICAL_CLAIMS)
    ]
    try:
        await claims_col.insert_many(historical_docs, ordered=False)
        logger.info("seeded_historical_claims", count=len(historical_docs))
    except BulkWriteError as exc:
        _raise_on_real_conflict(exc, "claims")


async def seed_demo_users() -> None:
    """Idempotently seed the demo adjuster/customer accounts for development.

    Driven entirely by settings (DEMO_ADJUSTER_EMAIL/PASSWORD and
    DEMO_CUSTOMER_EMAIL/PASSWORD). An empty email or password skips that role —
    no known default credential is ever shipped silently. Existing users are
    never overwritten, so production password rotations survive redeploys.
    """
    # Defensive gate: production provisions users server-side; demo accounts
    # must never exist there even if settings carry credentials.
    if settings.environment == "production":
        logger.info("demo_user_seeding_skipped", reason="production environment")
        return

    from app.security import hash_password  # local import: avoids config-at-import cycle risk

    demo_accounts = [
        (settings.demo_adjuster_email, settings.demo_adjuster_password, "adjuster"),
        (settings.demo_customer_email, settings.demo_customer_password, "customer"),
    ]
    for email, password, role in demo_accounts:
        if not email or not password:
            continue
        email = email.strip().lower()
        if await users_col.find_one({"email": email}, {"_id": 1}):
            continue
        await users_col.insert_one(
            {
                "id": f"usr_{secrets.token_hex(8)}",
                "email": email,
                "password_hash": hash_password(password),
                "role": role,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        logger.info("seeded_demo_user", email=email, role=role)
