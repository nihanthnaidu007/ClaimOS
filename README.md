# ClaimOS

Glass-box AI claims adjudication. ClaimOS walks an insurance claim through a
six-agent pipeline — **Intake → Policy → Documents → Fraud → Eligibility → Decision** —
and streams every step live, so a human can watch exactly how the machine
reached its verdict. Every decision ships with a downloadable PDF letter and a
complete per-agent reasoning trace.

> **Status:** pre-production baseline. This tree is the starting point of an
> in-flight production upgrade (authentication, input validation, durable
> pipeline execution, CI/CD). Treat it as a demo, not a deployable system.

## How it works

1. **Submit a claim** — policy number, incident details, pasted evidence text.
   The API mints a claim ID and enqueues a durable `claim_runs` document; the
   HTTP response returns immediately.
2. **A worker process claims the run** (`queued → running` via an atomic
   `find_one_and_update`) and executes the six agents in sequence, each a
   Claude-backed step with a strict JSON contract. Deterministic MongoDB tool
   calls (policy lookup, claim history) run *before* the LLM and hand it facts,
   not tool schemas. Every finished stage is checkpointed onto the run
   document; a re-run resumes from the last completed checkpoint instead of
   re-executing agents.
3. **Halt semantics** — the pipeline stops after Intake (invalid claim) or
   Policy (policy not found / suspended) and records why.
4. **Live stream** — every `agent_start` / `agent_complete` / `agent_error`
   event is appended to the durable `events` collection with a per-claim
   sequence number. `GET /api/events/streams/{claim_id}` tails that collection:
   reconnects send `Last-Event-ID` and replay exactly what they missed, and
   any number of API replicas can serve any client (no in-memory queues).
5. **Decision letter** — the Decision agent produces a verdict, payout amount,
   and next steps, rendered as a PDF with the reasoning trace.
6. **Customer decision transparency** — the status portal explains how a claim
   was decided: `/api/status/lookup` returns a deny-by-default projection of
   the stored agent trace (`CUSTOMER_VISIBLE_TRACE_FIELDS` in
   `backend/app/portal_projection.py` — only allowlisted fields cross the
   boundary), pre-written stage copy from `PORTAL_STAGE_COPY` (never raw agent
   output), and the decision's plain-language summary plus customer citations
   on decided claims. Internal data — fraud scores, similar-incident matches,
   thresholds, model metadata, internal notes — has no path into any customer
   endpoint, enforced by a dedicated regression test.

## Architecture

| Layer | Tech | Where |
|---|---|---|
| API | FastAPI, REST + durable SSE | `backend/server.py` |
| Worker | claim_runs queue consumer, checkpointed pipeline execution | `backend/worker.py`, `backend/pipeline.py` |
| Agents | Five agent steps, tool-first LLM calls | `backend/agents.py` |
| LLM | Anthropic adapter: structured outputs, retries, usage logging | `backend/app/llm/adapter.py` |
| Data | MongoDB via Motor, seeded demo data | `backend/database.py` |
| PDF | fpdf2 decision letters (base64 over JSON) | `backend/pdf_generator.py` |
| Web | React 19 + Vite 7 (Vitest), Tailwind, shadcn/radix | `frontend/` |

```
frontend (Vite dev server, :5173)
   │  POST /api/claims ──────────► FastAPI (:8001)
   │                                   └─ enqueues claim_runs (Mongo)
   │  GET  /api/events/streams/{id} ◄─ SSE: tail of the events collection,
   │                                     Last-Event-ID replay on reconnect
   ▼
worker (python worker.py) ── claims run atomically ──► six agents
   └─ checkpoints + events + claim row ──► MongoDB (claim_runs, events, claims)
```

Claim state lives in MongoDB, never in a process: the API and the worker are
separate services built from the same image (different commands — see
`docker-compose.yml`), so each scales and restarts independently and a worker
crash loses at most the single stage in flight.

## Features

- **Dashboard** — claim stats, total payout, average risk score, active
  policies, recent claims.
- **Guided submission** with a live pipeline board showing each agent's status
  and reasoning trace as it streams in.
- **Policy lookup** by exact policy number or free-text search.
- **Claim history** — sortable table with expandable per-agent traces.
- **Internal notes** — adjuster-only working notes on a case, with @mention
  bell notifications, length caps, sanitization, and an audit trail; never
  exposed to customers.
