---
name: local-dev
description: How to bring ClaimOS (FastAPI + MongoDB + React/CRA) up locally in this sandbox — verified during automated onboarding 2026-09-17.
---

# Local dev — ClaimOS

## Verified working state (2026-09-17)

- MongoDB 8.0.12 on `127.0.0.1:27017` (official debian12 tarball, dbpath `/home/user/data/db`)
- Backend: uvicorn on `0.0.0.0:8001`, venv `/home/user/venv-claimos` — health `{"status":"ok","service":"ClaimOS API","agents":5}`
- Frontend: CRA dev server on `3000`, compiled successfully, renders dashboard with live data
- Seed data: auto-seeded on backend startup (10 policies, 15 historical claims)
- Evidence: `/home/user/data/evidence/dashboard.png`, `new-claim.png` (0 console errors); API suite 8/8

## Startup order

1. Mongo → 2. backend (needs `backend/.env`) → 3. frontend (needs `frontend/.env`). Exact commands live in `.obvious/obvious.md`.

## Gotchas learned onboarding

- **`backend/.env` is mandatory** — `database.py` does `os.environ['MONGO_URL']` at import; uvicorn fails to boot without it. Minimum: `MONGO_URL=mongodb://127.0.0.1:27017`, `DB_NAME=claimos`.
- **uvicorn must run from `backend/`** as the app dir (`uvicorn server:app`), not repo root.
- **`emergentintegrations` is not on public PyPI.** The venv contains a stub (`emergentintegrations/llm/chat.py`) that keeps imports working; real LLM pipeline steps raise a clear RuntimeError until the Emergent base-image package + `EMERGENT_LLM_KEY` are available. Without a key, claims submit fine but stay `pending` with agent errors in `agent_logs` — this is graceful, not a crash.
- **`backend_test.py` cannot be run directly** (hardcoded Emergent preview URL, writes to `/app/test_reports`). Import `ClaimOSAPITester` and pass `base_url="http://127.0.0.1:8001/api"`.
- **No frontend unit tests exist** — `yarn test` exits 1 with "0 matches"; treat as expected, not a regression.
- **No lockfile for the frontend** — no `yarn.lock` in git; `yarn install` resolves fresh.
- Ports 27017/8001/3000 were free at onboarding; if 27017 is taken, the backend import chain fails (service_unreachable symptom = `ServerSelectionTimeoutError` in backend log).
