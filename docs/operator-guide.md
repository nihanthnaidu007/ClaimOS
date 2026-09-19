# ClaimOS operator guide

ClaimOS is a small fleet of containers: a FastAPI API, a pipeline worker, an nginx web tier serving the React console, and MongoDB. This guide covers running one locally, configuring it, what the CI pipeline proves, and where to look when something breaks.

---

## Local development quickstart

The compose file mirrors the production topology — API and worker are the same backend image running different commands, and the web tier proxies `/api` to the API:

```bash
git clone https://github.com/nihanthnaidu007/ClaimOS && cd ClaimOS
cp backend/.env.example backend/.env    # add your ANTHROPIC_API_KEY for live pipelines
docker compose up -d --build
# open http://localhost:3000 — API on http://localhost:8000
```

Compose wires the API to Mongo at `mongodb://mongo:27017/claimos`, sets `CORS_ORIGINS=http://localhost:3000`, mounts an `uploads` volume into both backend containers, and health-checks Mongo (then the API) before starting the services that depend on them. On startup the API creates indexes and idempotently seeds demo data — 10 policies and 15 historical claims — when the database is empty; a marker document keeps concurrent seeds from racing.

**Demo accounts.** To get a ready-made login for each role, set `DEMO_ADJUSTER_EMAIL` / `DEMO_ADJUSTER_PASSWORD` and `DEMO_CUSTOMER_EMAIL` / `DEMO_CUSTOMER_PASSWORD` in `backend/.env` — both accounts are seeded at startup (passwords hashed at rest; an empty value skips that role). To create users manually, registration is invite-gated: set `INVITE_CODE` and pass it as `inviteCode` to `POST /api/auth/register`. An unset invite code disables registration entirely, which is the intended production posture.

**ANTHROPIC_API_KEY handling.** The key is read from `backend/.env` (compose-injected environment works too). It is optional at boot by design: the API serves and probes pass with no key set, and the first real LLM call fails closed with a clear, typed adapter error instead of limping on — the claim stays `pending` with the failure recorded in its event log and run history. Never commit a real key; the `.env.example` template says it in so many words. CI never receives the key, so automated pipelines exercise the deterministic code paths, not the LLM.

**Developing without Docker:**

```bash
# terminal 1 — API on :8001
cd backend && uvicorn server:app --reload --port 8001
# terminal 2 — worker (required for claims to actually adjudicate)
cd backend && python worker.py
# terminal 3 — frontend dev server on :5173 (proxies /api to the API)
cd frontend && npm install && npm run dev
```

The frontend reads its API base from `VITE_API_BASE_URL` (default `http://localhost:8001` for local development; `VITE_*` vars are baked in at build time, so set it explicitly for any deployed build).

## Environment variables

Copy `backend/.env.example` to `backend/.env`; never commit `.env`. The typed source of truth is `backend/app/config.py` (plus the adapter-level knobs the LLM client reads directly). Every variable below is optional unless marked.

### Core

| Variable | Default | What it does |
|---|---|---|
| `MONGO_URL` | `mongodb://127.0.0.1:27017` | MongoDB connection string. Bare URI for local dev; Atlas SRV strings work. |
| `DB_NAME` | `claimos` | Database name. |
| `ENVIRONMENT` | `development` | `development` / `staging` / `production`. Production enables the boot guards below and forces secure cookies. |
| `CORS_ORIGINS` | `*` | Comma-separated origin allowlist. The wildcard disables credential-bearing requests (CORS spec); production refuses to boot with a wildcard. |

### Auth & sessions

| Variable | Default | What it does |
|---|---|---|
| `JWT_SECRET` | *(empty)* | HS256 signing secret for access tokens. **Required in production** — boot fails without it. |
| `ACCESS_TOKEN_TTL_MINUTES` | `15` | Access-token lifetime; the client holds it in memory. |
| `REFRESH_TOKEN_TTL_DAYS` | `7` | Refresh-session lifetime; the token travels only as an httpOnly cookie scoped to `/api/auth`. |
| `INVITE_CODE` | *(empty)* | Exact invite code for registration; empty disables registration. |
| `COOKIE_SECURE` | auto | Force the `Secure` cookie flag on/off; defaults to secure everywhere except development. |
| `DEMO_ADJUSTER_EMAIL` / `DEMO_ADJUSTER_PASSWORD` | *(empty)* | Adjuster account seeded at startup for local development. |
| `DEMO_CUSTOMER_EMAIL` / `DEMO_CUSTOMER_PASSWORD` | *(empty)* | Customer account seeded at startup for local development. |

