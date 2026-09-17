# ClaimOS Codebase Map

Depth cap: 2. Single table.

| Path | Purpose |
|---|---|
| `backend/server.py` | FastAPI app + `/api` routes: claim submit (kicks off async pipeline), claims CRUD, policy lookup/search, dashboard stats, SSE stream (`/api/claims/stream/{id}`), PDF download, health |
| `backend/agents.py` | 5-agent claim pipeline orchestrator (`ClaimOrchestrator`): INTAKE → POLICY → DOCUMENT → ELIGIBILITY → DECISION, each an LLM call via the Anthropic adapter (`app/llm/adapter.py`) |
| `backend/database.py` | Motor MongoDB client, collections (`policies`, `claims`, `claim_documents`), seed data (10 policies, 15 historical claims), seed-on-startup |
| `backend/pdf_generator.py` | fpdf2 claim decision PDF returned as base64 |
| `backend/requirements.txt` | Pinned Python deps (FastAPI, Motor, uvicorn, anthropic SDK, fpdf2, pytest, ruff) |
| `backend/.env` | Local only (gitignored): `MONGO_URL`, `DB_NAME`, `ANTHROPIC_API_KEY`, `CORS_ORIGINS` |
| `frontend/src/App.js` | Router: `/` (Dashboard), `/new-claim`, `/policies`, `/history` |
| `frontend/src/components/` | `Dashboard.js`, `NewClaim.js` (form + EventSource SSE consumer), `PolicyLookup.js`, `ClaimHistory.js`, `RiskGauge.js`, `Sidebar.js` |
| `frontend/src/components/ui/` | shadcn/ui primitives |
| `frontend/.env` | Local only (gitignored): `REACT_APP_BACKEND_URL` |
| `memory/PRD.md` | Product requirements doc |
