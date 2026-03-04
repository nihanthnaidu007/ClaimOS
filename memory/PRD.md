# ClaimOS - Agentic Claims Intelligence

## Problem Statement
Build a production-grade Insurance Claim Processing Agent — a full-stack web application demonstrating real agentic AI architecture with 5 specialized AI agents, tool use, shared state passing, routing logic, and live SSE streaming.

## Architecture
- **Frontend**: React CRA + Tailwind + Shadcn UI, dark Palantir-style theme
- **Backend**: FastAPI (Python) + MongoDB
- **AI**: Anthropic Claude via emergentintegrations (Emergent LLM Key)
- **Streaming**: Server-Sent Events (SSE) via FastAPI StreamingResponse
- **PDF**: fpdf2 library
- **Email**: Mocked (always emailSent: false)

## User Personas
- Insurance operations teams
- Claims adjusters
- Technical decision makers evaluating agentic AI

## Core Requirements
1. 5 sequential AI agents: Intake → Policy → Document → Eligibility → Decision
2. Live SSE streaming of agent reasoning to frontend
3. Policy database with 10 seeded records
4. Historical claims for fraud context
5. PDF generation for claim decisions
6. Dark operational UI (Palantir/Bloomberg Terminal aesthetic)

## What's Been Implemented (2026-03-04)
- [x] Full backend: FastAPI with all API routes
- [x] MongoDB collections: policies, claims, claim_documents
- [x] Database seeding: 10 policies + 15 historical claims
- [x] 5 AI agents with Claude integration
- [x] ClaimOrchestrator with SSE streaming
- [x] PDF generation with fpdf2
- [x] Dashboard with stats
- [x] New Claim form with policy lookup
- [x] Live Agent Pipeline Board (SSE)
- [x] Decision Panel with risk gauge
- [x] Policy Lookup page with search
- [x] Claim History with sortable table + expandable traces
- [x] Sidebar navigation
- [x] Mobile responsive design
- [x] All data-testid attributes

## Known Issues
- Emergent LLM Key budget exceeded (user needs to add balance at Profile → Universal Key → Add Balance)

## Prioritized Backlog
### P0 (Blocking)
- LLM key budget replenishment for AI processing

### P1 (Important)
- Email integration (currently mocked)
- Claim PDF download from history page

### P2 (Nice to have)
- Agent performance analytics dashboard
- Batch claim processing
- Claim search/filter in history
- Export claims to CSV
- Real-time notifications for claim updates

## Next Tasks
1. User adds balance to Universal Key
2. Test full end-to-end claim processing pipeline
3. Add email integration when Gmail SMTP credentials available
