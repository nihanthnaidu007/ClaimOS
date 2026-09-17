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
| LLM | Anthropic adapter: structured outputs, retries, usage logging | `backend/app/llm/adapter.py` |
| Data | MongoDB via Motor, seeded demo data | `backend/database.py` |
| PDF | fpdf2 decision letters (base64 over JSON) | `backend/pdf_generator.py` |
| Web | React 19 + Vite 7 (Vitest), Tailwind, shadcn/radix | `frontend/` |

```
frontend (Vite dev server, :5173)
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
| `ANTHROPIC_API_KEY` | backend | no* | empty | Key for the LLM adapter; the first real LLM call fails closed if unset |
| `LLM_MAX_RETRIES` | backend | no | `3` | Bounded retries per LLM call |
| `LLM_TIMEOUT_S` | backend | no | `60` | Per-call timeout in seconds |
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

# Frontend unit tests (Vitest + Testing Library, API mocked with MSW)
cd frontend && npm test
```

The automated suite covers the agent contracts and the LLM adapter (structured
outputs, retries, fail-closed behavior) with a mocked Anthropic client. End-to-end
behavior — SSE streaming, PDF letters, seeded data — is still verified manually:
start the backend, submit a claim for a seeded policy number from the UI, and
watch the pipeline stream to a decision letter.

## License

[MIT](LICENSE) — © Kalisetti Nihanth Naidu
