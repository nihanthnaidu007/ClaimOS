# ClaimOS Codebase Map

Depth cap: 2. Single table. Reflects origin/main at `8b2cb8d` (2026-09-18).

| Path | Purpose |
|---|---|
| `backend/server.py` | FastAPI app + `/api`: auth, workbench, status portal, notifications, claims/documents/evidence packs, FNOL drafts, settlements, dashboard stats, SSE streams (`/api/events/streams/{claim_id}`, Last-Event-ID replay), PDF download; `/api/health` (liveness, no DB) and `/api/ready` (Mongo ping) |
| `backend/agents.py` | The six pipeline stages in `PIPELINE_STAGES` — INTAKE, POLICY, DOCUMENT, FRAUD, ELIGIBILITY, DECISION: deterministic tool-first prep (policy lookup, claim history), then bounded LLM structured-output calls; prompts + cached system messages in `AGENT_PARAMS`; decision invariants, template-letter fallback, intake red-flag rules |
| `backend/app/llm/` | `adapter.py` (Anthropic structured outputs, bounded retries via `LLM_MAX_RETRIES`, usage logging, 180s default timeout), `fixture.py` (deterministic scripted adapter — `LLM_PROVIDER=fixture`, `FIXTURE_FAULT` fault injection), `schemas.py` (structured-output models) |
| `backend/pipeline.py` | Durable execution: claim_runs queue (queued → running → auto_approved / escalated / failed / halted), per-stage checkpoints written into the run document, resume from last completed checkpoint, STP gate wiring (`app/stp.py`) |
| `backend/worker.py` | claim_runs consumer: atomic `find_one_and_update` claim, one run at a time, SIGTERM → graceful checkpoint-and-requeue; same backend image as the API, different command |
| `backend/database.py` | Motor client built from `app.config.settings`; collections: policies, claims, claim_documents, counters, events, claim_runs, seed_state, users, refresh_tokens, audit_log, notifications; seed-on-startup when empty (10 policies / 15 historical claims, marker `seed:v1`) |
| `backend/app/config.py` | pydantic-settings `Settings` — every env var with dev defaults + production validators (non-empty `JWT_SECRET`, explicit `CORS_ORIGINS`) |
| `backend/app/schemas.py` | Pydantic request/response and stage result models (typed agent IO) |
| `backend/app/auth_routes.py`, `auth_store.py`, `security.py`, `deps.py` | Invite-gated registration, login, JWT access tokens, SHA-256-hashed rotating refresh sessions, CSRF, role guards (`require_adjuster`) |
| `backend/app/fraud.py`, `rating.py`, `stp.py` | Deterministic fraud rules + duplicate-incident fingerprints, pure-Python money math/risk scoring, STP gate + severity derivation |
| `backend/app/workbench.py`, `workbench_routes.py` | Adjuster queue, SLA aging, reasoned overrides with append-only audit trail (`audit_log`) |
| `backend/app/status_portal.py`, `status_routes.py` | Public customer portal: access codes, masked status, uniform 404s, per-IP rate limit |
| `backend/app/events.py` | Durable `events` collection + SSE tailing with replay |
| `backend/app/analytics.py`, `usage.py` | Ops analytics (distributions, STP rate, cycle-time percentiles); LLM usage logging |
| `backend/app/notify_routes.py` | Milestone notifications (console driver; `NotificationProvider` interface for future drivers) |
| `backend/app/evidence_pack.py`, `fnol_drafts.py`, `counters.py` | Evidence-pack export, resumable FNOL drafts, claim-number counters |
| `backend/app/middleware.py`, `rate_limit.py`, `logging_setup.py`, `storage/` | Request-ID middleware, slowapi limiter, structlog setup, provider-based document uploads |
| `backend/pdf_generator.py` | fpdf2 decision letters, returned as base64 |
| `backend/tests/`, `tests/` | 19 backend test modules + root smoke/config suite — 336 tests total, hermetic via mongomock |
| `frontend/vite.config.js` | Vite 7 + Vitest config: JSX-in-`.js` shim for CRA-era sources, dev proxy `/api` + `/events` → `localhost:8001` (`VITE_PROXY_TARGET`), jsdom setup, `e2e/` excluded from the Vitest pool |
| `frontend/src/App.js` | Router: `/` Dashboard, `/new-claim` (FNOL wizard), `/claims/:id` ClaimDetail, `/policies` PolicyLookup, `/history` ClaimHistory, `/status` StatusPortal, `/workbench` (adjuster-guarded: queue, `claims/:claimId` CaseView, `ops` OpsAnalytics) |
| `frontend/src/components/` | Dashboard, ClaimHistory, PolicyLookup, NotificationBell, RiskGauge, Sidebar, StatusPortal, StatusTimeline, `workbench/` (WorkbenchQueue, CaseView, OverrideModal, OpsAnalytics), `ui/` shadcn primitives |
| `frontend/src/features/claims/` | FNOLWizard (multi-step, resumable drafts), PipelineBoard (live agent board), TraceTimeline, pipelineState, `wizard/` (drafts, validation) |
| `frontend/src/hooks/` | `usePipelineEvents` — SSE consumption with polling fallback |
| `frontend/src/test/` | Vitest setup + MSW handlers |
| `frontend/e2e/` | 8 Playwright specs (17 tests, fixture mode; `agent-failure` and `refusal-fallback` are FIXTURE_FAULT-gated), `utils.js` (test credentials, per-spec policy assignment map), dogfood capture scripts |
| `frontend/nginx.conf.template` | Web tier: SPA on :80, `/api` + `/events` proxied to `${API_UPSTREAM}` (unbuffered for SSE) |
| `docker-compose.yml` | Production-mirroring stack: api `:8000`, worker (same image, `python worker.py`), web (nginx `3000:80`), mongo:7; fixture/E2E env plumbing (`LLM_PROVIDER`, `FIXTURE_FAULT`, `INVITE_CODE`, rate limits) |
| `.github/workflows/ci.yml` | 8-job CI: backend-ruff, backend-pytest, frontend-lint, frontend-test, docker-builds, security-audit, e2e-compose (with fault phases), lint-workflows |
| `docs/` | `user-guide.md`, `operator-guide.md`, `api-reference.md` (39 routes), `demo-script.md`, `acceptance.md` (AC-1..AC-13 evidence sweep) |
| `README.md` | Product overview + quickstart (note: still says "five-agent" in places; the code runs six stages — see `agents.py`) |
