# ClaimOS

Glass-box AI claims adjudication. ClaimOS walks an insurance claim through a
five-agent pipeline — **Intake → Policy → Documents → Eligibility → Decision** —
and streams every step live, so a human can watch exactly how the machine
reached its verdict. Every decision ships with a downloadable PDF letter and a
complete per-agent reasoning trace.

> **Status:** pre-production baseline. This tree is the starting point of an
> in-flight production upgrade (authentication, input validation, durable
> pipeline execution, CI/CD). Treat it as a demo, not a deployable system.

## How it works

1. **Submit a claim** — policy number, incident details, pasted evidence text.
2. **Five agents run in sequence**, each a Claude-backed step with a strict
   JSON contract. Deterministic MongoDB tool calls (policy lookup, claim
   history) run *before* the LLM and hand it facts, not tool schemas.
3. **Halt semantics** — the pipeline stops after Intake (invalid claim) or
   Policy (policy not found / suspended) and records why.
4. **Live stream** — every `agent_start` / `agent_complete` / `agent_error`
   event is pushed to the UI over Server-Sent Events while the pipeline runs.
5. **Decision letter** — the Decision agent produces a verdict, payout amount,
   and next steps, rendered as a PDF with the reasoning trace.

## Architecture

| Layer | Tech | Where |
|---|---|---|
| API | FastAPI, REST + hand-rolled SSE | `backend/server.py` |
| Agents | Five-step orchestrator, tool-first LLM calls | `backend/agents.py` |
| Data | MongoDB via Motor, seeded demo data | `backend/database.py` |
| PDF | fpdf2 decision letters (base64 over JSON) | `backend/pdf_generator.py` |
| Web | React 19 + CRA/craco, Tailwind, shadcn/radix | `frontend/` |

```
frontend (CRA dev server, :3000)
   │  POST /api/claims ──────────► FastAPI (:8001)
   │  GET  /api/claims/stream/{id} ◄── SSE: one event per agent step
   ▼
ClaimOrchestrator ── five agents ──► MongoDB (policies, claims, claim_documents)
```

The whole pipeline currently runs in one backend process (in-memory SSE queues,
in-process claim counter, fire-and-forget tasks). Making that state durable is
a primary goal of the production upgrade.

## Features

- **Dashboard** — claim stats, total payout, average risk score, active
  policies, recent claims.
- **Guided submission** with a live pipeline board showing each agent's status
  and reasoning trace as it streams in.
- **Policy lookup** by exact policy number or free-text search.
- **Claim history** — sortable table with expandable per-agent traces.
- **PDF decision letters** for adjudicated claims.
- **Seeded demo data** — 10 policies and 15 historical claims are seeded
  automatically when the database is empty.

## Environment variables

Copy `backend/.env.example` to `backend/.env` and set at least the required
vars — the backend loads `backend/.env` on startup.

| Variable | Where | Required | Default | Purpose |
|---|---|---|---|---|
| `MONGO_URL` | backend | yes | — | MongoDB connection string |
| `DB_NAME` | backend | yes | — | Database name |
| `CORS_ORIGINS` | backend | no | `*` | Comma-separated allowed origins; use an explicit allowlist outside local dev |
| `REACT_APP_BACKEND_URL` | frontend | yes | `http://localhost:8001` | API base URL, baked into the bundle at build time |

`backend/agents.py` also reads `EMERGENT_LLM_KEY` (legacy LLM wiring, default
empty). It is intentionally absent from `.env.example` — the Emergent
integration is being replaced by a provider-neutral LLM adapter, and the key
must never be committed.

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

**Frontend:**

```bash
cd frontend
npm install          # (the project historically used Yarn 1; npm works too)
npm start            # dev server on http://localhost:3000
```

## Testing

```bash
# Backend lint (ruff config lives in backend/pyproject.toml)
cd backend && ruff check .

# Backend app boots (proves the import graph is intact)
cd backend && uvicorn server:app --port 8001    # Ctrl-C after the boot banner

# Frontend production build
cd frontend && npm run build
```

An automated backend test suite is part of the in-flight production upgrade.
Until it lands, verify a full loop manually: start the backend, submit a claim
for a seeded policy number from the UI, and watch the pipeline stream to a
decision letter.

## License

[MIT](LICENSE) — © Kalisetti Nihanth Naidu
