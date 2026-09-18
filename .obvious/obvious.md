# ClaimOS — Agent Guidance

Glass-box AI claims adjudication platform. A FastAPI API and a separate durable worker run a six-stage checkpointed agent pipeline (INTAKE → POLICY → DOCUMENT → FRAUD → ELIGIBILITY → DECISION) over MongoDB; a React 19 + Vite console streams every step via replayable SSE. Auth is JWT access tokens with rotating refresh cookies and role enforcement (adjuster vs customer); customers get a public status portal. Claim state lives in MongoDB, never in a process: the API mints a claim ID and enqueues a `claim_runs` document; the worker claims and executes it with per-stage checkpoints.

## Stack

| Layer | Tech | Dev port | Compose |
|---|---|---|---|
| API | Python 3.12 (containers/CI), FastAPI, uvicorn, Motor, pydantic-settings, structlog, slowapi | 8001 | `8000:8000` |
| Worker | Same backend image, different command: `python worker.py` (claim_runs queue consumer) | — | no published port |
| Web | React 19, Vite 7 + Vitest, Tailwind, shadcn/radix, TanStack Query, axios, react-router-dom 7; npm + package-lock.json (no yarn) | 5173 (Vite default) | nginx web tier, `3000:80` |
| Database | MongoDB (dev: local mongod or Atlas; compose: `mongo:7`, internal-only) | 27017 | internal |
| E2E | Playwright (Chromium) against the compose stack on the fixture LLM | — | via compose |

## Environment variables

`backend/app/config.py` is the single source of truth (pydantic-settings `Settings`, loads `backend/.env`; `backend/.env.example` is committed). Every field has a development default, so the backend imports and boots **without** `backend/.env` — tests and CI never need it. `ENVIRONMENT=production` fails boot unless `JWT_SECRET` is non-empty and `CORS_ORIGINS` lists explicit origins (wildcard `*` rejected).

- `MONGO_URL` (default `mongodb://127.0.0.1:27017`), `DB_NAME` (default `claimos`; seeded on startup when empty — 10 policies, 15 historical claims, optional demo users)
- `ENVIRONMENT` — `development` | `staging` | `production`
- `CORS_ORIGINS` — comma-separated allowlist (default `*`)
- `ANTHROPIC_API_KEY` — optional at boot; the first real LLM call fails closed if unset. `LLM_PROVIDER` = `anthropic` (default) | `fixture` — the fixture adapter (`backend/app/llm/fixture.py`) scripts the whole pipeline deterministically (zero API spend) and is what CI/E2E run on. `FIXTURE_FAULT=timeout:intake|refusal:decision` injects live-LLM failure classes. Adapter tuning: `LLM_MAX_RETRIES=3`, `LLM_TIMEOUT_S` (config default 180s).
- Auth: `JWT_SECRET` (HS256; required non-empty in production — dev falls back to an obviously-insecure secret), `ACCESS_TOKEN_TTL_MINUTES=15`, `REFRESH_TOKEN_TTL_DAYS=7`, `INVITE_CODE` (exact code `/api/auth/register` requires; empty disables registration entirely), `DEMO_ADJUSTER_EMAIL/PASSWORD`, `DEMO_CUSTOMER_EMAIL/PASSWORD` (seeded at startup for dev; empty skips that role), `COOKIE_SECURE` (forced outside development)
- Rate limits (slowapi syntax, per client IP): `LOGIN_RATE_LIMIT=5/minute`, `FNOL_RATE_LIMIT=10/minute`, `STATUS_LOOKUP_RATE_LIMIT=60/minute`, `ACCESS_CODE_RECOVERY_RATE_LIMIT=3/minute`
- Email delivery: `EMAIL_PROVIDER` = `console` (default, logs structured events) | `smtp` — SMTP config: `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS` (true) / `SMTP_SSL` (false). `EMAIL_DISABLED=false` — `ENVIRONMENT=production` boots only with configured SMTP (`SMTP_HOST`+`SMTP_FROM`) or `EMAIL_DISABLED=true`.
- STP gate: `STP_CONFIDENCE_THRESHOLD=0.85`, `STP_LOW_SEVERITY_AMOUNT=10000`, `STP_LOW_SEVERITY_TYPES=theft,weather_damage,vandalism`
- Uploads: `UPLOAD_DIR=/data/uploads`, `UPLOAD_MAX_BYTES` (10 MiB), `UPLOAD_ALLOWED_CONTENT_TYPES`
- Worker: `WORKER_POLL_INTERVAL_S=1`
- Frontend: `VITE_API_BASE_URL` (build-time; `frontend/.env.example` committed, default `http://localhost:8001`), `VITE_PROXY_TARGET` (dev-proxy override)

## Commands (from repo root)

