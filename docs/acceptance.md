# Acceptance Coverage — AC-1..AC-13

Evidence sweep for the ClaimOS Production Upgrade Spec (`art_qGdARnzJ`, verification section), executed per the QA & E2E Test Plan (`art_x9dhhWUl`). This file records, for each acceptance criterion, the automated test or observable check that proves it, as of `test/e2e-suite`.

How to reproduce the E2E evidence:

```bash
# Deterministic, zero-LLM-spend stack (fixture adapter, no ANTHROPIC_API_KEY):
docker compose --profile e2e up -d --build
# wait for /api/ready to report database ok, then:
cd frontend && npx playwright test
```

The fixture adapter (`backend/app/llm/fixture.py`, `LLM_PROVIDER=fixture`) makes the whole pipeline deterministic: every E2E run exercises the same agent outputs, ratings, and routing with zero API spend. Fault injection (`FIXTURE_FAULT=timeout:intake|refusal:decision|…`) drives the failure paths.

## Coverage table

| AC | Criterion | Proven by (all green on this branch unless noted) | Evidence |
|---|---|---|---|
| AC-1 | Authn/authz on every non-public endpoint | `backend/tests/test_auth.py` (route-table parametrization, refresh rotation, CSRF), role interlock in `test_workbench.py`; E2E: every spec registers real users and logs in through the UI gate; customer-token 403s asserted in the adjuster-polling helper design | pytest |
| AC-2 | Zero scaffold artifacts | CI `lint` job (`rg` scaffold-reference gate, `.env` tracking check, README/LICENSE presence); CI `lint-workflows` (actionlint) | CI |
| AC-3 | Persisted traces, halt-on-error, retry-on-save-failure | `backend/tests/test_pipeline.py::test_full_run_checkpoints_usage_and_finalizes`, `::test_agent_failure_persists_sanitized_failed_claim`, `::test_invalid_intake_halts_as_pending`, `::test_shutdown_before_first_stage_requeues_intact`; E2E `agent-failure.spec.js` (visible PROCESSING FAILED), `validation.spec.js` (unknown-policy halt with visible reason), `refusal-fallback.spec.js` (refusal retry + template-letter fallback) | pytest + PW |
| AC-4 | Deterministic pure-Python money math | `backend/tests/test_rating.py` (golden payouts/risk vectors), `test_llm_fixture.py` (LLM outputs feed judgment fields only — fixture echoes, never recalculates, arithmetic) | pytest |
| AC-5 | STP gate both branches | `backend/tests/test_stp.py::test_gate_auto_finalizes_when_all_legs_hold`, `::test_stp_escalates_low_confidence_with_reason`; E2E `stp-approval.spec.js` ($2,000 theft → `auto_approved`, $1,500 payout, access code surfaced) and `override.spec.js` ($4,200 accident → escalated) | pytest + PW |
| AC-6 | SSE replay + two-worker delivery | `backend/tests/test_pipeline.py::test_last_event_id_replay`, `::test_two_pollers_execute_each_claim_exactly_once`; E2E `sse-fallback.spec.js` (SSE blocked → board still finalizes via polling; connection chip never reports LIVE while degraded); compose runs API + worker as separate containers sharing Mongo | pytest + PW |
| AC-7 | Override validation, audit, SLA aging | `backend/tests/test_workbench.py` (422 without reason, audit append, SLA state machine, case summary); E2E `override.spec.js` (missing-reason 422, client-mirrored disabled submit, reasoned override → `overridden` + exactly one audit entry with before/after + verbatim reason) | pytest + PW |
| AC-8 | Public status portal | `backend/tests/test_status_portal.py` (`test_valid_code_returns_masked_status`, `test_wrong_code_is_generic_404`, `test_unknown_claim_number_returns_identical_404`, `test_lookup_rate_limited_per_ip`); E2E `status-portal.spec.js` (uniform miss text, valid-code timeline with milestones, per-IP 429 message) | pytest + PW |
| AC-9 | Analytics against fixtures | `backend/tests/test_analytics.py` (distribution, STP rate, cycle-time percentiles over the seeded dataset) | pytest |
| AC-10 | Frontend build, decomposition, component + E2E tests | CI `frontend-lint` + `frontend-test` (Vitest/RTL, 19+ suites, coverage ≥ 88%) + `e2e-compose` (the eight Playwright specs against the compose stack, fixture mode) | CI |
| AC-11 | CI green, protected main, compose serves the app | CI: 8 jobs on PR (`backend-ruff`, `backend-pytest`, `frontend-lint`, `frontend-test`, `docker-builds`, `security-audit`, `e2e-compose`, `lint-workflows`); `e2e-compose` boots the full compose stack, waits on `/api/ready`, and runs the suite against the served UI — the compose-smoke proof | CI |
| AC-12 | Deployment live and observable (Railway + Atlas M0) | **GAP — requires deployed environment.** Not verifiable from CI/sandbox: needs Railway service deploy + Atlas M0 credentials, then the checklist (`/health` 200, `/ready` database ok, request-ID logs, mongodump job). **Owner: repository owner** (deployment credentials are user-scoped secrets; the compose stack in `e2e-compose` verifies the same readiness contract in CI) | checklist (pending) |
| AC-13 | Dogfood evidence per UI PR | `obvious autobuild upload` per acceptance criterion with a UI surface, captured at the tested head SHA (viewport 1440×900, before/after where visual); CI failures upload Playwright traces/reports as artifacts | QA uploads |

## Suite inventory (this branch)

- **Backend (pytest):** 335 tests green (includes `test_llm_fixture.py`, 22 tests, added this wave for the deterministic adapter + fault semantics).
- **Frontend (Vitest/RTL):** green in CI (`frontend-test`).
- **Playwright E2E (8 spec files, fixture mode):** fnol (3), validation, stp-approval, override, status-portal, sse-fallback, agent-failure (fault-gated), refusal-fallback (fault-gated). Serial mode, one worker; incident dates are distinct per spec because incident fingerprints are (policy, date, type) — duplicates intentionally escalate as fraud twins.

## Known gaps / deferred

| Gap | Why | Owner |
|---|---|---|
| AC-12 deployment checklist | Requires live Railway + Atlas M0 credentials (user-scoped secrets) | Repo owner |
| Forced-failure reason omits LLM error class | Failure banner/`failure.reason` names the agent ("Agent ELIGIBILITY_AGENT failed") but not the error class ("LLMTimeout after 3 retries") required by the QA plan's AC-3 shape. Sanitization is intentional (customer-safe surface); the class belongs in the adjuster-visible record. Tracked for the backend wave — not fixable in an E2E PR | Backend wave |
| Customer login lands on "Dashboard stats failed to load" | Customers hitting the ops dashboard (adjuster-only `/api/dashboard/stats`) see an error surface instead of their own landing view. UX defect, product code — tracked separately, not fixable in an E2E PR | Frontend wave |
