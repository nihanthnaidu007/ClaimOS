# ClaimOS API reference

Every route below was verified against the actual FastAPI application at `ca70e8a` (September 18, 2026) — read from `backend/server.py` and the routers it mounts (`backend/app/auth_routes.py`, `claims_routes.py`, `workbench_routes.py`, `status_routes.py`, `notify_routes.py`, `fnol_drafts.py`), typed against `backend/app/schemas.py`.

**Base URL:** under compose, the API is on `http://localhost:8000/api` directly, or same-origin `http://localhost:3000/api` through the nginx web tier. In development without Docker it is `http://localhost:8001/api`.

**Authentication model.** Login and refresh return a short-TTL JWT access token in the response body — clients hold it in memory and send it as an `Authorization: Bearer` header on every authenticated call. A rotating refresh token travels only as an httpOnly, `SameSite=Lax` cookie scoped to `/api/auth`; refresh requests additionally require an `X-CSRF-Token` header (double-submit against the readable CSRF cookie). Two roles exist — `adjuster` and `customer` — and routes are gated per role.

Three access levels appear below:

- **Public** — no header needed.
- **Any authenticated user** — requires `Authorization: Bearer <accessToken>` (either role). Missing/expired/invalid tokens return `401` with `{"detail": "Not authenticated"}` or `{"detail": "Invalid or expired token"}`.
- **Adjuster** — Bearer token plus the adjuster role; customers receive `403` `{"detail": "Adjuster role required"}`.

Getting a token and calling an authenticated route:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "adjuster@example.com", "password": "…"}' | jq -r .accessToken)

curl -s http://localhost:8000/api/claims \
  -H "Authorization: Bearer $TOKEN"