### LLM adapter

| Variable | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(empty)* | Credential for the six-agent pipeline. Optional at boot; the first real LLM call fails closed without it. |
| `LLM_PROVIDER` | `anthropic` | Adapter in use. |
| `LLM_MODEL` | `claude-sonnet-4-20250514` | Model for agent calls. |
| `LLM_TIMEOUT_S` | `60` | Per-request LLM timeout in seconds (read by the adapter). |
| `LLM_MAX_RETRIES` | `3` | Bounded retries per LLM call on transient errors (read by the adapter). |
| `LLM_REQUEST_TIMEOUT_SECONDS` | `180` | Adapter client timeout — generous because a full structured-output generation at the eligibility budget runs 90–120s under API load. |

### Pipeline & straight-through processing

| Variable | Default | What it does |
|---|---|---|
| `STP_CONFIDENCE_THRESHOLD` | `0.85` | Decision confidence at or above this, with low severity and a clean eligibility verdict, auto-finalizes a claim; anything else escalates to the workbench. |
| `STP_LOW_SEVERITY_AMOUNT` | `10000` | Claimed amount at or under this counts toward low severity. |
| `STP_LOW_SEVERITY_TYPES` | `theft,weather_damage,vandalism` | Incident types eligible for low severity. |
| `INTAKE_FLAG_AMOUNT_THRESHOLD` | `500000` | Claims above this get the high-amount flag: never an invalidation, just risk points and a route to human review. |
| `WORKER_POLL_INTERVAL_SECONDS` | `1` | Seconds between claim_runs queue polls when the queue is empty. |

### SLA, workbench, uploads, notifications

| Variable | Default | What it does |
|---|---|---|
| `SLA_HOURS_PER_SEVERITY` | `low:72,elevated:24` | SLA target per derived severity for the workbench queue. |
| `SLA_DEFAULT_HOURS` | `48` | Fallback target for an unknown severity. |
| `SLA_AT_RISK_FRACTION` | `0.75` | Fraction of the target past which a row shows at-risk (amber); past 100% it is breached (red). |
| `WORKBENCH_STREAM_INTERVAL_SECONDS` | `2` | Seconds between queue re-reads on an open workbench SSE stream. |
| `SLA_LOW_HOURS` / `SLA_ELEVATED_HOURS` | `48` / `24` | Ops-analytics SLA targets (submission → decision). |
| `UPLOAD_DIR` | `/data/uploads` | Where uploaded documents land (a mounted volume in deployments). |
| `UPLOAD_MAX_BYTES` | `10485760` | Upload size cap (10 MiB). |
| `UPLOAD_ALLOWED_CONTENT_TYPES` | `application/pdf,image/png,image/jpeg` | Upload allowlist. |
| `NOTIFICATION_DRIVER` | `console` | Notification delivery driver (`console` logs structured events; email/SMS drivers implement the same provider interface). |

### Rate limits (per client IP, slowapi syntax)

| Variable | Default | Applies to |
|---|---|---|
| `LOGIN_RATE_LIMIT` | `5/minute` | `POST /api/auth/login` |
| `FNOL_RATE_LIMIT` | `10/minute` | `POST /api/claims` |
| `STATUS_LOOKUP_RATE_LIMIT` | `60/minute` | Public status-portal lookups and decision-letter downloads |

## CI: the jobs and what green means

Seven GitHub Actions jobs run on every PR and push (workflow: `.github/workflows/ci.yml`, runs grouped under `ci-<ref>`, in-progress runs cancelled):