```bash
# 1. MongoDB (if not running locally)
/home/user/dl/mongodb-linux-x86_64-debian12-8.0.12/bin/mongod \
  --dbpath /home/user/data/db --port 27017 --fork \
  --logpath /home/user/data/mongod.log --bind_ip 127.0.0.1

# 2. Backend (backend/ as app dir; sandbox venv at /home/user/venv-claimos)
cd backend && /home/user/venv-claimos/bin/uvicorn server:app --host 0.0.0.0 --port 8001

# 3. Worker — required for claims to actually adjudicate (the API only enqueues)
cd backend && python worker.py

# 4. Frontend (deps: cd frontend && npm install)
cd frontend && npm run dev
```

Compose (mirrors the production topology — nginx web tier 3000→80, api 8000, worker, mongo:7): `docker compose up -d --build`, then open `http://localhost:3000`. Fixture/E2E mode (deterministic, no API spend): `LLM_PROVIDER=fixture INVITE_CODE=<code> docker compose up -d --build`.

Health checks: `GET /api/` → `{"status":"ok","service":"ClaimOS API","agents":6}`; `GET /api/health` is liveness (never touches the DB); `GET /api/ready` pings Mongo (503 if down). Frontend dev server: `http://localhost:5173`; compose web tier: `http://localhost:3000`.

## CI (`.github/workflows/ci.yml` — 8 jobs on every PR to main)

1. **backend-ruff** — `ruff check backend tests` (ruff pinned 0.16.8; config in `backend/pyproject.toml`, select E4/E7/E9/F)
2. **backend-pytest** — `pytest tests/ backend/tests/ -v` (mongo:7 service sidecar; mongomock keeps the suite hermetic)
3. **frontend-lint** — npm ci; there is no `lint` script in package.json, so the job falls back to `npx eslint .` (committed `eslint.config.js`)
4. **frontend-test** — `CI=true npm test` (Vitest + Testing Library + MSW, 17 suites)
5. **docker-builds** — backend + frontend images (GHCR push on main)
6. **security-audit** — pip-audit and `npm audit --omit=dev`, both blocking
7. **e2e-compose** — boots the compose stack on `LLM_PROVIDER=fixture`, waits for `/api/ready` and the web tier, runs the 8 Playwright specs (17 tests), then two fault phases recreate the worker with `FIXTURE_FAULT=timeout:intake` (agent-failure spec) and `FIXTURE_FAULT=refusal:decision` (refusal-fallback spec)
8. **lint-workflows** — actionlint 1.7.12

QA evidence: `docs/acceptance.md` maps AC-1..AC-13 to the tests and screenshots that prove them; UI PRs record evidence at the tested head SHA (`obvious autobuild upload`), and CI uploads Playwright artifacts on failure.

## Codebase map

See [codebase-map.md](codebase-map.md). One-line version: `backend/server.py` (FastAPI app + `/api` routers) + `backend/agents.py` (six `PIPELINE_STAGES`, typed stage IO) + `backend/pipeline.py` (claim_runs queue, checkpointed runner) + `backend/worker.py` (queue consumer) + `backend/database.py` (Motor, collections, seed) + `backend/app/` (config, auth, fraud/rating/stp, workbench, status portal, analytics, events, uploads, LLM adapter/fixture) + `frontend/src/` (Vite app: dashboard, FNOL wizard + pipeline board, workbench, status portal).

## Local verification

- Backend tests: `pytest tests/ backend/tests/` — 336 passed in ~6s, no live MongoDB needed (mongomock-motor). Verified 2026-09-18 at commit `8b2cb8d` with the sandbox venv `/home/user/venv-claimos` (Python 3.13; containers/CI pin 3.12).
- Backend lint: `ruff check backend tests` (flake8 is gone; ruff config in `backend/pyproject.toml`).
- Frontend unit: `cd frontend && npm test`; frontend lint: `cd frontend && npx eslint .`
- E2E: fixture-mode compose stack + `cd frontend && npx playwright test`

## Snapshot

- Snapshot ID: `09rksuhd6vory7wd2p4r:default`
- Captured: 2026-09-17T19:05:39Z (e2b template with MongoDB, backend, and frontend running)

## Known quirks

- `README.md` (real since PR #32) still says "five-agent pipeline" in its how-it-works and architecture sections; `agents.py` runs six stages — `FRAUD_AGENT` (deterministic fraud rules + duplicate-incident similarity) sits between DOCUMENT and ELIGIBILITY. `GET /api/` reports `agents: len(PIPELINE_STAGES)` = 6.
- `frontend/package.json` has no `lint` script — CI's frontend-lint job falls back to `npx eslint .` against the committed `eslint.config.js`.
- The JSX-in-`.js` esbuild shim in `vite.config.js` exists only for CRA-era sources; new components/tests use `.jsx` (the Vitest suite is `.test.jsx`).
- Playwright specs live in `frontend/e2e/*.spec.js` and are deliberately excluded from the Vitest pool — importing `@playwright/test` inside a Vitest worker crashes it.
- `docs/acceptance.md` records AC-12 (Railway + Atlas deployment) as an open GAP — it needs owner-supplied deploy credentials.
- Product requirements live outside the repo (Obvious artifacts referenced from `docs/acceptance.md`); there is no `memory/` directory anymore.
