# ClaimOS Codebase Map

Depth cap: 2. Single table.

| Path | Purpose |
|---|---|
| `backend/server.py` | FastAPI app + `/api` routes: claim submit (kicks off async pipeline), claims CRUD, policy lookup/search, dashboard stats, SSE stream (`/api/claims/stream/{id}`), PDF download, health |
| `backend/agents.py` | 5-agent claim pipeline orchestrator (`ClaimOrchestrator`): INTAKE → POLICY → DOCUMENT → ELIGIBILITY → DECISION, each an LLM call via `emergentintegrations.LlmChat` |
| `backend/database.py` | Motor MongoDB client, collections (`policies`, `claims`, `claim_documents`), seed data (10 policies, 15 historical claims), seed-on-startup |
| `backend/pdf_generator.py` | fpdf2 claim decision PDF returned as base64 |
| `backend/requirements.txt` | Pinned Python deps (FastAPI, Motor, uvicorn, litellm/openai/gemini SDKs, fpdf2, flake8/pytest; includes `emergentintegrations==0.1.0` which is NOT installable from public PyPI) |
| `backend/.env` | Local only (gitignored): `MONGO_URL`, `DB_NAME`, `EMERGENT_LLM_KEY`, `CORS_ORIGINS` |
| `frontend/src/App.js` | Router: `/` (Dashboard), `/new-claim`, `/policies`, `/history` |
| `frontend/src/components/` | `Dashboard.js`, `NewClaim.js` (form + EventSource SSE consumer), `PolicyLookup.js`, `ClaimHistory.js`, `RiskGauge.js`, `Sidebar.js` |
| `frontend/src/components/ui/` | shadcn/ui primitives |
| `frontend/craco.config.js` | CRA overrides: `@` path alias, optional Emergent "visual edits" webpack plugin (skips gracefully when absent) |
| `frontend/.env` | Local only (gitignored): `REACT_APP_BACKEND_URL` |
| `.emergent/emergent.yml` | Emergent platform manifest (`fastapi_react_mongo_shadcn` base image) |
| `memory/PRD.md` | Product requirements doc |
| `backend_test.py` | Standalone API test script; defaults to Emergent preview URL and `/app` results path — import `ClaimOSAPITester` with explicit `base_url` instead |
| `test_reports/` | Saved test artifacts from Emergent runs |
| `tests/` | Empty placeholder (`__init__.py` only) |
| `design_guidelines.json` | Emergent UI design tokens |