| Job | What it proves when green |
|---|---|
| `backend-ruff` | Python passes the Ruff lint gate (version pinned in `backend/requirements.txt`). |
| `backend-pytest` | The pytest suite passes — root smoke suite plus backend tests: deterministic rating golden values, validation limits, durable-event behavior, adapter fail-closed handling, and the workbench/analytics math, all with mocked or in-memory Mongo. |
| `frontend-lint` | The React app lints clean (npm ci → yarn frozen → npm install fallback chain). |
| `frontend-test` | Vitest suites pass (app smoke, workbench, notification bell, status timeline, RiskGauge) with MSW-mocked API calls. |
| `docker-builds` | Both backend and frontend images build; GHCR login happens on main pushes only. |
| `security-audit` | `pip-audit` (backend, resolved environment) and `npm audit` (frontend production deps) find no accepted vulnerabilities — blocking: a finding is a red build, not a warning. |
| `lint-workflows` | The workflow files themselves pass actionlint (pinned release, checksum-verified). |

Green CI means: the code lints, the tests pass, both images build, and no known dependency vulnerability is present. The Playwright end-to-end specs in `frontend/e2e/` are not wired into CI yet — E2E runs against the compose stack are a manual step today.

## Deploying

Any host that runs the two backend containers, the web tier, and a reachable MongoDB will do; the compose file is the reference topology. Before exposing ClaimOS beyond your machine, set `ENVIRONMENT=production` — it activates the non-negotiable guards: boot refuses without `JWT_SECRET`, refuses a wildcard `CORS_ORIGINS`, and forces secure cookies. Deployments need both the API **and** a worker process; an API without a worker accepts claims that will never run.

## Backups

Mongo data lives in the compose `mongodata` volume (or your Atlas cluster — Atlas M0 includes scheduled snapshots). A local backup:

```bash
docker compose exec mongo mongodump --db claimos --archive > claimos.archive
# restore:
docker compose exec -T mongo mongorestore --archive < claimos.archive
```

Verify a restore before calling it done: either a `mongosh` count on `claims` (and on `document_requests` — the adjuster checklist collection added with the document-requests feature, whose entries point at claims via `claim_id`), or an authenticated `GET /api/claims` with an adjuster token comparing claim counts.

## Failure triage basics

**Start with the probes.** `GET /api/health` is liveness — the process is up; it never touches the database. `GET /api/ready` is readiness — it pings Mongo and returns 503 `Database unavailable` on failure. If the web tier is up but the API errors, check `ready` first: nine times in ten it is Mongo. Every response carries an `X-Request-ID` header (injected by middleware, echoed in every log line) — grep structured logs for that ID to follow one request end to end.

**The pipeline is durable — a restart is not an incident.** Claim state lives in MongoDB, never in a process. A claim is a `claim_runs` document; the worker claims runs atomically and checkpoints each finished stage onto the run. A worker crash loses at most the single stage in flight, and the re-run resumes from the last completed checkpoint instead of re-executing agents. `SIGTERM` (what `docker compose stop` sends) triggers a graceful checkpoint-and-requeue exit, so a stopped worker leaves nothing stranded. If claims sit `pending` with a worker supposedly running, check the worker container's logs first — an API alone accepts claims but never runs them.

**Failed claim states.** A claim whose pipeline hit an unrecoverable error is recorded `failed` — not silently dropped, not left "running" forever. Transient LLM errors are retried inside the run first (`LLM_MAX_RETRIES`, default 3); only after that does the attempt fail, the reason is persisted with the claim (`failure_reason`), and the failure event is written to the durable store before any stream sees it. A claim may show several attempts in its run history — that is the resume machinery working, not duplicate processing.

**Agent error surfaces.** Three places, in order of freshness: the SSE stream (an `agent_error` event on the claim's live stream, or a terminal banner if the claim ends failed), the claim record itself (`status: failed`, failure details persisted), and the structured logs (`pipeline_failed` with the claim ID and stack via structlog). Completed stages stay intact in the trace — an error never erases what already succeeded.

**Where events persist.** Every pipeline event is appended to the `events` collection with a per-claim monotonic sequence number (`claim_id`, `seq`, `event`, `data`, `created_at`). Streaming endpoints tail that collection: a reconnecting client sends `Last-Event-ID` and replays exactly what it missed, and any number of API replicas can serve any subscriber, because there is no per-process queue to lose. Even if nobody was watching, the full event history of every claim is queryable in Mongo.