- **PDF decision letters** for adjudicated claims.
- **Customer status portal** — claim-number + hashed access-code lookup (no
  login), milestone timeline, decision-letter PDF, a plain-language
  "What happens next" card, and an honest ETA ("within about 3 business
  days…" — never a fabricated date; the field is omitted when the SLA state
  can't back one).
- **Audited reopen** — a decided claim can go back under review via a
  reason-required, adjuster-only reopen action (no pipeline re-run); the
  customer portal shows the review-again state, and an explicit second
  decision replaces the first on the portal.
- **Seeded demo data** — 10 policies and 15 historical claims are seeded
  automatically when the database is empty.
- **Claim assignment** — new claims auto-assign round-robin across active
  adjusters (least-recently-assigned first; `AUTO_ASSIGN=none` opts out),
  adjusters can reassign from the workbench with a full audit trail, the queue
  filters by Mine / Unassigned / All, and ops analytics shows workload per
  adjuster.

## Environment variables

Copy `backend/.env.example` to `backend/.env` and set at least the required
vars — the backend loads `backend/.env` on startup.

| Variable | Where | Required | Default | Purpose |
|---|---|---|---|---|
| `MONGO_URL` | backend | yes | — | MongoDB connection string |
| `DB_NAME` | backend | yes | — | Database name |
| `CORS_ORIGINS` | backend | no | `*` | Comma-separated allowed origins; use an explicit allowlist outside local dev |
| `ANTHROPIC_API_KEY` | backend | no* | empty | Key for the LLM adapter; the first real LLM call fails closed if unset |
| `LLM_MAX_RETRIES` | backend | no | `3` | Bounded retries per LLM call |
| `LLM_TIMEOUT_S` | backend | no | `60` | Per-call timeout in seconds |
| `STP_CONFIDENCE_THRESHOLD` | backend | no | `0.85` | Straight-through gate: Decision confidence at or above this auto-finalizes low-severity, clean-eligibility claims; anything else escalates |
| `STP_LOW_SEVERITY_AMOUNT` | backend | no | `10000` | Claimed amount at or under this counts as low severity for the STP gate |
| `STP_LOW_SEVERITY_TYPES` | backend | no | `theft,weather_damage,vandalism` | Comma-separated incident types eligible for low severity |
| `SLA_ESCALATION_FACTOR` | backend | no | `1.0` | Escalation threshold as a multiplier on the severity's SLA window (spec F9): the worker sets a claim's set-once `escalated_at` when age crosses factor × target hours; `1.0` escalates at-breach |
| `AUTO_ASSIGN` | backend | no | `round_robin` | Claim auto-assignment at creation: `round_robin` picks the active adjuster with the oldest last-assignment stamp (stable order, id tiebreak); `none` leaves claims unassigned |
| `WORKER_POLL_INTERVAL_S` | backend | no | `1` | Seconds between claim_runs queue polls when the queue is empty |
| `VITE_API_BASE_URL` | frontend | no | `http://localhost:8001` | API base URL; `VITE_*` vars are exposed to client code at build time — set it for any non-local deployment |

\* Required in practice: adjudication needs LLM calls, and the adapter refuses
to run them without a key.

## Local development

Prerequisites: Python 3.11+, Node 20+, and a reachable MongoDB (local or
MongoDB Atlas).

**Backend** (from the repo root):

```bash
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env        # adjust MONGO_URL / DB_NAME if needed
cd backend && uvicorn server:app --reload --port 8001
```

The API serves on `http://localhost:8001`; `GET /api/` is a health check.

**Worker** (separate terminal, from the repo root — required for claims to
actually adjudicate; the API only enqueues):

```bash
cd backend && python worker.py
```

**Frontend:**

```bash
cd frontend
cp .env.example .env    # sets VITE_API_BASE_URL for local development
npm install             # Vite toolchain — no peer-dep flags needed
npm run dev             # dev server on http://localhost:5173
```

## Testing

```bash
# Backend lint (ruff config lives in backend/pyproject.toml)
cd backend && ruff check .

# Backend unit tests (LLM adapter + agents, fully mocked — no MongoDB needed)
cd backend && pytest -q

# Backend app boots (proves the import graph is intact)
cd backend && uvicorn server:app --port 8001    # Ctrl-C after the boot banner

# Frontend production build
cd frontend && npm run build
```

Frontend testing: Vitest is scaffolded (config, scripts, Testing Library deps)
but the suite has no test files at this baseline — `npm test` exits 1 until
frontend tests are added. The automated backend suite covers the agent
contracts and the LLM adapter (structured outputs, retries, fail-closed
behavior) with a mocked Anthropic client. End-to-end behavior — SSE streaming,
PDF letters, seeded data — is verified manually: start the backend, submit a
claim for a seeded policy number from the UI, and watch the pipeline stream to
a decision letter.

## Documentation

| Doc | For |
|---|---|
| [User guide](docs/user-guide.md) | Adjusters working claims in the console, and customers checking on a claim |
| [Operator guide](docs/operator-guide.md) | Running ClaimOS: quickstart, environment variables, CI, backups, triage |
| [API reference](docs/api-reference.md) | Every route, verified against the code: auth, claims, streams, portal, workbench |
| [Demo script](docs/demo-script.md) | A 10-minute guided demo with a rehearsal-ready beat sheet |

## License

[MIT](LICENSE) — © Kalisetti Nihanth Naidu
