# ClaimOS — Agent Guidance

Agentic insurance claims platform: FastAPI backend orchestrates a 5-agent claim pipeline (intake → policy → documents → eligibility → decision) over MongoDB, with a React dashboard that watches pipeline progress via Server-Sent Events.

## Stack

| Layer | Tech | Port |
|---|---|---|
| Backend | Python 3.13, FastAPI, uvicorn, Motor (MongoDB async) | 8001 |
| Database | MongoDB (installed from official tarball at `/home/user/dl/mongodb-linux-x86_64-debian12-8.0.12/`) | 27017 |
| Frontend | React 19, Create React App + craco, shadcn/ui, Tailwind, yarn 1.22.22 | 3000 |

## Environment variables

`backend/.env` (gitignored by `*.env` pattern — must be created manually):
- `MONGO_URL` — **required** (e.g. `mongodb://127.0.0.1:27017`)
- `DB_NAME` — **required** (e.g. `claimos`; created and seeded automatically on backend startup)
- `ANTHROPIC_API_KEY` — optional; without it LLM-backed agent pipeline runs fail fast with a typed adapter error and submitted claims stay `pending` with the failure recorded in `agent_logs` (all other API/UI functionality works)
- `CORS_ORIGINS` — optional (default `*`)

`frontend/.env` (gitignored):
- `REACT_APP_BACKEND_URL` — backend origin (e.g. `http://localhost:8001`)

No `.env.example` exists in the repo — these were recovered from source (`backend/server.py`, `backend/database.py`, `backend/agents.py`, `frontend/src/components/*.js`).

## Commands (from repo root `/home/user/work/ClaimOS`)

```bash
# 1. MongoDB (if not running)
/home/user/dl/mongodb-linux-x86_64-debian12-8.0.12/bin/mongod \
  --dbpath /home/user/data/db --port 27017 --fork \
  --logpath /home/user/data/mongod.log --bind_ip 127.0.0.1

# 2. Backend (must run with backend/ as app dir; venv at /home/user/venv-claimos)
cd backend && /home/user/venv-claimos/bin/uvicorn server:app --host 0.0.0.0 --port 8001

# 3. Frontend (deps: cd frontend && yarn install)
cd frontend && BROWSER=none CI=false yarn start
```

Health check: `curl http://127.0.0.1:8001/api/` → `{"status":"ok","service":"ClaimOS API","agents":5}`. Frontend: `curl -s -o /dev/null -w '%{http_code}' http://localhost:3000/` → `200`.

## Codebase map

See [codebase-map.md](codebase-map.md). One-line version: `backend/server.py` (API routes) + `backend/agents.py` (pipeline) + `backend/database.py` (Mongo collections/seed) + `frontend/src/` (CRA app: Dashboard, NewClaim with SSE, PolicyLookup, ClaimHistory).

## Local verification

- Test suite: `cd /home/user/work/ClaimOS && /home/user/venv-claimos/bin/python -m pytest tests/ backend/tests/ -v` (no live MongoDB needed; CI runs the same command).
- Frontend unit tests: run from `frontend/` (Vite + Vitest).
- Backend lint: `/home/user/venv-claimos/bin/flake8 --select=F backend/` → only 2 trivial F401 unused imports.
- Browser evidence from onboarding: `/home/user/data/evidence/dashboard.png`, `/home/user/data/evidence/new-claim.png` (Playwright + headless Chromium, 0 console errors).

## Snapshot

- Snapshot ID: `09rksuhd6vory7wd2p4r:default`
- Captured: 2026-09-17T19:05:39Z (e2b template with MongoDB, backend, and frontend running)

## Known quirks

- `README.md` is a placeholder ("Here are your Instructions"); `memory/PRD.md` holds the product requirements.
- `database.py` calls `os.environ['MONGO_URL']` at import time — the backend will not start without `backend/.env`.