```

**Conventions.** All routes are prefixed `/api`. Validation failures return `422` with Pydantic's field-level detail. Errors are JSON `{"detail": "..."}`. Every response carries an `X-Request-ID` header. Rate-limited routes return `429` when the per-IP limit is exceeded.

---

## Health & meta (public)

**`GET /api/`** → `200` `{"status": "ok", "service": "ClaimOS API", "agents": 5}`

**`GET /api/health`** → `200` `{"status": "ok"}` — liveness; never touches the database.

**`GET /api/ready`** → `200` `{"status": "ready", "database": "connected"}` — readiness; pings Mongo. Errors: `503` `{"detail": "Database unavailable"}`.

## Auth (public)

**`POST /api/auth/register`** → `201` `PublicUser` `{id, email, role, createdAt}`. Request: `{email, password (8–128 chars), inviteCode}`. Registration is customer-only — the server assigns the role, and adjuster accounts are the seeded demo credentials (`DEMO_ADJUSTER_EMAIL`/`DEMO_ADJUSTER_PASSWORD`). Invite-only: an unset `INVITE_CODE` disables registration (`403` "Registration is disabled"); a wrong code is `403` "Invalid invite code" (constant-time comparison); a duplicate email is `409`.

**`POST /api/auth/login`** → `200` `{"accessToken", "tokenType": "bearer", "expiresInSeconds", "user": {id, email, role, createdAt}}`. Request: `{email, password}`. Sets the httpOnly refresh cookie (path `/api/auth`) and the CSRF cookie (path `/`). Unknown email and wrong password return the same `401` "Invalid email or password" — no enumeration. Rate-limited `5/minute` per IP.

**`POST /api/auth/refresh`** → `200` same shape as login, with rotated cookies. Requires the refresh cookie **and** an `X-CSRF-Token` header matching both the CSRF cookie and the session's server-side hash (`403` on mismatch). Presenting an already-rotated/revoked token revokes the whole session family and returns `401` "Refresh token reuse detected; session revoked". Expired or unknown tokens: `401`.

**`POST /api/auth/logout`** → `204`. Revokes the session family and clears both cookies.

## Claims

**`POST /api/claims`** — submit a claim. *Any authenticated user.* Rate-limited `10/minute`. The claim record exists the instant the API responds (the wizard attaches uploads and the portal resolves the claim number immediately); the pipeline runs in the worker process. Request body (`ClaimSubmission`, camelCase):

| Field | Type | Rules |
|---|---|---|
| `policyNumber` | string | required, 3–40 chars |
| `holderName` | string | optional, ≤120 chars |
| `incidentDate` | string | required, must parse as `YYYY-MM-DD` |
| `incidentType` | string | required, 2–40 chars |
| `claimedAmount` | number | required, > 0, ≤ 5,000,000 |
| `description` | string | required, 20–5000 chars |
| `contactEmail` | string | optional (empty allowed), ≤254 chars, must match a minimal `name@domain.tld` shape |
| `documentText` | string | optional, ≤20,000 chars |

Response `200`: `{"claimId": "CLM-YYYYMMDD-NNN", "message": "Claim queued for processing.", "accessCode": "…"}` — IDs come from an atomic per-day Mongo counter, and `accessCode` is the credential the customer uses on the public status portal (also hashed and stored on the claim). Errors: `422` on any field violation.

**`GET /api/claims`** — *adjuster.* `200` `ClaimRecord[]` — newest first, capped at 100.

**`GET /api/claims/{claim_id}`** — *adjuster.* `200` `ClaimRecord`. Errors: `404` `{"detail": "Claim not found"}`.

**`GET /api/claims/{claim_id}/trace`** — *adjuster.* `200` `TraceResponse`: `{claimId, claimStatus, events: [{seq, event, data, createdAt}], runs: [{attempt, status, createdAt, finishedAt, failureReason, escalationReason, stp}], agentLogs, agentTrace, decision}` — everything the claim-detail timeline renders, from durable state only.

**`GET /api/claims/{claim_id}/pdf`** — *adjuster.* `200` `{"pdf": "<base64 PDF>", "claimId": "…"}` — the decision letter, rebuilt from the stored claim. Errors: `404`.

## Streaming (any authenticated user)

**`GET /api/events/streams/{claim_id}`** — Server-Sent Events (`text/event-stream`), tailing the claim's durable event log. Each frame carries a numeric `id` — the replay cursor:

```
id: 7
data: {"event": "agent_complete", "agent": "policy", "label": "…", "duration": 1200, …}
```

- **Replay:** send `Last-Event-ID: <seq>` (or the header a browser sets automatically on `EventSource` reconnect) and the stream resumes exactly after that sequence number. A non-integer header is `400`.
- **Heartbeat:** a `: heartbeat` comment every 15 seconds of silence.
- **Terminates** after a terminal event (`pipeline_complete`, `pipeline_halted`, `claim_failed`).
- **Event payloads:** `agent_start` `{agent, label, description, timestamp}`, `agent_complete` `{agent, label, duration, output, toolsCalled}`, `pipeline_halted` `{reason, …}`, `agent_error`, plus lifecycle events emitted by routes and the worker: `claim_submitted`, `document_uploaded` `{documentId, fileName, contentType, sizeBytes, uploadedBy}`, `claim_overridden` `{decision, payoutAmount, actor, reason, at}`, `payout_recorded` `{amount, method, reference, recordedBy}`.

**`GET /api/claims/stream/{claim_id}`** — compatibility alias serving the same durable stream.

## FNOL drafts (any authenticated user)

Draft IDs are client-generated (the wizard's localStorage key doubles as the server key) and must match `^[A-Za-z0-9][A-Za-z0-9_.-]{5,79}$` — anything else is `400` "Invalid draft id".

**`PUT /api/fnol/drafts/{draft_id}`** — create-or-replace (upsert). Request: `{"data": {…}}`. → `200` `{"draftId", "data", "updatedAt"}`.

**`GET /api/fnol/drafts/{draft_id}`** → `200` `{"draftId", "data", "updatedAt"}`. Errors: `404` "Draft not found".

**`DELETE /api/fnol/drafts/{draft_id}`** → `200` `{"ok": true}`.

## Documents (adjuster)

**`POST /api/claims/{claim_id}/documents`** — attach one document. Multipart form field `file`. Rate-limited `30/minute`. → `201` `UploadedDocumentResponse`. Validation fails closed before storage: `415` on a non-allowlisted content type (PDF/PNG/JPEG only), `413` over the 10 MiB cap, `422` for an empty body. Filenames are sanitized to safe basenames; blobs land under the storage provider with a collision-proof per-claim key; the claim's event log records the upload. Errors: `404` unknown claim.

**`GET /api/claims/{claim_id}/documents`** → `200` `UploadedDocumentResponse[]` — this claim's uploads, newest first, capped at 100.

## Document requests (adjuster)

**`POST /api/claims/{claim_id}/document-requests`** — add an entry to the claim's document checklist ("documents we need" from the claimant). Request: `{title (required, 1–120 chars), description (optional, ≤2000 chars)}` — the description tells the claimant what a good upload looks like. → `201` `DocumentRequestRecord` with `status: "requested"`. Every create is audited (`document_request_created`) and announced on the claim's event log. Errors: `404` unknown claim; `422` validation.

**`GET /api/claims/{claim_id}/document-requests`** → `200` `DocumentRequestRecord[]` — this claim's checklist, oldest first.

**`PATCH /api/claims/{claim_id}/document-requests/{request_id}`** — edit wording or waive. Body: `{title?, description?}` (edit) or `{waive: true, reason?}` (close without an upload; the reason lands in the audit trail when given). Editing is refused once the request is no longer `requested` (`409`), and only `requested` items can be waived (`409`) — receiving (F4) or waiving is terminal. Successful mutations are audited (`document_request_updated` / `document_request_waived`) and land on the event log. Errors: `404` unknown claim or request; `409` state conflict; `422` validation. Customer tokens are refused on every document-request route (`403`).

## Evidence pack (adjuster)

**`GET /api/claims/{claim_id}/evidence-pack`** → `200` `{"claimId", "filename": "evidence-pack-<claim_id>.pdf", "pdf": "<base64 PDF>"}` — the full adjudication record: traces, decision, and the claim's event history. Errors: `404`.

## Settlement (adjuster)

**`POST /api/claims/{claim_id}/settlement`** — record settlement facts. Record-only: no money moves and no payment provider is contacted. Rate-limited `30/minute`. Request: `{amount (0–5,000,000), method ("bank_transfer" | "cheque" | "upi" | "other"), reference (optional, ≤120 chars)}`. → `201` `{"claimId", "status", "settlement": {amount, method, reference, settled_at, recorded_by}, "auditEntry": AuditEntry}` — the reference doubles as the audit entry's reason. Errors: `404` unknown claim; `409` "Settlement already recorded for this claim"; `422` validation.

## Policies (adjuster)

**`GET /api/policies`** → `200` `PolicyRecord[]` (up to 100).

**`GET /api/policies/lookup?policy_number=...`** → `200` `PolicyRecord`. Errors: `400` when the parameter is empty; `404` when no policy matches.

**`GET /api/policies/search?q=...`** → `200` `PolicyRecord[]` — case-insensitive partial match on policy number, holder name, or policy type; empty `q` returns all (capped at 100).

## Dashboard (adjuster)

**`GET /api/dashboard/stats`** → `200` `{totalClaims, approved, rejected, underReview, pending, totalPayout, avgRiskScore, activePolicies, recentClaims: RecentClaimRecord[]}`. `approved` counts `approved` + `auto_approved`; `underReview` counts `under_review`/`escalated`; `totalPayout` sums claimed amounts over approved claims; `avgRiskScore` averages only scored claims (risk > 0), rounded to one decimal; `recentClaims` carries the five newest.

## Ops analytics (adjuster)

**`GET /api/analytics/ops`** → `200` `OpsAnalyticsResponse` — five metric groups computed from stored claim timelines:

```json
{
  "cycleTime": {"p50": …, "p90": …, "p95": …, "decided": …},
  "stp": {"decided": …, "autoApproved": …, "escalated": …, "rate": …},
  "fraud": {"totalClaims": …, "flaggedClaims": …, "rate": …},
  "decisions": [{"status": "approved", "count": …}, …],
  "sla": {"bySeverity": [{"severity": "low", "slaHours": 48, "decided": …, "breaches": …, "breachRate": …}]}
}
```

## Workbench (all adjuster)

**`GET /api/workbench/queue`** → `200` `{rows: WorkbenchQueueRow[], generatedAt}`. Query parameters:

| Parameter | Default | Notes |
|---|---|---|
| `status` | the reviewable set (`pending,under_review,escalated`) | comma-separated statuses |
| `severity` | *(all)* | comma-separated: `low`, `elevated` |
| `min_age_hours` / `max_age_hours` | *(none)* | on enriched SLA hours |
| `sort` | `age` | `age` \| `severity` \| `risk` \| `created_at` (alias of `age`) |
| `direction` | `asc` | `asc` = most-urgent-first for every key |
| `search` | *(none)* | case-insensitive: claim **number by prefix**, policy **number by substring**, customer **name by substring** — one hit on any field matches (spec F8); composes with the filters above, honors the same cap, and also applies to `/stream` |

**`GET /api/workbench/stream`** — SSE over the same filtered queue. The stream re-reads the durable store on an interval and emits a `queue_update` frame (`{"event": "queue_update", "rows": …, "generatedAt": …}`) only when the rendered digest changes, with `: keep-alive` comments between. Same query parameters as `/queue`.

**`GET /api/workbench/claims/{claim_id}/summary`** → `200` `CaseSummaryResponse` — the deterministic case view: `{claimId, holderName, policyNumber, incidentType, incidentDate, claimedAmount, status, severity, riskScore, fraudFlags, recommendation, confidence, eligibility, coverage, documents, decision, intakeValid, stages: [{agent, label, reached, status, durationMs, reasoning}], sla, escalationReason, failureReason, override}`. No LLM is called.

**`GET /api/workbench/claims/{claim_id}/events`** → `200` `{"claimId", "events": [...]}` — the claim's durable event log (up to 1000).

**`GET /api/workbench/claims/{claim_id}/letter`** → `200` `{"claimId", "subject", "body"}` — the decision letter as persisted by the Decision agent. Errors: `404` "No decision letter recorded for this claim".

**`POST /api/workbench/claims/{claim_id}/override`** — the workbench's one write path. Request: `{decision ("approved" | "rejected"), reason (required, 1–2000 chars, not blank), payoutAmount (optional, 0–5,000,000)}`. → `200` `{"claimId", "status": "overridden", "auditEntry": AuditEntry}` — stamps the claim, appends an immutable audit_log entry (who, before/after, why), and emits `claim_overridden` on the claim's event stream. Errors: `404` unknown claim; `409` when the claim is already decided ("re-open it before overriding") or its status cannot be overridden; `422` when the reason is missing or blank.

**`GET /api/workbench/claims/{claim_id}/audit`** → `200` `AuditEntry[]` — append-only history, newest first, capped at 200.

## Status portal (public — credential-bearing POSTs)

The first two routes are POSTs because the access code is a credential: keeping it out of URLs and access logs matters more than cache friendliness. Lookup and decision-letter are rate-limited per client IP (`STATUS_LOOKUP_RATE_LIMIT`, default `60/minute`) and non-enumerable — unknown claim number, wrong code, and claims without a decision all return the same generic `404` `{"detail": "Claim not found"}`.

**`POST /api/status/lookup`** — request: `claimNumber` (matches `CLM-YYYYMMDD-N` or `CLM-HIST-N`) and `accessCode` (16–200 chars). → `200` `StatusLookupResponse` — the masked portal payload: `{claimNumber, firstName, status, statusLabel, currentStage, incidentType, decisionOutcome, decisionReady, pdfAvailable, milestones: [{key, label, at, done}]}`. Amounts, contact details, and policy numbers never appear.

**`POST /api/status/decision-letter`** — same request shape. → `200` `{"pdf": "<base64 PDF>", "claimId"}` when a decision exists; the generic `404` otherwise (there is nothing to reveal).

**`POST /api/status/recover-access-code`** — request: `claimNumber` and `contactEmail` (both trimmed; the email is matched exactly against the claim's contact email, case-insensitively). On a match the portal access code is rotated (the old code stops working immediately) and the new code is emailed to the stored address. → `200` `{"status": "ok", "message": "If this claim number and email match a claim on file, a new access code has been sent to that address."}` — the same fixed response for matches and every mismatch (unknown number, unknown email, claim filed without an email), so the endpoint reveals nothing about which claims or addresses exist. Rate-limited per client IP (`ACCESS_CODE_RECOVERY_RATE_LIMIT`, default `3/minute` — a match sends a real email).

## Notifications (any authenticated user)

Scoped to the caller's own email — the fan-out stamps `recipient_email` (the claim's contact email, else the policy holder) and both routes filter on it. There is no cross-customer surface.

**`GET /api/notifications`** → `200` `{notifications: [{id, claimId, milestone, title, body, read, createdAt}], unreadCount}` — newest first, capped at 50.

**`POST /api/notifications/mark-read`** — request: `{ids: [...]}` (1–100 ids). Ids belonging to someone else simply don't match. → `200` `{"markedRead": <count>}`.

---

## Error codes

| Code | When |
|---|---|
| `400` | Malformed request that isn't field validation (bad draft id, empty lookup parameter, non-integer `Last-Event-ID`) |
| `401` | Missing, invalid, or expired Bearer token; missing/invalid/expired refresh token; refresh reuse detected; bad login credentials |
| `403` | Role required (adjuster routes as a customer); CSRF token missing or mismatched; cross-site request rejected; registration disabled or bad invite code |
| `404` | Unknown claim, policy, draft, or letter — portal 404s are deliberately generic |
| `409` | Idempotency conflict: settlement already recorded; override of an already-decided claim |
| `413` | Upload over the size cap |
| `415` | Upload content type not on the allowlist |
| `422` | Field-level validation (Pydantic detail) or empty upload |
| `429` | Per-IP rate limit exceeded |
| `503` | Readiness probe failure (database unavailable) |

## Record shapes (storage documents, snake_case)

`PolicyRecord`: `id, policy_number, holder_name, holder_email, holder_phone, policy_type, status, coverage_limit, deductible, monthly_premium, start_date, end_date, covered_events[]`.

`ClaimRecord`: `id, policy_number, claim_date, incident_date, incident_type, claimed_amount, status, risk_score, decision_reason, agent_trace{intake, policy, documents, eligibility, decision}, agent_logs[], holder_name, is_historical, created_at, fraud_flags[{code, severity, detail, evidence}], incident_fingerprint, contact_email, access_code, failure_reason, escalation_reason, usage, settlement`. `access_code` is the portal credential and only ever leaves on adjuster-scoped routes.

`WorkbenchQueueRow`: `id, policy_number, holder_name, incident_type, claimed_amount, status, risk_score, created_at, severity ("low" | "elevated"), sla {targetHours, hoursElapsed, hoursRemaining, breached, state ("ok" | "at_risk" | "breached")}, escalation_reason, failure_reason, fraud_flags[]`.

`AuditEntry`: `id, claim_id, actor, actor_email, action, before, after, reason, at`.

`DocumentRequestRecord`: `id, claim_id, title, description, status ("requested" | "received" | "waived"), requested_by, document_id, created_at, updated_at`.
